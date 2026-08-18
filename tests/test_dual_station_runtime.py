from __future__ import annotations

import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from combined_test.automation import AutomaticTestSettings, AutomaticTestState
from combined_test.concurrency import Signal
from combined_test.excel_export import ExcelTestRecord
from combined_test.ch341_ports import Ch341DeviceDescriptor
from combined_test.models import PowerMeterOption, PowerMeterReading, SpectrometerReading
from combined_test.record_store import AttemptValidity
from tauri_bridge.headless_backend import (
    ExportQueueWorker,
    ExportTask,
    HeadlessBackend,
    _AutomaticPointRetry,
)
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

    def test_automatic_spectrum_follows_active_acquisition(self) -> None:
        station = self.backend.stations["1"]

        self.assertFalse(station._automatic_settings().use_spectrometer)

        station.spectrometer_reader = MagicMock(is_ready=True)
        self.assertTrue(station._automatic_settings().use_spectrometer)

    def test_tdk_discovery_publishes_progress_before_it_finishes(self) -> None:
        started = threading.Event()
        release = threading.Event()
        self.backend.dispatch("app.configure", {"stationId": "1", "powerSupplyKind": "tdk"})

        def list_resources() -> list[str]:
            started.set()
            release.wait(timeout=2)
            return ["ASRL7::INSTR"]

        with patch("tauri_bridge.headless_backend.list_tdk_serial_resources", side_effect=list_resources):
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

    def test_human_station_label_does_not_broadcast_configuration_to_both_stations(self) -> None:
        self.backend.stations["1"].configuration["sn"] = "SN-001"
        self.backend.stations["2"].configuration["sn"] = "SN-002"

        self.backend.dispatch(
            "app.configure",
            {"station": "熔接前", "sn": "SN-LEGACY"},
        )

        self.assertEqual(self.backend.stations["1"].configuration["sn"], "SN-LEGACY")
        self.assertEqual(self.backend.stations["1"].configuration["station"], "熔接前")
        self.assertEqual(self.backend.stations["2"].configuration["sn"], "SN-002")

    def test_station_notice_keeps_machine_readable_code_and_station_id(self) -> None:
        self.backend.stations["2"].notify(
            "warning",
            "光谱饱和",
            "请调整光路",
            code="spectrum_saturation",
        )

        notice = self.backend.snapshot({"view": "automatic_1"})["backend"]["notices"][-1]
        self.assertEqual(notice["stationId"], "2")
        self.assertEqual(notice["code"], "spectrum_saturation")
        self.assertEqual(notice["title"], "[工位 2] 光谱饱和")

    def test_dual_station_sessions_cannot_share_the_same_workbook_path(self) -> None:
        station_one = self.backend.stations["1"]
        station_two = self.backend.stations["2"]
        shared_configuration = {
            "outputDir": self.temp_dir.name,
            "sn": "SAME-SN",
            "station": "熔接前",
        }
        station_one.configuration.update(shared_configuration)
        station_two.configuration.update(shared_configuration)
        settings = AutomaticTestSettings()
        station_one.automatic.start(
            settings,
            power_meter_ready=False,
            spectrum_meter_ready=False,
        )
        station_two.automatic.start(
            settings,
            power_meter_ready=False,
            spectrum_meter_ready=False,
        )

        with patch("tauri_bridge.headless_backend.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 8, 18, 12, 34, 0)
            first_path = station_one._begin_recording_session(settings)
            with self.assertRaisesRegex(RuntimeError, "测试文件路径相同"):
                station_two._begin_recording_session(settings)

        self.assertEqual(first_path, station_two.record_store.workbook_path)

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

    def test_chart_reset_does_not_touch_pd_revision(self) -> None:
        station = self.backend.stations["1"]
        station.series_revisions["pd"] = 7

        station.reset_charts()

        self.assertEqual(station.series_revisions["pd"], 7)

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

    def test_loaded_ophir_display_label_migrates_resource_and_driver(self) -> None:
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
            config = backend.stations["1"].configuration
            self.assertEqual(config["powerMeterResource"], "OPHIR::USB::3103403")
            self.assertEqual(config["powerMeterDriverKind"], "ophir")
            persisted = settings.get("runtime/stations")["1"]
            self.assertEqual(persisted["powerMeterResource"], "OPHIR::USB::3103403")
            self.assertEqual(persisted["powerMeterDriverKind"], "ophir")
        finally:
            backend.export_worker.stop()

    def test_configuring_ophir_resource_synchronizes_driver_kind(self) -> None:
        self.backend.dispatch(
            "app.configure",
            {
                "stationId": "1",
                "powerMeterResource": "Ophir USB | OPHIR::USB::ABC123 | USB SN ABC123",
            },
        )

        config = self.backend.stations["1"].configuration
        self.assertEqual(config["powerMeterResource"], "OPHIR::USB::ABC123")
        self.assertEqual(config["powerMeterDriverKind"], "ophir")

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

    def test_tdk_output_is_not_enabled_until_zero_current_is_verified(self) -> None:
        station = self.backend.stations["1"]
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
        station = self.backend.stations["1"]
        controller = MagicMock()
        controller.is_connected = True
        controller.disconnect_device.return_value = False
        station.power_supply_controller = controller

        with self.assertRaisesRegex(RuntimeError, "无法确认设备已经断开"):
            station.disconnect_power_supply()

        self.assertIs(station.power_supply_controller, controller)

    def test_ch341_does_not_create_or_write_second_samples(self) -> None:
        station = self.backend.stations["1"]
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
        station = self.backend.stations["1"]
        controller = MagicMock()
        controller.is_connected = True
        station.power_supply_kind = "ch341"
        station.power_supply_controller = controller
        station._last_power_supply_command_at = time.monotonic()

        with self.assertRaisesRegex(RuntimeError, "电源命令过快"):
            station.read_power_supply({"value": "outputVoltage"})

        controller.read_output_voltage.assert_not_called()

    def test_acquisition_failure_pauses_and_retries_current_point(self) -> None:
        station = self.backend.stations["1"]
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
        with self.assertRaises(_AutomaticPointRetry):
            station._await_automatic_resume()

    def test_manual_stable_point_can_be_saved_to_excel(self) -> None:
        station = self.backend.stations["1"]
        station.configuration.update({"outputDir": self.temp_dir.name, "sn": "MANUAL-01"})
        station.active_current_a = 2.0
        station.recorded_manual_stable_current_a = 2.0
        station.recorded_manual_stability_generation = 3
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
        snapshot = self.backend.snapshot({"view": "manual_1"})
        self.assertEqual(snapshot["manualTest"]["pendingCount"], 1)
        result = self.backend.dispatch("manual.saveExcel", {"stationId": "1"})
        self.assertTrue(Path(result["outputPath"]).exists())
        self.assertEqual(station.record_store.unsaved_records(), ())

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
        station = self.backend.stations["1"]
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

    def test_power_meter_detection_persists_resource_and_driver_together(self) -> None:
        option = PowerMeterOption(
            resource="OPHIR::USB::3103403",
            device_type="Ophir USB",
            detail="USB SN 3103403",
            driver_kind="ophir",
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

        with patch("tauri_bridge.headless_backend.PowerMeterDetectThread", FakeDetectThread):
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
        instances[0].detected.emit([option])
        instances[0].finished.emit()

        persisted = self.backend.settings.get("runtime/stations")["1"]
        self.assertEqual(persisted["powerMeterResource"], "OPHIR::USB::3103403")
        self.assertEqual(persisted["powerMeterDriverKind"], "ophir")
        self.assertEqual(
            self.backend.snapshot({"stationId": "1"})["devices"]["powerMeter"]["detail"],
            "识别完成：发现 1 台兼容功率计",
        )

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
            with patch.object(self.backend, "scan_ch341", return_value=None):
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
            "tauri_bridge.headless_backend.create_ch341_controller",
            return_value=controller,
        ):
            with patch.object(self.backend, "scan_ch341", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "无法打开设备"):
                    station.connect_power_supply()

        self.assertIsNone(station.power_supply_controller)
        controller.get_device_interface_path.assert_not_called()

    def test_ch341_post_open_port_mismatch_disconnects_device(self) -> None:
        location = "PCIROOT(0)#USB(1)"
        station = self.backend.stations["1"]
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
            "tauri_bridge.headless_backend.create_ch341_controller",
            return_value=controller,
        ):
            with patch.object(self.backend, "scan_ch341", return_value=None):
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

    def test_pd_state_and_snapshot_are_isolated_per_station(self) -> None:
        self.backend.stations["1"].pd_state["points"] = [{"elapsedS": 1.0, "value": 1.0}]
        self.backend.stations["2"].pd_state["points"] = [{"elapsedS": 2.0, "value": 2.0}]
        self.backend.stations["1"].series_revisions["pd"] = 1
        self.backend.stations["2"].series_revisions["pd"] = 1

        snap1 = self.backend.snapshot({"view": "pd_1"})
        snap2 = self.backend.snapshot({"view": "pd_2"})

        self.assertEqual(snap1["activeStation"], "1")
        self.assertEqual(snap1["pd"]["points"], [{"elapsedS": 1.0, "value": 1.0}])
        self.assertEqual(snap2["activeStation"], "2")
        self.assertEqual(snap2["pd"]["points"], [{"elapsedS": 2.0, "value": 2.0}])

    def test_snapshot_is_safe_while_power_samples_arrive(self) -> None:
        station = self.backend.stations["1"]
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
                    snapshot = self.backend.snapshot({"view": "automatic_1"})
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
