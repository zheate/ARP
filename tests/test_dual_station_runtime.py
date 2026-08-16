from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from combined_test.automation import AutomaticTestState
from combined_test.excel_export import ExcelTestRecord
from tauri_bridge.headless_backend import ExportQueueWorker, ExportTask, HeadlessBackend
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


class DualStationRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        settings_path = Path(self.temp_dir.name) / "settings.json"
        self.backend = HeadlessBackend(JsonSettings(settings_path))

    def tearDown(self) -> None:
        self.backend.export_worker.stop()
        self.temp_dir.cleanup()

    def test_dual_stations_have_independent_configurations_and_runtimes(self) -> None:
        self.backend.dispatch("app.configure", {"stationId": "1", "sn": "SN-001", "targetCurrentA": 10.0})
        self.backend.dispatch("app.configure", {"stationId": "2", "sn": "SN-002", "targetCurrentA": 15.0})

        st1 = self.backend.stations["1"]
        st2 = self.backend.stations["2"]

        self.assertEqual(st1.configuration["sn"], "SN-001")
        self.assertEqual(st2.configuration["sn"], "SN-002")
        self.assertEqual(st1.configuration["targetCurrentA"], 10.0)
        self.assertEqual(st2.configuration["targetCurrentA"], 15.0)

    def test_secondary_station_does_not_claim_default_hardware_resources(self) -> None:
        station_one = self.backend.stations["1"].configuration
        station_two = self.backend.stations["2"].configuration

        self.assertEqual(station_one["powerMeterResource"], "ASRL3::INSTR")
        self.assertEqual(station_two["ch341SelectedLocationPath"], "")
        self.assertEqual(station_two["ch341PortBindings"], [])
        self.assertEqual(station_two["tdkResource"], "")
        self.assertEqual(station_two["powerMeterResource"], "")
        self.assertEqual(station_two["spectrometerResource"], "")
        self.assertIsNone(station_two["spectrometerDeviceId"])

    def test_duplicate_persisted_hardware_resource_is_released_from_secondary_station(self) -> None:
        settings_path = Path(self.temp_dir.name) / "duplicate-settings.json"
        settings = JsonSettings(settings_path)
        settings.set(
            "runtime/stations",
            {
                "1": {"powerMeterResource": "ASRL3::INSTR"},
                "2": {"powerMeterResource": "ASRL3::INSTR"},
            },
        )
        backend = HeadlessBackend(settings)
        try:
            self.assertEqual(backend.stations["1"].configuration["powerMeterResource"], "ASRL3::INSTR")
            self.assertEqual(backend.stations["2"].configuration["powerMeterResource"], "")
        finally:
            backend.export_worker.stop()

    def test_hardware_commands_target_specific_station(self) -> None:
        ctrl1 = _FakeController()
        ctrl2 = _FakeController()
        self.backend.stations["1"].power_supply_controller = ctrl1
        self.backend.stations["2"].power_supply_controller = ctrl2

        self.backend.dispatch("powerSupply.setCurrent", {"stationId": "1", "currentA": 3.0})
        self.backend.dispatch("powerSupply.setCurrent", {"stationId": "2", "currentA": 6.0})

        self.assertEqual(ctrl1.current_a, 3.0)
        self.assertEqual(ctrl2.current_a, 6.0)
        self.assertEqual(self.backend.stations["1"].active_current_a, 3.0)
        self.assertEqual(self.backend.stations["2"].active_current_a, 6.0)

    def test_device_conflict_check_prevents_binding_same_hardware(self) -> None:
        ctrl1 = _FakeController()
        self.backend.stations["1"].power_supply_controller = ctrl1
        self.backend.stations["1"].configuration["powerSupplyKind"] = "ch341"
        self.backend.stations["1"].configuration["ch341SelectedLocationPath"] = "PCIROOT(0)#USB(1)"

        with self.assertRaisesRegex(RuntimeError, "正在被工位 1 使用"):
            self.backend.check_device_conflicts("2", "ch341", "PCIROOT(0)#USB(1)")

    def test_global_stop_all_stops_both_stations(self) -> None:
        ctrl1 = _FakeController()
        ctrl2 = _FakeController()
        ctrl1.current_a = 5.0
        ctrl2.current_a = 8.0
        self.backend.stations["1"].power_supply_controller = ctrl1
        self.backend.stations["1"].active_current_a = 5.0
        self.backend.stations["2"].power_supply_controller = ctrl2
        self.backend.stations["2"].active_current_a = 8.0

        self.backend.dispatch("app.stopAll", {})

        self.assertEqual(self.backend.stations["1"].active_current_a, 0.0)
        self.assertEqual(self.backend.stations["2"].active_current_a, 0.0)
        self.assertEqual(ctrl1.current_a, 0.0)
        self.assertEqual(ctrl2.current_a, 0.0)

    def test_snapshots_distinguish_active_station_and_summaries(self) -> None:
        self.backend.stations["1"].latest_spectrum = SimpleNamespace(
            wavelength=[975.0, 976.0],
            intensity=[100.0, 500.0],
            saturation=None,
            stats=None,
            smsr=None,
            peak_annotations=(),
        )
        self.backend.stations["2"].latest_spectrum = SimpleNamespace(
            wavelength=[976.0, 977.0],
            intensity=[200.0, 800.0],
            saturation=None,
            stats=None,
            smsr=None,
            peak_annotations=(),
        )

        snap1 = self.backend.snapshot({"view": "automatic_1"})
        self.assertEqual(snap1["activeStation"], "1")
        self.assertEqual(snap1["measurements"]["spectrum"]["wavelengthNm"], [975.0, 976.0])
        self.assertIn("1", snap1["stations"])
        self.assertIn("2", snap1["stations"])

        snap2 = self.backend.snapshot({"view": "automatic_2"})
        self.assertEqual(snap2["activeStation"], "2")
        self.assertEqual(snap2["measurements"]["spectrum"]["wavelengthNm"], [976.0, 977.0])

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

        task1 = ExportTask("1", path1, records, lambda p, e: results.append(str(p)))
        task2 = ExportTask("2", path2, records, lambda p, e: results.append(str(p)))

        self.assertTrue(worker.submit(task1))
        self.assertTrue(worker.submit(task2))

        deadline = time.time() + 5.0
        while len(results) < 2 and time.time() < deadline:
            time.sleep(0.05)

        worker.stop()
        self.assertEqual(len(results), 2)
        self.assertTrue(path1.exists())
        self.assertTrue(path2.exists())


if __name__ == "__main__":
    unittest.main()
