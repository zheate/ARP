"""Event-driven TDK voltage feedback, using simulated controllers only."""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from combined_test.automation import AutomaticTestState
from combined_test.models import PowerMeterReading
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings
from tests.test_station_runtime import _FakeController


class TdkVoltageBurstTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = HeadlessBackend(JsonSettings(Path(self.temp.name) / "settings.json"))
        self.addCleanup(self.backend.export_worker.graceful_stop, 2)
        self.station = self.backend.runtime
        self.controller = _FakeController()
        self.station.power_supply_controller = self.controller
        self.station.power_supply_kind = "tdk"
        self.station.configuration.update({"powerSupplyKind": "tdk", "sn": "BURST", "outputDir": self.temp.name})
        self.workers = []
        self.addCleanup(self.stop_workers)

    def stop_workers(self):
        self.station._tdk_voltage_burst_stop.set()
        for worker in self.workers:
            worker.join(2)
            self.assertFalse(worker.is_alive())

    def apply(self, current, automatic=False):
        if automatic:
            self.station.automatic.state = AutomaticTestState.SETTING_CURRENT
            self.station._set_automatic_current(current)
        else:
            self.backend.dispatch("powerSupply.setCurrent", {"currentA": current})
        worker = self.station._tdk_voltage_burst_thread
        self.workers.append(worker)
        return worker

    def test_five_spaced_reads_update_manual_and_automatic_snapshots_without_recording(self):
        for automatic in (False, True):
            with self.subTest(automatic=automatic):
                self.station.automatic.state = AutomaticTestState.IDLE
                samples = []

                def voltage():
                    samples.append((time.monotonic(), self.controller.current_a))
                    return 10.0 + len(samples)

                with patch.object(self.controller, "read_output_voltage", side_effect=voltage):
                    worker = self.apply(2.0, automatic=automatic)
                    worker.join(2)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(len(samples), 5)
                    self.assertEqual([current for _, current in samples], [2.0] * 5)
                    for before, after in zip(samples, samples[1:]):
                        self.assertGreaterEqual(after[0] - before[0], 0.18)
                    for view in ("manual", "automatic"):
                        # Isolate burst feedback from the independent periodic display refresh.
                        with patch.object(self.station, "refresh_manual_power_supply_in_background"):
                            supply = self.backend.snapshot({"view": view})["devices"]["powerSupply"]
                        self.assertEqual(supply["outputVoltageV"], 15.0)
                    self.assertEqual(self.station.record_store.snapshot(), ())

    def test_serial_read_and_wait_do_not_block_command_response_or_snapshot(self):
        entered, release = threading.Event(), threading.Event()

        def slow_voltage():
            entered.set()
            release.wait(2)
            return 13.75

        with patch.object(self.controller, "read_output_voltage", side_effect=slow_voltage):
            try:
                worker = self.apply(2.0)
                self.assertTrue(entered.wait(1))
                done = threading.Event()
                snapshot_worker = threading.Thread(target=lambda: (self.backend.snapshot({"view": "manual"}), done.set()))
                snapshot_worker.start()
                self.assertTrue(done.wait(1))
                snapshot_worker.join(1)
                self.station._tdk_voltage_burst_stop.set()
            finally:
                release.set()
                worker.join(2)

    def test_new_current_cancels_previous_burst(self):
        entered = threading.Event()
        samples = []

        def voltage():
            samples.append(self.controller.current_a)
            entered.set()
            return 10.0 + self.controller.current_a

        with patch.object(self.controller, "read_output_voltage", side_effect=voltage):
            first = self.apply(2.0)
            self.assertTrue(entered.wait(1))
            second = self.apply(3.0)
            first.join(2)
            second.join(2)
        self.assertEqual(samples, [2.0] + [3.0] * 5)
        self.assertEqual(self.station.latest_power_supply_output_voltage_v, 13.0)

    def test_bad_reads_clear_old_value_and_later_reads_recover(self):
        self.station.latest_power_supply_output_voltage_v = 99.0
        with patch.object(self.controller, "read_output_voltage", side_effect=[RuntimeError("timeout"), float("nan"), float("inf"), 12.0, 13.0]) as read:
            worker = self.apply(2.0)
            worker.join(2)
            self.assertEqual(read.call_count, 5)
        self.assertEqual(self.station.active_current_a, 2.0)
        self.assertEqual(self.station.latest_power_supply_output_voltage_v, 13.0)

    def test_stable_read_is_final_and_records_its_own_value(self):
        entered = threading.Event()
        self.station.power_meter_reader = SimpleNamespace(is_ready=True, reset_stability_window=Mock(return_value=2))
        with patch.object(self.controller, "read_output_voltage", side_effect=lambda: entered.set() or 12.0):
            worker = self.apply(2.0)
            self.assertTrue(entered.wait(1))
            reading = PowerMeterReading(elapsed_s=5.0, power_w=4.0, stable=True, stable_span_w=0.02,
                                        stable_window_s=3.0, stable_median_w=4.0, stability_generation=2)
            with patch("tauri_bridge.station_runtime.threading.Thread") as timer:
                self.station._on_power_reading(reading)
            callback = timer.call_args.kwargs["target"]
            with patch("tauri_bridge.station_runtime.time.sleep") as delay, \
                 patch.object(self.controller, "read_output_voltage", return_value=14.0) as final:
                callback()
                final.assert_called_once_with()
                delay.assert_not_called()
            worker.join(2)
        self.assertEqual(self.station.latest_power_supply_output_voltage_v, 14.0)
        self.assertEqual(self.station.manual_capture["state"], "recorded")
        self.assertEqual(len(self.station.record_store.snapshot()), 1)

    def test_safe_down_cancels_pending_reads(self):
        entered = threading.Event()
        with patch.object(self.controller, "read_output_voltage", side_effect=lambda: entered.set() or 12.0) as read:
            worker = self.apply(2.0)
            self.assertTrue(entered.wait(1))
            with patch("tauri_bridge.station_automatic.time.sleep"):
                self.station.safe_ramp_down()
            worker.join(2)
            self.assertEqual(read.call_count, 1)
        self.assertTrue(self.station.shutdown_confirmed)

    def test_disconnect_cancels_pending_reads_and_clears_feedback(self):
        entered = threading.Event()
        with patch.object(self.controller, "read_output_voltage", side_effect=lambda: entered.set() or 12.0) as read:
            worker = self.apply(2.0)
            self.assertTrue(entered.wait(1))
            self.station.disconnect_power_supply()
            worker.join(2)
            self.assertEqual(read.call_count, 1)
        self.assertIsNone(self.station.power_supply_controller)
        self.assertIsNone(self.station.latest_power_supply_output_voltage_v)


if __name__ == "__main__":
    unittest.main()
