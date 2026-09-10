from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from combined_test.automation import AutomaticTestSettings
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings
from tests.test_dual_station_runtime import _FakeController


class LiveCurrentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.backend = HeadlessBackend(JsonSettings(Path(self.temp.name) / "settings.json"))
        self.station = self.backend.stations["1"]
        self.controller = _FakeController()
        self.station.power_supply_controller = self.controller
        self.station.power_supply_kind = "tdk"
        self.station.configuration.update({"powerSupplyKind": "tdk", "rampDownStepA": 1, "rampDownIntervalS": 0.1})
        self.station.active_current_a = 5.0
        self.controller.current_a = 5.0
        self.station.automatic.start(
            AutomaticTestSettings(initial_current_a=5, target_current_a=5, ramp_down_step_a=1, ramp_down_interval_s=0.1),
            power_meter_ready=True,
            spectrum_meter_ready=False,
        )

    def tearDown(self) -> None:
        self.backend.export_worker.graceful_stop(timeout_s=5)
        self.temp.cleanup()

    def test_automatic_wait_publishes_measured_current_and_throttles_reads(self) -> None:
        with patch.object(self.controller, "read_output_current", side_effect=[4.82, 4.81]) as read, \
             patch("tauri_bridge.station_runtime.time.monotonic", side_effect=[10, 10.1, 10.21]):
            self.station._await_automatic_resume()
            self.station._await_automatic_resume()
            self.station._await_automatic_resume()
        self.assertEqual(read.call_count, 2)
        snapshot = self.backend.snapshot({"stationId": "1"})
        self.assertEqual(snapshot["devices"]["powerSupply"]["outputCurrentA"], 4.81)
        self.assertEqual(snapshot["automaticTest"]["currentA"], 5)
        self.assertIsNone(self.backend.snapshot({"stationId": "2"})["devices"]["powerSupply"]["outputCurrentA"])

    def test_ramp_publishes_measured_steps_and_zero_while_last_test_point_stays_five(self) -> None:
        observed = []
        with ThreadPoolExecutor(max_workers=1) as executor:
            def capture(_seconds: float) -> None:
                snapshot = executor.submit(self.backend.snapshot, {"stationId": "1"}).result(timeout=1)
                observed.append(snapshot["devices"]["powerSupply"]["outputCurrentA"])
                self.assertEqual(snapshot["automaticTest"]["currentA"], 5)

            with patch.object(self.controller, "read_output_current", side_effect=lambda: max(0, self.controller.current_a - 0.02)), \
                 patch("tauri_bridge.station_automatic.time.sleep", side_effect=capture):
                self.station.safe_ramp_down()
        self.station.automatic.complete()
        final = self.backend.snapshot({"stationId": "1"})
        self.assertEqual(observed, [3.98, 2.98, 1.98, 0.98])
        self.assertEqual(self.controller.writes, [4, 3, 2, 1, 0])
        self.assertEqual(final["devices"]["powerSupply"]["outputCurrentA"], 0)
        self.assertEqual(final["automaticTest"]["currentA"], 5)
        self.assertTrue(self.station.shutdown_confirmed)

    def test_ramp_read_failure_does_not_interrupt_shutdown_or_reuse_old_current(self) -> None:
        self.station.latest_power_supply_output_current_a = 5
        with patch.object(self.controller, "read_output_current", side_effect=[RuntimeError("timeout"), 0]) as read, \
             patch("tauri_bridge.station_automatic.time.sleep") as sleep:
            sleep.side_effect = lambda _seconds: self.assertIsNone(self.station.latest_power_supply_output_current_a)
            self.station.safe_ramp_down()
        self.assertEqual(read.call_count, 2)
        self.assertEqual(self.controller.writes, [4, 3, 2, 1, 0])
        self.assertTrue(self.station.shutdown_confirmed)
        self.assertEqual(self.station.latest_power_supply_output_current_a, 0)

    def test_ramp_still_requires_measured_zero_even_if_intermediate_reads_succeed(self) -> None:
        with patch.object(self.controller, "read_output_current", side_effect=[4, 3, 2, 1, 0.25]), \
             patch("tauri_bridge.station_automatic.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "无法确认"):
                self.station.safe_ramp_down()
        self.assertFalse(self.station.shutdown_confirmed)
        self.assertEqual(self.station.latest_power_supply_output_current_a, 0.25)

    def test_telemetry_never_waits_for_busy_supply_io_or_reads_ch341(self) -> None:
        with patch.object(self.controller, "read_output_current") as read:
            with ThreadPoolExecutor(max_workers=1) as executor, self.station._supply_io_lock:
                self.assertFalse(executor.submit(self.station._refresh_live_power_supply_current).result(timeout=1))
            self.station.power_supply_kind = "ch341"
            self.assertFalse(self.station._refresh_live_power_supply_current(force=True))
            read.assert_not_called()

    def test_new_current_command_invalidates_previous_measurement(self) -> None:
        self.station.latest_power_supply_output_current_a = 5
        self.station._last_live_current_read_at = 10
        self.station.set_current({"currentA": 3})
        self.assertIsNone(self.station.latest_power_supply_output_current_a)
        self.assertIsNone(self.station._last_live_current_read_at)
        self.assertEqual(self.station.active_current_a, 3)

    def test_invalid_live_read_clears_the_display_value(self) -> None:
        for value in (float("nan"), float("inf"), RuntimeError("timeout")):
            with self.subTest(value=value):
                self.station.latest_power_supply_output_current_a = 5
                with patch.object(self.controller, "read_output_current", side_effect=[value]):
                    self.assertFalse(self.station._refresh_live_power_supply_current(force=True))
                self.assertIsNone(self.station.latest_power_supply_output_current_a)

    def test_second_sample_updates_live_current_even_if_voltage_read_fails(self) -> None:
        self.station.automatic_sample_recorder = MagicMock()
        with patch.object(self.controller, "read_output_current", return_value=4.93), \
             patch.object(self.controller, "read_output_voltage", side_effect=RuntimeError("voltage timeout")):
            self.station._write_automatic_second_sample(point_index=0, elapsed_s=1, target_current_a=5)
        self.assertEqual(self.station.latest_power_supply_output_current_a, 4.93)
