from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from combined_test.concurrency import Signal, WorkerThread
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings


class _FakeController:
    def __init__(self) -> None:
        self.is_connected = True
        self.output_enabled = False
        self.current_a = 0.0
        self.voltage_v = 12.0
        self.disconnected = False

    def set_output_current(self, value: float) -> None:
        self.current_a = float(value)

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
        self.disconnected = True
        return True


class _Worker(WorkerThread):
    value = Signal(int)

    def run(self) -> None:
        self.value.emit(7)


class HeadlessBackendTests(unittest.TestCase):
    def backend(self, directory: str) -> HeadlessBackend:
        return HeadlessBackend(JsonSettings(Path(directory) / "settings.json"))

    def test_standard_thread_signals_are_isolated_and_finished(self) -> None:
        first = _Worker()
        second = _Worker()
        values: list[int] = []
        finished: list[str] = []
        first.value.connect(values.append)
        first.finished.connect(lambda: finished.append("first"))
        second.finished.connect(lambda: finished.append("second"))
        first.start()
        second.start()
        self.assertTrue(first.wait(1_000))
        self.assertTrue(second.wait(1_000))
        self.assertEqual(values, [7])
        self.assertCountEqual(finished, ["first", "second"])

    def test_manual_power_commands_and_shutdown_keep_the_safety_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = self.backend(directory)
            controller = _FakeController()
            backend.power_supply_controller = controller
            backend.power_supply_kind = "tdk"
            backend.configuration["powerSupplyKind"] = "tdk"

            backend.dispatch("powerSupply.setOutput", {"enabled": True})
            backend.dispatch("powerSupply.setCurrent", {"currentA": 4.5})
            self.assertEqual(controller.current_a, 4.5)
            result = backend.dispatch("app.shutdown", {})
            self.assertTrue(result["shutdownConfirmed"])
            self.assertTrue(result["exportsDrained"])
            self.assertEqual(controller.current_a, 0.0)
            self.assertFalse(controller.output_enabled)

    def test_snapshot_keeps_the_existing_frontend_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshot = self.backend(directory).snapshot({"view": "manual"})
        self.assertEqual(snapshot["backend"]["mode"], "active")
        self.assertTrue(snapshot["safety"]["hardwareAccess"])
        self.assertIn("powerSupply", snapshot["devices"])
        self.assertIn("automaticTest", snapshot)
        self.assertIn("configuration", snapshot)
        self.assertNotIn("points", snapshot["pd"])
        aging_script = next(item for item in snapshot["testScripts"] if item["id"] == "aging")
        self.assertEqual(aging_script["powerSupplyKinds"], ["ch341"])

    def test_aging_script_is_fixed_to_ch341_two_amp_ramp_and_two_hour_hold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = self.backend(directory)
            station = backend.stations["1"]
            station.configure(
                {
                    "powerSupplyKind": "ch341",
                    "selectedScriptId": "aging",
                    "initialCurrentA": 8,
                    "targetCurrentA": 10,
                    "currentStepA": 1,
                    "currentPlanMode": "range",
                    "customCurrentPoints": [],
                }
            )

            self.assertEqual(station.configuration["initialCurrentA"], 2.0)
            self.assertEqual(station.configuration["targetCurrentA"], 20.0)
            self.assertEqual(station.configuration["currentStepA"], 2.0)
            self.assertEqual(
                station.configuration["customCurrentPoints"],
                [{"currentA": 20.0, "dwellS": 7_200.0}],
            )

            settings = station._automatic_settings()
            station.automatic.start(
                settings,
                power_meter_ready=False,
                spectrum_meter_ready=False,
            )
            waited: list[float] = []

            def wait_duration(duration_s: float, **_kwargs: object) -> bool:
                waited.append(duration_s)
                return True

            with (
                patch.object(station, "_wait_for_aging_power_meter") as wait_power_meter,
                patch.object(station, "_set_automatic_current") as set_current,
                patch.object(
                    station,
                    "_wait_automatic_duration",
                    side_effect=wait_duration,
                ) as wait_duration_mock,
                patch.object(
                    station,
                    "_wait_for_automatic_stability",
                    side_effect=AssertionError("AU 老化测试不应判稳"),
                ) as wait_stable,
            ):
                station._run_aging_cycle()

            wait_power_meter.assert_called_once_with()
            self.assertEqual(
                [call.args[0] for call in set_current.call_args_list],
                [2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0],
            )
            self.assertEqual(waited, [1.0] * 9 + [7_200.0])
            self.assertEqual(wait_duration_mock.call_args_list[-1].kwargs["interval_s"], 300.0)
            self.assertEqual(
                wait_duration_mock.call_args_list[-1].kwargs["on_interval"],
                station._record_aging_power_sample,
            )
            wait_stable.assert_not_called()

    def test_aging_power_sample_records_elapsed_minutes_and_fresh_power(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = self.backend(directory)
            station = backend.stations["1"]
            station.power_meter_reader = SimpleNamespace(is_ready=True)
            station.latest_power = SimpleNamespace(power_w=1.234, stable=True)
            station.latest_power_received_monotonic_s = time.monotonic()

            station._record_aging_power_sample(300.0)
            station.latest_power = SimpleNamespace(power_w=1.198, stable=True)
            station.latest_power_received_monotonic_s = time.monotonic()
            station._record_aging_power_sample(600.0)

            self.assertEqual(
                station.aging_power_points,
                [
                    {"elapsedMinutes": 5.0, "powerW": 1.234},
                    {"elapsedMinutes": 10.0, "powerW": 1.198},
                ],
            )
            self.assertEqual(station.series_revisions["aging"], 2)
            snapshot = backend.snapshot({"view": "automatic"})
            self.assertEqual(
                snapshot["measurements"]["agingPower"],
                station.aging_power_points,
            )

    def test_automatic_duration_runs_interval_callback_on_exact_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            station = self.backend(directory).stations["1"]
            clock = [0.0]
            recorded: list[float] = []

            def advance_clock(timeout_s: float) -> bool:
                clock[0] += timeout_s
                return False

            with (
                patch.object(station, "_await_automatic_resume", return_value=0.0),
                patch.object(station._automatic_stop, "wait", side_effect=advance_clock),
                patch("tauri_bridge.station_runtime.time.monotonic", side_effect=lambda: clock[0]),
            ):
                completed = station._wait_automatic_duration(
                    15.0,
                    interval_s=5.0,
                    on_interval=recorded.append,
                )

            self.assertTrue(completed)
            self.assertEqual(recorded, [5.0, 10.0, 15.0])

    def test_tdk_rejects_aging_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            station = self.backend(directory).stations["1"]

            with self.assertRaisesRegex(ValueError, "仅支持 CH341"):
                station.configure(
                    {
                        "powerSupplyKind": "tdk",
                        "selectedScriptId": "aging",
                    }
                )

            self.assertEqual(station.power_supply_kind, "ch341")
            self.assertEqual(station.configuration["selectedScriptId"], "standard")

    def test_aging_start_starts_power_meter_but_not_spectrometer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            station = self.backend(directory).stations["1"]
            station.power_supply_controller = _FakeController()

            with (
                patch("tauri_bridge.station_automatic.threading.Thread") as thread_class,
                patch.object(station, "start_power_meter") as start_power_meter,
                patch.object(station, "start_spectrometer") as start_spectrometer,
            ):
                station.start_automatic(
                    {
                        "powerSupplyKind": "ch341",
                        "selectedScriptId": "aging",
                        "sn": "AGING-001",
                        "powerMeterResource": "ASRL3::INSTR",
                    }
                )

            start_power_meter.assert_called_once_with()
            start_spectrometer.assert_not_called()
            thread_class.return_value.start.assert_called_once_with()

    def test_manual_power_meter_start_clears_power_efficiency_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = self.backend(directory)
            station = backend.stations["1"]
            station.configuration["powerMeterResource"] = "ASRL3::INSTR"
            station.stable_power_points = {2.0: 0.204, 6.0: 0.612}
            station.efficiency_points = {2.0: 4.2, 6.0: 10.2}
            station.series_revisions["stable"] = 7

            with patch("tauri_bridge.station_runtime.PowerMeterReaderThread"):
                station.start_power_meter()

            self.assertEqual(station.stable_power_points, {})
            self.assertEqual(station.efficiency_points, {})
            self.assertEqual(station.series_revisions["stable"], 8)

    def test_runtime_modules_do_not_attempt_to_import_pyside(self) -> None:
        script = r'''
import importlib.abc
import json
import sys

class BlockPySide(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("PySide6"):
            raise AssertionError(f"unexpected GUI toolkit import: {fullname}")
        return None

sys.meta_path.insert(0, BlockPySide())
import combined_test.shipping_report
import tools.pd_daq_mvp
import tools.power_meter_mvp
import tools.spectrometer_mvp
from combined_test.concurrency import WorkerThread
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings
assert issubclass(tools.power_meter_mvp.PowerReaderThread, WorkerThread)
assert issubclass(tools.spectrometer_mvp.SpectrumReaderThread, WorkerThread)
backend = HeadlessBackend(JsonSettings())
print(json.dumps(backend.ping()))
'''
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.json"
            with patch.dict("os.environ", {"ARP_SETTINGS_PATH": str(settings_path)}):
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,
                    text=True,
                    check=True,
                )
        self.assertEqual(json.loads(result.stdout), {"status": "ok", "mode": "active"})


if __name__ == "__main__":
    unittest.main()
