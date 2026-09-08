from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from combined_test.models import PowerMeterReading
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings


class ManualPowerCaptureRegressions(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.backend = HeadlessBackend(JsonSettings(Path(self.directory.name) / "settings.json"))
        self.station = self.backend.stations["1"]
        self.station.configuration.update({"sn": "MANUAL-REGRESSION", "outputDir": self.directory.name})
        self.station.power_supply_controller = SimpleNamespace(
            read_output_voltage=Mock(return_value=10.0), is_connected=True,
        )
        self.station.power_meter_reader = self._reader()
        self.station.active_current_a = 1.0
        self.station.latest_power = self._reading(1)
        self.station.latest_power_received_monotonic_s = time.monotonic()
        self.station.recorded_manual_stable_current_a = 1.0
        self.station.recorded_manual_stability_generation = 1
        self.station._record_manual_voltage_point(10.0)
        self.saved_records = self.station.record_store.snapshot()
        self.assertEqual(len(self.saved_records), 1)

    def tearDown(self) -> None:
        self.backend.export_worker.graceful_stop()
        self.directory.cleanup()

    @staticmethod
    def _reader():
        return SimpleNamespace(is_ready=True, stop=Mock(), wait=Mock(return_value=True))

    @staticmethod
    def _reading(generation):
        return PowerMeterReading(
            elapsed_s=5.0, power_w=4.0, stable=True, stable_span_w=0.02,
            stable_window_s=3.0, stable_median_w=4.0, stability_generation=generation,
        )

    def _arm_delayed_capture(self):
        station = self.station
        station.power_meter_reader = self._reader()
        station.active_current_a = 2.0
        station.pending_manual_stable_current_a = 2.0
        station.pending_manual_stability_generation = 2
        with patch("tauri_bridge.station_runtime.threading.Thread") as timer:
            station._on_power_reading(self._reading(2))
        self.assertEqual(station.recorded_manual_stable_current_a, 2.0)
        return timer.call_args.kwargs["target"]

    def test_failure_finish_and_stop_cancel_pending_timer_but_preserve_sample_and_records(self):
        station = self.station
        for stop_kind in ("failed", "finished", "stop"):
            with self.subTest(stop=stop_kind):
                callback = self._arm_delayed_capture()
                sample = station.latest_power
                sampled_at = station.latest_power_received_monotonic_s
                timer_generation = station._manual_voltage_timer_generation
                if stop_kind == "failed":
                    station._on_power_failed("Ophir read timeout")
                elif stop_kind == "finished":
                    station._on_power_finished()
                else:
                    station.stop_power_meter()
                with patch("tauri_bridge.station_runtime.time.sleep"):
                    callback()
                station._record_manual_voltage_point(10.0)

                self.assertGreater(station._manual_voltage_timer_generation, timer_generation)
                self.assertIsNone(station.pending_manual_stable_current_a)
                self.assertIsNone(station.pending_manual_stability_generation)
                self.assertIsNone(station.recorded_manual_stable_current_a)
                self.assertIsNone(station.recorded_manual_stability_generation)
                self.assertIs(station.latest_power, sample)
                self.assertEqual(station.latest_power_received_monotonic_s, sampled_at)
                self.assertEqual(station.record_store.snapshot(), self.saved_records)
                self.assertIn(1.0, station.efficiency_points)
                self.assertIn(2.0, station.stable_power_points)
                station.power_supply_controller.read_output_voltage.assert_not_called()

    def test_failure_during_voltage_read_prevents_late_record(self):
        callback = self._arm_delayed_capture()

        def fail_power_while_reading_voltage():
            self.station._on_power_failed("power stream stopped during voltage read")
            return 10.0

        self.station.power_supply_controller.read_output_voltage.side_effect = fail_power_while_reading_voltage
        with patch("tauri_bridge.station_runtime.time.sleep"):
            callback()
        self.station.power_supply_controller.read_output_voltage.assert_called_once()
        self.assertEqual(self.station.record_store.snapshot(), self.saved_records)

    def test_stale_or_stopped_reader_cannot_record_and_new_stable_capture_recovers(self):
        self._arm_delayed_capture()
        station = self.station
        station.latest_power_received_monotonic_s = time.monotonic() - 10.0
        station._record_manual_voltage_point(10.0)
        self.assertEqual(station.record_store.snapshot(), self.saved_records)
        station.latest_power_received_monotonic_s = time.monotonic()
        station.power_meter_reader.is_ready = False
        station._record_manual_voltage_point(10.0)
        self.assertEqual(station.record_store.snapshot(), self.saved_records)

        station._on_power_finished()
        callback = self._arm_delayed_capture()
        with patch("tauri_bridge.station_runtime.time.sleep"):
            callback()
        self.assertEqual(len(station.record_store.snapshot()), 2)
        self.assertAlmostEqual(station.efficiency_points[2.0], 20.0)


if __name__ == "__main__":
    unittest.main()
