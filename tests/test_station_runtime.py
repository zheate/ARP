from __future__ import annotations

import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from combined_test.automation import AutomaticTestSettings, AutomaticTestState
from combined_test.concurrency import Signal
from combined_test.core import PowerStabilityDetector, WavelengthStabilityDetector
from combined_test.excel_export import ExcelTestRecord
from combined_test.ch341_ports import Ch341DeviceDescriptor
from combined_test.models import PowerMeterOption, PowerMeterReading, SpectrometerReading
from combined_test.record_store import AttemptValidity
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.export_queue import ExportQueueWorker, ExportTask
from tauri_bridge.station_automatic import (
    automatic_power_stability_span_limit_w,
    automatic_stability_window_s,
    automatic_wavelength_stability_tolerance_nm,
)
from tauri_bridge.station_support import AutomaticPointRetry
from tauri_bridge.settings import JsonSettings


class _FakeController:
    def __init__(self) -> None:
        self.is_connected = True
        self.output_enabled = True
        self.current_a = 0.0
        self.voltage_v = 12.0
        self.writes: list[float] = []

    def set_current(self, value: float) -> None:
        self.current_a = float(value)
        self.writes.append(self.current_a)

    def set_output_current(self, value: float) -> None:
        self.set_current(value)

    def set_output_voltage(self, value: float) -> None:
        self.voltage_v = float(value)

    def set_output_enabled(self, enabled: bool) -> None:
        self.output_enabled = bool(enabled)

    def read_output_current(self) -> float:
        return self.current_a

    def read_output_voltage(self) -> float:
        return self.voltage_v

    def disconnect_device(self) -> bool:
        self.is_connected = False
        return True


class StationRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        settings_path = Path(self.temp_dir.name) / "settings.json"
        self.backend = HeadlessBackend(JsonSettings(settings_path))

    def tearDown(self) -> None:
        self.backend.export_worker.graceful_stop(timeout_s=5.0)
        self.temp_dir.cleanup()

    def test_configuration_updates_the_single_runtime(self) -> None:
        self.backend.dispatch("app.configure", {"sn": "SN-001", "targetCurrentA": 10.0})
        snapshot = self.backend.snapshot()
        self.assertEqual(snapshot["configuration"]["sn"], "SN-001")
        self.assertEqual(self.backend.runtime.configuration["targetCurrentA"], 10.0)
        self.assertFalse(hasattr(self.backend, "stations"))
        for removed in ("activeStation", "stations", "configurations"):
            self.assertNotIn(removed, snapshot)

    def test_stability_snapshot_tracks_current_generation_and_survives_view_changes(self) -> None:
        station = self.backend.runtime
        station.automatic.currents = (0.6, 2.0)
        station.automatic.current_index = 0
        station.automatic.state = AutomaticTestState.WAITING_STABLE
        station.power_meter_reader = MagicMock(is_ready=True)
        station.power_meter_reader.reset_stability_window.return_value = 7
        station._begin_automatic_measurement_window(0.6)
        reading = PowerMeterReading(
            elapsed_s=6.3, power_w=1.3, stable=False, stable_span_w=0.05,
            stable_window_s=6.3, stable_median_w=1.3,
            stability_generation=7, stable_duration_s=6.3,
        )
        station._on_power_reading(replace(reading, stability_generation=6))
        self.assertFalse(self.backend.snapshot()["automaticTest"]["stability"]["hasReading"])
        station._on_power_reading(reading)
        for view in ("automatic", "manual", "automatic"):
            progress = self.backend.snapshot({"view": view})["automaticTest"]["stability"]
            self.assertEqual(progress["elapsedS"], 6.3)
            self.assertEqual(progress["requiredS"], 10.0)
            self.assertEqual(progress["spanLimitW"], 0.1)
            self.assertEqual(progress["quality"], "live")
            self.assertFalse(progress["powerStable"])
        station._on_power_reading(replace(reading, stable_duration_s=9.96))
        self.assertEqual(self.backend.snapshot()["automaticTest"]["stability"]["elapsedS"], 9.9)
        station._on_power_reading(replace(reading, stable=True, stable_duration_s=10.0))
        self.assertEqual(self.backend.snapshot()["automaticTest"]["stability"]["elapsedS"], 10.0)
        station.automatic.current_index = 1
        station.automatic.state = AutomaticTestState.SETTING_CURRENT
        self.assertFalse(self.backend.snapshot()["automaticTest"]["stability"]["hasReading"])
        station.power_meter_reader.reset_stability_window.return_value = 8
        station._begin_automatic_measurement_window(2.0)
        station.automatic.state = AutomaticTestState.WAITING_STABLE
        station._on_power_reading(reading)
        progress = self.backend.snapshot()["automaticTest"]["stability"]
        self.assertEqual(progress["elapsedS"], 0.0)
        self.assertEqual(progress["requiredS"], 5.0)
        self.assertEqual(progress["spanLimitW"], 0.2)

    def test_stability_snapshot_rejects_stale_data_and_freezes_on_pause_or_completion(self) -> None:
        station = self.backend.runtime
        station.automatic.currents = (0.6,)
        station.automatic.current_index = 0
        station.automatic.state = AutomaticTestState.WAITING_STABLE
        station.power_meter_reader = MagicMock(is_ready=True)
        station.power_meter_reader.reset_stability_window.return_value = 7
        station._begin_automatic_measurement_window(0.6)
        reading = PowerMeterReading(
            elapsed_s=6.3, power_w=1.3, stable=False, stable_span_w=0.05,
            stable_window_s=6.3, stable_median_w=1.3,
            stability_generation=7, stable_duration_s=6.3,
        )
        station._on_power_reading(reading)
        station.latest_power_received_monotonic_s -= 60
        self.assertEqual(self.backend.snapshot()["automaticTest"]["stability"]["elapsedS"], 0.0)
        station.automatic.state = AutomaticTestState.PAUSED
        station._on_power_reading(replace(reading, stable_duration_s=0.0))
        self.assertEqual(self.backend.snapshot()["automaticTest"]["stability"]["elapsedS"], 6.3)
        station.automatic.state = AutomaticTestState.WAITING_STABLE
        station._on_power_reading(replace(reading, stable=True, stable_duration_s=10.0))
        station.automatic.state = AutomaticTestState.COMPLETED
        station._on_power_reading(replace(reading, stable_duration_s=0.0))
        progress = self.backend.snapshot()["automaticTest"]["stability"]
        self.assertTrue(progress["powerStable"])
        self.assertEqual(progress["elapsedS"], 10.0)

    def _simulate_stable_hold(self, *, stable=lambda _t: True, samples=lambda _t: True, wave=lambda _t: True, spectral=False, dwell=0.0, pause=False, stop_at=None, full_point=False, point_count=2, index=0, power_supply_kind="ch341"):
        station = self.backend.runtime
        station.power_supply_kind = power_supply_kind
        station.configuration["powerSupplyKind"] = power_supply_kind
        clock = [0.0]
        paused = [False]
        settings = AutomaticTestSettings(use_spectrometer=spectral, point_timeout_s=20.0)
        station.automatic.settings = settings
        station.automatic.currents = (0.6, 2.0)[:point_count]
        station.automatic.current_index = index
        station.automatic.dwell_times_s = (dwell,) * point_count
        current_a = station.automatic.current_a
        station.automatic.state = AutomaticTestState.WAITING_VOLTAGE
        station.automatic_stability_generation = 0
        station.power_meter_reader = MagicMock(is_ready=True)
        if spectral:
            station.spectrometer_reader = MagicMock(is_ready=True)
        voltage_times = []

        def publish():
            now = clock[0]
            if samples(now):
                station._on_power_reading(PowerMeterReading(now, 1.3, stable(now), 0.01, 10.0, 1.3))
            if spectral:
                station.latest_spectrum = SimpleNamespace(
                    saturation=SimpleNamespace(saturated=False), stats=None, smsr=None,
                    wavelength=[975.0, 976.0], intensity=[10.0, 20.0], peak_annotations=(),
                    peak_intensity=60000.0,
                )
                station.latest_spectrum_received_monotonic_s = now
                station.spectrum_revision += 1
                station.latest_wavelength_stable = wave(now)

        def advance(seconds):
            clock[0] = round(clock[0] + seconds, 6)
            publish()
            return stop_at is not None and clock[0] >= stop_at

        def resume():
            if pause and not paused[0] and clock[0] >= 2:
                paused[0] = True
                clock[0] += 3
                return 3.0
            return 0.0

        def read_voltage():
            voltage_times.append(clock[0])
            return 12.0

        supply = MagicMock()
        supply.read_output_voltage.side_effect = read_voltage
        with (
            patch("tauri_bridge.station_automatic.time.monotonic", side_effect=lambda: clock[0]),
            patch.object(station, "_await_automatic_resume", side_effect=resume),
            patch.object(station._automatic_stop, "wait", side_effect=advance),
            patch.object(station, "_wait_for_automatic_acquisition"),
            patch.object(station, "_set_automatic_current"),
            patch.object(station, "_reserve_power_supply_command"),
            patch.object(station, "_require_power_supply", return_value=supply),
            patch.object(station.record_store, "queue"),
        ):
            publish()
            if full_point:
                with patch.object(station, "_wait_for_automatic_stability", return_value=(station.latest_power, None, 0, 0)):
                    station._run_automatic_point(index, current_a, settings)
            else:
                station._wait_for_automatic_stable_hold(settings, current_a, index, 0, 0)
            progress = self.backend.snapshot()["automaticTest"]["stability"]
        return clock[0], progress, voltage_times

    def test_voltage_and_record_wait_for_five_continuous_stable_seconds(self) -> None:
        elapsed, progress, voltage_times = self._simulate_stable_hold(full_point=True)
        self.assertEqual(elapsed, 5.0)
        self.assertEqual(voltage_times, [5.0])
        self.assertEqual(progress["holdElapsedS"], 5.0)
        self.assertEqual(progress["holdRequiredS"], 5.0)

    def test_tdk_reads_final_voltage_immediately_after_stability_including_last_point(self) -> None:
        for point_count, index in ((2, 0), (2, 1), (1, 0)):
            with self.subTest(point_count=point_count, index=index):
                elapsed, progress, voltage_times = self._simulate_stable_hold(
                    power_supply_kind="tdk", full_point=True, point_count=point_count, index=index,
                )
                self.assertEqual(elapsed, 0.0)
                self.assertEqual(voltage_times, [0.0])
                self.assertEqual(progress["holdRequiredS"], 0.0)
                self.assertEqual(self.backend.runtime.latest_power_supply_output_voltage_v, 12.0)

    def test_tdk_keeps_explicit_dwell_and_requires_stable_live_power_and_wavelength(self) -> None:
        cases = (
            ({"dwell": 4.0}, 4.0),
            ({"dwell": 4.0, "pause": True}, 7.0),
            ({"stable": lambda t: t >= 2.0}, 2.0),
            ({"samples": lambda t: t >= 2.0}, 2.0),
            ({"spectral": True, "wave": lambda t: t >= 2.0}, 2.0),
        )
        for params, expected in cases:
            with self.subTest(params=params):
                elapsed, progress, voltage_times = self._simulate_stable_hold(
                    power_supply_kind="tdk", full_point=True, **params,
                )
                self.assertEqual(elapsed, expected)
                self.assertEqual(voltage_times, [expected])
                self.assertEqual(progress["holdRequiredS"], 0.0)

    def test_tdk_direct_voltage_read_still_honors_stop_and_stability_timeout(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "自动测试已停止"):
            self._simulate_stable_hold(power_supply_kind="tdk", dwell=4.0, stop_at=2.0)
        with self.assertRaisesRegex(TimeoutError, "等待稳定超时"):
            self._simulate_stable_hold(power_supply_kind="tdk", stable=lambda _t: False)

    def test_tdk_timeout_validation_no_longer_includes_removed_confirmation(self) -> None:
        station = self.backend.runtime
        station.power_supply_kind = "tdk"
        station.configuration.update({"initialCurrentA": 2.0, "pointTimeoutS": 5.0})
        self.assertEqual(station._automatic_settings().point_timeout_s, 5.0)
        station.power_supply_kind = "ch341"
        with self.assertRaisesRegex(ValueError, "单点超时"):
            station._automatic_settings()

    def test_final_point_waits_ten_seconds_and_reports_the_matching_target(self) -> None:
        for point_count, index in ((1, 0), (2, 1)):
            with self.subTest(point_count=point_count):
                elapsed, progress, voltage_times = self._simulate_stable_hold(full_point=True, point_count=point_count, index=index)
                self.assertEqual(elapsed, 10.0)
                self.assertEqual(voltage_times, [10.0])
                self.assertEqual(progress["holdElapsedS"], 10.0)
                self.assertEqual(progress["holdRequiredS"], 10.0)

    def test_final_point_instability_restarts_the_full_ten_seconds(self) -> None:
        elapsed, progress, voltage_times = self._simulate_stable_hold(stable=lambda t: not 2 <= t < 3, full_point=True, index=1)
        self.assertEqual(elapsed, 13.0)
        self.assertEqual(voltage_times, [13.0])
        self.assertEqual(progress["holdRequiredS"], 10.0)

    def test_instability_during_hold_restarts_the_full_five_seconds(self) -> None:
        elapsed, _, voltage_times = self._simulate_stable_hold(stable=lambda t: not 2 <= t < 3, full_point=True)
        self.assertEqual(elapsed, 8.0)
        self.assertEqual(voltage_times, [8.0])

    def test_sampling_gap_during_hold_restarts_confirmation(self) -> None:
        elapsed, _, _ = self._simulate_stable_hold(samples=lambda t: not 2 <= t < 4)
        self.assertEqual(elapsed, 9.0)

    def test_wavelength_instability_also_restarts_confirmation(self) -> None:
        elapsed, _, _ = self._simulate_stable_hold(spectral=True, wave=lambda t: not 2 <= t < 3)
        self.assertEqual(elapsed, 8.0)

    def test_custom_dwell_must_finish_as_well_as_stability_confirmation(self) -> None:
        elapsed, progress, _ = self._simulate_stable_hold(dwell=12.0)
        self.assertEqual(elapsed, 12.0)
        self.assertEqual(progress["holdElapsedS"], 5.0)

    def test_pause_requires_a_new_stable_interval_after_resume(self) -> None:
        elapsed, _, _ = self._simulate_stable_hold(pause=True)
        self.assertGreaterEqual(elapsed, 10.0)

    def test_confirmation_honors_stop_and_times_out_without_new_samples(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "自动测试已停止"):
            self._simulate_stable_hold(stop_at=2.0)
        with self.assertRaisesRegex(TimeoutError, "连续保持稳定 5 秒"):
            self._simulate_stable_hold(samples=lambda t: t == 0)

    def test_run_summary_duration_is_live_then_freezes_and_context_is_copied(self) -> None:
        station = self.backend.runtime
        self.assertIsNone(self.backend.snapshot()["automaticTest"]["elapsedS"])
        station.automatic.state = AutomaticTestState.WAITING_STABLE
        station.automatic_started_at = 100.0
        station.automatic_run_context = {"sn": "SN-TEST", "station": "站别 1"}
        station.configuration["sn"] = "SN-EDITED"
        with patch("tauri_bridge.snapshot_builder.time.monotonic", return_value=256.9):
            active = self.backend.snapshot()["automaticTest"]
        self.assertEqual(active["elapsedS"], 156)
        self.assertEqual(active["runContext"]["sn"], "SN-TEST")
        active["runContext"]["sn"] = "MUTATED"
        self.assertEqual(station.automatic_run_context["sn"], "SN-TEST")
        station.automatic.state = AutomaticTestState.COMPLETED
        station.automatic_finished_at = 263.2
        with patch("tauri_bridge.snapshot_builder.time.monotonic", return_value=999.0):
            self.assertEqual(self.backend.snapshot()["automaticTest"]["elapsedS"], 163)

    def test_spectrum_smoothing_is_enabled_by_default_and_persists(self) -> None:
        self.assertTrue(self.backend.runtime.configuration["spectrumSmoothing"])
        self.backend.dispatch("app.configure", {"spectrumSmoothing": False})
        self.assertFalse(self.backend.snapshot()["configuration"]["spectrumSmoothing"])
        restarted = HeadlessBackend(JsonSettings(Path(self.temp_dir.name) / "settings.json"))
        try:
            self.assertFalse(restarted.runtime.configuration["spectrumSmoothing"])
        finally:
            restarted.export_worker.graceful_stop(timeout_s=5.0)

    def test_spectrum_smoothing_rejects_non_boolean_values_without_mutating_configuration(self) -> None:
        original = dict(self.backend.runtime.configuration)
        for value in ("false", 0, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "spectrumSmoothing"):
                self.backend.dispatch("app.configure", {"stationId": "1", "spectrumSmoothing": value})
            self.assertEqual(self.backend.runtime.configuration, original)

    def test_automatic_spectrum_follows_active_acquisition(self) -> None:
        station = self.backend.runtime

        self.assertFalse(station._automatic_settings().use_spectrometer)

        station.configuration.update(
            {
                "spectrometerResource": "Ocean Insight | 设备 ID 7",
                "spectrometerDeviceId": 7,
            }
        )
        self.assertFalse(station._automatic_settings().use_spectrometer)

        station.spectrometer_reader = SimpleNamespace(is_ready=True)
        self.assertTrue(station._automatic_settings().use_spectrometer)

    def test_low_current_automatic_points_use_relaxed_wavelength_stability(self) -> None:
        low_current_detector = WavelengthStabilityDetector(
            3.0,
            automatic_wavelength_stability_tolerance_nm(2.0),
        )
        normal_current_detector = WavelengthStabilityDetector(
            3.0,
            automatic_wavelength_stability_tolerance_nm(2.01),
        )
        samples = (976.0, 976.15, 976.3, 976.45, 976.3, 976.15, 976.0)

        for elapsed_s, wavelength_nm in zip(
            (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0),
            samples,
        ):
            low_result = low_current_detector.add_sample(elapsed_s, wavelength_nm)
            normal_result = normal_current_detector.add_sample(elapsed_s, wavelength_nm)

        self.assertTrue(low_result.stable)
        self.assertFalse(normal_result.stable)
        self.assertEqual(low_current_detector.tolerance, 0.5)
        self.assertEqual(normal_current_detector.tolerance, 0.2)
        self.assertEqual(automatic_wavelength_stability_tolerance_nm(1.0), 0.2)

    def test_standard_test_requires_five_seconds_of_power_and_wavelength_stability(self) -> None:
        window_s = automatic_stability_window_s("standard", 3.0)
        detector = WavelengthStabilityDetector(window_s, 0.2)

        for elapsed_s in range(5):
            result = detector.add_sample(float(elapsed_s), 976.0)

        self.assertEqual(window_s, 5.0)
        self.assertFalse(result.stable)
        self.assertTrue(detector.add_sample(5.0, 976.0).stable)
        self.assertEqual(
            automatic_stability_window_s("hold-points", 3.0),
            3.0,
        )
        self.assertEqual(automatic_power_stability_span_limit_w(2.0), 0.2)
        self.assertIsNone(automatic_power_stability_span_limit_w(2.01))

    def test_low_current_power_stability_accepts_one_tenth_watt_after_ten_seconds(self) -> None:
        station = self.backend.runtime
        reader = MagicMock()
        station.power_meter_reader = reader
        station._begin_automatic_measurement_window(0.6)
        detector = PowerStabilityDetector(*reader.update_stability_settings.call_args.args)

        for elapsed_s in range(10):
            power_w = 1.25 if elapsed_s % 2 == 0 else 1.35
            self.assertFalse(detector.add_sample(float(elapsed_s), power_w).stable)

        result = detector.add_sample(10.0, 1.35)
        self.assertTrue(result.stable)
        self.assertAlmostEqual(result.span_w, 0.1)
        self.assertGreater(result.stability_percent, 0.2)
        self.assertEqual(result.window_s, 10.0)
        self.assertFalse(detector.add_sample(11.0, 1.351).stable)
        for elapsed_s in range(12, 22):
            self.assertFalse(detector.add_sample(float(elapsed_s), 1.25).stable)
        self.assertTrue(detector.add_sample(22.0, 1.25).stable)

    def test_point_specific_power_limits_are_reset_when_current_changes(self) -> None:
        station = self.backend.runtime
        reader = MagicMock()
        station.power_meter_reader = reader

        for script_id, normal_window_s in (("standard", 5.0), ("hold-points", 3.0)):
            station.configuration["selectedScriptId"] = script_id
            for current_a, expected_span_w, expected_window_s in (
                (0.1, 0.1, 10.0), (0.6, 0.1, 10.0), (0.59, 0.1, 10.0),
                (0.7, 0.1, 10.0), (1.0, 0.1, 10.0), (1.99, 0.1, 10.0),
                (2.0, 0.2, normal_window_s), (2.01, None, normal_window_s),
                (None, None, normal_window_s), (0.6, 0.1, 10.0),
            ):
                with self.subTest(script_id=script_id, current_a=current_a):
                    reader.reset_mock()
                    station._begin_automatic_measurement_window(current_a)
                    reader.update_stability_settings.assert_called_once_with(
                        expected_window_s, 0.2, expected_span_w,
                    )
                    reader.reset_stability_window.assert_called_once_with()

    def test_ophir_point_limits_and_snapshot_use_one_watt_with_existing_windows(self) -> None:
        station = self.backend.runtime
        station.configuration["powerMeterDriverKind"] = "ophir"
        reader = MagicMock()
        station.power_meter_reader = reader
        for current_a, window_s in ((0.6, 10.0), (2.0, 5.0), (20.0, 5.0)):
            with self.subTest(current_a=current_a):
                station._begin_automatic_measurement_window(current_a)
                reader.update_stability_settings.assert_called_with(window_s, 0.2, 1.0)
                snapshot = self.backend.snapshot()["automaticTest"]["stability"]
                self.assertEqual(snapshot["spanLimitW"], 1.0)
        for driver in ("caihuang", "coherent", "laserpoint"):
            station.configuration["powerMeterDriverKind"] = driver
            station._begin_automatic_measurement_window(20.0)
            reader.update_stability_settings.assert_called_with(5.0, 0.2, None)
            self.assertIsNone(self.backend.snapshot()["automaticTest"]["stability"]["spanLimitW"])

    def test_low_current_point_timeout_allows_ten_second_stability_window(self) -> None:
        station = self.backend.runtime
        station.configuration.update({"initialCurrentA": 0.6, "pointTimeoutS": 19.9})
        with self.assertRaisesRegex(ValueError, "单点超时不能小于 20.0 s"):
            station._automatic_settings()
        station.configuration["pointTimeoutS"] = 20.0
        self.assertEqual(station._automatic_settings().point_timeout_s, 20.0)

        station.configuration.update({"initialCurrentA": 2.0, "pointTimeoutS": 15.0})
        self.assertEqual(station._automatic_settings().point_timeout_s, 15.0)

        # A custom hold at 2 A only passes through low currents while ramping.
        station.power_supply_kind = "tdk"
        station.configuration.update({
            "initialCurrentA": 0.6,
            "currentPlanMode": "custom",
            "customCurrentPoints": [{"currentA": 2.0, "dwellS": 5.0}],
        })
        self.assertEqual(station._automatic_settings().point_timeout_s, 15.0)

    def test_two_amp_monotonic_wavelength_drift_is_not_stable(self) -> None:
        detector = WavelengthStabilityDetector(
            3.0,
            automatic_wavelength_stability_tolerance_nm(2.0),
        )

        for elapsed_s, wavelength_nm in zip(
            (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0),
            (976.0, 976.075, 976.15, 976.225, 976.3, 976.375, 976.45),
        ):
            result = detector.add_sample(elapsed_s, wavelength_nm)

        self.assertFalse(result.stable)
        self.assertLessEqual(result.span_w, 0.5)

    def test_other_currents_also_reject_monotonic_wavelength_drift(self) -> None:
        detector = WavelengthStabilityDetector(
            3.0,
            automatic_wavelength_stability_tolerance_nm(6.0),
        )

        for elapsed_s, wavelength_nm in zip(
            (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0),
            (976.0, 976.03, 976.06, 976.09, 976.12, 976.15, 976.18),
        ):
            result = detector.add_sample(elapsed_s, wavelength_nm)

        self.assertFalse(result.stable)
        self.assertLessEqual(result.span_w, 0.2)
        self.assertEqual(detector.tolerance, 0.2)

    def test_automatic_readiness_matches_start_requirements(self) -> None:
        station = self.backend.runtime
        station.power_supply_controller = _FakeController()

        readiness = station.automatic_readiness()

        self.assertFalse(readiness["ready"])
        blocker_codes = {item["code"] for item in readiness["blockers"]}
        self.assertIn("missing_sn", blocker_codes)
        self.assertIn("power_meter_not_selected", blocker_codes)
        self.assertNotIn("spectrometer_not_selected", blocker_codes)
        with self.assertRaisesRegex(ValueError, "请填写产品 SN"):
            station.start_automatic({})

        station.configuration.update(
            {
                "sn": "SN-READY",
                "powerMeterResource": "ASRL3::INSTR",
            }
        )
        self.assertTrue(station.automatic_readiness()["ready"])

    def test_automatic_readiness_only_requires_ch341_ramp_down_command_interval(self) -> None:
        for kind in ("tdk", "ch341"):
            station = self.backend.runtime
            station.power_supply_controller = _FakeController()
            station.power_supply_kind = kind
            station.configuration.update(
                {
                    "powerSupplyKind": kind,
                    "sn": f"RAMP-INTERVAL-{kind}",
                    "outputDir": self.temp_dir.name,
                    "powerMeterResource": "ASRL3::INSTR",
                    "selectedScriptId": "standard",
                }
            )
            for interval_s in (0.0, 0.1, 1.0, 1.1):
                with self.subTest(kind=kind, interval_s=interval_s):
                    station.configuration["rampDownIntervalS"] = interval_s
                    readiness = station.automatic_readiness()
                    if kind == "tdk" or interval_s >= 1.1:
                        self.assertTrue(readiness["ready"], readiness["blockers"])
                        self.assertEqual(station._require_automatic_ready().ramp_down_interval_s, interval_s)
                    else:
                        self.assertEqual(
                            readiness["blockers"],
                            [{"code": "invalid_test_settings", "message": "下电间隔不能小于 1.1 s"}],
                        )
                        with self.assertRaisesRegex(ValueError, "1.1 s"):
                            station._require_automatic_ready()

    def test_tdk_automatic_start_and_shutdown_use_short_configured_ramp_interval(self) -> None:
        station = self.backend.runtime
        controller = _FakeController()
        station.power_supply_controller = controller
        station.power_supply_kind = "tdk"
        station.configuration["powerMeterResource"] = "ASRL3::INSTR"
        station.power_meter_reader = MagicMock(is_ready=True)

        with patch("tauri_bridge.station_automatic.threading.Thread") as thread_class:
            station.start_automatic(
                {
                    "powerSupplyKind": "tdk",
                    "sn": "TDK-SHORT-RAMP",
                    "outputDir": self.temp_dir.name,
                    "powerMeterResource": "ASRL3::INSTR",
                    "selectedScriptId": "standard",
                    "rampDownStepA": 5.0,
                    "rampDownIntervalS": 0.1,
                }
            )

        thread_class.return_value.start.assert_called_once_with()
        self.assertEqual(station.automatic.settings.ramp_down_interval_s, 0.1)
        station.active_current_a = 12.0
        controller.current_a = 12.0
        station._last_power_supply_command_at = time.monotonic()

        with patch("tauri_bridge.station_automatic.time.sleep") as sleep:
            station.safe_ramp_down()

        self.assertEqual(controller.writes, [7.0, 2.0, 0.0])
        self.assertEqual([item.args for item in sleep.call_args_list], [(0.1,), (0.1,)])
        self.assertFalse(controller.output_enabled)
        self.assertTrue(station.shutdown_confirmed)

    def test_automatic_start_without_connected_spectrometer_uses_power_only(self) -> None:
        station = self.backend.runtime
        station.power_supply_controller = _FakeController()
        station.configuration["powerMeterResource"] = "ASRL3::INSTR"
        station.power_meter_reader = MagicMock(is_ready=True)

        with (
            patch("tauri_bridge.station_automatic.threading.Thread") as thread_class,
            patch.object(station, "start_power_meter") as start_power_meter,
            patch.object(station, "start_spectrometer") as start_spectrometer,
        ):
            station.start_automatic(
                {
                    "sn": "NO-SPECTRUM-001",
                    "powerMeterResource": "ASRL3::INSTR",
                    "spectrometerResource": "Ocean Insight | 设备 ID 7",
                    "spectrometerDeviceId": 7,
                }
            )

        self.assertFalse(station.automatic.settings.use_spectrometer)
        self.assertEqual(
            station.power_meter_reader.update_stability_settings.call_args.args,
            (5.0, 0.2),
        )
        start_power_meter.assert_not_called()
        start_spectrometer.assert_not_called()
        thread_class.return_value.start.assert_called_once_with()

    def test_stable_duration_uses_samples_and_does_not_advance_with_snapshot_refresh(self) -> None:
        station = self.backend.runtime
        station.power_meter_reader = MagicMock(is_ready=True)
        for tick in range(7):
            elapsed = tick * 0.5
            with patch("tauri_bridge.station_runtime.time.monotonic", return_value=100.0 + elapsed):
                station._on_power_reading(PowerMeterReading(elapsed, 0.8, True, 0.001, 3.0, 0.8))
        for now in (103.0, 103.9):
            with patch("tauri_bridge.station_runtime.time.monotonic", return_value=now):
                meter = self.backend.snapshot()["devices"]["powerMeter"]
            self.assertEqual(meter["stableElapsedS"], 3)
        with patch("tauri_bridge.station_runtime.time.monotonic", return_value=106.0):
            self.assertIsNone(self.backend.snapshot()["devices"]["powerMeter"]["stableElapsedS"])
        # A new stable sample after an acquisition gap starts a new interval.
        with patch("tauri_bridge.station_runtime.time.monotonic", return_value=110.0):
            station._on_power_reading(PowerMeterReading(10.0, 0.8, True, 0.001, 3.0, 0.8))
            self.assertEqual(self.backend.snapshot()["devices"]["powerMeter"]["stableElapsedS"], 0)

    def test_stable_duration_resets_on_instability_new_generation_and_reader_stop(self) -> None:
        station = self.backend.runtime
        station.power_meter_reader = MagicMock(is_ready=True)

        def sample(elapsed: float, *, stable: bool = True, generation: int = 0) -> None:
            with patch("tauri_bridge.station_runtime.time.monotonic", return_value=100.0 + elapsed):
                station._on_power_reading(PowerMeterReading(elapsed, 0.8, stable, 0.001, 3.0, 0.8, generation))

        sample(0)
        sample(0.5)
        sample(1)
        self.assertEqual(station.power_stable_elapsed_s, 1)
        sample(1.5, stable=False)
        self.assertIsNone(station.power_stable_elapsed_s)
        sample(2)
        self.assertEqual(station.power_stable_elapsed_s, 0)
        sample(2.5)
        sample(3)
        self.assertEqual(station.power_stable_elapsed_s, 1)
        sample(3.5, generation=1)
        self.assertEqual(station.power_stable_elapsed_s, 0)
        sample(4, generation=1)
        sample(4.5, generation=1)
        self.assertEqual(station.power_stable_elapsed_s, 1)
        sample(0, generation=1)
        self.assertEqual(station.power_stable_elapsed_s, 0)
        station._on_power_finished()
        self.assertIsNone(station.power_stable_elapsed_s)
        self.assertIsNone(station.power_stable_started_elapsed_s)

    def test_snapshot_marks_old_measurements_stale_then_invalid(self) -> None:
        station = self.backend.runtime
        station.latest_power = PowerMeterReading(
            elapsed_s=1.0,
            power_w=0.8,
            stable=True,
            stable_span_w=0.01,
            stable_window_s=3.0,
            stable_median_w=0.8,
        )
        station.latest_power_received_monotonic_s = 99.5
        station.latest_power_sampled_at_utc = "2026-08-23T01:00:00.000+00:00"
        station.power_meter_reader = MagicMock(is_ready=True)

        with patch("tauri_bridge.station_runtime.time.monotonic", return_value=100.0):
            live = self.backend.snapshot({"view": "automatic"})["devices"]["powerMeter"]
        self.assertEqual(live["quality"], "live")
        self.assertEqual(live["ageMs"], 0)

        station.power_meter_reader = None
        with patch("tauri_bridge.station_runtime.time.monotonic", return_value=100.0):
            stale = self.backend.snapshot({"view": "automatic"})["devices"]["powerMeter"]
        self.assertEqual(stale["quality"], "stale")

        with patch("tauri_bridge.station_runtime.time.monotonic", return_value=110.0):
            invalid = self.backend.snapshot({"view": "automatic"})["devices"]["powerMeter"]
        self.assertEqual(invalid["quality"], "invalid")

    def test_saturated_measurement_is_not_reported_live(self) -> None:
        with patch("tauri_bridge.station_runtime.time.monotonic", return_value=10.0):
            freshness = self.backend.runtime.measurement_freshness(
                received_at_monotonic_s=9.9,
                sampled_at_utc="2026-08-23T01:00:00.000+00:00",
                running=True,
                interval_ms=200.0,
                valid=True,
                saturated=True,
            )

        self.assertEqual(freshness["quality"], "saturated")

    def test_tdk_discovery_publishes_progress_before_it_finishes(self) -> None:
        started = threading.Event()
        release = threading.Event()
        self.backend.dispatch("app.configure", {"stationId": "1", "powerSupplyKind": "tdk"})

        def list_resources() -> list[str]:
            started.set()
            release.wait(timeout=2)
            return ["ASRL7::INSTR"]

        with (
            patch("tauri_bridge.device_discovery.list_tdk_serial_resources", side_effect=list_resources),
            patch("tauri_bridge.device_discovery.probe_tdk_serial_resource", return_value=True),
        ):
            self.backend.dispatch("device.refresh", {"stationId": "1", "device": "powerSupply"})
            self.assertTrue(started.wait(timeout=1))
            scanning = self.backend.snapshot({"stationId": "1"})["devices"]["powerSupply"]
            self.assertTrue(scanning["detecting"])
            self.assertEqual(scanning["detail"], "正在枚举 TDK RS232 串口")

            release.set()
            deadline = time.monotonic() + 1
            while self.backend.snapshot({"stationId": "1"})["devices"]["powerSupply"]["detecting"] and time.monotonic() < deadline:
                time.sleep(0.01)

        completed = self.backend.snapshot({"stationId": "1"})["devices"]["powerSupply"]
        self.assertFalse(completed["detecting"])
        self.assertEqual(completed["detail"], "识别完成：发现 1 个 TDK 串口")

    def test_tdk_discovery_excludes_power_meter_and_keeps_only_verified_supply(self) -> None:
        self.backend.dispatch("app.configure", {"stationId": "1", "powerSupplyKind": "tdk"})
        self.backend.power_meter_options = [
            PowerMeterOption(
                resource="ASRL3::INSTR",
                device_type="Caihuang CHLP-P",
                detail="COM3",
                driver_kind="caihuang",
            )
        ]

        def probe(resource: str) -> bool:
            return resource == "ASRL9::INSTR"

        with (
            patch(
                "tauri_bridge.device_discovery.list_tdk_serial_resources",
                return_value=["ASRL3::INSTR", "ASRL7::INSTR", "ASRL9::INSTR"],
            ),
            patch(
                "tauri_bridge.device_discovery.probe_tdk_serial_resource",
                side_effect=probe,
            ) as probe_resource,
        ):
            self.backend.dispatch("device.refresh", {"stationId": "1", "device": "powerSupply"})
            deadline = time.monotonic() + 1
            while (
                self.backend.snapshot({"stationId": "1"})["devices"]["powerSupply"]["detecting"]
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)

        supply = self.backend.snapshot({"stationId": "1"})["devices"]["powerSupply"]
        self.assertEqual(supply["resources"], ["ASRL9::INSTR"])
        self.assertEqual(
            [call.args[0] for call in probe_resource.call_args_list],
            ["ASRL7::INSTR", "ASRL9::INSTR"],
        )

    def test_test_stage_label_is_metadata_including_numeric_labels(self) -> None:
        for label in ("熔接前", "2"):
            self.backend.dispatch("app.configure", {"station": label, "sn": "SN-LEGACY"})
            self.assertEqual(self.backend.runtime.configuration["station"], label)
            self.assertEqual(self.backend.runtime.configuration["sn"], "SN-LEGACY")

    def test_notice_keeps_machine_readable_code_without_station_routing(self) -> None:
        self.backend.runtime.notify("warning", "光谱饱和", "请调整光路", code="spectrum_saturation")
        notice = self.backend.snapshot()["backend"]["notices"][-1]
        self.assertNotIn("stationId", notice)
        self.assertEqual(notice["code"], "spectrum_saturation")
        self.assertEqual(notice["title"], "光谱饱和")

    def test_successive_sessions_reserve_unique_workbook_paths(self) -> None:
        station = self.backend.runtime
        station.configuration.update({"outputDir": self.temp_dir.name, "sn": "SAME-SN", "station": "熔接前"})
        settings = AutomaticTestSettings()
        station.automatic.start(settings, power_meter_ready=False, spectrum_meter_ready=False)
        with patch("tauri_bridge.station_automatic.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 8, 18, 12, 34, 0)
            first_path = station._begin_recording_session(settings)
            station._close_automatic_sample_recorder()
            second_path = station._begin_recording_session(settings)
            station._close_automatic_sample_recorder()
        self.assertNotEqual(first_path, second_path)
        self.assertEqual(second_path.stem, first_path.stem + "_02")

    def test_fresh_runtime_has_no_preselected_hardware(self) -> None:
        config = self.backend.runtime.configuration
        for key in ("ch341SelectedLocationPath", "tdkResource", "powerMeterResource", "spectrometerResource"):
            self.assertEqual(config[key], "")
        self.assertEqual(config["ch341PortBindings"], [])
        self.assertIsNone(config["spectrometerDeviceId"])

    def test_spectrum_x_axis_locks_to_stable_peak_like_main(self) -> None:
        station = self.backend.runtime
        for peak_nm in (976.0, 976.1, 976.2, 976.3):
            station._update_spectrum_center_lock(
                SpectrometerReading(peak_nm, peak_nm + 0.05, 0.4)
            )
            self.assertIsNone(station.spectrum_center_locked_nm)

        station._update_spectrum_center_lock(
            SpectrometerReading(976.4, 976.45, 0.4)
        )

        snapshot = self.backend.snapshot({"view": "automatic"})
        spectrum_window = snapshot["devices"]["spectrometer"]["spectrumWindow"]
        self.assertAlmostEqual(spectrum_window["centerNm"], 976.2)
        self.assertEqual(spectrum_window["halfRangeNm"], 20.0)

        station.reset_charts()
        self.assertIsNone(station.spectrum_center_locked_nm)

    def test_chart_reset_does_not_touch_pd_revision(self) -> None:
        station = self.backend.runtime
        station.series_revisions["pd"] = 7

        station.reset_charts()

        self.assertEqual(station.series_revisions["pd"], 7)

    def test_legacy_configuration_migrates_station_one_once(self) -> None:
        settings = JsonSettings(Path(self.temp_dir.name) / "legacy-settings.json")
        settings.update({
            "runtime/configuration": {"sn": "STALE-FALLBACK"},
            "runtime/stations": {
                "1": {
                    "sn": "KEEP-SN", "station": "熔接前",
                    "powerMeterResource": "ASRL3::INSTR", "powerMeterDriverKind": "coherent",
                    "spectrometerDeviceId": 5, "integrationTimeUs": 12345,
                    "ch341SelectedLocationPath": "STALE-USB-PATH",
                },
                "2": {"sn": "DISCARD-SN", "spectrometerDeviceId": 7},
            },
            "shipping_report/pole/custom_fields": ["keep-field"],
        })
        backend = HeadlessBackend(settings)
        try:
            config = backend.runtime.configuration
            self.assertEqual(config["sn"], "KEEP-SN")
            self.assertEqual(config["station"], "熔接前")
            self.assertEqual(config["powerMeterResource"], "ASRL3::INSTR")
            self.assertEqual(config["powerMeterDriverKind"], "coherent")
            self.assertEqual(config["spectrometerDeviceId"], 5)
            self.assertEqual(config["integrationTimeUs"], 12345)
            self.assertEqual(config["ch341SelectedLocationPath"], "")
            self.assertIsNone(settings.get("runtime/stations"))
            self.assertEqual(settings.get("shipping_report/pole/custom_fields"), ["keep-field"])
            backend.dispatch("app.configure", {"sn": "NEW-SN"})
        finally:
            backend.export_worker.graceful_stop(timeout_s=5.0)
        restarted = HeadlessBackend(JsonSettings(settings.path))
        try:
            self.assertEqual(restarted.runtime.configuration["sn"], "NEW-SN")
            self.assertEqual(restarted.runtime.configuration["spectrometerDeviceId"], 5)
        finally:
            restarted.export_worker.graceful_stop(timeout_s=5.0)

    def test_legacy_migration_preserves_settings_when_atomic_write_fails(self) -> None:
        settings = JsonSettings(Path(self.temp_dir.name) / "failed-migration.json")
        original = {"runtime/stations": {"1": {"sn": "KEEP"}, "2": {"sn": "OLD"}}}
        settings.update(original)
        original_bytes = settings.path.read_bytes()
        with patch.object(settings, "flush", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                HeadlessBackend(settings)
        self.assertEqual(settings.path.read_bytes(), original_bytes)
        self.assertEqual(settings.get("runtime/stations"), original["runtime/stations"])
        self.assertIsNone(settings.get("runtime/configuration"))

    def test_legacy_migration_uses_main_configuration_when_station_one_is_missing(self) -> None:
        settings = JsonSettings(Path(self.temp_dir.name) / "fallback-migration.json")
        settings.update({
            "runtime/configuration": {"sn": "KEEP", "station": "出厂测试"},
            "runtime/stations": {"2": {"sn": "DO-NOT-ADOPT"}},
        })
        backend = HeadlessBackend(settings)
        try:
            self.assertEqual(backend.runtime.configuration["sn"], "KEEP")
            self.assertEqual(backend.runtime.configuration["station"], "出厂测试")
            self.assertIsNone(settings.get("runtime/stations"))
        finally:
            backend.export_worker.graceful_stop(timeout_s=5.0)

    def test_loaded_power_meter_identity_is_normalized_and_restored(self) -> None:
        settings = JsonSettings(Path(self.temp_dir.name) / "ophir-settings.json")
        settings.set(
            "runtime/stations",
            {
                "1": {
                    "powerMeterResource": "Ophir USB | OPHIR::USB::3103403 | USB SN 3103403",
                    "powerMeterDriverKind": "caihuang",
                }
            },
        )
        backend = HeadlessBackend(settings)
        try:
            config = backend.runtime.configuration
            self.assertEqual(config["powerMeterResource"], "OPHIR::USB::3103403")
            self.assertEqual(config["powerMeterDriverKind"], "ophir")
            persisted = settings.get("runtime/configuration")
            self.assertEqual(persisted["powerMeterResource"], "OPHIR::USB::3103403")
            self.assertEqual(persisted["powerMeterDriverKind"], "ophir")
        finally:
            backend.export_worker.graceful_stop(timeout_s=5.0)

    def test_configuring_ophir_resource_synchronizes_driver_kind(self) -> None:
        self.backend.power_meter_options = [
            PowerMeterOption(
                resource="OPHIR::USB::METER123",
                device_type="Ophir USB",
                detail="USB 表头 SN METER123 | 探头 SN PROBE456",
                driver_kind="ophir",
                serial_number="PROBE456",
            )
        ]
        self.backend.dispatch(
            "app.configure",
            {
                "stationId": "1",
                "powerMeterResource": (
                    "Ophir USB | OPHIR::USB::METER123 | "
                    "USB 表头 SN METER123 | 探头 SN PROBE456"
                ),
            },
        )

        config = self.backend.runtime.configuration
        self.assertEqual(config["powerMeterResource"], "OPHIR::USB::METER123")
        self.assertEqual(config["powerMeterDriverKind"], "ophir")
        self.assertEqual(config["powerMeterSerialNumber"], "PROBE456")

    def test_power_meter_gain_is_restored_only_for_the_same_serial_number(self) -> None:
        meter_a = PowerMeterOption(
            resource="ASRL3::INSTR",
            device_type="Caihuang CHLP-P",
            detail="OK | 增益身份：探头 SN PROBE-A",
            serial_number="PROBE-A",
        )
        meter_b = PowerMeterOption(
            resource="ASRL7::INSTR",
            device_type="Caihuang CHLP-P",
            detail="OK | 增益身份：探头 SN PROBE-B",
            serial_number="PROBE-B",
        )
        self.backend.power_meter_options = [meter_a, meter_b]

        self.backend.dispatch("app.configure", {"powerMeterResource": meter_a.resource})
        self.backend.dispatch(
            "powerMeter.profile.create",
            {
                "driverKind": "caihuang", "serialNumber": "PROBE-A",
                "name": "Probe A", "softwareGain": 1.23, "wavelengthNm": 976.0,
            },
        )
        self.backend.dispatch(
            "app.configure",
            {"stationId": "1", "powerMeterResource": meter_b.resource},
        )
        self.assertEqual(self.backend.runtime.configuration["softwareGain"], 1.0)

        self.backend.dispatch(
            "powerMeter.profile.create",
            {
                "driverKind": "caihuang", "serialNumber": "PROBE-B",
                "name": "Probe B", "softwareGain": 0.98, "wavelengthNm": 977.0,
            },
        )
        self.backend.dispatch(
            "app.configure",
            {"stationId": "1", "powerMeterResource": meter_a.resource},
        )
        config = self.backend.runtime.configuration
        self.assertEqual(config["powerMeterSerialNumber"], "PROBE-A")
        self.assertEqual(config["softwareGain"], 1.23)

    def test_full_frontend_config_restores_gain_when_only_meter_changes(self) -> None:
        meter_a = PowerMeterOption(
            resource="ASRL7::INSTR",
            device_type="Caihuang CHLP-P",
            detail="OK | 增益身份：探头 SN W500-CB-40",
            driver_kind="caihuang",
            serial_number="W500-CB-40",
        )
        meter_b = PowerMeterOption(
            resource="ASRL6::INSTR",
            device_type="LaserPoint",
            detail="探头 SN 222483",
            driver_kind="laserpoint",
            serial_number="222483",
        )
        self.backend.power_meter_options = [meter_a, meter_b]

        self.backend.dispatch("app.configure", {"powerMeterResource": meter_a.resource})
        self.backend.dispatch(
            "powerMeter.profile.create",
            {
                "driverKind": "caihuang", "serialNumber": "W500-CB-40",
                "name": "Meter A", "softwareGain": 1.04, "wavelengthNm": 976.0,
            },
        )
        self.backend.dispatch("app.configure", {"powerMeterResource": meter_b.resource})
        self.backend.dispatch(
            "powerMeter.profile.create",
            {
                "driverKind": "laserpoint", "serialNumber": "222483",
                "name": "Meter B", "softwareGain": 0.97, "wavelengthNm": 977.0,
            },
        )
        self.backend.dispatch(
            "app.configure",
            {"stationId": "1", "powerMeterResource": meter_a.resource},
        )

        profiles = {
            item["resource"]: item
            for item in self.backend.snapshot({"stationId": "1"})["devices"]["powerMeter"]["resourceProfiles"]
        }
        profile_a = self.backend.power_meter_profiles.selected(
            profiles[meter_a.resource]["coefficientLibrary"]
        )
        profile_b = self.backend.power_meter_profiles.selected(
            profiles[meter_b.resource]["coefficientLibrary"]
        )
        self.assertEqual(profile_a["softwareGain"], 1.04)
        self.assertEqual(profile_b["softwareGain"], 0.97)
        self.assertEqual(profiles[meter_b.resource]["serialNumber"], "222483")

        frontend_payload = dict(self.backend.runtime.configuration)
        frontend_payload.update(
            {"stationId": "1", "powerMeterResource": meter_b.resource}
        )
        self.assertEqual(frontend_payload["softwareGain"], 1.04)

        self.backend.dispatch("app.configure", frontend_payload)

        config = self.backend.runtime.configuration
        self.assertEqual(config["powerMeterDriverKind"], "laserpoint")
        self.assertEqual(config["powerMeterSerialNumber"], "222483")
        self.assertEqual(config["softwareGain"], 0.97)
        selected = self.backend.power_meter_profiles.selected(
            self.backend.device_profiles["powerMeters"]["laserpoint:222483"]
        )
        self.assertEqual(selected["softwareGain"], 0.97)

    def test_full_frontend_config_cannot_write_gain_while_switching_meter(self) -> None:
        meter_a = PowerMeterOption(
            resource="ASRL7::INSTR",
            device_type="Caihuang CHLP-P",
            detail="OK | 增益身份：探头 SN W500-CB-40",
            driver_kind="caihuang",
            serial_number="W500-CB-40",
        )
        meter_b = PowerMeterOption(
            resource="ASRL6::INSTR",
            device_type="LaserPoint",
            detail="探头 SN 222483",
            driver_kind="laserpoint",
            serial_number="222483",
        )
        self.backend.power_meter_options = [meter_a, meter_b]
        self.backend.dispatch("app.configure", {"powerMeterResource": meter_a.resource})
        self.backend.dispatch(
            "powerMeter.profile.create",
            {
                "driverKind": "caihuang", "serialNumber": "W500-CB-40",
                "name": "Meter A", "softwareGain": 1.04, "wavelengthNm": 976.0,
            },
        )

        frontend_payload = dict(self.backend.runtime.configuration)
        frontend_payload.update(
            {
                "stationId": "1",
                "powerMeterResource": meter_b.resource,
                "softwareGain": 1.08,
            }
        )
        self.backend.dispatch("app.configure", frontend_payload)

        config = self.backend.runtime.configuration
        self.assertEqual(config["powerMeterSerialNumber"], "222483")
        self.assertEqual(config["softwareGain"], 1.0)
        selected = self.backend.power_meter_profiles.selected(
            self.backend.device_profiles["powerMeters"]["laserpoint:222483"]
        )
        self.assertEqual(selected["softwareGain"], 1.0)

    def test_remembered_serial_rebinds_power_meter_to_its_current_port(self) -> None:
        station = self.backend.runtime
        station.configuration.update(
            {
                "powerMeterResource": "ASRL3::INSTR",
                "powerMeterDriverKind": "caihuang",
                "powerMeterSerialNumber": "PROBE-A",
                "softwareGain": 1.0,
            }
        )
        self.backend.device_profiles["powerMeters"]["caihuang:PROBE-A"] = {
            "serialNumber": "PROBE-A",
            "driverKind": "caihuang",
            "softwareGain": 1.37,
        }
        moved_meter = PowerMeterOption(
            resource="ASRL11::INSTR",
            device_type="Caihuang CHLP-P",
            detail="OK | 增益身份：探头 SN PROBE-A",
            serial_number="PROBE-A",
        )

        self.backend.discovery._rebind_remembered_power_meters([moved_meter])

        self.assertEqual(station.configuration["powerMeterResource"], "ASRL11::INSTR")
        self.assertEqual(station.configuration["softwareGain"], 1.37)

    def test_ophir_probe_gain_survives_a_meter_head_change(self) -> None:
        station = self.backend.runtime
        station.configuration.update(
            {
                "powerMeterResource": "OPHIR::USB::OLD-METER",
                "powerMeterDriverKind": "ophir",
                "powerMeterSerialNumber": "PROBE-009",
                "softwareGain": 1.0,
            }
        )
        self.backend.device_profiles["powerMeters"]["ophir:PROBE-009"] = {
            "serialNumber": "PROBE-009",
            "driverKind": "ophir",
            "softwareGain": 0.87,
        }
        same_probe_on_new_meter = PowerMeterOption(
            resource="OPHIR::USB::NEW-METER",
            device_type="Ophir USB",
            detail="USB 表头 SN NEW-METER | 探头 SN PROBE-009",
            driver_kind="ophir",
            serial_number="PROBE-009",
        )

        self.backend.discovery._rebind_remembered_power_meters([same_probe_on_new_meter])

        self.assertEqual(station.configuration["powerMeterResource"], "OPHIR::USB::NEW-METER")
        self.assertEqual(station.configuration["powerMeterSerialNumber"], "PROBE-009")
        self.assertEqual(station.configuration["softwareGain"], 0.87)

    def test_remembered_spectrometer_serial_rebinds_new_oceandirect_id(self) -> None:
        station = self.backend.runtime
        station.configuration.update(
            {
                "spectrometerResource": "Ocean Insight | 设备 ID 5 | SN OCEAN-A",
                "spectrometerDeviceId": 5,
                "spectrometerSerialNumber": "OCEAN-A",
            }
        )
        resources = [
            {
                "label": "Ocean Insight | 设备 ID 19 | SN OCEAN-A",
                "deviceId": 19,
                "serialNumber": "OCEAN-A",
            }
        ]

        self.backend.discovery._rebind_remembered_spectrometers(resources)

        self.assertEqual(station.configuration["spectrometerDeviceId"], 19)
        self.assertEqual(station.configuration["spectrometerResource"], resources[0]["label"])

    def test_startup_restores_remembered_devices_and_scans_spectrometer(self) -> None:
        station = self.backend.runtime
        station.configuration.update(
            {
                "powerMeterResource": "ASRL8::INSTR",
                "powerMeterDriverKind": "coherent",
                "powerMeterSerialNumber": "PROBE-B",
                "spectrometerResource": "Ocean Insight | 设备 ID 5 | SN OCEAN-B",
                "spectrometerDeviceId": 5,
                "spectrometerSerialNumber": "OCEAN-B",
            }
        )

        with (
            patch.object(self.backend.discovery, "_refresh_power_meter") as refresh_power_meter,
            patch.object(self.backend.discovery, "_refresh_spectrometer") as refresh_spectrometer,
        ):
            self.backend.start_remembered_device_discovery()

        refresh_power_meter.assert_not_called()
        refresh_spectrometer.assert_called_once_with()
        self.assertEqual(len(self.backend.power_meter_options), 1)
        self.assertEqual(self.backend.power_meter_options[0].serial_number, "PROBE-B")
        self.assertEqual(
            self.backend.spectrometer_resources,
            [
                {
                    "label": "Ocean Insight | 设备 ID 5 | SN OCEAN-B",
                    "deviceId": 5,
                    "serialNumber": "OCEAN-B",
                }
            ],
        )

    def test_hardware_commands_use_single_runtime_and_accept_legacy_station_one(self) -> None:
        controller = _FakeController()
        self.backend.runtime.power_supply_controller = controller
        self.backend.runtime.power_supply_kind = "tdk"
        self.backend.dispatch("powerSupply.setCurrent", {"currentA": 3.0})
        self.backend.dispatch("powerSupply.setCurrent", {"stationId": "1", "currentA": 6.0})
        self.assertEqual(controller.writes, [3.0, 6.0])
        self.assertEqual(self.backend.runtime.active_current_a, 6.0)

    def test_tdk_output_is_not_enabled_until_zero_current_is_verified(self) -> None:
        station = self.backend.runtime
        controller = MagicMock()
        controller.is_connected = True
        controller.output_enabled = False
        controller.read_output_current.return_value = 0.25
        station.power_supply_kind = "tdk"
        station.power_supply_controller = controller

        with self.assertRaisesRegex(RuntimeError, "电流未归零"):
            station.set_output({"enabled": True})

        controller.set_output_current.assert_called_once_with(0.0)
        controller.set_output_enabled.assert_not_called()

    def test_failed_disconnect_keeps_controller_state_for_retry(self) -> None:
        station = self.backend.runtime
        controller = MagicMock()
        controller.is_connected = True
        controller.disconnect_device.return_value = False
        station.power_supply_controller = controller

        with self.assertRaisesRegex(RuntimeError, "无法确认设备已经断开"):
            station.disconnect_power_supply()

        self.assertIs(station.power_supply_controller, controller)

    def test_ch341_does_not_create_or_write_second_samples(self) -> None:
        station = self.backend.runtime
        controller = MagicMock()
        controller.is_connected = True
        station.power_supply_kind = "ch341"
        station.power_supply_controller = controller
        station.automatic_sample_recorder = MagicMock()
        recorder = station.automatic_sample_recorder

        station._write_automatic_second_sample(
            point_index=2,
            elapsed_s=1.0,
            target_current_a=4.0,
        )

        controller.read_output_current.assert_not_called()
        controller.read_output_voltage.assert_not_called()
        recorder.write.assert_not_called()

    def test_ch341_manual_reads_share_the_command_interval_guard(self) -> None:
        station = self.backend.runtime
        controller = MagicMock()
        controller.is_connected = True
        station.power_supply_kind = "ch341"
        station.power_supply_controller = controller
        station._last_power_supply_command_at = time.monotonic()

        with self.assertRaisesRegex(RuntimeError, "电源命令过快"):
            station.read_power_supply({"value": "outputVoltage"})

        controller.read_output_voltage.assert_not_called()

    def test_acquisition_failure_pauses_and_retries_current_point(self) -> None:
        station = self.backend.runtime
        station.configuration["outputDir"] = self.temp_dir.name
        station.configuration["sn"] = "AUTO-RETRY"
        station.automatic.start(
            AutomaticTestSettings(use_spectrometer=False),
            power_meter_ready=True,
            spectrum_meter_ready=False,
        )
        station.automatic.set_state(AutomaticTestState.WAITING_STABLE)
        station._begin_recording_session(station.automatic.settings)

        station._on_power_failed("serial timeout")

        self.assertEqual(station.automatic.state, AutomaticTestState.PAUSED)
        self.assertFalse(station._automatic_stop.is_set())
        attempts = station.record_store.list_attempts(
            station.record_store.current_session.session_id
        )
        self.assertEqual(attempts[-1].validity, AttemptValidity.DEVICE_ERROR)
        station.power_meter_reader = MagicMock(is_ready=True)
        station.resume_automatic()
        with self.assertRaises(AutomaticPointRetry):
            station._await_automatic_resume()

    def test_manual_stable_point_can_be_saved_to_excel(self) -> None:
        station = self.backend.runtime
        station.configuration.update(
            {
                "outputDir": self.temp_dir.name,
                "sn": "MANUAL-DRAFT",
                "station": "暂存站",
            }
        )
        station.active_current_a = 2.0
        station.recorded_manual_stable_current_a = 2.0
        station.recorded_manual_stability_generation = 3
        station.power_meter_reader = MagicMock(is_ready=True)
        station.latest_power_received_monotonic_s = time.monotonic()
        station.latest_power = PowerMeterReading(
            elapsed_s=5.0,
            power_w=4.0,
            stable=True,
            stable_span_w=0.02,
            stable_window_s=3.0,
            stable_median_w=4.0,
            stability_generation=3,
        )

        station._record_manual_voltage_point(10.0)

        self.assertAlmostEqual(station.efficiency_points[2.0], 20.0)
        snapshot = self.backend.snapshot({"view": "manual"})
        self.assertEqual(snapshot["manualTest"]["pendingCount"], 1)
        selected_output_dir = Path(self.temp_dir.name) / "selected"
        station.configuration.update(
            {
                "outputDir": str(selected_output_dir),
                "sn": "MANUAL-01",
                "station": "老化站 1",
            }
        )
        result = self.backend.dispatch("manual.saveExcel", {"stationId": "1"})
        output_path = Path(result["outputPath"])
        self.assertTrue(output_path.exists())
        self.assertEqual(output_path.parent, selected_output_dir / "MANUAL-01" / "老化站 1")
        self.assertEqual(station.record_store.current_session.sn, "MANUAL-01")
        self.assertEqual(station.record_store.current_session.station, "老化站 1")
        self.assertIsNotNone(station.record_store.current_session.ended_at_utc)
        self.assertEqual(station.record_store.snapshot()[0].test_station, "老化站 1")
        self.assertEqual(station.record_store.unsaved_records(), ())

    def test_power_supply_read_returns_snapshot_and_only_reads_requested_value(self) -> None:
        station = self.backend.runtime
        controller = MagicMock()
        controller.is_connected = True
        controller.output_enabled = False
        controller.read_output_current.return_value = 4.56
        station.power_supply_controller = controller

        snapshot = self.backend.dispatch(
            "powerSupply.read",
            {"stationId": "1", "value": "outputCurrent"},
        )

        self.assertIn("devices", snapshot)
        self.assertTrue(snapshot["devices"]["powerSupply"]["connected"])
        controller.read_output_current.assert_called_once_with()
        controller.read_output_voltage.assert_not_called()
        self.assertEqual(station.latest_power_supply_output_current_a, 4.56)
        self.assertEqual(snapshot["devices"]["powerSupply"]["outputCurrentA"], 4.56)
        self.assertEqual(snapshot["backend"]["notices"][-1]["message"], "输出电流：4.56 A")

    def test_automatic_measurement_rejects_samples_from_previous_setpoint(self) -> None:
        station = self.backend.runtime
        reader = MagicMock()
        reader.reset_stability_window.return_value = 7
        station.power_meter_reader = reader
        station.latest_power = PowerMeterReading(
            elapsed_s=1.0,
            power_w=0.11457,
            stable=True,
            stable_span_w=0.0,
            stable_window_s=3.0,
            stable_median_w=0.11457,
            stability_generation=6,
        )
        station.latest_spectrum = SimpleNamespace(
            saturation=SimpleNamespace(saturated=False)
        )
        station.spectrum_revision = 12

        generation, spectrum_revision = station._begin_automatic_measurement_window(2.0)

        reader.update_stability_settings.assert_called_once_with(5.0, 0.2, 0.2)
        reader.reset_stability_window.assert_called_once_with()
        self.assertEqual((generation, spectrum_revision), (7, 12))
        self.assertIsNone(
            station._fresh_automatic_measurement(
                stability_generation=generation,
                spectrum_revision=spectrum_revision,
                use_spectrometer=True,
            )
        )

        station.latest_power = PowerMeterReading(
            elapsed_s=1.1,
            power_w=0.2,
            stable=True,
            stable_span_w=0.0,
            stable_window_s=3.0,
            stable_median_w=0.2,
            stability_generation=7,
        )
        self.assertIsNone(
            station._fresh_automatic_measurement(
                stability_generation=generation,
                spectrum_revision=spectrum_revision,
                use_spectrometer=True,
            )
        )

        station.spectrum_revision = 13
        station.latest_wavelength_stable = True
        measurement = station._fresh_automatic_measurement(
            stability_generation=generation,
            spectrum_revision=spectrum_revision,
            use_spectrometer=True,
        )

        self.assertIsNotNone(measurement)
        reading, frame = measurement
        self.assertEqual(reading.power_w, 0.2)
        self.assertIs(frame, station.latest_spectrum)

    def test_power_efficiency_curve_uses_stability_window_median_like_main(self) -> None:
        station = self.backend.runtime
        reading = PowerMeterReading(
            elapsed_s=1.0,
            power_w=0.123,
            stable=True,
            stable_span_w=0.01,
            stable_window_s=3.0,
            stable_median_w=0.115,
            stability_generation=1,
        )

        self.assertEqual(station._stable_power_w(reading), 0.115)

    def test_ch341_reads_use_the_same_four_operations_as_main(self) -> None:
        station = self.backend.runtime

        class Ch341Controller:
            is_connected = True
            output_enabled = True

            def __init__(self) -> None:
                self.commands: list[list[int]] = []
                self.responses = iter(
                    (
                        [0, 0, 24, 68],
                        [0, 0, 12, 34],
                        [0, 0, 4, 56],
                        [0, 0, 37, 25],
                    )
                )

            def i2c_write_read(
                self,
                _address: int,
                command: list[int],
                _read_length: int,
            ) -> tuple[bool, list[int]]:
                self.commands.append(command)
                return True, next(self.responses)

        controller = Ch341Controller()
        station.power_supply_controller = controller
        station.power_supply_kind = "ch341"

        for value_name in ("inputVoltage", "outputVoltage", "outputCurrent", "temperature"):
            # This test verifies the command bytes independently; command-rate
            # enforcement is covered by a dedicated regression test below.
            station._last_power_supply_command_at = None
            snapshot = self.backend.dispatch(
                "powerSupply.read",
                {"stationId": "1", "value": value_name},
            )

        self.assertEqual(
            controller.commands,
            [
                [0xB4, 0x88, 0x00, 0x00],
                [0xB4, 0x8B, 0x00, 0x00],
                [0xB4, 0x8C, 0x00, 0x00],
                [0xB4, 0x8D, 0x00, 0x00],
            ],
        )
        power_supply = snapshot["devices"]["powerSupply"]
        self.assertAlmostEqual(power_supply["inputVoltageV"], 24.68)
        self.assertAlmostEqual(power_supply["outputVoltageV"], 12.34)
        self.assertAlmostEqual(power_supply["outputCurrentA"], 4.56)
        self.assertAlmostEqual(power_supply["temperatureC"], 37.25)

    def test_stale_second_station_requests_cannot_control_the_single_runtime(self) -> None:
        controller = _FakeController()
        self.backend.runtime.power_supply_controller = controller
        original = dict(self.backend.runtime.configuration)
        for method, params in (
            ("powerSupply.setCurrent", {"currentA": 3}),
            ("app.configure", {"sn": "WRONG-SN"}),
            ("automatic.start", {}),
            ("device.refresh", {"device": "powerSupply"}),
        ):
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, "单工位"):
                self.backend.dispatch(method, {"stationId": "2", **params})
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, "未知视图"):
                self.backend.dispatch(method, {"view": "automatic_2", **params})
        self.assertEqual(controller.writes, [])
        self.assertEqual(self.backend.runtime.configuration, original)
        with self.assertRaisesRegex(ValueError, "单工位"):
            self.backend.snapshot({"stationId": "2"})

    def test_snapshot_lists_all_ch341_ports_with_the_selected_port(self) -> None:
        locations = ["PCIROOT(0)#USB(1)", "PCIROOT(0)#USB(2)"]
        self.backend.runtime.power_supply_controller = _FakeController()
        self.backend.runtime.configuration["ch341SelectedLocationPath"] = locations[0]
        self.backend.ch341_descriptors = [Ch341DeviceDescriptor(i, f"iface-{i}", loc) for i, loc in enumerate(locations)]
        ports = self.backend.snapshot()["devices"]["powerSupply"]["ch341"]["ports"]
        self.assertEqual([(port["locationPath"], port["deviceIndex"]) for port in ports], list(zip(locations, (0, 1))))

    def test_spectrometer_open_updates_and_persists_identity(self) -> None:
        station = self.backend.runtime
        station.configuration["spectrometerDeviceId"] = 5
        station.configuration["spectrometerResource"] = "Ocean Insight | 设备 ID 5"
        self.backend.spectrometer_resources = [
            {"label": "Ocean Insight | 设备 ID 5", "deviceId": 5}
        ]

        station._on_spectrometer_device_opened(7)

        self.assertEqual(station.configuration["spectrometerDeviceId"], 7)
        self.assertEqual(
            station.configuration["spectrometerResource"],
            "Ocean Insight | 设备 ID 7",
        )
        self.assertEqual(
            self.backend.spectrometer_resources,
            [{"label": "Ocean Insight | 设备 ID 7", "deviceId": 7, "serialNumber": ""}],
        )
        self.backend._persist_configuration()
        persisted = self.backend.settings.get("runtime/configuration")
        self.assertEqual(persisted["spectrometerResource"], "Ocean Insight | 设备 ID 7")
        self.assertEqual(persisted["spectrometerDeviceId"], 7)
        self.assertIn("光谱仪设备 ID 已从 5 自动更新为 7", self.backend.status_message)
        self.assertFalse(
            any(notice["title"].endswith("光谱仪设备已重绑定") for notice in self.backend.notices)
        )

        notice_count = len(self.backend.notices)
        station._on_spectrometer_device_opened(7)
        self.assertEqual(len(self.backend.notices), notice_count)

    def test_power_meter_detection_selects_first_device_and_persists_it(self) -> None:
        option = PowerMeterOption(
            resource="OPHIR::USB::3103403",
            device_type="Ophir USB",
            detail="USB 表头 SN 3103403 | 探头 SN PROBE-001",
            driver_kind="ophir",
            serial_number="PROBE-001",
        )
        second_option = PowerMeterOption(
            resource="ASRL9::INSTR",
            device_type="LaserPoint",
            detail="探头 SN 654321",
            driver_kind="laserpoint",
            serial_number="654321",
        )
        instances = []

        class FakeDetectThread:
            detected = Signal(object)
            status = Signal(str)
            failed = Signal(str)
            finished = Signal()

            def __init__(self, preferred_resource: str = "") -> None:
                self.preferred_resource = preferred_resource
                instances.append(self)

            def start(self) -> None:
                return

        with patch("tauri_bridge.device_discovery.PowerMeterDetectThread", FakeDetectThread):
            self.backend.dispatch(
                "device.refresh",
                {"stationId": "1", "device": "powerMeter"},
            )

        instances[0].status.emit("正在检测串口 1/1：ASRL7::INSTR（依次尝试 彩煌、Coherent、LaserPoint）")
        scanning_snapshot = self.backend.snapshot({"stationId": "1"})
        self.assertTrue(scanning_snapshot["devices"]["powerMeter"]["detecting"])
        self.assertEqual(
            scanning_snapshot["devices"]["powerMeter"]["detail"],
            "正在检测串口 1/1：ASRL7::INSTR（依次尝试 彩煌、Coherent、LaserPoint）",
        )
        instances[0].detected.emit([option, second_option])
        instances[0].finished.emit()

        runtime = self.backend.runtime.configuration
        self.assertEqual(runtime["powerMeterResource"], option.resource)
        self.assertEqual(runtime["powerMeterDriverKind"], "ophir")
        self.assertEqual(runtime["powerMeterSerialNumber"], "PROBE-001")
        persisted = self.backend.settings.get("runtime/configuration")
        self.assertEqual(persisted["powerMeterResource"], option.resource)
        self.assertEqual(persisted["powerMeterDriverKind"], "ophir")
        self.assertEqual(persisted["powerMeterSerialNumber"], "PROBE-001")
        self.assertEqual(
            self.backend.snapshot({"stationId": "1"})["devices"]["powerMeter"]["resources"],
            [option.label(), second_option.label()],
        )
        self.assertEqual(
            self.backend.snapshot({"stationId": "1"})["devices"]["powerMeter"]["detail"],
            "识别完成：发现 2 台兼容功率计",
        )

    def test_selected_devices_remain_in_available_options(self) -> None:
        occupied_meter = PowerMeterOption(
            resource="ASRL3::INSTR",
            device_type="彩煌串口",
            detail="COM3",
            driver_kind="caihuang",
        )
        available_meter = PowerMeterOption(
            resource="OPHIR::USB::3103403",
            device_type="Ophir USB",
            detail="USB SN 3103403",
            driver_kind="ophir",
        )
        self.backend.power_meter_options = [occupied_meter, available_meter]
        self.backend.spectrometer_resources = [
            {"label": "Ocean Insight | 设备 ID 5", "deviceId": 5},
            {"label": "Ocean Insight | 设备 ID 7", "deviceId": 7},
        ]
        station_one = self.backend.runtime
        station_one.configuration["powerMeterResource"] = occupied_meter.resource
        station_one.configuration["spectrometerResource"] = "Ocean Insight | 设备 ID 5"
        station_one.configuration["spectrometerDeviceId"] = 5
        station_one.power_meter_reader = MagicMock(is_ready=True)
        station_one.spectrometer_reader = MagicMock(is_ready=True)

        station_one_devices = self.backend.snapshot({"stationId": "1"})["devices"]
        self.assertEqual(
            station_one_devices["powerMeter"]["resources"],
            [occupied_meter.label(), available_meter.label()],
        )
        self.assertEqual(
            station_one_devices["spectrometer"]["resources"],
            ["Ocean Insight | 设备 ID 5", "Ocean Insight | 设备 ID 7"],
        )

    def test_spectrometer_requires_an_explicit_runtime_selection(self) -> None:
        with self.assertRaisesRegex(ValueError, "请先选择光谱仪设备"):
            self.backend.runtime.start_spectrometer()

    def test_ch341_detection_completion_is_published_as_toast(self) -> None:
        instances = []

        class FakeDiscoveryThread:
            completed = Signal(object)
            status = Signal(str)
            failed = Signal(str)
            finished = Signal()

            def __init__(self, discover) -> None:
                self.discover = discover
                instances.append(self)

            def start(self) -> None:
                return

        with patch("tauri_bridge.device_discovery._DeviceDiscoveryThread", FakeDiscoveryThread):
            self.backend.dispatch(
                "device.refresh",
                {"device": "powerSupply"},
            )

        instances[0].completed.emit(("ch341", []))
        instances[0].finished.emit()

        notice = self.backend.notices[-1]
        self.assertEqual(notice["level"], "info")
        self.assertEqual(notice["title"], "CH341 识别")
        self.assertEqual(notice["message"], "识别完成：发现 0 个 CH341 设备")
        self.assertNotIn("stationId", notice)

        station = self.backend.runtime
        station.power_supply_kind = "tdk"
        station.configuration["powerSupplyKind"] = "tdk"
        tdk_snapshot = self.backend.snapshot()["devices"]["powerSupply"]
        self.assertFalse(tdk_snapshot["detecting"])
        self.assertEqual(tdk_snapshot["detail"], "未连接")

    def test_ch341_detection_selects_device_zero_automatically(self) -> None:
        instances = []

        class FakeDiscoveryThread:
            completed = Signal(object)
            status = Signal(str)
            failed = Signal(str)
            finished = Signal()

            def __init__(self, discover) -> None:
                self.discover = discover
                instances.append(self)

            def start(self) -> None:
                return None

        descriptors = [
            Ch341DeviceDescriptor(0, "iface-0", "PCIROOT(0)#USB(1)"),
            Ch341DeviceDescriptor(1, "iface-1", "PCIROOT(0)#USB(2)"),
        ]
        with patch("tauri_bridge.device_discovery._DeviceDiscoveryThread", FakeDiscoveryThread):
            self.backend.dispatch(
                "device.refresh",
                {"stationId": "1", "device": "powerSupply"},
            )

        instances[0].completed.emit(("ch341", descriptors))
        instances[0].finished.emit()

        self.assertEqual(
            self.backend.runtime.configuration["ch341SelectedLocationPath"],
            "PCIROOT(0)#USB(1)",
        )

    def test_spectrometer_detection_selects_first_device_automatically(self) -> None:
        instances = []

        class FakeDiscoveryThread:
            completed = Signal(object)
            status = Signal(str)
            failed = Signal(str)
            finished = Signal()

            def __init__(self, discover) -> None:
                self.discover = discover
                instances.append(self)

            def start(self) -> None:
                return None

        with patch("tauri_bridge.device_discovery._DeviceDiscoveryThread", FakeDiscoveryThread):
            self.backend.dispatch(
                "device.refresh",
                {"stationId": "1", "device": "spectrometer"},
            )

        instances[0].completed.emit(
            [
                {"deviceId": 11, "serialNumber": "OCEAN-A"},
                {"deviceId": 12, "serialNumber": "OCEAN-B"},
            ]
        )
        instances[0].finished.emit()

        config = self.backend.runtime.configuration
        self.assertEqual(config["spectrometerDeviceId"], 11)
        self.assertEqual(config["spectrometerSerialNumber"], "OCEAN-A")
        self.assertEqual(
            config["spectrometerResource"],
            "Ocean Insight | 设备 ID 11 | SN OCEAN-A",
        )
        self.assertEqual(len(self.backend.spectrometer_resources), 2)

    def test_ch341_connection_opens_selected_index_at_20_khz(self) -> None:
        location = "PCIROOT(0)#USB(2)"
        station = self.backend.runtime
        station.configuration.update(
            {
                "powerSupplyKind": "ch341",
                "ch341SelectedLocationPath": location,
            }
        )
        station.power_supply_kind = "ch341"
        self.backend.ch341_discovery_state = "ready"
        self.backend.ch341_descriptors = [
            Ch341DeviceDescriptor(1, "iface-1", location)
        ]
        controller = MagicMock()
        controller.connect_device.return_value = (True, {"device_index": 1})
        controller.get_device_interface_path.return_value = "iface-1"

        with patch(
            "tauri_bridge.station_runtime.create_ch341_controller",
            return_value=controller,
        ) as create_controller:
            with patch.object(self.backend, "scan_ch341", return_value=None):
                with patch(
                    "tauri_bridge.station_runtime.get_location_path_from_interface_path",
                    return_value=location,
                ):
                    station.connect_power_supply()

        create_controller.assert_called_once_with(device_index=1)
        controller.set_i2c_speed.assert_called_once_with(0)
        controller.connect_device.assert_called_once_with(1)
        self.assertIs(station.power_supply_controller, controller)
        self.assertEqual(station.connected_ch341_port["name"], "Device 1")

    def test_ch341_failed_open_is_not_reported_as_connected(self) -> None:
        location = "PCIROOT(0)#USB(1)"
        station = self.backend.runtime
        station.configuration["ch341SelectedLocationPath"] = location
        station.configuration["ch341PortBindings"] = [
            {"locationPath": location, "name": "1号工位"}
        ]
        station.power_supply_kind = "ch341"
        self.backend.ch341_discovery_state = "ready"
        self.backend.ch341_descriptors = [
            Ch341DeviceDescriptor(0, "iface-0", location)
        ]
        controller = MagicMock()
        controller.connect_device.return_value = (False, "无法打开设备")

        with patch(
            "tauri_bridge.station_runtime.create_ch341_controller",
            return_value=controller,
        ):
            with patch.object(self.backend, "scan_ch341", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "无法打开设备"):
                    station.connect_power_supply()

        self.assertIsNone(station.power_supply_controller)
        controller.get_device_interface_path.assert_not_called()

    def test_ch341_post_open_port_mismatch_disconnects_device(self) -> None:
        location = "PCIROOT(0)#USB(1)"
        station = self.backend.runtime
        station.configuration["ch341SelectedLocationPath"] = location
        station.configuration["ch341PortBindings"] = [
            {"locationPath": location, "name": "1号工位"}
        ]
        station.power_supply_kind = "ch341"
        self.backend.ch341_discovery_state = "ready"
        self.backend.ch341_descriptors = [
            Ch341DeviceDescriptor(0, "iface-0", location)
        ]
        controller = MagicMock()
        controller.connect_device.return_value = (True, "connected")
        controller.get_device_interface_path.return_value = "iface-other"
        controller.disconnect_device.return_value = True

        with patch(
            "tauri_bridge.station_runtime.create_ch341_controller",
            return_value=controller,
        ):
            with patch.object(self.backend, "scan_ch341", return_value=None):
                with patch(
                    "tauri_bridge.station_runtime.get_location_path_from_interface_path",
                    return_value="PCIROOT(0)#USB(2)",
                ):
                    with self.assertRaisesRegex(RuntimeError, "端口复核不一致"):
                        station.connect_power_supply()

        controller.disconnect_device.assert_called_once_with()
        self.assertIsNone(station.power_supply_controller)

    def test_emergency_stop_confirms_zero_current(self) -> None:
        controller = _FakeController()
        controller.current_a = 5.0
        self.backend.runtime.power_supply_controller = controller
        self.backend.runtime.active_current_a = 5.0
        self.backend.dispatch("app.stopAll", {})
        self.assertEqual(controller.current_a, 0.0)
        self.assertEqual(self.backend.runtime.active_current_a, 0.0)

    def test_emergency_stop_signals_before_cleanup_and_still_stops_pd_on_failure(self) -> None:
        events = []
        def fail_cleanup():
            events.append("cleanup")
            raise RuntimeError("power shutdown failed")
        with (
            patch.object(self.backend.runtime, "request_emergency_stop", side_effect=lambda _reason: events.append("signal")),
            patch.object(self.backend.runtime, "stop_all", side_effect=fail_cleanup),
            patch.object(self.backend, "_stop_pd", side_effect=lambda _runtime: events.append("pd")),
        ):
            with self.assertRaisesRegex(RuntimeError, "power shutdown failed"):
                self.backend._stop_all()
        self.assertEqual(events, ["signal", "cleanup", "pd"])

    def test_emergency_stop_signal_does_not_wait_for_station_state_lock(self) -> None:
        station = self.backend.runtime
        station._lock.acquire()
        try:
            worker = threading.Thread(
                target=station.request_emergency_stop,
                args=("急停",),
            )
            worker.start()
            worker.join(0.5)
            self.assertFalse(worker.is_alive())
            self.assertTrue(station._automatic_stop.is_set())
        finally:
            station._lock.release()
        ramp_thread = station._emergency_ramp_thread
        if ramp_thread is not None:
            ramp_thread.join(1.0)

    def test_safety_commands_do_not_depend_on_configuration_persistence(self) -> None:
        with patch.object(
            self.backend,
            "_persist_configuration",
            side_effect=RuntimeError("settings disk full"),
        ):
            snapshot = self.backend.dispatch("app.stopAll", {})

        self.assertTrue(snapshot["safety"]["shutdown"]["confirmed"])

    def test_manual_hardware_commands_are_rejected_while_automatic_owns_control(self) -> None:
        station = self.backend.runtime
        station.automatic.state = AutomaticTestState.PAUSED

        with self.assertRaisesRegex(RuntimeError, "硬件控制权由自动测试持有"):
            self.backend.dispatch("powerSupply.setCurrent", {"stationId": "1", "currentA": 1.0})

        snapshot = self.backend.snapshot({"view": "manual"})
        self.assertEqual(snapshot["safety"]["controlOwner"], "automatic")
        self.assertFalse(snapshot["safety"]["manualControlsAllowed"])

    def test_snapshot_reports_actual_saved_point_count(self) -> None:
        station = self.backend.runtime
        station.record_store.recorded_currents.update({2.0, 4.0})

        snapshot = self.backend.snapshot({"view": "automatic"})

        self.assertEqual(snapshot["automaticTest"]["savedPointCount"], 2)

    def test_operator_pause_publishes_reason_actions_and_deadline(self) -> None:
        station = self.backend.runtime
        station.automatic.settings = AutomaticTestSettings(
            pause_ramp_down_timeout_s=60.0,
            use_spectrometer=False,
        )
        station.automatic.state = AutomaticTestState.WAITING_STABLE

        station.pause_automatic()
        snapshot = self.backend.snapshot({"view": "automatic"})["automaticTest"]

        self.assertEqual(snapshot["pauseKind"], "operator")
        self.assertEqual(snapshot["pauseActions"], ["resume", "safe_end"])
        self.assertIsNotNone(snapshot["pauseStartedAtUtc"])
        self.assertIsNotNone(snapshot["pauseDeadlineAtUtc"])
        station.resume_automatic("resume")
        self.assertEqual(station.automatic.state, AutomaticTestState.WAITING_STABLE)

    def test_spectrum_pause_only_accepts_controlled_recovery_action(self) -> None:
        station = self.backend.runtime
        station.automatic.settings = AutomaticTestSettings(use_spectrometer=True)
        station.automatic.state = AutomaticTestState.WAITING_STABLE
        station.spectrometer_reader = MagicMock()
        station._set_automatic_pause(
            "光谱饱和",
            kind="spectrum_saturated",
            hold_current=True,
            retry_point=True,
        )

        with self.assertRaisesRegex(ValueError, "不允许"):
            station.resume_automatic("resume")

        station.resume_automatic("adjust_spectrum", {"integrationTimeUs": 12_000})
        self.assertEqual(station.configuration["integrationTimeUs"], 12_000)
        station.spectrometer_reader.use_fixed_integration_time.assert_called_once_with(12_000)

    def test_next_device_preserves_setup_and_clears_session_state(self) -> None:
        station = self.backend.runtime
        station.configuration.update(
            {
                "sn": "SN-DONE",
                "productModel": "PUMP-976",
                "batch": "B-23",
                "selectedScriptId": "standard",
            }
        )
        station.automatic.state = AutomaticTestState.COMPLETED
        station.shutdown_confirmed = True
        station.stable_power_points[2.0] = 5.0
        station.record_store.recorded_currents.add(2.0)
        station.automatic_started_at = 100.0
        station.automatic_finished_at = 200.0
        station.automatic_run_context = {"sn": "SN-DONE"}

        snapshot = self.backend.dispatch("automatic.nextDevice", {"stationId": "1"})

        self.assertEqual(snapshot["configuration"]["sn"], "")
        self.assertEqual(snapshot["configuration"]["productModel"], "PUMP-976")
        self.assertEqual(snapshot["configuration"]["batch"], "B-23")
        self.assertEqual(snapshot["automaticTest"]["state"], "idle")
        self.assertEqual(snapshot["automaticTest"]["savedPointCount"], 0)
        self.assertEqual(snapshot["measurements"]["stable"], [])
        self.assertIsNone(snapshot["automaticTest"]["elapsedS"])
        self.assertIsNone(snapshot["automaticTest"]["runContext"])
        self.assertIsNone(station.automatic_finished_at)

    def test_completed_test_can_restart_without_using_next_device(self) -> None:
        station = self.backend.runtime
        previous_store = station.record_store
        station.configuration.update(
            {
                "sn": "SN-RETEST",
                "powerMeterResource": "ASRL3::INSTR",
            }
        )
        station.power_supply_controller = _FakeController()
        station.power_meter_reader = MagicMock(is_ready=True)
        station.automatic.state = AutomaticTestState.COMPLETED
        station.shutdown_confirmed = False

        self.assertTrue(station.automatic_readiness()["ready"])

        with patch("tauri_bridge.station_automatic.threading.Thread") as thread_class:
            station.start_automatic({})

        self.assertEqual(station.configuration["sn"], "SN-RETEST")
        self.assertIsNot(station.record_store, previous_store)
        self.assertEqual(station.automatic.state, AutomaticTestState.STARTING)
        self.assertEqual(station.automatic_run_context["sn"], "SN-RETEST")
        self.assertIsNone(station.automatic_finished_at)
        thread_class.return_value.start.assert_called_once_with()

    def test_manual_output_off_unlocks_completed_test(self) -> None:
        station = self.backend.runtime
        controller = _FakeController()
        controller.current_a = 0.0
        station.power_supply_kind = "tdk"
        station.power_supply_controller = controller
        station.automatic.state = AutomaticTestState.COMPLETED
        station.shutdown_confirmed = False

        snapshot = self.backend.dispatch(
            "powerSupply.setOutput",
            {"stationId": "1", "enabled": False},
        )

        self.assertTrue(snapshot["safety"]["shutdown"]["confirmed"])
        self.assertTrue(snapshot["automaticTest"]["canNextDevice"])
        self.assertNotIn(
            "automatic_not_idle",
            {item["code"] for item in snapshot["automaticTest"]["readiness"]["blockers"]},
        )

    def test_manual_output_on_relocks_completed_test(self) -> None:
        station = self.backend.runtime
        controller = _FakeController()
        controller.output_enabled = False
        station.power_supply_kind = "tdk"
        station.power_supply_controller = controller
        station.automatic.state = AutomaticTestState.COMPLETED
        station.shutdown_confirmed = True

        snapshot = self.backend.dispatch(
            "powerSupply.setOutput",
            {"stationId": "1", "enabled": True},
        )

        self.assertFalse(snapshot["safety"]["shutdown"]["confirmed"])
        self.assertFalse(snapshot["automaticTest"]["canNextDevice"])

    def test_safe_disconnect_unlocks_completed_test(self) -> None:
        station = self.backend.runtime
        controller = _FakeController()
        controller.output_enabled = False
        controller.current_a = 0.0
        station.power_supply_kind = "tdk"
        station.power_supply_controller = controller
        station.automatic.state = AutomaticTestState.COMPLETED
        station.shutdown_confirmed = False

        snapshot = self.backend.dispatch(
            "powerSupply.disconnect",
            {"stationId": "1"},
        )

        self.assertTrue(snapshot["safety"]["shutdown"]["confirmed"])
        blocker_codes = {
            item["code"] for item in snapshot["automaticTest"]["readiness"]["blockers"]
        }
        self.assertNotIn("automatic_not_idle", blocker_codes)
        self.assertIn("power_supply_not_connected", blocker_codes)

    def test_automatic_export_is_queued_without_waiting_for_excel(self) -> None:
        station = self.backend.runtime
        station.record_store.begin_session(
            Path(self.temp_dir.name) / "excel",
            "SN-ASYNC",
            datetime.now(),
            test_station="工位 1",
            mode="automatic",
        )
        record = ExcelTestRecord(
            current_a=2.0,
            voltage_v=8.0,
            power_w=5.0,
            efficiency=0.3,
            peak_wavelength_nm=976.0,
            centroid_nm=976.0,
            fwhm_nm=0.4,
            pib=0.95,
            wavelength=[975.0, 976.0],
            intensity=[10.0, 20.0],
            test_station="工位 1",
        )
        station.record_store.queue(record)
        captured: list[ExportTask] = []

        with patch.object(
            self.backend.export_worker,
            "submit",
            side_effect=lambda task: captured.append(task) or True,
        ):
            station._queue_automatic_export()

        self.assertEqual(station.export_status, "exporting")
        self.assertEqual(len(captured), 1)
        captured[0].callback(str(captured[0].target_path), None)  # type: ignore[misc]
        self.assertEqual(station.export_status, "completed")
        self.assertEqual(station.record_store.exported_currents, {2.0})

    def test_snapshot_views_use_single_measurements_and_reject_removed_views(self) -> None:
        self.backend.runtime.latest_spectrum = SimpleNamespace(
            wavelength=[975.0, 976.0], intensity=[100.0, 500.0], saturation=None,
            stats=None, smsr=None, peak_annotations=(),
        )
        for view in ("automatic", "manual", "automatic_1", "manual_1"):
            snapshot = self.backend.snapshot({"view": view})
            self.assertEqual(snapshot["measurements"]["spectrum"]["wavelengthNm"], [975.0, 976.0])
            self.assertNotIn("stations", snapshot)
        for view in ("automatic_2", "manual_2", "pd_2"):
            with self.subTest(view=view), self.assertRaisesRegex(ValueError, "未知视图"):
                self.backend.snapshot({"view": view})

    def test_pd_history_is_only_included_in_the_pd_view(self) -> None:
        points = [{"elapsedS": 1.0, "value": 2.0}]
        self.backend.runtime.pd_state["points"] = points
        self.backend.runtime.series_revisions["pd"] = 1
        self.assertEqual(self.backend.snapshot({"view": "pd"})["pd"]["points"], points)
        self.assertEqual(self.backend.snapshot({"view": "pd_1"})["pd"]["points"], points)
        self.assertNotIn("points", self.backend.snapshot({"view": "automatic"})["pd"])

    def test_snapshot_is_safe_while_power_samples_arrive(self) -> None:
        station = self.backend.runtime
        errors: list[Exception] = []
        start = threading.Barrier(2)

        def publish_samples() -> None:
            try:
                start.wait()
                for index in range(1_000):
                    station._on_power_reading(
                        PowerMeterReading(
                            elapsed_s=index / 100.0,
                            power_w=0.1 + index / 1_000_000.0,
                            stable=False,
                            stable_span_w=0.0,
                            stable_window_s=3.0,
                            stable_median_w=0.1,
                            stability_generation=0,
                        )
                    )
            except Exception as exc:
                errors.append(exc)

        def collect_snapshots() -> None:
            try:
                start.wait()
                for _ in range(300):
                    snapshot = self.backend.snapshot({"view": "automatic"})
                    self.assertIn("measurements", snapshot)
            except Exception as exc:
                errors.append(exc)

        publisher = threading.Thread(target=publish_samples)
        collector = threading.Thread(target=collect_snapshots)
        publisher.start()
        collector.start()
        publisher.join()
        collector.join()
        self.assertEqual(errors, [])

    def test_export_queue_processes_tasks_sequentially(self) -> None:
        worker = ExportQueueWorker()
        worker.start()
        out_dir = Path(self.temp_dir.name)
        records = [
            ExcelTestRecord(
                current_a=1.0,
                voltage_v=1.5,
                power_w=0.8,
                efficiency=53.3,
                peak_wavelength_nm=976.0,
                centroid_nm=976.0,
                fwhm_nm=0.5,
                pib=95.0,
                wavelength=[975.0, 976.0, 977.0],
                intensity=[10.0, 100.0, 10.0],
                smsr_db=35.0,
                test_station="工位 1",
            )
        ]
        results: list[str] = []
        path1 = out_dir / "st1.xlsx"
        path2 = out_dir / "st2.xlsx"

        task1 = ExportTask(path1, records, lambda p, e: results.append(str(p)))
        task2 = ExportTask(path2, records, lambda p, e: results.append(str(p)))

        self.assertTrue(worker.submit(task1))
        self.assertTrue(worker.submit(task2))

        self.assertTrue(worker.graceful_stop(timeout_s=5.0))
        self.assertEqual(len(results), 2)
        self.assertTrue(path1.exists())
        self.assertTrue(path2.exists())
        self.assertFalse(worker.submit(task1))

    def test_export_queue_reports_timeout_without_dropping_accepted_task(self) -> None:
        worker = ExportQueueWorker()
        started = threading.Event()
        release = threading.Event()
        callback_results: list[tuple[str | None, Exception | None]] = []
        task = ExportTask(
            Path(self.temp_dir.name) / "slow.xlsx",
            [],
            lambda path, error: callback_results.append((path, error)),
        )

        def slow_save(*_args: object, **_kwargs: object) -> Path:
            started.set()
            release.wait(2.0)
            return task.target_path

        with patch("tauri_bridge.export_queue.save_test_records", side_effect=slow_save):
            worker.start()
            self.assertTrue(worker.submit(task))
            self.assertTrue(started.wait(1.0))
            self.assertFalse(worker.graceful_stop(timeout_s=0.01))
            self.assertFalse(worker.submit(task))
            release.set()
            self.assertTrue(worker.graceful_stop(timeout_s=1.0))

        self.assertEqual(callback_results, [(str(task.target_path), None)])


if __name__ == "__main__":
    unittest.main()
