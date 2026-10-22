"""Manual supply telemetry through the real snapshot path, with fake hardware."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from combined_test.automation import AutomaticTestState
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings
from tests.test_station_runtime import _FakeController


class ManualController(_FakeController):
    def validate_current(self, current):
        pass


class ManualLiveCurrentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = HeadlessBackend(JsonSettings(Path(self.temp.name) / "settings.json"))
        self.addCleanup(self.backend.export_worker.graceful_stop, 2)
        self.station = self.backend.runtime
        self.controller = ManualController()
        self.station.power_supply_controller = self.controller
        self.station.power_supply_kind = "ru"
        self.station.configuration["powerSupplyKind"] = "ru"
        self.station.workflow_access.owner = "manual"
        self.addCleanup(self.join_worker)

    def join_worker(self):
        worker = self.station._manual_power_supply_refresh_thread
        if worker is not None:
            worker.join(2)
            self.assertFalse(worker.is_alive())

    def refresh(self):
        self.station._last_manual_power_supply_refresh_at = None
        self.backend.snapshot({"view": "manual"})
        self.join_worker()

    def test_setting_current_then_polling_publishes_measured_value_without_read_command(self):
        for kind in ("ru", "tdk"):
            with self.subTest(kind=kind):
                self.station.power_supply_kind = kind
                self.station.configuration["powerSupplyKind"] = kind
                self.controller.voltage_v = 100.7345
                with patch.object(self.controller, "read_output_current", return_value=0.97) as read:
                    self.station.set_current({"currentA": 1})
                    self.refresh()
                    snapshot = self.backend.snapshot({"view": "manual"})
                self.assertEqual(read.call_count, 1)
                supply = snapshot["devices"]["powerSupply"]
                self.assertEqual(supply["outputCurrentA"], 0.97)
                self.assertEqual(supply["outputVoltageV"], 100.7345)
                self.assertEqual(supply["activeCurrentA"], 1)
                self.assertEqual(self.station.record_store.snapshot(), ())

    def test_polling_throttles_and_does_not_queue_duplicate_reads_or_block_snapshots(self):
        entered, release = threading.Event(), threading.Event()

        def slow_read():
            entered.set()
            self.assertTrue(release.wait(2))
            return 1.234

        with patch.object(self.controller, "read_output_current", side_effect=slow_read) as read:
            try:
                self.backend.snapshot({"view": "manual"})
                self.assertTrue(entered.wait(1))
                with ThreadPoolExecutor(max_workers=1) as executor:
                    for _ in range(3):
                        executor.submit(self.backend.snapshot, {"view": "manual"}).result(timeout=1)
                self.assertEqual(read.call_count, 1)
            finally:
                release.set()
                self.join_worker()
            self.backend.snapshot({"view": "manual"})
            self.assertEqual(read.call_count, 1)
        self.assertEqual(self.station.latest_power_supply_output_current_a, 1.234)
        self.assertEqual(self.controller.writes, [])

    def test_busy_serial_link_is_skipped_without_waiting(self):
        with patch.object(self.controller, "read_output_current") as read, \
             patch.object(self.controller, "read_output_voltage") as voltage:
            with self.station._supply_io_lock:
                self.backend.snapshot({"view": "manual"})
                self.join_worker()
            read.assert_not_called()
            voltage.assert_not_called()

    def test_later_poll_refreshes_changed_current_including_zero_without_confirming_shutdown(self):
        with patch.object(self.controller, "read_output_current", side_effect=[0.98, 0.96, 0.0]) as read:
            self.backend.snapshot({"view": "manual"})
            self.join_worker()
            for expected in (0.96, 0.0):
                self.station._last_manual_power_supply_refresh_at -= 1.01
                self.backend.snapshot({"view": "manual"})
                self.join_worker()
                snapshot = self.backend.snapshot({"view": "manual"})
                self.assertEqual(snapshot["devices"]["powerSupply"]["outputCurrentA"], expected)
            self.assertEqual(read.call_count, 3)
        self.assertFalse(self.station.shutdown_confirmed)
        self.assertEqual(self.controller.writes, [])

    def test_failed_and_invalid_reads_clear_old_value_and_next_refresh_recovers(self):
        with patch.object(self.controller, "read_output_current", side_effect=[
            RuntimeError("timeout"), float("nan"), float("inf"), 0.9987,
        ]):
            for _ in range(3):
                self.station.latest_power_supply_output_current_a = 9
                self.refresh()
                self.assertIsNone(self.station.latest_power_supply_output_current_a)
            self.refresh()
        self.assertEqual(self.station.latest_power_supply_output_current_a, 0.9987)
        self.assertEqual(self.station.latest_power_supply_output_voltage_v, 12.0)

    def test_voltage_failure_clears_old_value_without_losing_current_and_recovers(self):
        self.controller.current_a = 0.9876
        with patch.object(self.controller, "read_output_voltage", side_effect=[
            RuntimeError("timeout"), float("nan"), float("inf"), 100.7345, 0.0,
        ]):
            for _ in range(3):
                self.station.latest_power_supply_output_voltage_v = 99.0
                self.refresh()
                self.assertIsNone(self.station.latest_power_supply_output_voltage_v)
                self.assertEqual(self.station.latest_power_supply_output_current_a, 0.9876)
            for expected in (100.7345, 0.0):
                self.refresh()
                snapshot = self.backend.snapshot({"view": "manual"})
                self.assertEqual(snapshot["devices"]["powerSupply"]["outputVoltageV"], expected)
        self.assertEqual(self.station.record_store.snapshot(), ())

    def test_safety_invalidation_between_reads_skips_voltage(self):
        def current():
            self.station.command_safety.invalidate()
            return 1.0

        with patch.object(self.controller, "read_output_current", side_effect=current), \
             patch.object(self.controller, "read_output_voltage") as voltage:
            self.refresh()
            voltage.assert_not_called()
        self.assertIsNone(self.station.latest_power_supply_output_voltage_v)

    def test_slow_voltage_does_not_block_snapshots_and_cannot_publish_after_stop(self):
        entered, release = threading.Event(), threading.Event()

        def slow_voltage():
            entered.set()
            self.assertTrue(release.wait(2))
            return 100.7

        with patch.object(self.controller, "read_output_voltage", side_effect=slow_voltage) as voltage:
            try:
                self.backend.snapshot({"view": "manual"})
                self.assertTrue(entered.wait(1))
                with ThreadPoolExecutor(max_workers=1) as executor:
                    executor.submit(self.backend.snapshot, {"view": "manual"}).result(timeout=1)
                self.assertEqual(voltage.call_count, 1)
                self.station.command_safety.invalidate()
            finally:
                release.set()
                self.join_worker()
        self.assertIsNone(self.station.latest_power_supply_output_voltage_v)
        self.assertEqual(self.controller.writes, [])

    def test_automatic_ch341_disconnected_and_stopping_paths_do_not_read(self):
        cases = (
            {"power_supply_kind": "ch341"},
            {"power_supply_controller": None},
            {"manual_power_stopping": True},
            {"manual_current_request_a": 1.0},
        )
        with patch.object(self.controller, "read_output_current") as read, \
             patch.object(self.controller, "read_output_voltage") as voltage:
            for values in cases:
                with self.subTest(values=values), patch.multiple(self.station, **values):
                    self.refresh()
            with self.station.command_safety.stopping():
                self.refresh()
            with patch.object(self.station.automatic, "state", AutomaticTestState.WAITING_STABLE):
                self.refresh()
            with patch.object(self.station.workflow_access, "owner", "automatic"):
                self.refresh()
            for view in ("automatic", "pd", "data", "shipping"):
                self.backend.snapshot({"view": view})
            read.assert_not_called()
            voltage.assert_not_called()

    def test_inflight_read_cannot_publish_after_safety_invalidation_or_controller_change(self):
        entered, release = threading.Event(), threading.Event()

        def slow_read():
            entered.set()
            self.assertTrue(release.wait(2))
            return 7.0

        for change_controller in (False, True):
            with self.subTest(change_controller=change_controller):
                entered.clear()
                release.clear()
                self.station.latest_power_supply_output_current_a = None
                self.station.power_supply_controller = self.controller
                with patch.object(self.controller, "read_output_current", side_effect=slow_read):
                    try:
                        self.station._last_manual_power_supply_refresh_at = None
                        self.backend.snapshot({"view": "manual"})
                        self.assertTrue(entered.wait(1))
                        if change_controller:
                            self.station.power_supply_controller = ManualController()
                        else:
                            self.station.command_safety.invalidate()
                    finally:
                        release.set()
                        self.join_worker()
                self.assertIsNone(self.station.latest_power_supply_output_current_a)
