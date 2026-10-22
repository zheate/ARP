"""Hardware-free lifecycle, ownership and persistence acceptance."""

import json
import math
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from combined_test.automation import AutomaticTestSettings, AutomaticTestState
from combined_test.excel_export import ExcelTestRecord
from combined_test.models import PowerMeterReading
from combined_test.power_stability import learn_background
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.errors import CommandRejected
from tauri_bridge.settings import JsonSettings


class PowerStabilityRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.backend = HeadlessBackend(JsonSettings(Path(self.directory.name) / "settings.json"))
        self.station = self.backend.runtime
        self.clock = [1000.0]
        self.time_patch = patch("tauri_bridge.power_stability_runtime.time.monotonic", side_effect=lambda: self.clock[0])
        self.time_patch.start()
        self.station.configuration.update({
            "powerMeterDriverKind": "laserpoint", "powerMeterSerialNumber": "head-A",
            "powerMeterResource": "ASRL1::INSTR", "powerStabilityQuantizationW": 0.15,
            "powerStabilityInstrumentSettings": "100 W range; filter off; fixed range",
            "powerStabilityResponseS": 2, "powerStabilitySettleS": 1,
            "powerStabilityCurrentToleranceA": 0.01,
            "powerStabilityAbsoluteDriftW": 0.35, "powerStabilityParametersConfirmed": True,
        })
        self.station._validate_configuration(self.station.configuration)
        self.station.power_meter_reader = SimpleNamespace(is_ready=True, isRunning=lambda: True,
                                                         update_stability_settings=lambda *args, **kwargs: None)
        self.manager = self.station.power_stability
        self.station.configuration["powerStabilityConfirmedSignature"] = self.manager.confirmation_signature()
        self.publish(0, 0)

    def tearDown(self):
        self.time_patch.stop()
        self.backend.export_worker.graceful_stop(timeout_s=5)
        self.directory.cleanup()

    def publish(self, seconds, power=32.5, *, connection=0, generation=7, stable=False, transaction=None):
        self.clock[0] = 1000 + seconds
        reading = PowerMeterReading(seconds, power, stable, 0.15, 5, power,
                                    stability_generation=generation, connection_generation=connection,
                                    transaction_started_monotonic_s=self.clock[0] if transaction is None else transaction)
        self.station._on_power_reading(reading)
        return self.station.latest_power

    def install_model(self):
        samples = [(i * 0.5, 0.15 if i % 2 else 0) for i in range(361)]
        self.manager.model = learn_background(samples, identity=self.manager.identity(), model_id="runtime-model",
                                              created_at="2026-10-22", policy=self.manager.policy())
        return samples

    def verify(self):
        self.manager.start_background({"confirmedDark": True}, verify=True)
        started = self.clock[0] - 1000
        for i in range(1, 42):
            self.publish(started + i * 0.5, 0.15 if i % 2 else 0)
        self.assertIsNone(self.manager.blocker())
        self.assertIn("复核通过", self.manager.state)

    def activate_dual(self):
        self.station.configuration["powerStabilityMode"] = "dual"
        self.install_model()
        self.verify()
        self.manager.freeze()
        self.station.automatic.state = AutomaticTestState.WAITING_STABLE
        self.station.automatic_stability_generation = 7
        self.station.automatic.currents = (1, 2)
        self.station.automatic.current_index = 0
        self.manager.begin_point(7)

    def test_unknown_parameters_block_dual_but_do_not_take_over_shadow(self):
        self.station.configuration.update({"powerStabilityMode": "dual", "powerStabilityParametersConfirmed": False})
        self.assertIn("尚未确认", self.manager.blocker())
        self.station.configuration["powerStabilityMode"] = "shadow"
        self.assertIsNone(self.manager.blocker())
        self.station.automatic.state = AutomaticTestState.WAITING_STABLE
        self.manager.begin_point(7)
        self.assertTrue(self.publish(1, stable=True).stable)
        self.assertFalse(self.manager.decision.stable)

    def test_zero_current_dark_confirmation_and_ownership_are_required(self):
        for params in ({}, {"confirmedDark": False}, {"confirmedDark": "yes"}):
            with self.assertRaises(ValueError):
                self.manager.start_background(params)
        self.station.active_current_a = 1
        with self.assertRaisesRegex(ValueError, "归零"):
            self.manager.start_background({"confirmedDark": True})
        self.station.active_current_a = 0
        self.manager.start_background({"confirmedDark": True})
        with self.assertRaisesRegex(CommandRejected, "本底任务进行中"):
            self.backend.dispatch("powerSupply.setCurrent", {"currentA": 1})
        with self.assertRaisesRegex(ValueError, "本底任务"):
            self.station.configure({"powerStabilityAbsoluteDriftW": 0.5})
        self.backend.dispatch("powerMeter.background.cancel", {})
        self.assertIsNone(self.manager.job)

    def test_background_model_and_raw_learning_samples_are_persisted_atomically(self):
        samples = self.install_model()
        self.manager._finish_learning(self.manager.job_epoch, samples, self.manager.identity(), self.manager.policy())
        saved = self.backend.settings.get("runtime/powerBackgrounds")[self.manager.identity()]
        self.assertEqual(saved["samples"], samples)
        self.manager.model = None
        self.manager._load_model()
        self.assertEqual(self.manager.model.model_id, saved["model"]["model_id"])
        self.assertIsNone(self.manager.verified_at)
        self.assertEqual(len(json.loads(self.backend.settings.path.read_text())["runtime/powerBackgrounds"]), 1)

    def test_cancelled_learning_worker_cannot_replace_saved_model(self):
        samples = self.install_model()
        original = self.manager.model
        epoch = self.manager.job_epoch
        self.manager.cancel_background()
        self.manager._finish_learning(epoch, samples, self.manager.identity(), self.manager.policy())
        self.assertIs(self.manager.model, original)
        self.assertIsNone(self.backend.settings.get("runtime/powerBackgrounds"))

    def test_response_boundary_and_full_window_override_legacy_result_only_in_dual(self):
        self.activate_dual()
        start = self.clock[0] - 1000
        for i in range(1, 44):
            reading = self.publish(start + i * 0.5, 32.4 + (0.15 if i % 2 else 0), stable=True)
            self.assertFalse(reading.stable)
        reading = self.publish(start + 22, 32.4, stable=False)
        self.assertTrue(reading.stable, self.manager.decision)
        self.assertEqual(reading.power_w, 32.4)
        self.assertEqual(reading.stable_median_w, 32.4)
        snapshot = self.backend.snapshot()
        self.assertEqual(snapshot["automaticTest"]["stability"]["requiredS"], 20)
        self.assertEqual(snapshot["automaticTest"]["stability"]["holdRequiredS"], 5)
        self.assertEqual(snapshot["devices"]["powerMeter"]["powerBackground"]["algorithm"], "background-dual-window-v1")

    def test_old_inflight_transactions_and_generation_never_enter_new_window(self):
        self.activate_dual()
        start = self.clock[0] - 1000
        for i in range(1, 50):
            reading = self.publish(start + i * 0.5, stable=True, generation=6)
            self.assertFalse(reading.stable)
        self.assertEqual(len(self.manager.window.samples), 0)
        reading = self.publish(start + 25, stable=True, transaction=1000)
        self.assertFalse(reading.stable)
        self.assertEqual(len(self.manager.window.samples), 0)

    def test_reconnect_and_sampling_gap_cancel_candidate_and_require_zero_recheck(self):
        for reconnect in (True, False):
            with self.subTest(reconnect=reconnect):
                self.station.automatic.state = AutomaticTestState.IDLE
                self.station.configuration["powerStabilityMode"] = "dual"
                self.manager.last_received = None
                self.manager.last_sample = None
                self.clock[0] = 1000
                self.publish(0, 0)
                self.install_model()
                self.verify()
                self.manager.freeze()
                self.station.automatic.state = AutomaticTestState.WAITING_STABLE
                self.manager.begin_point(7)
                start = self.clock[0] - 1000
                for i in range(1, 50):
                    self.publish(start + i * 0.5)
                self.assertTrue(self.station.latest_power.stable)
                reading = self.publish(start + (25 if reconnect else 28), connection=1 if reconnect else 0, stable=True)
                self.assertFalse(reading.stable)
                self.assertIsNone(self.manager.verified_connection)

    def test_probe_gain_interval_profile_and_range_filter_bind_model_identity(self):
        self.install_model()
        identity = self.manager.identity()
        for key, value in (("softwareGain", 1.04), ("powerMeterSerialNumber", "other"),
                           ("powerMeterProfileVersion", 2), ("powerMeterIntervalMs", 200),
                           ("powerStabilityInstrumentSettings", "different range")):
            with self.subTest(key=key):
                self.assertNotEqual(self.manager.identity({**self.station.configuration, key: value}), identity)

    def test_parameter_confirmation_cannot_be_reused_for_another_probe_range_or_controller(self):
        self.assertTrue(self.manager.policy().ready)
        for key, value in (("powerMeterSerialNumber", "head-B"), ("powerSupplyKind", "ru"),
                           ("powerStabilityResponseS", 3), ("powerStabilityAbsoluteDriftW", 0.5)):
            previous = self.station.configuration[key]
            self.station.configuration[key] = value
            self.assertFalse(self.manager.policy().ready, key)
            self.station.configuration[key] = previous
        self.manager.instrument_token = "new range"
        self.assertFalse(self.manager.policy().ready)

    def test_confirming_valid_parameters_creates_a_backend_owned_signature(self):
        self.station.power_meter_reader = None
        self.station.configure({"powerStabilityParametersConfirmed": False})
        self.station.configure({"powerStabilityParametersConfirmed": True, "powerStabilityConfirmedSignature": "client-forged"})
        self.assertTrue(self.manager.policy().ready)
        self.assertEqual(self.station.configuration["powerStabilityConfirmedSignature"], self.manager.confirmation_signature())
        self.assertNotEqual(self.station.configuration["powerStabilityConfirmedSignature"], "client-forged")

    def test_actual_laserpoint_range_change_invalidates_the_learned_model(self):
        self.activate_dual()
        old_identity = self.manager.identity()
        old_model = self.manager.model
        self.clock[0] += 0.5
        reading = PowerMeterReading(self.clock[0] - 1000, 32.5, True, 0, 5, 32.5,
                                    stability_generation=7, instrument_configuration_token="laserpoint:2:1:0.001",
                                    transaction_started_monotonic_s=self.clock[0])
        self.station._on_power_reading(reading)
        self.assertFalse(self.station.latest_power.stable)
        self.assertNotEqual(self.manager.identity(), old_identity)
        self.assertIsNone(self.manager.verified_connection)
        self.assertIsNone(self.manager.model)
        self.assertIs(self.manager.frozen_model, old_model)

    def test_zero_command_retires_saved_models_across_restart_without_deleting_raw_data(self):
        samples = self.install_model()
        self.manager._finish_learning(self.manager.job_epoch, samples, self.manager.identity(), self.manager.policy())
        identity = self.manager.identity()
        self.manager.before_zero_command()
        self.assertNotEqual(identity, self.manager.identity())
        self.assertIsNone(self.manager.model)
        self.manager._load_model()
        self.assertIsNone(self.manager.model)
        saved = self.backend.settings.get("runtime/powerBackgrounds")
        self.assertEqual(saved[identity]["samples"], samples)

    def test_freeze_preserves_recheck_for_next_run_and_parameters_cannot_change_midrun(self):
        self.activate_dual()
        verified_at = self.manager.verified_at
        model = self.manager.frozen_model
        with self.assertRaisesRegex(ValueError, "期间不能修改"):
            self.station.configure({"powerStabilityAbsoluteDriftW": 0.5})
        for state in (AutomaticTestState.COMPLETED, AutomaticTestState.IDLE):
            self.station.automatic.state = state
            self.assertIsNone(self.manager.blocker())
            self.manager.freeze()
            self.assertEqual(self.manager.verified_at, verified_at)
            self.assertIs(self.manager.frozen_model, model)

    def test_continuous_unchanged_acquisition_has_no_sixty_second_expiry(self):
        self.activate_dual()
        verified_at = self.manager.verified_at
        self.station.automatic.state = AutomaticTestState.COMPLETED
        start = self.clock[0] - 1000
        for i in range(1, 242):
            self.publish(start + i * 0.5, 0.15 if i % 2 else 0)
        self.assertGreater(self.clock[0] - verified_at, 120)
        self.assertIsNone(self.manager.blocker())
        self.manager.freeze()
        self.assertEqual(self.manager.verified_at, verified_at)

    def test_switching_shadow_to_dual_does_not_require_an_extra_background_check(self):
        self.install_model()
        self.verify()
        verified_at = self.manager.verified_at
        self.station.configure({"powerStabilityMode": "dual"})
        self.assertTrue(self.manager.policy().ready)
        self.assertEqual(self.manager.verified_at, verified_at)
        self.assertIsNone(self.manager.blocker())

    def test_hung_or_invalid_acquisition_still_requires_recheck(self):
        self.activate_dual()
        self.station.automatic.state = AutomaticTestState.IDLE
        self.clock[0] += self.manager.policy().gap_s + 0.1
        self.assertIn("采样中断", self.manager.blocker())
        self.publish(self.clock[0] - 1000 + 0.1)
        self.assertIsNone(self.manager.verified_connection)
        self.assertIsNotNone(self.manager.blocker())
        self.verify()
        self.assertIsNone(self.manager.blocker())
        self.publish(self.clock[0] - 1000 + 0.1, math.nan)
        self.assertIsNone(self.manager.verified_connection)
        self.assertIsNotNone(self.manager.blocker())

    def test_reconnect_reuses_saved_model_only_after_recheck_and_then_allows_further_runs(self):
        samples = self.install_model()
        self.manager._finish_learning(self.manager.job_epoch, samples, self.manager.identity(), self.manager.policy())
        self.station.configuration["powerStabilityMode"] = "dual"
        self.verify()
        model_id = self.manager.model.model_id
        self.manager.freeze()
        self.publish(self.clock[0] - 1000 + 0.5, 0, connection=1)
        self.assertEqual(self.manager.model.model_id, model_id)
        self.assertIsNotNone(self.manager.blocker())
        self.manager.start_background({"confirmedDark": True}, verify=True)
        start = self.clock[0] - 1000
        for i in range(1, 42):
            self.publish(start + i * 0.5, 0.15 if i % 2 else 0, connection=1)
        self.assertIsNone(self.manager.blocker())
        for _ in range(3):
            self.manager.freeze()
            self.assertEqual(self.manager.model.model_id, model_id)
            self.assertIsNone(self.manager.blocker())

    def test_saved_model_does_not_persist_connection_verification_across_runtime_restart(self):
        from tauri_bridge.power_stability_runtime import PowerStabilityRuntime

        samples = self.install_model()
        self.manager._finish_learning(self.manager.job_epoch, samples, self.manager.identity(), self.manager.policy())
        self.station.configuration["powerStabilityMode"] = "dual"
        self.verify()
        restarted = PowerStabilityRuntime(self.station)
        self.assertEqual(restarted.model.model_id, self.manager.model.model_id)
        self.assertIsNone(restarted.verified_connection)
        self.assertIsNotNone(restarted.blocker())

    def test_point_journal_contains_frozen_model_and_separate_metrics(self):
        self.install_model()
        self.manager.freeze()
        store = self.station.record_store
        store.begin_session(Path(self.directory.name), "SN", datetime.now(), mode="automatic",
                            settings={"powerStability": self.manager.session_metadata()})
        record = ExcelTestRecord(1, 12, 32.5, 0.1, 976, 976, 0.4, 0.99, [], [])
        store.queue(record, power_stability=self.manager.snapshot())
        self.manager.journal_diagnostic(1, force=True)
        events = [json.loads(line) for line in store.journal_path.read_text().splitlines()]
        self.assertEqual(events[0]["session"]["settings"]["powerStability"]["background"]["model_id"], "runtime-model")
        self.assertIn("power_stability", events[1]["attempt"])
        self.assertEqual(events[-1]["type"], "power_stability")

    def simulate_controlled_point(self, *, break_during_voltage=False, timeout=120, stop_at=None):
        self.activate_dual()
        st = self.station
        st.power_meter_reader.update_stability_settings = lambda *args, **kwargs: None
        st.power_meter_reader.reset_stability_window = lambda: 7
        self.manager.connection = (id(st.power_meter_reader), 0, "")
        st.automatic.dwell_times_s = (0, 0)
        settings = AutomaticTestSettings(use_spectrometer=False, point_timeout_s=timeout)
        st.automatic.settings = settings
        clock_start = self.clock[0]
        voltage_times = []
        last_published = [self.clock[0]]

        def advance(seconds):
            self.clock[0] = round(self.clock[0] + seconds, 6)
            if stop_at is not None and self.clock[0] - clock_start >= stop_at:
                st._automatic_stop.set()
                return True
            if self.clock[0] - last_published[0] >= 0.099:
                last_published[0] = self.clock[0]
                tick = round((self.clock[0] - clock_start) * 10)
                self.publish(self.clock[0] - 1000, 32.4 + (0.15 if tick % 2 else 0))
            return False

        def voltage():
            voltage_times.append(self.clock[0] - clock_start)
            if break_during_voltage and len(voltage_times) == 1:
                self.publish(self.clock[0] - 1000 + 0.1, 34.0)
            else:
                advance(0.1)
            return 12.0

        supply = SimpleNamespace(read_output_voltage=voltage)
        with (patch.object(st, "_wait_for_automatic_acquisition"),
              patch.object(st, "_set_automatic_current", side_effect=lambda current: setattr(st, "active_current_a", current)),
              patch.object(st, "_await_automatic_resume", return_value=0),
              patch.object(st, "_reserve_power_supply_command"),
              patch.object(st, "_require_power_supply", return_value=supply),
              patch.object(st.record_store, "queue") as queue,
              patch.object(st.record_store, "record_stability_diagnostic"),
              patch.object(st._automatic_stop, "wait", side_effect=advance),
              patch("tauri_bridge.station_automatic.time.sleep", side_effect=advance)):
            self.last_queue = queue
            st._run_automatic_point(0, 1, settings)
        return voltage_times, queue

    def test_controlled_point_waits_for_settling_response_window_and_five_second_confirmation(self):
        voltages, queue = self.simulate_controlled_point()
        self.assertGreaterEqual(voltages[0], 1 + 2 + 20 + 5)
        self.assertEqual(len(voltages), 1)
        queue.assert_called_once()
        record = queue.call_args.args[0]
        self.assertIn(record.power_w, (32.4, 32.55))
        self.assertEqual(queue.call_args.kwargs["power_stability"]["mode"], "dual")

    def test_instability_during_voltage_invalidates_candidate_and_repeats_full_confirmation(self):
        voltages, queue = self.simulate_controlled_point(break_during_voltage=True)
        self.assertEqual(len(voltages), 2)
        self.assertGreaterEqual(voltages[1] - voltages[0], 5)
        queue.assert_called_once()

    def test_point_timeout_is_shared_by_window_and_confirmation(self):
        with self.assertRaises(TimeoutError):
            self.simulate_controlled_point(timeout=26)
        self.assertLess(self.clock[0], 1000 + 20.5 + 27)
        self.last_queue.assert_not_called()

    def test_operator_stop_cancels_response_window_or_confirmation_without_queueing(self):
        with self.assertRaisesRegex(RuntimeError, "自动测试已停止"):
            self.simulate_controlled_point(stop_at=25)
        self.last_queue.assert_not_called()

    def test_learning_job_uses_existing_reader_stream_and_saves_after_180_seconds(self):
        finished = threading.Event()
        original = self.manager._finish_learning

        def finish(*args):
            try:
                original(*args)
            finally:
                finished.set()

        with patch.object(self.manager, "_finish_learning", side_effect=finish):
            self.manager.start_background({"confirmedDark": True})
            for i in range(1, 362):
                self.publish(i * 0.5, 0.15 if i % 2 else 0)
            self.assertTrue(finished.wait(2), "background learning worker did not finish")
        self.assertIsNotNone(self.manager.model)
        self.assertEqual(self.manager.model.sample_count, 361)
        self.assertIsNotNone(self.manager.verified_at)
        self.assertEqual(self.manager.verified_connection, self.manager.connection)
        self.station.configuration["powerStabilityMode"] = "dual"
        self.assertIsNone(self.manager.blocker())

    def test_automatic_learning_needs_no_manual_fields_and_reuses_after_reconnect_check(self):
        self.station.configuration.update({"powerStabilityQuantizationW": 0,
                                            "powerStabilityInstrumentSettings": "", "softwareGain": 1.04})
        finished = threading.Event()
        original = self.manager._finish_learning

        def finish(*args):
            try:
                original(*args)
            finally:
                finished.set()

        with patch.object(self.manager, "_finish_learning", side_effect=finish):
            self.manager.start_background({"confirmedDark": True})
            for i in range(1, 362):
                self.publish(i * 0.5, 0.156 if i % 2 else 0, stable=True)
            self.assertTrue(finished.wait(2))
        model = self.manager.model
        self.assertIsNotNone(model, self.manager.state)
        self.assertAlmostEqual(model.quantization_w, 0.156)
        self.assertEqual(model.quantization_source, "observed-grid-v1")
        self.assertAlmostEqual(self.manager.policy().quantization_w, 0.156)
        self.assertFalse(self.manager.policy().ready)
        self.assertEqual(self.station.configuration["powerStabilityQuantizationW"], 0)
        self.assertIsNotNone(self.manager.verified_connection)
        for _ in range(2):
            self.manager.freeze()
            self.assertIs(self.manager.model, model)
        self.station.configuration["powerStabilityMode"] = "dual"
        self.assertIn("仅用于旁路", self.manager.blocker())
        self.station.configuration["powerStabilityMode"] = "shadow"
        self.publish(181, 0, connection=1)
        self.assertIsNone(self.manager.verified_connection)
        self.manager.start_background({"confirmedDark": True}, verify=True)
        for i in range(1, 42):
            self.publish(181 + i * 0.5, 0.156 if i % 2 else 0, connection=1)
        self.assertIn("复核通过", self.manager.state)
        self.assertEqual(self.manager.model.model_id, model.model_id)
        # Relearning must estimate again, rather than silently reusing old q.
        finished.clear()
        started = self.clock[0] - 1000
        with patch.object(self.manager, "_finish_learning", side_effect=finish):
            self.manager.start_background({"confirmedDark": True})
            for i in range(1, 362):
                self.publish(started + i * 0.5, 0.312 if i % 2 else 0, connection=1)
            self.assertTrue(finished.wait(2))
        self.assertAlmostEqual(self.manager.model.quantization_w, 0.312)
        self.assertNotEqual(self.manager.model.model_id, model.model_id)

    def test_failed_auto_estimation_retains_raw_data_without_qualifying_background(self):
        self.station.configuration.update({"powerStabilityQuantizationW": 0,
                                            "powerStabilityInstrumentSettings": ""})
        self.manager.start_background({"confirmedDark": True})
        samples = [(i * 0.5, 0) for i in range(361)]
        self.manager._finish_learning(self.manager.job_epoch, samples, self.manager.identity(), self.manager.policy())
        self.assertIn("无法可靠估计", self.manager.state)
        self.assertIsNone(self.manager.verified_connection)
        self.assertIsNone(self.manager.model)
        attempt = self.backend.settings.get("runtime/powerBackgroundLearningAttempts")[self.manager.identity()]
        self.assertEqual(attempt["samples"], samples)

    def test_detected_settings_use_existing_range_token_and_do_not_invent_filter(self):
        self.manager.instrument_token = "laserpoint:2:3000:1"
        settings = self.manager.snapshot()["detectedSettings"]
        self.assertEqual(settings["fullScaleW"], 3000)
        self.assertEqual(settings["filter"], "unavailable")
        self.assertEqual(settings["softwareGain"], self.station.configuration["softwareGain"])
        self.assertEqual(settings["intervalMs"], self.station.configuration["powerMeterIntervalMs"])
        for token in ("", "other:2:3000:1", "laserpoint:2:nan:1", "laserpoint:2:bad:1"):
            self.manager.instrument_token = token
            self.assertIsNone(self.manager.detected_settings()["fullScaleW"])


if __name__ == "__main__":
    unittest.main()
