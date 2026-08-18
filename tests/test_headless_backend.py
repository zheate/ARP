from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
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

    def test_manual_power_meter_start_clears_power_efficiency_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = self.backend(directory)
            station = backend.stations["1"]
            station.configuration["powerMeterResource"] = "ASRL3::INSTR"
            station.stable_power_points = {2.0: 0.204, 6.0: 0.612}
            station.efficiency_points = {2.0: 4.2, 6.0: 10.2}
            station.series_revisions["stable"] = 7

            with patch("tauri_bridge.headless_backend.PowerMeterReaderThread"):
                station.start_power_meter()

            self.assertEqual(station.stable_power_points, {})
            self.assertEqual(station.efficiency_points, {})
            self.assertEqual(station.series_revisions["stable"], 8)

    def test_sidecar_starts_when_every_pyside_import_is_blocked(self) -> None:
        script = r'''
import importlib.abc
import json
import sys

class BlockPySide(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("PySide6"):
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, BlockPySide())
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings
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
