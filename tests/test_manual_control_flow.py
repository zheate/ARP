"""Manual current/shutdown contracts, using fake controllers only."""

import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from combined_test.automation import AutomaticTestState
from combined_test.models import PowerMeterReading
from combined_test.record_store import AttemptValidity
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.service import SAFETY_TASK_METHODS
from tauri_bridge.settings import JsonSettings
from tauri_bridge.station_support import AutomaticPointQualityError
from tests.test_station_runtime import _FakeController


class ManualControlFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = HeadlessBackend(JsonSettings(Path(self.temp.name) / "settings.json"))
        self.addCleanup(self.backend.export_worker.graceful_stop, 2)
        self.station = self.backend.runtime
        burst = patch.object(self.station, "_start_tdk_voltage_read_burst")
        burst.start()
        self.addCleanup(burst.stop)
        self.controller = _FakeController()
        self.station.power_supply_controller = self.controller
        self.station.configuration.update({"sn": "MANUAL", "outputDir": self.temp.name})
        self.station.power_meter_reader = SimpleNamespace(
            is_ready=True, reset_stability_window=Mock(return_value=2), stop=Mock(), wait=Mock(),
        )

    def tdk(self):
        self.station.power_supply_kind = "tdk"
        self.station.configuration["powerSupplyKind"] = "tdk"

    def reading(self, stable=True, generation=2):
        return PowerMeterReading(
            elapsed_s=5.0, power_w=4.0, stable=stable, stable_span_w=0.02,
            stable_window_s=3.0, stable_median_w=4.0, stability_generation=generation,
        )

    def arm_capture(self):
        self.tdk()
        self.backend.dispatch("powerSupply.setCurrent", {"currentA": 2.0})
        with patch("tauri_bridge.station_runtime.threading.Thread") as worker:
            self.station._on_power_reading(self.reading())
        return worker.call_args.kwargs["target"]

    def test_ch341_wait_exposes_countdown_without_holding_snapshot_lock_and_writes_once(self):
        waiting, release = threading.Event(), threading.Event()
        self.station._last_power_supply_command_at = time.monotonic()
        errors = []

        def wait_for_slot(remaining):
            self.assertGreater(remaining, 0)
            waiting.set()
            self.assertTrue(release.wait(2))
            self.station._last_power_supply_command_at = None

        def apply():
            try:
                self.backend.dispatch("powerSupply.setCurrent", {"currentA": 3.0})
            except Exception as exc:
                errors.append(exc)

        with patch.object(self.station, "_wait_power_supply_command_interval", side_effect=wait_for_slot):
            worker = threading.Thread(target=apply, daemon=True)
            worker.start()
            try:
                self.assertTrue(waiting.wait(1))
                state = self.backend.snapshot({"view": "manual"})["manualTest"]["currentCommand"]
                self.assertTrue(state["pending"])
                self.assertEqual(state["requestedCurrentA"], 3.0)
                self.assertGreater(state["remainingS"], 0)
                self.assertEqual(self.controller.writes, [])
            finally:
                release.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.controller.writes, [3.0])
        self.assertIsNone(self.station.manual_current_request_a)

    def test_safe_down_cancels_waiting_write_and_retains_connection_and_acquisition(self):
        waiting, release = threading.Event(), threading.Event()
        self.station._last_power_supply_command_at = time.monotonic()
        reader = self.station.power_meter_reader
        failures = []

        def wait_for_slot(_remaining):
            waiting.set()
            release.wait(2)

        def apply():
            try:
                self.backend.dispatch("powerSupply.setCurrent", {"currentA": 8.0})
            except Exception as exc:
                failures.append(str(exc))

        with patch.object(self.station, "_wait_power_supply_command_interval", side_effect=wait_for_slot):
            worker = threading.Thread(target=apply, daemon=True)
            worker.start()
            try:
                self.assertTrue(waiting.wait(1))
                queued_generation = self.backend.capture_command_generation()
                self.station._last_power_supply_command_at = None
                response = self.backend.dispatch("powerSupply.safeRampDown", {})
                self.assertTrue(response["safety"]["shutdown"]["confirmed"])
                self.assertIs(self.station.power_supply_controller, self.controller)
                self.assertIs(self.station.power_meter_reader, reader)
                reader.stop.assert_not_called()
                with self.assertRaisesRegex(RuntimeError, "安全停止失效"):
                    self.backend.dispatch("powerSupply.setCurrent", {"currentA": 9.0}, command_generation=queued_generation)
            finally:
                release.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertNotIn(8.0, self.controller.writes)
        self.assertNotIn(9.0, self.controller.writes)
        self.assertEqual(self.station.active_current_a, 0)

    def test_tdk_safe_down_verifies_zero_and_keeps_readers_and_records(self):
        callback = self.arm_capture()
        with patch("tauri_bridge.station_runtime.time.sleep"):
            callback()
        records = self.station.record_store.snapshot()
        reader = self.station.power_meter_reader
        with patch("tauri_bridge.station_automatic.time.sleep"):
            response = self.backend.dispatch("powerSupply.safeRampDown", {})
        self.assertTrue(response["safety"]["shutdown"]["confirmed"])
        self.assertFalse(self.controller.output_enabled)
        self.assertIs(self.station.power_supply_controller, self.controller)
        self.assertIs(self.station.power_meter_reader, reader)
        self.assertEqual(self.station.record_store.snapshot(), records)
        self.assertEqual(len(records), 1)

    def test_failed_tdk_current_write_does_not_read_voltage(self):
        self.tdk()
        with patch.object(self.controller, "set_output_current", side_effect=RuntimeError("write failed")), \
             patch.object(self.controller, "read_output_voltage") as voltage:
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                self.backend.dispatch("powerSupply.setCurrent", {"currentA": 2.0})
            voltage.assert_not_called()
            self.station._start_tdk_voltage_read_burst.assert_not_called()

    def test_ch341_current_write_does_not_add_voltage_reads(self):
        with patch.object(self.controller, "read_output_voltage") as voltage:
            self.backend.dispatch("powerSupply.setCurrent", {"currentA": 2.0})
            voltage.assert_not_called()
            self.station._start_tdk_voltage_read_burst.assert_not_called()

    def test_tdk_failed_zero_confirmation_is_visible_and_can_be_retried(self):
        self.tdk()
        with patch.object(self.controller, "read_output_current", return_value=0.2):
            with self.assertRaisesRegex(RuntimeError, "无法确认"):
                self.backend.dispatch("powerSupply.safeRampDown", {})
        state = self.backend.snapshot({"view": "manual"})
        self.assertFalse(state["safety"]["shutdown"]["confirmed"])
        self.assertFalse(state["manualTest"]["currentCommand"]["stopping"])
        self.assertIs(self.station.power_supply_controller, self.controller)
        result = self.backend.dispatch("powerSupply.safeRampDown", {})
        self.assertTrue(result["safety"]["shutdown"]["confirmed"])

    def test_tdk_enable_still_zeros_and_confirms_before_output_on(self):
        self.tdk()
        events = []
        with patch.object(self.controller, "set_output_current", side_effect=lambda value: events.append(("current", value))), \
             patch.object(self.controller, "read_output_current", side_effect=lambda: events.append(("read",)) or 0.0), \
             patch.object(self.controller, "set_output_enabled", side_effect=lambda value: events.append(("output", value))):
            self.backend.dispatch("powerSupply.setOutput", {"enabled": True})
        self.assertEqual(events, [("current", 0.0), ("read",), ("output", True)])

    def test_safe_down_is_priority_routed_and_rejects_automatic_ownership(self):
        self.assertIn("powerSupply.safeRampDown", SAFETY_TASK_METHODS)
        self.station.automatic.state = AutomaticTestState.WAITING_STABLE
        with self.assertRaisesRegex(Exception, "硬件控制权"):
            self.backend.dispatch("powerSupply.safeRampDown", {})
        self.assertEqual(self.controller.writes, [])

    def test_invalid_manual_current_never_writes_or_leaves_a_pending_command(self):
        for current_a in (True, -1, 20.1, float("nan"), float("inf")):
            with self.subTest(current=current_a), self.assertRaises(ValueError):
                self.backend.dispatch("powerSupply.setCurrent", {"currentA": current_a})
            self.assertIsNone(self.station.manual_current_request_a)
        self.assertEqual(self.controller.writes, [])

    def test_tdk_stable_capture_reads_immediately_then_validates_and_records(self):
        callback = self.arm_capture()
        capture = self.backend.snapshot({"view": "manual"})["manualTest"]["capture"]
        self.assertEqual(capture["state"], "reading_voltage")
        self.assertIsNone(capture["remainingS"])
        stages = []
        original_validation = self.station._validate_automatic_spectrum

        def voltage():
            stages.append(self.station.manual_capture["state"])
            return 10.0

        def validate(frame):
            stages.append(self.station.manual_capture["state"])
            return original_validation(frame)

        with patch("tauri_bridge.station_runtime.time.sleep") as delay, \
             patch.object(self.controller, "read_output_voltage", side_effect=voltage), \
             patch.object(self.station, "_validate_automatic_spectrum", side_effect=validate):
            callback()
        delay.assert_not_called()
        self.assertEqual(stages, ["reading_voltage", "validating"])
        self.assertEqual(self.station.manual_capture["state"], "recorded")
        record = self.station.record_store.snapshot()[0]
        self.assertEqual(record.current_a, 2.0)
        self.assertEqual(record.power_w, 4.0)
        # TDK voltage compensation remains in the existing record path.
        self.assertEqual(record.efficiency, 4.0 / 2.0 / record.voltage_v)

    def test_ch341_stable_capture_keeps_existing_delay(self):
        self.backend.dispatch("powerSupply.setCurrent", {"currentA": 2.0})
        with patch("tauri_bridge.station_runtime.threading.Thread") as worker:
            self.station._on_power_reading(self.reading())
        capture = self.backend.snapshot({"view": "manual"})["manualTest"]["capture"]
        self.assertEqual(capture["state"], "waiting_voltage")
        self.assertGreater(capture["remainingS"], 4.9)
        with patch("tauri_bridge.station_runtime.time.sleep") as delay, \
             patch.object(self.station, "_reserve_power_supply_command"), \
             patch.object(self.station, "_record_manual_voltage_point"):
            worker.call_args.kwargs["target"]()
        delay.assert_called_once_with(5.0)

    def test_changed_current_cancels_old_timer_and_explains_the_replacement(self):
        callback = self.arm_capture()
        self.backend.dispatch("powerSupply.setCurrent", {"currentA": 3.0})
        self.assertEqual(self.station.manual_capture["state"], "waiting_stable")
        self.assertIn("上一测试点已取消", self.station.manual_capture["detail"])
        with patch("tauri_bridge.station_runtime.time.sleep"), patch.object(self.controller, "read_output_voltage") as voltage:
            callback()
        voltage.assert_not_called()
        self.assertEqual(self.station.record_store.snapshot(), ())

    def test_safe_down_cancels_pending_voltage_capture(self):
        callback = self.arm_capture()
        with patch("tauri_bridge.station_automatic.time.sleep"):
            self.backend.dispatch("powerSupply.safeRampDown", {})
        self.assertEqual(self.station.manual_capture["state"], "cancelled")
        self.assertIn("安全下电", self.station.manual_capture["detail"])
        with patch("tauri_bridge.station_runtime.time.sleep"), patch.object(self.controller, "read_output_voltage") as voltage:
            callback()
        voltage.assert_not_called()
        self.assertEqual(self.station.record_store.snapshot(), ())

    def test_stability_interruption_reports_restarting_the_stability_window(self):
        self.arm_capture()
        self.station._on_power_reading(replace(self.reading(), stable=False, elapsed_s=5.1))
        self.assertEqual(self.station.manual_capture["state"], "waiting_stable")
        self.assertIn("稳定中断", self.station.manual_capture["detail"])

    def test_power_failure_cancels_unfinished_point(self):
        callback = self.arm_capture()
        self.station._on_power_failed("read timeout")
        self.assertEqual(self.station.manual_capture["state"], "cancelled")
        with patch("tauri_bridge.station_runtime.time.sleep"):
            callback()
        self.assertEqual(self.station.record_store.snapshot(), ())

    def test_spectrum_quality_failure_reports_why_point_was_not_recorded(self):
        callback = self.arm_capture()
        error = AutomaticPointQualityError(AttemptValidity.DEVICE_ERROR, "光谱饱和")
        with patch("tauri_bridge.station_runtime.time.sleep"), \
             patch.object(self.station, "_validate_automatic_spectrum", side_effect=error):
            callback()
        self.assertEqual(self.station.manual_capture["state"], "failed")
        self.assertIn("光谱饱和", self.station.manual_capture["detail"])
        self.assertEqual(self.station.record_store.snapshot(), ())


if __name__ == "__main__":
    unittest.main()
