from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from combined_test.automation import AutomaticTestState
from combined_test.excel_export import ExcelTestRecord
from combined_test.ch341_ports import Ch341DeviceDescriptor
from combined_test.models import PowerMeterReading, SpectrometerReading
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

    def test_spectrum_x_axis_locks_to_stable_peak_like_main(self) -> None:
        station = self.backend.stations["1"]
        for peak_nm in (976.0, 976.1, 976.2, 976.3):
            station._update_spectrum_center_lock(
                SpectrometerReading(peak_nm, peak_nm + 0.05, 0.4)
            )
            self.assertIsNone(station.spectrum_center_locked_nm)

        station._update_spectrum_center_lock(
            SpectrometerReading(976.4, 976.45, 0.4)
        )

        snapshot = self.backend.snapshot({"view": "automatic_1"})
        spectrum_window = snapshot["devices"]["spectrometer"]["spectrumWindow"]
        self.assertAlmostEqual(spectrum_window["centerNm"], 976.2)
        self.assertEqual(spectrum_window["halfRangeNm"], 20.0)

        station.reset_charts()
        self.assertIsNone(station.spectrum_center_locked_nm)

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

    def test_power_supply_read_returns_snapshot_and_only_reads_requested_value(self) -> None:
        station = self.backend.stations["1"]
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
        station = self.backend.stations["1"]
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

        generation, spectrum_revision = station._begin_automatic_measurement_window()

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
        measurement = station._fresh_automatic_measurement(
            stability_generation=generation,
            spectrum_revision=spectrum_revision,
            use_spectrometer=True,
        )

        self.assertIsNotNone(measurement)
        reading, frame = measurement
        self.assertEqual(reading.power_w, 0.2)
        self.assertIs(frame, station.latest_spectrum)

    def test_ch341_reads_use_the_same_four_operations_as_main(self) -> None:
        station = self.backend.stations["1"]

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

    def test_device_conflict_check_prevents_binding_same_hardware(self) -> None:
        ctrl1 = _FakeController()
        self.backend.stations["1"].power_supply_controller = ctrl1
        self.backend.stations["1"].configuration["powerSupplyKind"] = "ch341"
        self.backend.stations["1"].configuration["ch341SelectedLocationPath"] = "PCIROOT(0)#USB(1)"

        with self.assertRaisesRegex(RuntimeError, "正在被工位 1 使用"):
            self.backend.check_device_conflicts("2", "ch341", "PCIROOT(0)#USB(1)")

    def test_spectrometer_rebinding_updates_and_persists_station_resource(self) -> None:
        station = self.backend.stations["1"]
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
            [{"label": "Ocean Insight | 设备 ID 7", "deviceId": 7}],
        )
        persisted = self.backend.settings.get("runtime/stations")
        self.assertEqual(persisted["1"]["spectrometerDeviceId"], 7)
        self.assertIn("原设备 ID 5 已失效", self.backend.notices[-1]["message"])

    def test_ch341_connection_opens_selected_index_at_20_khz(self) -> None:
        location = "PCIROOT(0)#USB(2)"
        station = self.backend.stations["1"]
        station.configuration.update(
            {
                "powerSupplyKind": "ch341",
                "ch341SelectedLocationPath": location,
                "ch341PortBindings": [{"locationPath": location, "name": "2号工位"}],
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
            "tauri_bridge.headless_backend.create_ch341_controller",
            return_value=controller,
        ) as create_controller:
            with patch(
                "tauri_bridge.headless_backend.get_location_path_from_interface_path",
                return_value=location,
            ):
                station.connect_power_supply()

        create_controller.assert_called_once_with(device_index=1)
        controller.set_i2c_speed.assert_called_once_with(0)
        controller.connect_device.assert_called_once_with(1)
        self.assertIs(station.power_supply_controller, controller)
        self.assertEqual(station.connected_ch341_port["name"], "2号工位")

    def test_ch341_failed_open_is_not_reported_as_connected(self) -> None:
        location = "PCIROOT(0)#USB(1)"
        station = self.backend.stations["1"]
        station.configuration["ch341SelectedLocationPath"] = location
        station.power_supply_kind = "ch341"
        self.backend.ch341_discovery_state = "ready"
        self.backend.ch341_descriptors = [
            Ch341DeviceDescriptor(0, "iface-0", location)
        ]
        controller = MagicMock()
        controller.connect_device.return_value = (False, "无法打开设备")

        with patch(
            "tauri_bridge.headless_backend.create_ch341_controller",
            return_value=controller,
        ):
            with self.assertRaisesRegex(RuntimeError, "无法打开设备"):
                station.connect_power_supply()

        self.assertIsNone(station.power_supply_controller)
        controller.get_device_interface_path.assert_not_called()

    def test_ch341_post_open_port_mismatch_disconnects_device(self) -> None:
        location = "PCIROOT(0)#USB(1)"
        station = self.backend.stations["1"]
        station.configuration["ch341SelectedLocationPath"] = location
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
            "tauri_bridge.headless_backend.create_ch341_controller",
            return_value=controller,
        ):
            with patch(
                "tauri_bridge.headless_backend.get_location_path_from_interface_path",
                return_value="PCIROOT(0)#USB(2)",
            ):
                with self.assertRaisesRegex(RuntimeError, "端口复核不一致"):
                    station.connect_power_supply()

        controller.disconnect_device.assert_called_once_with()
        self.assertIsNone(station.power_supply_controller)

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
