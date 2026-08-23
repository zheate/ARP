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
        self.backend.export_worker.graceful_stop(timeout_s=5.0)
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

        station.configuration.update(
            {
                "spectrometerResource": "Ocean Insight | 设备 ID 7",
                "spectrometerDeviceId": 7,
            }
        )
        self.assertTrue(station._automatic_settings().use_spectrometer)

    def test_automatic_readiness_matches_start_requirements(self) -> None:
        station = self.backend.stations["1"]
        station.power_supply_controller = _FakeController()

        readiness = station.automatic_readiness()

        self.assertFalse(readiness["ready"])
        blocker_codes = {item["code"] for item in readiness["blockers"]}
        self.assertIn("missing_sn", blocker_codes)
        self.assertIn("power_meter_not_selected", blocker_codes)
        self.assertIn("spectrometer_not_selected", blocker_codes)
        with self.assertRaisesRegex(ValueError, "请填写产品 SN"):
            station.start_automatic({})

        station.configuration.update(
            {
                "sn": "SN-READY",
                "powerMeterResource": "ASRL3::INSTR",
                "spectrometerResource": "Ocean Insight | 设备 ID 7",
                "spectrometerDeviceId": 7,
            }
        )
        self.assertTrue(station.automatic_readiness()["ready"])

    def test_snapshot_marks_old_measurements_stale_then_invalid(self) -> None:
        station = self.backend.stations["1"]
        station.latest_power = PowerMeterReading(
            elapsed_s=1.0,
            power_w=0.8,
            stable=True,
            stable_span_w=0.01,
            stable_window_s=3.0,
            stable_median_w=0.8,
        )
        station.latest_power_received_monotonic_s = 99.5
        station.latest_power_sampled_at_utc = "2026-08-23T01:00:00.000+00:00"
        station.power_meter_reader = MagicMock(is_ready=True)

        with patch("tauri_bridge.headless_backend.time.monotonic", return_value=100.0):
            live = self.backend.snapshot({"view": "automatic_1"})["devices"]["powerMeter"]
        self.assertEqual(live["quality"], "live")
        self.assertEqual(live["ageMs"], 0)

        station.power_meter_reader = None
        with patch("tauri_bridge.headless_backend.time.monotonic", return_value=100.0):
            stale = self.backend.snapshot({"view": "automatic_1"})["devices"]["powerMeter"]
        self.assertEqual(stale["quality"], "stale")

        with patch("tauri_bridge.headless_backend.time.monotonic", return_value=110.0):
            invalid = self.backend.snapshot({"view": "automatic_1"})["devices"]["powerMeter"]
        self.assertEqual(invalid["quality"], "invalid")

    def test_saturated_measurement_is_not_reported_live(self) -> None:
        with patch("tauri_bridge.headless_backend.time.monotonic", return_value=10.0):
            freshness = self.backend.stations["1"].measurement_freshness(
                received_at_monotonic_s=9.9,
                sampled_at_utc="2026-08-23T01:00:00.000+00:00",
                running=True,
                interval_ms=200.0,
                valid=True,
                saturated=True,
            )

        self.assertEqual(freshness["quality"], "saturated")

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

        self.assertEqual(station_one["powerMeterResource"], "")
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

    def test_persisted_runtime_device_identities_are_discarded_for_both_stations(self) -> None:
        settings_path = Path(self.temp_dir.name) / "duplicate-settings.json"
        settings = JsonSettings(settings_path)
        settings.set(
            "runtime/stations",
            {
                "1": {
                    "powerMeterResource": "ASRL3::INSTR",
                    "powerMeterDriverKind": "coherent",
                    "spectrometerResource": "Ocean Insight | 设备 ID 5",
                    "spectrometerDeviceId": 5,
                    "integrationTimeUs": 12345,
                },
                "2": {
                    "powerMeterResource": "OPHIR::USB::3103403",
                    "powerMeterDriverKind": "ophir",
                    "spectrometerResource": "Ocean Insight | 设备 ID 7",
                    "spectrometerDeviceId": 7,
                },
            },
        )
        backend = HeadlessBackend(settings)
        try:
            for station in backend.stations.values():
                self.assertEqual(station.configuration["powerMeterResource"], "")
                self.assertEqual(station.configuration["powerMeterDriverKind"], "caihuang")
                self.assertEqual(station.configuration["spectrometerResource"], "")
                self.assertIsNone(station.configuration["spectrometerDeviceId"])
            self.assertEqual(backend.stations["1"].configuration["integrationTimeUs"], 12345)

            persisted = settings.get("runtime/stations")
            for config in persisted.values():
                self.assertEqual(config["powerMeterResource"], "")
                self.assertEqual(config["spectrometerResource"], "")
                self.assertIsNone(config["spectrometerDeviceId"])
        finally:
            backend.export_worker.graceful_stop(timeout_s=5.0)

    def test_loaded_power_meter_identity_is_not_restored(self) -> None:
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
            self.assertEqual(config["powerMeterResource"], "")
            self.assertEqual(config["powerMeterDriverKind"], "caihuang")
            persisted = settings.get("runtime/stations")["1"]
            self.assertEqual(persisted["powerMeterResource"], "")
            self.assertEqual(persisted["powerMeterDriverKind"], "caihuang")
        finally:
            backend.export_worker.graceful_stop(timeout_s=5.0)

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

    def test_station_snapshot_excludes_ch341_port_connected_by_other_station(self) -> None:
        occupied_location = "PCIROOT(0)#USB(1)"
        available_location = "PCIROOT(0)#USB(2)"
        station_one = self.backend.stations["1"]
        station_one.power_supply_kind = "ch341"
        station_one.power_supply_controller = _FakeController()
        station_one.configuration["ch341SelectedLocationPath"] = occupied_location
        self.backend.ch341_discovery_state = "ready"
        self.backend.ch341_descriptors = [
            Ch341DeviceDescriptor(0, "iface-0", occupied_location),
            Ch341DeviceDescriptor(1, "iface-1", available_location),
        ]

        station_two_snapshot = self.backend.snapshot({"stationId": "2"})
        station_two_ports = station_two_snapshot["devices"]["powerSupply"]["ch341"]["ports"]
        self.assertEqual(
            [(port["locationPath"], port["deviceIndex"]) for port in station_two_ports],
            [(available_location, 1)],
        )

        station_one_snapshot = self.backend.snapshot({"stationId": "1"})
        station_one_paths = {
            port["locationPath"]
            for port in station_one_snapshot["devices"]["powerSupply"]["ch341"]["ports"]
        }
        self.assertIn(occupied_location, station_one_paths)

    def test_spectrometer_open_updates_runtime_but_not_persisted_identity(self) -> None:
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
        self.backend._persist_configuration()
        persisted = self.backend.settings.get("runtime/stations")
        self.assertEqual(persisted["1"]["spectrometerResource"], "")
        self.assertIsNone(persisted["1"]["spectrometerDeviceId"])
        self.assertIn("原设备 ID 5 已失效", self.backend.notices[-1]["message"])

    def test_power_meter_detection_lists_devices_without_selecting_or_persisting(self) -> None:
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
        other_station_snapshot = self.backend.snapshot({"stationId": "2"})
        self.assertFalse(other_station_snapshot["devices"]["powerMeter"]["detecting"])
        self.assertEqual(other_station_snapshot["devices"]["powerMeter"]["detail"], "已停止")
        instances[0].detected.emit([option])
        instances[0].finished.emit()

        runtime = self.backend.stations["1"].configuration
        self.assertEqual(runtime["powerMeterResource"], "")
        self.assertEqual(runtime["powerMeterDriverKind"], "caihuang")
        persisted = self.backend.settings.get("runtime/stations")["1"]
        self.assertEqual(persisted["powerMeterResource"], "")
        self.assertEqual(persisted["powerMeterDriverKind"], "caihuang")
        self.assertEqual(
            self.backend.snapshot({"stationId": "1"})["devices"]["powerMeter"]["resources"],
            [option.label()],
        )
        self.assertEqual(
            self.backend.snapshot({"stationId": "1"})["devices"]["powerMeter"]["detail"],
            "识别完成：发现 1 台兼容功率计",
        )

    def test_open_devices_are_removed_from_other_station_options(self) -> None:
        occupied_meter = PowerMeterOption(
            resource="ASRL3::INSTR",
            device_type="彩煌串口",
            detail="COM3",
            driver_kind="caihuang",
        )
        available_meter = PowerMeterOption(
            resource="OPHIR::USB::3103403",
            device_type="Ophir USB",
            detail="USB SN 3103403",
            driver_kind="ophir",
        )
        self.backend.power_meter_options = [occupied_meter, available_meter]
        self.backend.spectrometer_resources = [
            {"label": "Ocean Insight | 设备 ID 5", "deviceId": 5},
            {"label": "Ocean Insight | 设备 ID 7", "deviceId": 7},
        ]
        station_one = self.backend.stations["1"]
        station_one.configuration["powerMeterResource"] = occupied_meter.resource
        station_one.configuration["spectrometerResource"] = "Ocean Insight | 设备 ID 5"
        station_one.configuration["spectrometerDeviceId"] = 5
        station_one.power_meter_reader = MagicMock(is_ready=True)
        station_one.spectrometer_reader = MagicMock(is_ready=True)

        station_two_devices = self.backend.snapshot({"stationId": "2"})["devices"]
        self.assertEqual(
            station_two_devices["powerMeter"]["resources"],
            [available_meter.label()],
        )
        self.assertEqual(
            station_two_devices["spectrometer"]["resources"],
            ["Ocean Insight | 设备 ID 7"],
        )

        station_one_devices = self.backend.snapshot({"stationId": "1"})["devices"]
        self.assertEqual(
            station_one_devices["powerMeter"]["resources"],
            [occupied_meter.label(), available_meter.label()],
        )
        self.assertEqual(
            station_one_devices["spectrometer"]["resources"],
            ["Ocean Insight | 设备 ID 5", "Ocean Insight | 设备 ID 7"],
        )

    def test_spectrometer_requires_an_explicit_runtime_selection(self) -> None:
        with self.assertRaisesRegex(ValueError, "请先选择光谱仪设备"):
            self.backend.stations["1"].start_spectrometer()

    def test_ch341_detection_completion_is_published_as_station_toast(self) -> None:
        instances = []

        class FakeDiscoveryThread:
            completed = Signal(object)
            status = Signal(str)
            failed = Signal(str)
            finished = Signal()

            def __init__(self, discover) -> None:
                self.discover = discover
                instances.append(self)

            def start(self) -> None:
                return

        with patch("tauri_bridge.headless_backend._DeviceDiscoveryThread", FakeDiscoveryThread):
            self.backend.dispatch(
                "device.refresh",
                {"stationId": "2", "device": "powerSupply"},
            )

        instances[0].completed.emit(("ch341", []))
        instances[0].finished.emit()

        notice = self.backend.notices[-1]
        self.assertEqual(notice["level"], "info")
        self.assertEqual(notice["title"], "CH341 识别")
        self.assertEqual(notice["message"], "识别完成：发现 0 个 CH341 设备")
        self.assertEqual(notice["stationId"], "2")

        station_two = self.backend.stations["2"]
        station_two.power_supply_kind = "tdk"
        station_two.configuration["powerSupplyKind"] = "tdk"
        tdk_snapshot = self.backend.snapshot({"stationId": "2"})["devices"]["powerSupply"]
        self.assertFalse(tdk_snapshot["detecting"])
        self.assertEqual(tdk_snapshot["detail"], "未连接")

    def test_ch341_connection_opens_selected_index_at_20_khz(self) -> None:
        location = "PCIROOT(0)#USB(2)"
        station = self.backend.stations["1"]
        station.configuration.update(
            {
                "powerSupplyKind": "ch341",
                "ch341SelectedLocationPath": location,
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
        self.assertEqual(station.connected_ch341_port["name"], "Device 1")

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

    def test_global_stop_broadcasts_before_parallel_station_cleanup(self) -> None:
        first = self.backend.stations["1"]
        second = self.backend.stations["2"]
        first_cleanup_release = threading.Event()
        second_cleanup_started = threading.Event()
        broadcasts: list[str] = []

        first.request_emergency_stop = lambda _reason: broadcasts.append("1")
        second.request_emergency_stop = lambda _reason: broadcasts.append("2")
        first.stop_all = lambda: first_cleanup_release.wait(2.0)
        second.stop_all = second_cleanup_started.set

        with patch.object(self.backend, "_stop_pd", return_value=None):
            worker = threading.Thread(target=self.backend._stop_all)
            worker.start()
            self.assertTrue(second_cleanup_started.wait(1.0))
            self.assertEqual(broadcasts, ["1", "2"])
            first_cleanup_release.set()
            worker.join(2.0)

        self.assertFalse(worker.is_alive())

    def test_emergency_stop_signal_does_not_wait_for_station_state_lock(self) -> None:
        station = self.backend.stations["1"]
        station._lock.acquire()
        try:
            worker = threading.Thread(
                target=station.request_emergency_stop,
                args=("全部工位急停",),
            )
            worker.start()
            worker.join(0.5)
            self.assertFalse(worker.is_alive())
            self.assertTrue(station._automatic_stop.is_set())
        finally:
            station._lock.release()
        ramp_thread = station._emergency_ramp_thread
        if ramp_thread is not None:
            ramp_thread.join(1.0)

    def test_safety_commands_do_not_depend_on_configuration_persistence(self) -> None:
        with patch.object(
            self.backend,
            "_persist_configuration",
            side_effect=RuntimeError("settings disk full"),
        ):
            snapshot = self.backend.dispatch("app.stopAll", {})

        self.assertTrue(snapshot["safety"]["shutdown"]["confirmed"])

    def test_manual_hardware_commands_are_rejected_while_automatic_owns_control(self) -> None:
        station = self.backend.stations["1"]
        station.automatic.state = AutomaticTestState.PAUSED

        with self.assertRaisesRegex(RuntimeError, "硬件控制权由自动测试持有"):
            self.backend.dispatch("powerSupply.setCurrent", {"stationId": "1", "currentA": 1.0})

        snapshot = self.backend.snapshot({"view": "manual_1"})
        self.assertEqual(snapshot["safety"]["controlOwner"], "automatic")
        self.assertFalse(snapshot["safety"]["manualControlsAllowed"])

    def test_snapshot_reports_actual_saved_point_count(self) -> None:
        station = self.backend.stations["1"]
        station.record_store.recorded_currents.update({2.0, 4.0})

        snapshot = self.backend.snapshot({"view": "automatic_1"})

        self.assertEqual(snapshot["automaticTest"]["savedPointCount"], 2)

    def test_operator_pause_publishes_reason_actions_and_deadline(self) -> None:
        station = self.backend.stations["1"]
        station.automatic.settings = AutomaticTestSettings(
            pause_ramp_down_timeout_s=60.0,
            use_spectrometer=False,
        )
        station.automatic.state = AutomaticTestState.WAITING_STABLE

        station.pause_automatic()
        snapshot = self.backend.snapshot({"view": "automatic_1"})["automaticTest"]

        self.assertEqual(snapshot["pauseKind"], "operator")
        self.assertEqual(snapshot["pauseActions"], ["resume", "safe_end"])
        self.assertIsNotNone(snapshot["pauseStartedAtUtc"])
        self.assertIsNotNone(snapshot["pauseDeadlineAtUtc"])
        station.resume_automatic("resume")
        self.assertEqual(station.automatic.state, AutomaticTestState.WAITING_STABLE)

    def test_spectrum_pause_only_accepts_controlled_recovery_action(self) -> None:
        station = self.backend.stations["1"]
        station.automatic.settings = AutomaticTestSettings(use_spectrometer=True)
        station.automatic.state = AutomaticTestState.WAITING_STABLE
        station.spectrometer_reader = MagicMock()
        station._set_automatic_pause(
            "光谱饱和",
            kind="spectrum_saturated",
            hold_current=True,
            retry_point=True,
        )

        with self.assertRaisesRegex(ValueError, "不允许"):
            station.resume_automatic("resume")

        station.resume_automatic("adjust_spectrum", {"integrationTimeUs": 12_000})
        self.assertEqual(station.configuration["integrationTimeUs"], 12_000)
        station.spectrometer_reader.use_fixed_integration_time.assert_called_once_with(12_000)

    def test_next_device_preserves_setup_and_clears_session_state(self) -> None:
        station = self.backend.stations["1"]
        station.configuration.update(
            {
                "sn": "SN-DONE",
                "productModel": "PUMP-976",
                "batch": "B-23",
                "selectedScriptId": "standard",
            }
        )
        station.automatic.state = AutomaticTestState.COMPLETED
        station.shutdown_confirmed = True
        station.stable_power_points[2.0] = 5.0
        station.record_store.recorded_currents.add(2.0)

        snapshot = self.backend.dispatch("automatic.nextDevice", {"stationId": "1"})

        self.assertEqual(snapshot["configuration"]["sn"], "")
        self.assertEqual(snapshot["configuration"]["productModel"], "PUMP-976")
        self.assertEqual(snapshot["configuration"]["batch"], "B-23")
        self.assertEqual(snapshot["automaticTest"]["state"], "idle")
        self.assertEqual(snapshot["automaticTest"]["savedPointCount"], 0)
        self.assertEqual(snapshot["measurements"]["stable"], [])

    def test_automatic_export_is_queued_without_waiting_for_excel(self) -> None:
        station = self.backend.stations["1"]
        station.record_store.begin_session(
            Path(self.temp_dir.name) / "excel",
            "SN-ASYNC",
            datetime.now(),
            test_station="工位 1",
            mode="automatic",
        )
        record = ExcelTestRecord(
            current_a=2.0,
            voltage_v=8.0,
            power_w=5.0,
            efficiency=0.3,
            peak_wavelength_nm=976.0,
            centroid_nm=976.0,
            fwhm_nm=0.4,
            pib=0.95,
            wavelength=[975.0, 976.0],
            intensity=[10.0, 20.0],
            test_station="工位 1",
        )
        station.record_store.queue(record)
        captured: list[ExportTask] = []

        with patch.object(
            self.backend.export_worker,
            "submit",
            side_effect=lambda task: captured.append(task) or True,
        ):
            station._queue_automatic_export()

        self.assertEqual(station.export_status, "exporting")
        self.assertEqual(len(captured), 1)
        captured[0].callback(str(captured[0].target_path), None)  # type: ignore[misc]
        self.assertEqual(station.export_status, "completed")
        self.assertEqual(station.record_store.exported_currents, {2.0})

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

        self.assertTrue(worker.graceful_stop(timeout_s=5.0))
        self.assertEqual(len(results), 2)
        self.assertTrue(path1.exists())
        self.assertTrue(path2.exists())
        self.assertFalse(worker.submit(task1))

    def test_export_queue_reports_timeout_without_dropping_accepted_task(self) -> None:
        worker = ExportQueueWorker()
        started = threading.Event()
        release = threading.Event()
        callback_results: list[tuple[str | None, Exception | None]] = []
        task = ExportTask(
            "1",
            Path(self.temp_dir.name) / "slow.xlsx",
            [],
            lambda path, error: callback_results.append((path, error)),
        )

        def slow_save(*_args: object, **_kwargs: object) -> Path:
            started.set()
            release.wait(2.0)
            return task.target_path

        with patch("tauri_bridge.headless_backend.save_test_records", side_effect=slow_save):
            worker.start()
            self.assertTrue(worker.submit(task))
            self.assertTrue(started.wait(1.0))
            self.assertFalse(worker.graceful_stop(timeout_s=0.01))
            self.assertFalse(worker.submit(task))
            release.set()
            self.assertTrue(worker.graceful_stop(timeout_s=1.0))

        self.assertEqual(callback_results, [(str(task.target_path), None)])


if __name__ == "__main__":
    unittest.main()
