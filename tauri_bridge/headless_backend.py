"""Pure-Python hardware backend for the Tauri sidecar with dual-station support.

This module contains no GUI toolkit imports. Blocking vendor drivers run on
worker threads and publish immutable readings into isolated StationRuntime
instances, consumed by the newline-delimited JSON bridge.
"""

from __future__ import annotations

import math
import platform
import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from combined_test.automation import (
    AutomaticCurrentPoint,
    AutomaticTestOrchestrator,
    AutomaticTestSettings,
    AutomaticTestState,
    build_ramp_down_currents,
    validate_automatic_test_settings,
)
from combined_test.device_interfaces import ControllerPowerSupply
from combined_test.devices import (
    PowerMeterDetectThread,
    PowerMeterReaderThread,
    SpectrometerReaderThread,
    create_ch341_controller,
    extract_power_resource_name,
    load_legacy_ch341_controller_class,
    load_spectrometer_components,
    scan_ch341_devices,
)
from combined_test.ch341_ports import Ch341DeviceDescriptor, get_location_path_from_interface_path
from combined_test.excel_export import ExcelTestRecord, build_test_workbook_path, save_test_records
from combined_test.models import (
    PowerMeterOption,
    PowerMeterReading,
    PowerMeterSettings,
    SpectrumFrame,
    SpectrometerReading,
    SpectrometerSettings,
)
from combined_test.persistence import save_spectrum_curve
from combined_test.runtime_logging import get_runtime_logger, runtime_log_path
from combined_test.spectrum import SPECTRUM_CENTER_LOCK_HALF_RANGE_NM
from combined_test.spectrum_math import calculate_pib
from combined_test.tdk_power_supply import TdkLambdaPowerSupply, list_tdk_serial_resources

from .settings import JsonSettings


_LOGGER = get_runtime_logger("headless_backend")


DEFAULT_CONFIGURATION: dict[str, Any] = {
    "sn": "",
    "productModel": "",
    "batch": "",
    "station": "工位 1",
    "outputDir": "test_records",
    "powerSupplyKind": "ch341",
    "ch341SelectedLocationPath": "",
    "ch341PortBindings": [],
    "tdkResource": "",
    "setCurrentA": 0.0,
    "tdkVoltageV": 12.0,
    "powerMeterResource": "ASRL3::INSTR",
    "powerMeterDriverKind": "caihuang",
    "powerMeterWavelengthNm": 976.0,
    "softwareGain": 1.0,
    "powerMeterIntervalMs": 100,
    "spectrometerResource": "",
    "spectrometerDeviceId": None,
    "integrationTimeUs": 10_000,
    "autoIntegration": False,
    "spectrometerIntervalMs": 100,
    "spectrumAverageFrames": 1,
    "pibCenterWavelengthNm": 976.0,
    "pibHalfRangeNm": 1.5,
    "stableWindowS": 3.0,
    "stableToleranceW": 0.15,
    "initialCurrentA": 1.0,
    "targetCurrentA": 20.0,
    "currentStepA": 3.0,
    "currentPlanMode": "range",
    "customCurrentPoints": [],
    "pointTimeoutS": 120.0,
    "rampDownStepA": 5.0,
    "rampDownIntervalS": 1.1,
    "pauseRampDownTimeoutS": 30.0,
    "useSpectrometer": True,
}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _device_state(connected: bool, running: bool, error: str) -> str:
    if error:
        return "error"
    if connected or running:
        return "connected"
    return "disconnected"


@dataclass
class ExportTask:
    station_id: str
    target_path: Path
    records: list[ExcelTestRecord]
    callback: Callable[[str | None, Exception | None], None] | None = None


class ExportQueueWorker:
    """Sequential worker for heavy Excel export operations."""

    def __init__(self) -> None:
        self._queue: queue.Queue[ExportTask | None] = queue.Queue(maxsize=32)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="export-queue-worker",
                daemon=True,
            )
            self._thread.start()

    def submit(self, task: ExportTask) -> bool:
        try:
            self._queue.put_nowait(task)
            return True
        except queue.Full:
            return False

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                task = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if task is None:
                break
            started_at = time.perf_counter()
            out_path: str | None = None
            error: Exception | None = None
            try:
                out_path = save_test_records(task.target_path, task.records)
                duration_ms = (time.perf_counter() - started_at) * 1000.0
                _LOGGER.info(
                    "工位 %s Excel 导出完成 | path=%s | duration_ms=%.2f",
                    task.station_id,
                    out_path,
                    duration_ms,
                )
            except Exception as exc:
                error = exc
                _LOGGER.exception("工位 %s Excel 导出失败", task.station_id)
            finally:
                self._queue.task_done()
                if task.callback is not None:
                    try:
                        task.callback(out_path, error)
                    except Exception:
                        pass


class StationRuntime:
    """Isolated hardware, automation, and measurement state for one test station."""

    def __init__(self, station_id: str, backend: HeadlessBackend, initial_config: dict[str, Any] | None = None) -> None:
        self.station_id = str(station_id)
        self.backend = backend
        self._lock = threading.RLock()

        self.configuration = dict(DEFAULT_CONFIGURATION)
        self.configuration["station"] = f"工位 {self.station_id}"
        if self.station_id == "2":
            self.configuration.update(
                {
                    "ch341SelectedLocationPath": "",
                    "ch341PortBindings": [],
                    "tdkResource": "",
                    "powerMeterResource": "",
                    "spectrometerResource": "",
                    "spectrometerDeviceId": None,
                }
            )
        if initial_config:
            self.configuration.update({k: v for k, v in initial_config.items() if k in DEFAULT_CONFIGURATION})

        self.power_supply_controller: Any | None = None
        self.power_supply_kind = str(self.configuration["powerSupplyKind"])
        self.active_current_a: float | None = 0.0
        self.connected_ch341_port: dict[str, Any] | None = None
        self.power_supply_identity_error = ""
        self.latest_tdk_current_a: float | None = None
        self.latest_tdk_voltage_v: float | None = None

        self.power_meter_detect_thread: PowerMeterDetectThread | None = None
        self.power_meter_reader: PowerMeterReaderThread | None = None
        self.power_meter_error = ""
        self.latest_power: PowerMeterReading | None = None
        self.power_points: deque[dict[str, float]] = deque(maxlen=1_200)

        self.spectrometer_reader: SpectrometerReaderThread | None = None
        self.spectrometer_error = ""
        self.latest_spectrometer_reading: SpectrometerReading | None = None
        self.latest_spectrum: SpectrumFrame | None = None
        self.spectrum_revision = 0
        self.spectrum_grid_revision = 0
        self._last_spectrum_grid: list[float] = []

        self.stable_power_points: dict[float, float] = {}
        self.efficiency_points: dict[float, float] = {}
        self.series_revisions = {"power": 0, "stable": 0, "spectrum": 0}

        self.automatic = AutomaticTestOrchestrator()
        self.automatic_detail = "准备测试"
        self.automatic_started_at: float | None = None
        self.automatic_hold_started_at: float | None = None
        self.automatic_terminal_outcome: str | None = None
        self.automatic_terminal_reason = ""
        self.automatic_output_shutdown_unconfirmed = False
        self.automatic_sample_path = ""
        self._automatic_thread: threading.Thread | None = None
        self._automatic_stop = threading.Event()
        self._automatic_resume = threading.Event()
        self._automatic_resume.set()
        self._test_reference: Any | None = None
        self.export_status: str = "idle"
        self.export_path: str = ""

    def notify(self, level: str, title: str, message: str) -> None:
        self.backend.notify(level, f"[工位 {self.station_id}] {title}", message)

    def _power_supply(self) -> ControllerPowerSupply | None:
        return ControllerPowerSupply(self.power_supply_controller) if self.power_supply_controller is not None else None

    def _require_power_supply(self) -> ControllerPowerSupply:
        supply = self._power_supply()
        if supply is None:
            raise RuntimeError("请先连接电源控制器")
        if self.power_supply_identity_error:
            raise RuntimeError(f"电源状态异常：{self.power_supply_identity_error}")
        return supply

    def configure(self, params: dict[str, Any]) -> None:
        with self._lock:
            kind = str(params.get("powerSupplyKind", self.configuration["powerSupplyKind"])).lower()
            if kind not in {"ch341", "tdk"}:
                raise ValueError("powerSupplyKind must be ch341 or tdk")

            if "ch341PortBindings" in params:
                raw_bindings = params["ch341PortBindings"]
                if not isinstance(raw_bindings, list):
                    raise ValueError("ch341PortBindings 必须是列表")
                cleaned_bindings = []
                seen_paths = set()
                seen_names = set()
                for item in raw_bindings:
                    if not isinstance(item, dict):
                        raise ValueError("端口绑定项必须是对象")
                    loc = str(item.get("locationPath", "")).strip()
                    name = str(item.get("name", "")).strip()
                    if not loc:
                        raise ValueError("locationPath 不能为空")
                    if not name:
                        raise ValueError("工位名称不能为空")
                    if loc in seen_paths:
                        raise ValueError(f"存在重复的 locationPath: {loc}")
                    if name in seen_names:
                        raise ValueError(f"存在重复的工位名称: {name}")
                    seen_paths.add(loc)
                    seen_names.add(name)
                    cleaned_bindings.append({"locationPath": loc, "name": name})

                if self.power_supply_controller is not None and cleaned_bindings != self.configuration.get("ch341PortBindings", []):
                    raise ValueError("请先断开电源，再修改端口绑定或切换工位")
                params["ch341PortBindings"] = cleaned_bindings

            if "ch341SelectedLocationPath" in params:
                selected_loc = str(params["ch341SelectedLocationPath"]).strip()
                if self.power_supply_controller is not None and selected_loc != self.configuration.get("ch341SelectedLocationPath", ""):
                    raise ValueError("请先断开电源，再切换设备通道")
                params["ch341SelectedLocationPath"] = selected_loc

            if self.power_supply_controller is not None and kind != self.power_supply_kind:
                raise ValueError("请先断开电源，再切换控制器类型")

            for key in DEFAULT_CONFIGURATION:
                if key in params:
                    self.configuration[key] = params[key]

            if "spectrometerResource" in params:
                resource_str = str(self.configuration.get("spectrometerResource", "")).strip()
                match = re.search(r"(?:设备\s*ID|ID|Device\s*ID)\s*[:：]?\s*(\d+)", resource_str, re.IGNORECASE)
                if match:
                    self.configuration["spectrometerDeviceId"] = int(match.group(1))
                elif not resource_str:
                    self.configuration["spectrometerDeviceId"] = None

            mode = str(self.configuration["currentPlanMode"]).lower()
            if mode not in {"range", "custom"}:
                raise ValueError("currentPlanMode must be range or custom")
            points = self.configuration["customCurrentPoints"]
            if not isinstance(points, list):
                raise ValueError("customCurrentPoints must be a list")
            self.power_supply_kind = kind

    def connect_power_supply(self) -> None:
        with self._lock:
            if self.power_supply_controller is not None:
                return
            kind = self.power_supply_kind
            if kind == "tdk":
                resource = str(self.configuration["tdkResource"]).strip()
                if not resource:
                    raise ValueError("请选择 TDK 串口资源")
                self.backend.check_device_conflicts(self.station_id, "tdk", resource)
                supply = TdkLambdaPowerSupply.open_resource(resource)
                supply.set_voltage(float(self.configuration["tdkVoltageV"]))
                self.power_supply_controller = supply
                self.power_supply_identity_error = ""
                self.active_current_a = supply.read_output_current()
                self.notify("info", "电源连接", f"TDK 电源已连接：{resource}")
                return

            # CH341 connection
            selected_loc = str(self.configuration.get("ch341SelectedLocationPath", "")).strip()
            if not selected_loc:
                raise ValueError("请先选择 CH341 物理端口")
            self.backend.check_device_conflicts(self.station_id, "ch341", selected_loc)

            # Resolve location to device index via backend descriptors
            if self.backend.ch341_discovery_state != "ready":
                self.backend.scan_ch341()
            desc = next((d for d in self.backend.ch341_descriptors if d.location_path == selected_loc), None)
            if desc is None:
                raise RuntimeError(f"指定的 CH341 设备端口未在线：{selected_loc}")

            controller = create_ch341_controller(device_index=desc.device_index)
            actual_iface = controller.get_device_interface_path(desc.device_index)
            actual_loc = get_location_path_from_interface_path(actual_iface)
            if actual_loc != selected_loc:
                self.power_supply_identity_error = f"端口身份校验失败：期望 {selected_loc}，实际 {actual_loc}"
                raise RuntimeError(self.power_supply_identity_error)

            bindings = {b["locationPath"]: b["name"] for b in self.configuration.get("ch341PortBindings", []) if isinstance(b, dict)}
            name = bindings.get(selected_loc, f"端口 {desc.device_index}")
            self.connected_ch341_port = {
                "locationPath": selected_loc,
                "name": name,
                "deviceIndex": desc.device_index,
            }
            self.power_supply_controller = controller
            self.power_supply_identity_error = ""
            self.active_current_a = 0.0
            self.notify("info", "电源连接", f"CH341 {name} 已连接 (Device {desc.device_index})")

    def disconnect_power_supply(self) -> None:
        with self._lock:
            if self.power_supply_controller is None:
                return
            if self.active_current_a and abs(self.active_current_a) > 0.01:
                self.safe_ramp_down()
            disconnect = getattr(self.power_supply_controller, "disconnect_device", None)
            if callable(disconnect):
                try:
                    disconnect()
                except Exception:
                    pass
            self.power_supply_controller = None
            self.connected_ch341_port = None
            self.active_current_a = 0.0
            self.power_supply_identity_error = ""
            self.notify("info", "电源断开", "电源控制器已安全断开")

    def set_current(self, params: dict[str, Any]) -> None:
        with self._lock:
            current_a = float(params["currentA"])
            if current_a < 0.0:
                raise ValueError("电流设定值不能为负数")
            if self.power_supply_kind == "ch341" and current_a > 20.0:
                raise ValueError("CH341 最大电流不能超过 20 A")
            supply = self._require_power_supply()
            supply.set_current(current_a)
            self.active_current_a = current_a
            self.configuration["setCurrentA"] = current_a

    def set_voltage(self, params: dict[str, Any]) -> None:
        with self._lock:
            voltage_v = float(params["voltageV"])
            if self.power_supply_kind != "tdk":
                raise ValueError("仅 TDK 电源支持设置电压")
            supply = self._require_power_supply()
            supply.set_voltage(voltage_v)
            self.configuration["tdkVoltageV"] = voltage_v

    def set_output(self, params: dict[str, Any]) -> None:
        with self._lock:
            enabled = bool(params.get("enabled", False))
            supply = self._require_power_supply()
            supply.set_output_enabled(enabled)

    def read_power_supply(self, _params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            supply = self._require_power_supply()
            return {
                "currentA": supply.read_output_current(),
                "voltageV": supply.read_output_voltage(),
            }

    def start_power_meter(self) -> None:
        with self._lock:
            if self.power_meter_reader is not None:
                return
            resource = str(self.configuration["powerMeterResource"]).strip()
            if not resource:
                raise ValueError("请先选择功率计资源")
            self.backend.check_device_conflicts(self.station_id, "powerMeter", resource)

            settings = PowerMeterSettings(
                resource=resource,
                wavelength_nm=float(self.configuration["powerMeterWavelengthNm"]),
                interval_ms=int(self.configuration["powerMeterIntervalMs"]),
                software_gain=float(self.configuration["softwareGain"]),
                stable_window_s=float(self.configuration["stableWindowS"]),
                stable_tolerance_w=float(self.configuration["stableToleranceW"]),
                driver_kind=str(self.configuration.get("powerMeterDriverKind", "caihuang")),
            )
            reader = PowerMeterReaderThread(settings)
            self.power_meter_reader = reader
            self.power_meter_error = ""
            reader.reading.connect(self._on_power_reading)
            reader.ready.connect(self._on_power_ready)
            reader.failed.connect(self._on_power_failed)
            reader.status.connect(lambda msg: self.backend.set_status(f"[工位 {self.station_id}] {msg}"))
            reader.finished.connect(self._on_power_finished)
            reader.start()

    def stop_power_meter(self) -> None:
        reader = self.power_meter_reader
        if reader is not None:
            reader.stop()
            reader.wait(3_000)

    def _on_power_reading(self, reading: PowerMeterReading) -> None:
        with self._lock:
            self.latest_power = reading
            self.power_points.append({"elapsedS": reading.timestamp, "powerW": reading.power_w})
            self.series_revisions["power"] += 1

    def _on_power_ready(self) -> None:
        self.automatic.mark_power_meter_ready()

    def _on_power_failed(self, message: str) -> None:
        self.power_meter_error = str(message)
        self.notify("error", "功率计", message)
        if self.automatic.state not in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED}:
            self.automatic.pause(str(message))

    def _on_power_finished(self) -> None:
        self.power_meter_reader = None

    def set_relative_zero(self, params: dict[str, Any]) -> None:
        zero = float(params.get("zeroW", 0.0))
        reader = self.power_meter_reader
        if reader is not None:
            reader.set_relative_zero(zero)

    def start_spectrometer(self) -> None:
        with self._lock:
            if self.spectrometer_reader is not None:
                return
            device_id = self.configuration.get("spectrometerDeviceId")
            device_id_val = None if device_id in {None, ""} else int(device_id)
            if device_id_val is not None:
                self.backend.check_device_conflicts(self.station_id, "spectrometer", device_id_val)

            settings = SpectrometerSettings(
                integration_time_us=int(self.configuration["integrationTimeUs"]),
                interval_ms=int(self.configuration["spectrometerIntervalMs"]),
                device_id=device_id_val,
                auto_integration_enabled=bool(self.configuration["autoIntegration"]),
                average_frames=int(self.configuration["spectrumAverageFrames"]),
                pib_center_wavelength_nm=float(self.configuration.get("pibCenterWavelengthNm", 976.0)),
                pib_half_range_nm=float(self.configuration.get("pibHalfRangeNm", 1.5)),
            )
            reader = SpectrometerReaderThread(settings, headless=True)
            self.spectrometer_reader = reader
            self.spectrometer_error = ""
            reader.reading.connect(self._on_spectrometer_reading)
            reader.ready.connect(self._on_spectrometer_ready)
            reader.failed.connect(self._on_spectrometer_failed)
            reader.status.connect(lambda msg: self.backend.set_status(f"[工位 {self.station_id}] {msg}"))
            reader.integration_time_changed.connect(self._on_integration_changed)
            reader.finished.connect(self._on_spectrometer_finished)
            reader.start()

    def stop_spectrometer(self) -> None:
        reader = self.spectrometer_reader
        if reader is not None:
            reader.stop()
            reader.wait(3_000)

    def _on_spectrometer_reading(self, reading: SpectrometerReading) -> None:
        reader = self.spectrometer_reader
        frame = reader.take_latest_spectrum() if reader is not None else None
        with self._lock:
            self.latest_spectrometer_reading = reading
            if frame is not None:
                self.latest_spectrum = frame
                self.spectrum_revision += 1
                self.series_revisions["spectrum"] += 1

    def _on_spectrometer_ready(self) -> None:
        self.automatic.mark_spectrum_meter_ready()

    def _on_spectrometer_failed(self, message: str) -> None:
        self.spectrometer_error = str(message)
        self.notify("error", "光谱仪", message)
        if self.automatic.state not in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED}:
            self.automatic.pause(str(message))

    def _on_spectrometer_finished(self) -> None:
        self.spectrometer_reader = None

    def _on_integration_changed(self, value: int) -> None:
        self.configuration["integrationTimeUs"] = int(value)

    def save_spectrum_csv(self, params: dict[str, Any]) -> None:
        frame = self.latest_spectrum
        if frame is None:
            raise ValueError("当前没有可保存的光谱数据")
        requested = str(params.get("path", "")).strip()
        path = (
            Path(requested)
            if requested
            else Path(str(self.configuration["outputDir"])) / f"spectrum_st{self.station_id}_{datetime.now():%Y%m%d_%H%M%S}.csv"
        )
        save_spectrum_curve(path, frame.wavelength, frame.intensity)
        self.notify("info", "光谱保存", f"光谱已保存：{path}")

    def _automatic_settings(self) -> AutomaticTestSettings:
        points = tuple(
            AutomaticCurrentPoint(float(item["currentA"]), float(item["dwellS"]))
            for item in self.configuration["customCurrentPoints"]
        )
        settings = AutomaticTestSettings(
            initial_current_a=float(self.configuration["initialCurrentA"]),
            target_current_a=float(self.configuration["targetCurrentA"]),
            current_step_a=float(self.configuration["currentStepA"]),
            point_timeout_s=float(self.configuration["pointTimeoutS"]),
            ramp_down_step_a=float(self.configuration["rampDownStepA"]),
            ramp_down_interval_s=float(self.configuration["rampDownIntervalS"]),
            pause_ramp_down_timeout_s=float(self.configuration["pauseRampDownTimeoutS"]),
            use_spectrometer=bool(self.configuration["useSpectrometer"]),
            maximum_current_a=None if self.power_supply_kind == "tdk" else 20.0,
            current_plan_mode=str(self.configuration["currentPlanMode"]),
            custom_current_points=points,
        )
        return validate_automatic_test_settings(
            settings,
            stable_window_s=float(self.configuration["stableWindowS"]),
            post_stable_delay_s=10.0,
        )

    def start_automatic(self, params: dict[str, Any]) -> None:
        if self._automatic_thread is not None and self._automatic_thread.is_alive():
            raise ValueError(f"工位 {self.station_id} 自动测试已在运行")
        self.configure(params)
        self._select_test_reference(params)
        supply = self._require_power_supply()
        if self.power_supply_kind == "tdk" and not supply.output_enabled:
            raise ValueError("请先开启 TDK 电源输出")
        if float(self.active_current_a or 0.0) > 0.0:
            raise ValueError("开始自动测试前必须先将输出电流归零")

        settings = self._automatic_settings()
        if self.power_meter_reader is None:
            self.start_power_meter()
        if settings.use_spectrometer and self.spectrometer_reader is None:
            self.start_spectrometer()

        self.automatic.start(
            settings,
            power_meter_ready=bool(self.power_meter_reader and self.power_meter_reader.is_ready),
            spectrum_meter_ready=bool(self.spectrometer_reader and self.spectrometer_reader.is_ready),
        )
        self.automatic_started_at = time.monotonic()
        self.automatic_terminal_outcome = None
        self.automatic_terminal_reason = ""
        self._automatic_stop.clear()
        self._automatic_resume.set()
        self._automatic_thread = threading.Thread(
            target=self._run_automatic,
            name=f"automatic-test-station-{self.station_id}",
            daemon=True,
        )
        self._automatic_thread.start()

    def _run_automatic(self) -> None:
        records: list[ExcelTestRecord] = []
        try:
            deadline = time.monotonic() + 15.0
            while not self.automatic.acquisition_ready:
                if self._automatic_stop.wait(0.05):
                    raise RuntimeError("自动测试已停止")
                if time.monotonic() >= deadline:
                    raise TimeoutError("设备启动超时")
            settings = self.automatic.settings
            if settings is None:
                raise RuntimeError("自动测试配置不可用")

            for index, current_a in enumerate(self.automatic.currents):
                self.automatic.current_index = index
                self._automatic_resume.wait()
                if self._automatic_stop.is_set():
                    break
                self.automatic.set_state(AutomaticTestState.SETTING_CURRENT)
                self.automatic_detail = f"正在设置 {current_a:g} A"
                self.set_current({"currentA": current_a})
                self.automatic.set_state(AutomaticTestState.WAITING_STABLE)
                point_deadline = time.monotonic() + settings.point_timeout_s
                stable_reading: PowerMeterReading | None = None
                while time.monotonic() < point_deadline:
                    if self._automatic_stop.is_set():
                        break
                    self._automatic_resume.wait()
                    reading = self.latest_power
                    spectrum_ok = (
                        not settings.use_spectrometer
                        or (
                            self.latest_spectrum is not None
                            and not bool(getattr(self.latest_spectrum.saturation, "saturated", False))
                        )
                    )
                    if reading is not None and reading.stable and spectrum_ok:
                        stable_reading = reading
                        break
                    time.sleep(0.05)
                if self._automatic_stop.is_set():
                    break
                if stable_reading is None:
                    raise TimeoutError(f"{current_a:g} A 测试点等待稳定超时")

                dwell_s = self.automatic.current_dwell_s
                self.automatic_hold_started_at = time.monotonic()
                if dwell_s > 0.0 and self._automatic_stop.wait(dwell_s):
                    break

                self.automatic.set_state(AutomaticTestState.WAITING_VOLTAGE)
                voltage_v = self._require_power_supply().read_output_voltage()
                efficiency = (
                    stable_reading.power_w / current_a / voltage_v * 100.0
                    if current_a > 0.0 and voltage_v > 0.0
                    else math.nan
                )
                frame = self.latest_spectrum
                stats = None if frame is None else frame.stats
                record = ExcelTestRecord(
                    current_a=current_a,
                    voltage_v=voltage_v,
                    power_w=stable_reading.power_w,
                    efficiency=efficiency,
                    peak_wavelength_nm=math.nan if stats is None else stats.peak_wavelength_nm,
                    centroid_nm=math.nan if stats is None else stats.centroid_nm,
                    fwhm_nm=math.nan if stats is None else stats.fwhm_nm,
                    pib=(
                        math.nan
                        if frame is None
                        else calculate_pib(
                            frame.wavelength,
                            frame.intensity,
                            center_nm=float(self.configuration.get("pibCenterWavelengthNm", 976.0)),
                            half_range_nm=float(self.configuration.get("pibHalfRangeNm", 1.5)),
                        )
                    ),
                    wavelength=[] if frame is None else list(frame.wavelength),
                    intensity=[] if frame is None else list(frame.intensity),
                    smsr_db=math.nan if frame is None or frame.smsr is None else frame.smsr.smsr_db,
                    test_station=str(self.configuration["station"]),
                )
                records.append(record)
                with self._lock:
                    self.stable_power_points[current_a] = stable_reading.power_w
                    self.efficiency_points[current_a] = efficiency
                    self.series_revisions["stable"] += 1

            if records and not self._automatic_stop.is_set():
                self.automatic.set_state(AutomaticTestState.SAVING_POINT)
                output = Path(str(self.configuration["outputDir"])).expanduser()
                path = build_test_workbook_path(
                    output,
                    str(self.configuration["sn"]),
                    datetime.now(),
                    str(self.configuration["station"]),
                )
                self.export_status = "exporting"
                # Submit to non-blocking sequential background export worker
                def on_export_done(target: str | None, err: Exception | None) -> None:
                    if err:
                        self.export_status = "failed"
                        self.notify("error", "导出失败", str(err))
                    else:
                        self.export_status = "completed"
                        self.export_path = str(target)
                        self.notify("info", "导出完成", f"测试记录已保存：{target}")

                task = ExportTask(
                    station_id=self.station_id,
                    target_path=path,
                    records=list(records),
                    callback=on_export_done,
                )
                if not self.backend.export_worker.submit(task):
                    # Fallback synchronous save if queue is full
                    save_test_records(path, records)
                    on_export_done(str(path), None)

            self.safe_ramp_down()
            self.automatic.complete()
            self.automatic_terminal_outcome = "succeeded" if not self._automatic_stop.is_set() else "stopped_by_operator"
            self.automatic_detail = "自动测试已安全完成"
        except Exception as exc:
            _LOGGER.exception("工位 %s 自动测试异常", self.station_id)
            self.automatic.pause(str(exc))
            self.automatic_detail = str(exc)
            self.automatic_terminal_outcome = "aborted_safely"
            self.automatic_terminal_reason = str(exc)
            try:
                self.safe_ramp_down()
            except Exception as shutdown_error:
                self.automatic_output_shutdown_unconfirmed = True
                self.notify("error", "安全下电失败", str(shutdown_error))

    def safe_ramp_down(self) -> None:
        if self.power_supply_identity_error:
            raise RuntimeError("CH341 端口身份校验失败，禁止下发电流指令；请先安全断开")
        supply = self._power_supply()
        if supply is None:
            return
        settings = self.automatic.settings or self._automatic_settings()
        start = max(0.0, float(self.active_current_a or 0.0))
        self.automatic.set_state(AutomaticTestState.RAMPING_DOWN)
        for current_a in build_ramp_down_currents(start, settings.ramp_down_step_a, maximum_current_a=settings.maximum_current_a):
            supply.set_current(current_a)
            self.active_current_a = current_a
            if current_a > 0.0:
                time.sleep(settings.ramp_down_interval_s)
        supply.set_current(0.0)
        self.active_current_a = 0.0
        self.configuration["setCurrentA"] = 0.0
        self.automatic_output_shutdown_unconfirmed = False

    def pause_automatic(self) -> None:
        if self.automatic.state in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED, AutomaticTestState.PAUSED}:
            raise ValueError("当前状态不能暂停")
        self.automatic.pause("操作员暂停")
        self._automatic_resume.clear()

    def resume_automatic(self) -> None:
        if self.automatic.state != AutomaticTestState.PAUSED:
            raise ValueError("当前状态不能重试")
        self.automatic.set_state(self.automatic.paused_from_state)
        self.automatic.pause_reason = ""
        self._automatic_resume.set()

    def end_automatic(self) -> None:
        self._automatic_stop.set()
        self._automatic_resume.set()
        thread = self._automatic_thread
        if thread is not None:
            thread.join(15.0)

    def reset_automatic(self) -> None:
        if self._automatic_thread is not None and self._automatic_thread.is_alive():
            raise ValueError("自动测试运行中，不能重置")
        self.automatic = AutomaticTestOrchestrator()
        self.automatic_detail = "准备测试"
        self.automatic_terminal_outcome = None
        self.automatic_terminal_reason = ""
        self._test_reference = None

    def reset_charts(self) -> None:
        with self._lock:
            self.power_points.clear()
            self.stable_power_points.clear()
            self.efficiency_points.clear()
            self.latest_spectrum = None
            for key in self.series_revisions:
                self.series_revisions[key] += 1

    def stop_all(self) -> None:
        self.end_automatic()
        self.stop_power_meter()
        self.stop_spectrometer()
        try:
            self.safe_ramp_down()
        except Exception:
            pass

    def _history_records(self, params: dict[str, Any]) -> tuple[Any, ...]:
        from combined_test.test_history import find_test_history

        output = str(params.get("outputDir", self.configuration["outputDir"])).strip()
        sn = str(params.get("sn", self.configuration["sn"])).strip()
        return find_test_history(output, sn) if output and sn else ()

    def _select_test_reference(self, params: dict[str, Any]) -> None:
        selection = str(params.get("referencePath", "latest")).strip()
        if selection == "none":
            self._test_reference = None
            return
        records = self._history_records(params)
        if selection in {"", "latest"}:
            self._test_reference = records[0] if records else None
            return
        selected = next((r for r in records if str(r.inspection.source_path) == selection), None)
        if selected is None:
            raise ValueError("选择的参考记录不属于当前壳体")
        self._test_reference = selected

    def summary_snapshot(self) -> dict[str, Any]:
        supply = self._power_supply()
        currents = list(self.automatic.currents)
        current_index = int(self.automatic.current_index)
        progress = max(0.0, min(1.0, (current_index + 1) / len(currents))) if currents else 0.0
        return {
            "stationId": self.station_id,
            "station": str(self.configuration.get("station", f"工位 {self.station_id}")),
            "sn": str(self.configuration.get("sn", "")),
            "productModel": str(self.configuration.get("productModel", "")),
            "batch": str(self.configuration.get("batch", "")),
            "state": self.automatic.state.value,
            "detail": self.automatic_detail,
            "progress": progress,
            "activeCurrentA": _finite(self.active_current_a),
            "powerW": _finite(None if self.latest_power is None else self.latest_power.power_w),
            "powerSupplyConnected": supply is not None,
            "powerMeterRunning": self.power_meter_reader is not None,
            "spectrometerRunning": self.spectrometer_reader is not None,
            "canStart": supply is not None and not self.power_supply_identity_error and float(self.active_current_a or 0.0) <= 0.0,
            "canPause": self.automatic.state not in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED, AutomaticTestState.PAUSED, AutomaticTestState.RAMPING_DOWN},
            "canRetry": self.automatic.state == AutomaticTestState.PAUSED,
            "canEnd": self.automatic.state not in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED, AutomaticTestState.RAMPING_DOWN},
            "terminalOutcome": self.automatic_terminal_outcome,
            "terminalReason": self.automatic_terminal_reason,
        }

    def build_snapshot_measurements(self, include_live: bool) -> dict[str, Any]:
        frame = self.latest_spectrum
        wavelength = [] if frame is None else [float(v) for v in frame.wavelength]
        intensity = [] if frame is None else [float(v) for v in frame.intensity]
        if wavelength != self._last_spectrum_grid:
            self._last_spectrum_grid = wavelength
            self.spectrum_grid_revision += 1

        measurements: dict[str, Any] = {}
        if include_live:
            measurements["power"] = list(self.power_points)
            measurements["stable"] = [
                {
                    "currentA": current,
                    "powerW": power,
                    "efficiencyPercent": self.efficiency_points.get(current),
                }
                for current, power in sorted(self.stable_power_points.items())
            ]
            measurements["spectrum"] = {
                "gridRevision": self.spectrum_grid_revision,
                "wavelengthNm": wavelength,
                "intensity": intensity,
            }
            measurements["spectrumPeaks"] = [
                {
                    "label": item.label,
                    "centroidNm": _finite(item.centroid_nm),
                    "peakWavelengthNm": _finite(item.peak_wavelength_nm),
                    "peakIntensity": _finite(item.peak_intensity),
                }
                for item in (() if frame is None else frame.peak_annotations)
            ]
        return measurements


class HeadlessBackend:
    """Own dual-station hardware, automation, and serializable application state."""

    mode = "active"

    def __init__(self, settings: JsonSettings | None = None) -> None:
        self._lock = threading.RLock()
        self.settings = settings or JsonSettings()
        stored = self.settings.get("runtime/configuration", {})
        stored_dict = stored if isinstance(stored, dict) else {}

        # Shared export queue worker
        self.export_worker = ExportQueueWorker()
        self.export_worker.start()

        # Shared device discovery state
        self.power_supply_resources: list[str] = []
        self.ch341_discovery_state: Literal["not_scanned", "ready", "empty", "error"] = "not_scanned"
        self.ch341_scan_error: str = ""
        self.ch341_descriptors: list[Ch341DeviceDescriptor] = []
        self.power_meter_options: list[PowerMeterOption] = []
        self.power_meter_detect_thread: PowerMeterDetectThread | None = None
        self.spectrometer_resources: list[dict[str, Any]] = []

        # Initialize Stations
        station_configs = self.settings.get("runtime/stations", {})
        raw_st1_config = station_configs.get("1", stored_dict) if isinstance(station_configs, dict) else stored_dict
        raw_st2_config = station_configs.get("2", {}) if isinstance(station_configs, dict) else {}
        st1_config = dict(raw_st1_config) if isinstance(raw_st1_config, dict) else {}
        st2_config = dict(raw_st2_config) if isinstance(raw_st2_config, dict) else {}

        for key in (
            "ch341SelectedLocationPath",
            "tdkResource",
            "powerMeterResource",
            "spectrometerResource",
            "spectrometerDeviceId",
        ):
            station_one_resource = st1_config.get(key)
            if station_one_resource not in {None, ""} and st2_config.get(key) == station_one_resource:
                st2_config[key] = None if key == "spectrometerDeviceId" else ""

        self.stations: dict[str, StationRuntime] = {
            "1": StationRuntime("1", self, st1_config),
            "2": StationRuntime("2", self, st2_config),
        }

        self.notices: deque[dict[str, str]] = deque(maxlen=8)
        self._notice_sequence = 0
        self.status_message = "后台已就绪"

        self.pd_state: dict[str, Any] = {
            "state": "idle",
            "status": "未启动",
            "devices": [],
            "channels": [],
            "ranges": [],
            "settings": {
                "device": "",
                "channel": "",
                "terminal": "DIFF",
                "range": 10.0,
                "sampleRateHz": 1_000.0,
                "blockSize": 100,
                "scale": 1.0,
                "offset": 0.0,
                "unit": "V",
                "save": True,
                "outputDir": "",
            },
            "points": [],
            "currentValue": "--",
            "voltage": "--",
            "mean": "--",
            "standardDeviation": "--",
            "rangeText": "--",
            "sampleCount": "0",
        }
        self._pd_devices: list[Any] = []
        self._pd_reader: Any | None = None
        self.series_revisions = {"pd": 0}

    def _station(self, station_id: str | None = None) -> StationRuntime:
        key = str(station_id) if station_id is not None else "1"
        return self.stations.get(key, self.stations["1"])

    # Forwarding properties for Station 1 backwards compatibility
    @property
    def configuration(self) -> dict[str, Any]:
        return self.stations["1"].configuration

    @configuration.setter
    def configuration(self, value: dict[str, Any]) -> None:
        self.stations["1"].configuration = value

    @property
    def power_supply_controller(self) -> Any | None:
        return self.stations["1"].power_supply_controller

    @power_supply_controller.setter
    def power_supply_controller(self, value: Any | None) -> None:
        self.stations["1"].power_supply_controller = value

    @property
    def power_supply_kind(self) -> str:
        return self.stations["1"].power_supply_kind

    @power_supply_kind.setter
    def power_supply_kind(self, value: str) -> None:
        self.stations["1"].power_supply_kind = value

    @property
    def active_current_a(self) -> float | None:
        return self.stations["1"].active_current_a

    @active_current_a.setter
    def active_current_a(self, value: float | None) -> None:
        self.stations["1"].active_current_a = value

    @property
    def latest_spectrum(self) -> SpectrumFrame | None:
        return self.stations["1"].latest_spectrum

    @latest_spectrum.setter
    def latest_spectrum(self, value: SpectrumFrame | None) -> None:
        self.stations["1"].latest_spectrum = value

    @property
    def power_points(self) -> deque[dict[str, float]]:
        return self.stations["1"].power_points

    @property
    def stable_power_points(self) -> dict[float, float]:
        return self.stations["1"].stable_power_points

    @property
    def efficiency_points(self) -> dict[float, float]:
        return self.stations["1"].efficiency_points

    @property
    def automatic(self) -> AutomaticTestOrchestrator:
        return self.stations["1"].automatic

    @property
    def automatic_detail(self) -> str:
        return self.stations["1"].automatic_detail

    @automatic_detail.setter
    def automatic_detail(self, value: str) -> None:
        self.stations["1"].automatic_detail = value

    @property
    def automatic_output_shutdown_unconfirmed(self) -> bool:
        return any(st.automatic_output_shutdown_unconfirmed for st in self.stations.values())

    @automatic_output_shutdown_unconfirmed.setter
    def automatic_output_shutdown_unconfirmed(self, value: bool) -> None:
        self.stations["1"].automatic_output_shutdown_unconfirmed = value

    @property
    def power_meter_reader(self) -> PowerMeterReaderThread | None:
        return self.stations["1"].power_meter_reader

    @power_meter_reader.setter
    def power_meter_reader(self, value: PowerMeterReaderThread | None) -> None:
        self.stations["1"].power_meter_reader = value

    @property
    def spectrometer_reader(self) -> SpectrometerReaderThread | None:
        return self.stations["1"].spectrometer_reader

    @spectrometer_reader.setter
    def spectrometer_reader(self, value: SpectrometerReaderThread | None) -> None:
        self.stations["1"].spectrometer_reader = value

    def check_device_conflicts(self, target_station_id: str, device_type: str, resource_identifier: Any) -> None:
        """Prevent Station 1 and Station 2 from binding to the same physical hardware."""
        for other_id, other_st in self.stations.items():
            if other_id == target_station_id:
                continue
            if device_type == "ch341":
                other_loc = other_st.configuration.get("ch341SelectedLocationPath")
                if other_st.power_supply_controller is not None and other_loc == resource_identifier:
                    raise RuntimeError(f"CH341 端口 {resource_identifier} 正在被工位 {other_id} 使用")
            elif device_type == "tdk":
                other_res = other_st.configuration.get("tdkResource")
                if other_st.power_supply_controller is not None and other_res == resource_identifier:
                    raise RuntimeError(f"TDK 电源 {resource_identifier} 正在被工位 {other_id} 使用")
            elif device_type == "powerMeter":
                other_res = other_st.configuration.get("powerMeterResource")
                if other_st.power_meter_reader is not None and other_res == resource_identifier:
                    raise RuntimeError(f"功率计 {resource_identifier} 正在被工位 {other_id} 使用")
            elif device_type == "spectrometer":
                other_dev = other_st.configuration.get("spectrometerDeviceId")
                if other_st.spectrometer_reader is not None and other_dev == resource_identifier:
                    raise RuntimeError(f"光谱仪设备 ID {resource_identifier} 正在被工位 {other_id} 使用")

    def notify(self, level: str, title: str, message: str) -> None:
        with self._lock:
            self._notice_sequence += 1
            self.notices.append(
                {
                    "id": str(self._notice_sequence),
                    "level": str(level),
                    "title": str(title),
                    "message": str(message),
                }
            )
            self.status_message = str(message)

    def set_status(self, message: str) -> None:
        self.status_message = str(message)

    def _persist_configuration(self) -> None:
        self.settings.set("runtime/configuration", dict(self.stations["1"].configuration))
        self.settings.set(
            "runtime/stations",
            {sid: dict(st.configuration) for sid, st in self.stations.items()},
        )

    def ping(self) -> dict[str, Any]:
        return {"status": "ok", "mode": self.mode}

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        station_id = str(params.get("stationId", params.get("station", "1")))
        st = self._station(station_id)

        actions: dict[str, Callable[[dict[str, Any]], Any]] = {
            "app.configure": lambda p: self._configure(p),
            "device.refresh": self._refresh_device,
            "powerSupply.connect": lambda p: self._station(p.get("stationId", p.get("station", "1"))).connect_power_supply(),
            "powerSupply.disconnect": lambda p: self._station(p.get("stationId", p.get("station", "1"))).disconnect_power_supply(),
            "powerSupply.setCurrent": lambda p: self._station(p.get("stationId", p.get("station", "1"))).set_current(p),
            "powerSupply.setVoltage": lambda p: self._station(p.get("stationId", p.get("station", "1"))).set_voltage(p),
            "powerSupply.setOutput": lambda p: self._station(p.get("stationId", p.get("station", "1"))).set_output(p),
            "powerSupply.read": lambda p: self._station(p.get("stationId", p.get("station", "1"))).read_power_supply(p),
            "powerMeter.start": lambda p: self._station(p.get("stationId", p.get("station", "1"))).start_power_meter(),
            "powerMeter.stop": lambda p: self._station(p.get("stationId", p.get("station", "1"))).stop_power_meter(),
            "powerMeter.setRelativeZero": lambda p: self._station(p.get("stationId", p.get("station", "1"))).set_relative_zero(p),
            "spectrometer.start": lambda p: self._station(p.get("stationId", p.get("station", "1"))).start_spectrometer(),
            "spectrometer.stop": lambda p: self._station(p.get("stationId", p.get("station", "1"))).stop_spectrometer(),
            "spectrometer.saveCsv": lambda p: self._station(p.get("stationId", p.get("station", "1"))).save_spectrum_csv(p),
            "automatic.start": lambda p: self._station(p.get("stationId", p.get("station", "1"))).start_automatic(p),
            "automatic.references": lambda p: self._list_automatic_references(p),
            "automatic.pause": lambda p: self._station(p.get("stationId", p.get("station", "1"))).pause_automatic(),
            "automatic.retry": lambda p: self._station(p.get("stationId", p.get("station", "1"))).resume_automatic(),
            "automatic.end": lambda p: self._station(p.get("stationId", p.get("station", "1"))).end_automatic(),
            "automatic.reset": lambda p: self._station(p.get("stationId", p.get("station", "1"))).reset_automatic(),
            "shippingReport.inspect": self._inspect_shipping_report,
            "shippingReport.batchInspect": self._inspect_shipping_report_batch,
            "shippingReport.batchGenerateItem": self._generate_shipping_report_batch_item,
            "shippingReport.preview": self._preview_shipping_report,
            "shippingReport.generate": self._generate_shipping_report,
            "pd.refresh": lambda _p: self._refresh_pd(),
            "pd.configure": self._configure_pd,
            "pd.start": self._start_pd,
            "pd.stop": lambda _p: self._stop_pd(),
            "charts.reset": lambda p: self._station(p.get("stationId", p.get("station", "1"))).reset_charts(),
            "app.stopAll": lambda _p: self._stop_all(),
            "app.shutdown": lambda _p: self._shutdown(),
        }
        action = actions.get(method)
        if action is None:
            raise KeyError(method)
        result = action(params)
        if method not in {"automatic.references", "shippingReport.inspect", "shippingReport.batchInspect"}:
            self._persist_configuration()
        return self.snapshot(params) if result is None else result

    def dispatch_file_task(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "automatic.references":
            return self._list_automatic_references(params)
        if method == "shippingReport.inspect":
            return self._inspect_shipping_report(params)
        if method == "shippingReport.batchInspect":
            return self._inspect_shipping_report_batch(params)
        raise KeyError(method)

    def _configure(self, params: dict[str, Any]) -> None:
        target_station_id = str(params.get("stationId", params.get("station", "1")))
        if target_station_id in self.stations:
            self.stations[target_station_id].configure(params)
        else:
            for st in self.stations.values():
                st.configure(params)

    def scan_ch341(self) -> None:
        try:
            self.ch341_descriptors = scan_ch341_devices()
            self.ch341_discovery_state = "ready" if self.ch341_descriptors else "empty"
            self.ch341_scan_error = ""
        except Exception as exc:
            self.ch341_discovery_state = "error"
            self.ch341_scan_error = str(exc)
            self.ch341_descriptors = []

    def _refresh_device(self, params: dict[str, Any]) -> None:
        device = str(params.get("device", "all"))
        if device in {"all", "powerSupply"}:
            self.power_supply_resources = list_tdk_serial_resources()
            self.scan_ch341()

        if device in {"all", "powerMeter"}:
            self._refresh_power_meter(params)

        if device in {"all", "spectrometer"}:
            self._refresh_spectrometer()

    def _refresh_power_meter(self, params: dict[str, Any]) -> None:
        if self.power_meter_detect_thread is not None:
            return
        preferred = str(params.get("preferredResource", self.stations["1"].configuration["powerMeterResource"]))
        thread = PowerMeterDetectThread(preferred_resource=preferred)
        self.power_meter_detect_thread = thread

        def on_detected(options: list[PowerMeterOption]) -> None:
            self.power_meter_options = list(options)
            for st in self.stations.values():
                if options and not st.configuration["powerMeterResource"]:
                    st.configuration["powerMeterResource"] = options[0].resource

        def on_finished() -> None:
            self.power_meter_detect_thread = None

        thread.detected.connect(on_detected)
        thread.status.connect(lambda msg: self.set_status(msg))
        thread.failed.connect(lambda msg: self.notify("error", "功率计扫描", msg))
        thread.finished.connect(on_finished)
        thread.start()

    def _refresh_spectrometer(self) -> None:
        spectrometer_class, _ = load_spectrometer_components(None)
        device_ids = [int(value) for value in spectrometer_class.detect()]
        self.spectrometer_resources = [
            {"label": f"Ocean Insight | 设备 ID {device_id}", "deviceId": device_id}
            for device_id in device_ids
        ]

    def _set_current(self, params: dict[str, Any]) -> None:
        st = self._station(params.get("stationId", params.get("station", "1")))
        st.set_current(params)

    def _list_automatic_references(self, params: dict[str, Any]) -> dict[str, Any]:
        st = self._station(params.get("stationId", params.get("station", "1")))
        records = st._history_records(params)
        return {"items": [record.summary_payload() for record in records]}

    def _report_module(self) -> Any:
        from combined_test import shipping_report
        return shipping_report

    def _inspect_shipping_report(self, params: dict[str, Any]) -> dict[str, Any]:
        source = str(params.get("path", "")).strip()
        if not source:
            raise ValueError("请选择 Excel 测试文件")
        report = self._report_module()
        payload = report.inspect_shipping_workbook(source).to_payload()
        payload["preferences"] = report.load_shipping_report_preferences(self.settings)
        return payload

    def _inspect_shipping_report_batch(self, params: dict[str, Any]) -> dict[str, Any]:
        root = str(params.get("rootPath", "")).strip()
        if not root:
            raise ValueError("请选择 Excel 批量根目录")
        report = self._report_module()
        payload = report.inspect_shipping_report_batch(root).to_payload()
        payload["preferences"] = report.load_shipping_report_preferences(self.settings)
        return payload

    def _generate_shipping_report(self, params: dict[str, Any]) -> dict[str, Any]:
        report = self._report_module()
        request = report.ShippingReportRequest.from_mapping(params)
        target = report.generate_shipping_report(params["sourcePath"], params["outputPath"], request)
        report.save_shipping_report_preferences(self.settings, request)
        return {"outputPath": str(target)}

    def _generate_shipping_report_batch_item(self, params: dict[str, Any]) -> dict[str, Any]:
        report = self._report_module()
        target, request = report.generate_shipping_report_batch_item(
            params["sourcePath"], params["outputDirectory"], params
        )
        report.save_shipping_report_preferences(self.settings, request)
        return {"outputPath": str(target), "sourcePath": str(params["sourcePath"]), "sn": request.sn}

    def _preview_shipping_report(self, params: dict[str, Any]) -> dict[str, Any]:
        report = self._report_module()
        request = report.ShippingReportRequest.from_mapping(params)
        return {"pages": report.render_shipping_report_preview(params["sourcePath"], request)}

    def _refresh_pd(self) -> None:
        try:
            from tools.pd_daq_mvp import channels_for_terminal_mode, discover_ni_daq_devices

            driver, devices = discover_ni_daq_devices()
            self._pd_devices = list(devices)
            self.pd_state["status"] = driver
            self.pd_state["devices"] = [device.label() for device in devices]
            if devices:
                selected = devices[0]
                terminal = str(self.pd_state["settings"]["terminal"])
                self.pd_state["settings"]["device"] = selected.label()
                self.pd_state["channels"] = list(channels_for_terminal_mode(selected, terminal))
                self.pd_state["ranges"] = [
                    {"label": f"±{value:g} V", "value": value}
                    for value in selected.voltage_ranges
                ]
                if self.pd_state["channels"] and not self.pd_state["settings"]["channel"]:
                    self.pd_state["settings"]["channel"] = self.pd_state["channels"][0]
        except Exception as exc:
            self.pd_state["status"] = str(exc)
            self.notify("error", "PD 采集", str(exc))

    def _configure_pd(self, params: dict[str, Any]) -> None:
        for key in self.pd_state["settings"]:
            if key in params:
                self.pd_state["settings"][key] = params[key]

    def _start_pd(self, params: dict[str, Any]) -> None:
        self._configure_pd(params)
        if self._pd_reader is not None:
            return
        if not self._pd_devices:
            self._refresh_pd()
        if not self._pd_devices:
            raise RuntimeError("未检测到 NI-DAQmx 设备")
        from tools.pd_daq_mvp import (
            PdAcquisitionThread,
            PdDaqSettings,
            default_csv_path,
        )

        values = self.pd_state["settings"]
        requested_device = str(values["device"])
        device = next(
            (item for item in self._pd_devices if item.label() == requested_device or item.name == requested_device),
            self._pd_devices[0],
        )
        channel = str(values["channel"]).strip()
        if not channel:
            channel = device.ai_channels[0]
        save_path = None
        if bool(values["save"]):
            output_dir = Path(str(values["outputDir"] or self.stations["1"].configuration["outputDir"])).expanduser()
            save_path = default_csv_path(output_dir, channel)
        settings = PdDaqSettings(
            channel=channel,
            terminal_mode=str(values["terminal"]),
            voltage_range_v=float(values["range"]),
            sample_rate_hz=float(values["sampleRateHz"]),
            block_size=int(values["blockSize"]),
            scale=float(values["scale"]),
            offset=float(values["offset"]),
            unit=str(values["unit"]),
            save_path=save_path,
        )
        settings.validate(device.max_single_channel_rate_hz)
        reader = PdAcquisitionThread(settings)
        self._pd_reader = reader
        reader.block_ready.connect(self._on_pd_block)
        reader.acquisition_ready.connect(self._on_pd_ready)
        reader.acquisition_failed.connect(self._on_pd_failed)
        reader.recording_stopped.connect(self._on_pd_recording_stopped)
        reader.finished.connect(self._on_pd_finished)
        self.pd_state["state"] = "running"
        self.pd_state["status"] = "正在启动"
        reader.start()

    def _on_pd_block(self, block: Any) -> None:
        points = self.pd_state["points"]
        points.extend(
            {"elapsedS": float(elapsed), "value": float(value)}
            for elapsed, value in zip(block.plot_times_s, block.plot_values)
        )
        if len(points) > 13_000:
            del points[: len(points) - 13_000]
        calibrated = block.calibrated
        voltage = block.voltage
        self.pd_state["currentValue"] = f"{calibrated.latest:.6g} {self.pd_state['settings']['unit']}"
        self.pd_state["voltage"] = f"{voltage.latest:.6g} V"
        self.pd_state["mean"] = f"{calibrated.mean:.6g}"
        self.pd_state["standardDeviation"] = f"{calibrated.standard_deviation:.6g}"
        self.pd_state["rangeText"] = f"{calibrated.minimum:.6g} – {calibrated.maximum:.6g}"
        self.pd_state["sampleCount"] = str(block.sample_count)
        self.series_revisions["pd"] += 1

    def _on_pd_ready(self, channel: str) -> None:
        self.pd_state["state"] = "running"
        self.pd_state["status"] = f"正在采集 {channel}"

    def _on_pd_failed(self, message: str) -> None:
        self.pd_state["state"] = "error"
        self.pd_state["status"] = str(message)
        self.notify("error", "PD 采集", message)

    def _on_pd_recording_stopped(self, path: str) -> None:
        if path:
            self.pd_state["status"] = f"记录已保存：{path}"

    def _on_pd_finished(self) -> None:
        self._pd_reader = None
        if self.pd_state["state"] != "error":
            self.pd_state["state"] = "idle"

    def _stop_pd(self) -> None:
        reader = self._pd_reader
        if reader is not None:
            reader.stop()
            reader.wait(5_000)
        self.pd_state["state"] = "idle"
        self.pd_state["status"] = "已停止"

    def _stop_all(self) -> None:
        for st in self.stations.values():
            st.stop_all()
        self._stop_pd()

    def _shutdown(self) -> dict[str, bool]:
        self._stop_all()
        self.export_worker.stop()
        for st in self.stations.values():
            supply = st._power_supply()
            if supply is not None:
                if st.power_supply_kind == "tdk" and supply.output_enabled:
                    supply.set_output_enabled(False)
                measured = supply.read_output_current()
                if not math.isfinite(measured) or abs(measured) > 0.01:
                    st.automatic_output_shutdown_unconfirmed = True
                    raise RuntimeError(f"无法确认电源输出电流已归零（工位 {st.station_id}）")
        return {"shutdownConfirmed": True}

    def snapshot(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked(params or {})

    def _snapshot_locked(self, params: dict[str, Any]) -> dict[str, Any]:
        view = str(params.get("view", "full"))
        active_station_id = "2" if view in {"automatic_2", "manual_2"} else str(params.get("stationId", params.get("station", "1")))
        active_st = self._station(active_station_id)

        include_live = view in {"automatic", "automatic_1", "automatic_2", "manual", "manual_1", "manual_2", "full"}
        include_pd = view in {"pd", "full"}

        supply = active_st._power_supply()
        frame = active_st.latest_spectrum
        spectrum_saturated = bool(frame and frame.saturation and frame.saturation.saturated)
        spectrum_stats = None if frame is None else frame.stats

        measurements = active_st.build_snapshot_measurements(include_live)

        # CH341 device ports map
        bindings_map = {
            b["locationPath"]: b["name"]
            for b in active_st.configuration.get("ch341PortBindings", [])
            if isinstance(b, dict) and "locationPath" in b
        }
        online_map = {d.location_path: d.device_index for d in self.ch341_descriptors}
        all_paths = list(dict.fromkeys(list(bindings_map.keys()) + list(online_map.keys())))
        ports = [
            {
                "locationPath": loc,
                "name": bindings_map.get(loc),
                "online": loc in online_map,
                "deviceIndex": online_map.get(loc),
            }
            for loc in all_paths
        ]
        ch341_snapshot = {
            "discoveryState": self.ch341_discovery_state,
            "error": self.ch341_scan_error,
            "ports": ports,
            "connectedPort": active_st.connected_ch341_port,
        }

        if active_st.power_supply_identity_error:
            power_supply_detail = active_st.power_supply_identity_error
        elif supply is not None and active_st.power_supply_kind == "ch341" and active_st.connected_ch341_port:
            power_supply_detail = (
                f"{active_st.connected_ch341_port['name']} · 已连接 · "
                f"Device {active_st.connected_ch341_port['deviceIndex']}"
            )
        else:
            power_supply_detail = "已连接" if supply is not None else "未连接"

        currents = list(active_st.automatic.currents)
        current_index = int(active_st.automatic.current_index)
        progress = max(0.0, min(1.0, (current_index + 1) / len(currents))) if currents else 0.0
        dwell_elapsed = None
        if active_st.automatic_hold_started_at is not None and active_st.automatic.current_dwell_s > 0.0:
            dwell_elapsed = min(
                active_st.automatic.current_dwell_s,
                max(0.0, time.monotonic() - active_st.automatic_hold_started_at),
            )

        merged_revisions = {
            "power": active_st.series_revisions["power"],
            "stable": active_st.series_revisions["stable"],
            "spectrum": active_st.series_revisions["spectrum"],
            "pd": self.series_revisions["pd"],
        }

        stations_summary = {sid: st.summary_snapshot() for sid, st in self.stations.items()}

        return {
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "seriesRevisions": merged_revisions,
            "activeStation": active_station_id,
            "stations": stations_summary,
            "backend": {
                "connected": True,
                "mode": self.mode,
                "pythonVersion": platform.python_version(),
                "runtimeLogPath": str(runtime_log_path() or ""),
                "notices": list(self.notices),
            },
            "configuration": dict(active_st.configuration),
            "configurations": {sid: dict(st.configuration) for sid, st in self.stations.items()},
            "devices": {
                "powerSupply": {
                    "state": _device_state(supply is not None, False, active_st.power_supply_identity_error),
                    "label": "TDK RS232" if active_st.power_supply_kind == "tdk" else "CH341 I²C",
                    "detail": power_supply_detail,
                    "connected": supply is not None,
                    "outputEnabled": bool(supply and supply.output_enabled),
                    "activeCurrentA": _finite(active_st.active_current_a),
                    "resources": list(self.power_supply_resources),
                    "ch341": ch341_snapshot,
                },
                "powerMeter": {
                    "state": "connecting" if self.power_meter_detect_thread is not None else _device_state(False, active_st.power_meter_reader is not None, active_st.power_meter_error),
                    "label": "功率计",
                    "detail": active_st.power_meter_error or ("采集中" if active_st.power_meter_reader else "已停止"),
                    "running": active_st.power_meter_reader is not None,
                    "detecting": self.power_meter_detect_thread is not None,
                    "ready": bool(active_st.power_meter_reader and active_st.power_meter_reader.is_ready),
                    "powerW": _finite(None if active_st.latest_power is None else active_st.latest_power.power_w),
                    "stable": bool(active_st.latest_power and active_st.latest_power.stable),
                    "resources": [item.label() for item in self.power_meter_options],
                },
                "spectrometer": {
                    "state": _device_state(False, active_st.spectrometer_reader is not None, active_st.spectrometer_error),
                    "label": "Ocean Insight",
                    "detail": active_st.spectrometer_error or ("采集中" if active_st.spectrometer_reader else "已停止"),
                    "running": active_st.spectrometer_reader is not None,
                    "ready": bool(active_st.spectrometer_reader and active_st.spectrometer_reader.is_ready),
                    "peakWavelengthNm": None if spectrum_saturated else _finite(getattr(spectrum_stats, "peak_wavelength_nm", None)),
                    "centroidNm": None if spectrum_saturated else _finite(getattr(spectrum_stats, "centroid_nm", None)),
                    "fwhmNm": None if spectrum_saturated else _finite(getattr(spectrum_stats, "fwhm_nm", None)),
                    "pib": None if frame is None or not math.isfinite(getattr(frame, "pib", math.nan)) else _finite(frame.pib * 100.0 if frame.pib <= 1.0 else frame.pib),
                    "smsrDb": None if frame is None or frame.smsr is None else _finite(frame.smsr.smsr_db),
                    "saturated": spectrum_saturated,
                    "spectrumWindow": None,
                    "resources": [item["label"] for item in self.spectrometer_resources],
                },
            },
            "automaticTest": {
                "state": active_st.automatic.state.value,
                "detail": active_st.automatic_detail,
                "controlsEnabled": True,
                "canStart": supply is not None and not active_st.power_supply_identity_error and float(active_st.active_current_a or 0.0) <= 0.0,
                "canRetry": active_st.automatic.state == AutomaticTestState.PAUSED,
                "canPause": active_st.automatic.state not in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED, AutomaticTestState.PAUSED, AutomaticTestState.RAMPING_DOWN},
                "canEnd": active_st.automatic.state not in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED, AutomaticTestState.RAMPING_DOWN},
                "settingsError": "",
                "currents": currents,
                "dwellTimesS": list(active_st.automatic.dwell_times_s),
                "currentIndex": current_index,
                "currentA": _finite(active_st.automatic.current_a),
                "currentDwellS": active_st.automatic.current_dwell_s,
                "currentDwellElapsedS": dwell_elapsed,
                "samplePath": active_st.automatic_sample_path,
                "progress": progress,
                "pauseReason": active_st.automatic.pause_reason,
                "terminalOutcome": active_st.automatic_terminal_outcome,
                "terminalReason": active_st.automatic_terminal_reason,
                "reference": (
                    active_st._test_reference.reference_payload()
                    if active_st._test_reference is not None
                    else None
                ),
            },
            "measurements": measurements,
            "pd": {
                **{key: value for key, value in self.pd_state.items() if key != "points"},
                **({"points": list(self.pd_state["points"])} if include_pd else {}),
            },
            "safety": {
                "hardwareAccess": True,
                "commandMode": "controller_owned",
                "detail": "设备命令、自动流程和安全下电由无界面 Python 后台执行",
                "outputShutdownUnconfirmed": self.automatic_output_shutdown_unconfirmed,
            },
            "status": {"message": self.status_message},
        }
