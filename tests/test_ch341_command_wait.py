"""Hardware-free regressions for CH341 timing without blocking live snapshots."""

import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from combined_test.automation import AutomaticTestSettings, AutomaticTestState, MIN_POWER_SUPPLY_COMMAND_INTERVAL_S
from combined_test.models import PowerMeterReading
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings
from tests.test_station_runtime import _FakeController


class Ch341CommandWaitTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.backend = HeadlessBackend(JsonSettings(Path(directory.name) / "settings.json"))
        self.addCleanup(self.backend.export_worker.graceful_stop, 2)
        self.station = self.backend.runtime
        self.controller = _FakeController()
        self.station.power_supply_controller = self.controller
        self.station.power_supply_kind = "ch341"
        self.station.configuration["powerSupplyKind"] = "ch341"
        self.station.automatic.set_state(AutomaticTestState.SETTING_CURRENT)

    def test_live_reading_and_snapshot_continue_during_automatic_current_wait(self):
        waiting = threading.Event()
        original_wait = self.station._automatic_stop.wait

        def observe_wait(seconds):
            waiting.set()
            return original_wait(seconds)

        # The previous point's voltage read just consumed a command slot.
        previous = time.monotonic()
        self.station._last_power_supply_command_at = previous
        write_times = []
        write = self.controller.set_output_current

        def timed_write(value):
            write_times.append(time.monotonic())
            write(value)

        def publish_and_snapshot():
            self.station._on_power_reading(PowerMeterReading(1.0, 3.5, False, 0.1, 0.2, 3.5))
            return self.backend.snapshot({"view": "automatic"})

        with patch.object(self.station._automatic_stop, "wait", observe_wait), \
             patch.object(self.controller, "set_output_current", timed_write), \
             ThreadPoolExecutor(max_workers=2) as pool:
            current = pool.submit(self.station._set_automatic_current, 4.0)
            self.assertTrue(waiting.wait(1))
            snapshot = pool.submit(publish_and_snapshot).result(timeout=0.3)
            self.assertEqual(snapshot["devices"]["powerMeter"]["powerW"], 3.5)
            self.assertEqual(snapshot["measurements"]["power"][-1]["powerW"], 3.5)
            self.assertEqual(self.controller.writes, [], "live updates must finish before current is written")
            current.result(timeout=2)
        self.assertGreaterEqual(write_times[0] - previous, MIN_POWER_SUPPLY_COMMAND_INTERVAL_S)
        self.assertEqual(self.controller.writes, [4.0])

    def test_concurrent_waiters_recheck_the_interval_before_each_write(self):
        previous = time.monotonic()
        self.station._last_power_supply_command_at = previous
        write_times = []
        write = self.controller.set_output_current

        def timed_write(value):
            write_times.append(time.monotonic())
            write(value)

        with patch.object(self.controller, "set_output_current", timed_write), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.station.set_current, {"currentA": 2.0}, wait_for_slot=True)
            second = pool.submit(self.station.set_current, {"currentA": 4.0}, wait_for_slot=True)
            first.result(timeout=4)
            second.result(timeout=4)
        self.assertCountEqual(self.controller.writes, [2.0, 4.0])
        for earlier, later in zip([previous, *write_times], write_times):
            # Command slots are timestamped immediately before driver dispatch.
            self.assertGreaterEqual(later - earlier, MIN_POWER_SUPPLY_COMMAND_INTERVAL_S - 0.005)

    def test_emergency_stop_cancels_the_waiting_current(self):
        waiting = threading.Event()
        original_wait = self.station._automatic_stop.wait

        def observe_wait(seconds):
            waiting.set()
            return original_wait(seconds)

        self.station._last_power_supply_command_at = time.monotonic()
        with patch.object(self.station._automatic_stop, "wait", observe_wait), ThreadPoolExecutor(max_workers=1) as pool:
            current = pool.submit(self.station._set_automatic_current, 4.0)
            self.assertTrue(waiting.wait(1))
            self.station.request_emergency_stop()
            try:
                with self.assertRaisesRegex(RuntimeError, "停止|失效"):
                    current.result(timeout=0.3)
            finally:
                self.station._emergency_ramp_thread.join(3)
        self.assertFalse(self.station._emergency_ramp_thread.is_alive())
        self.assertNotIn(4.0, self.controller.writes)
        self.assertEqual(self.controller.current_a, 0.0)

    def test_manual_command_still_rejects_an_occupied_slot(self):
        self.station.automatic.set_state(AutomaticTestState.IDLE)
        self.station._last_power_supply_command_at = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "电源命令过快"):
            self.station.set_current({"currentA": 4.0})
        self.assertEqual(self.controller.writes, [])

    def test_disconnect_during_wait_does_not_send_to_old_or_replacement_controller(self):
        waiting = threading.Event()
        original_wait = self.station._automatic_stop.wait

        def observe_wait(seconds):
            waiting.set()
            return original_wait(seconds)

        self.station._last_power_supply_command_at = time.monotonic()
        replacement = _FakeController()
        with patch.object(self.station._automatic_stop, "wait", observe_wait), ThreadPoolExecutor(max_workers=2) as pool:
            current = pool.submit(self.station.set_current, {"currentA": 4.0}, wait_for_slot=True)
            self.assertTrue(waiting.wait(1))
            pool.submit(self.station.disconnect_power_supply).result(timeout=0.3)
            with self.station._lock:
                self.station.power_supply_controller = replacement
            with self.assertRaisesRegex(RuntimeError, "失效"):
                current.result(timeout=2)
        self.assertFalse(self.controller.is_connected)
        self.assertEqual(self.controller.writes, [])
        self.assertEqual(replacement.writes, [])

    def test_next_point_waits_for_actual_journal_fsync(self):
        station = self.station
        settings = AutomaticTestSettings(use_spectrometer=False)
        station.automatic.settings = settings
        station.automatic.currents = (2.0, 4.0)
        station.automatic.dwell_times_s = (0.0, 0.0)
        station.record_store.begin_session(self.backend.settings.path.parent / "excel", "WAIT-FSYNC", datetime.now())
        station.record_store.configure_sequence(station.automatic.currents)
        reading = PowerMeterReading(1.0, 3.5, True, 0.1, 5.0, 3.5)
        entered, release, first_saved = threading.Event(), threading.Event(), threading.Event()
        original_fsync = os.fsync

        def delayed_fsync(fd):
            if not first_saved.is_set():
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("test did not release fsync")
            original_fsync(fd)
            first_saved.set()

        def set_current(current):
            if current == 4.0:
                self.assertTrue(first_saved.is_set(), "next point preceded durable save")
            self.controller.set_output_current(current)
            station.active_current_a = current

        with patch.object(station, "_wait_for_automatic_acquisition"), \
             patch.object(station, "_wait_for_automatic_stability", return_value=(reading, None, 0, 0)), \
             patch.object(station, "_wait_for_automatic_stable_hold", return_value=(reading, None)), \
             patch.object(station, "_set_automatic_current", side_effect=set_current), \
             patch.object(station, "_reserve_power_supply_command"), \
             patch.object(station, "safe_ramp_down"), \
             patch.object(station, "_queue_automatic_export"), \
             patch("combined_test.session_journal.os.fsync", side_effect=delayed_fsync), \
             ThreadPoolExecutor(max_workers=1) as pool:
            run = pool.submit(station._run_automatic)
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(self.controller.writes, [2.0])
                self.assertEqual(station.record_store.recorded_currents, set())
            finally:
                release.set()
            run.result(timeout=3)
        self.assertEqual(self.controller.writes, [2.0, 4.0])
        self.assertEqual(station.automatic_terminal_outcome, "succeeded")
        events = [json.loads(line) for line in station.record_store.journal_path.read_text().splitlines()]
        self.assertEqual([event["record"]["current_a"] for event in events if event["type"] == "point_committed"], [2.0, 4.0])
