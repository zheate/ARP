"""Pure-Python hardware backend for the Tauri sidecar.

This module deliberately contains no GUI toolkit imports.  Blocking vendor
drivers run on worker threads and publish immutable readings into a single
thread-safe state model consumed by the existing newline-delimited JSON bridge.
"""

from __future__ import annotations

import math
import platform
import threading
import time
from collections import deque
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
    "station": "",
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


class HeadlessBackend:
    """Own hardware, automation and serializable application state."""

    mode = "active"

    def __init__(self, settings: JsonSettings | None = None) -> None:
        self._lock = threading.RLock()
        self.settings = settings or JsonSettings()
        stored = self.settings.value("runtime/configuration", {})
        self.configuration = dict(DEFAULT_CONFIGURATION)
        if isinstance(stored, dict):
            self.configuration.update(
                {key: value for key, value in stored.items() if key in DEFAULT_CONFIGURATION}
            )

        self.notices: deque[dict[str, str]] = deque(maxlen=8)
        self._notice_sequence = 0
        self.status_message = "后台已就绪"

        self.power_supply_controller: Any | None = None
        self.power_supply_kind = str(self.configuration["powerSupplyKind"])
        self.active_current_a: float | None = 0.0
        self.power_supply_resources: list[str] = []
        self.ch341_discovery_state: Literal["not_scanned", "ready", "empty", "error"] = "not_scanned"
        self.ch341_scan_error: str = ""
        self.ch341_descriptors: list[Ch341DeviceDescriptor] = []
        self.connected_ch341_port: dict[str, Any] | None = None
        self.power_supply_identity_error = ""
        self.latest_tdk_current_a: float | None = None
        self.latest_tdk_voltage_v: float | None = None

        self.power_meter_detect_thread: PowerMeterDetectThread | None = None
        self.power_meter_reader: PowerMeterReaderThread | None = None
        self.power_meter_options: list[PowerMeterOption] = []
        self.power_meter_error = ""
        self.latest_power: PowerMeterReading | None = None
        self.power_points: deque[dict[str, float]] = deque(maxlen=1_200)

        self.spectrometer_reader: SpectrometerReaderThread | None = None
        self.spectrometer_resources: list[dict[str, Any]] = []
        self.spectrometer_error = ""
        self.latest_spectrometer_reading: SpectrometerReading | None = None
        self.latest_spectrum: SpectrumFrame | None = None
        self.spectrum_revision = 0
        self.spectrum_grid_revision = 0
        self._last_spectrum_grid: list[float] = []

        self.stable_power_points: dict[float, float] = {}
        self.efficiency_points: dict[float, float] = {}
        self.series_revisions = {"power": 0, "stable": 0, "spectrum": 0, "pd": 0}

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

        log_path = runtime_log_path()
        if log_path is not None:
            self.notify("info", "运行日志已启用", f"完整日志保存至：{log_path}")

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

    def _persist_configuration(self) -> None:
        self.settings.setValue("runtime/configuration", dict(self.configuration))

    def ping(self) -> dict[str, Any]:
        return {"status": "ok", "mode": self.mode}

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        actions: dict[str, Callable[[dict[str, Any]], Any]] = {
            "app.configure": self._configure,
            "device.refresh": self._refresh_device,
            "powerSupply.connect": lambda _p: self._connect_power_supply(),
            "powerSupply.disconnect": lambda _p: self._disconnect_power_supply(),
            "powerSupply.setCurrent": self._set_current,
            "powerSupply.setVoltage": self._set_voltage,
            "powerSupply.setOutput": self._set_output,
            "powerSupply.read": self._read_power_supply,
            "powerMeter.start": lambda _p: self._start_power_meter(),
            "powerMeter.stop": lambda _p: self._stop_power_meter(),
            "powerMeter.setRelativeZero": self._set_relative_zero,
            "spectrometer.start": lambda _p: self._start_spectrometer(),
            "spectrometer.stop": lambda _p: self._stop_spectrometer(),
            "spectrometer.saveCsv": self._save_spectrum_csv,
            "automatic.start": self._start_automatic,
            "automatic.references": self._list_automatic_references,
            "automatic.pause": lambda _p: self._pause_automatic(),
            "automatic.retry": lambda _p: self._resume_automatic(),
            "automatic.end": lambda _p: self._end_automatic(),
            "automatic.reset": lambda _p: self._reset_automatic(),
            "shippingReport.inspect": self._inspect_shipping_report,
            "shippingReport.batchInspect": self._inspect_shipping_report_batch,
            "shippingReport.batchGenerateItem": self._generate_shipping_report_batch_item,
            "shippingReport.preview": self._preview_shipping_report,
            "shippingReport.generate": self._generate_shipping_report,
            "pd.refresh": lambda _p: self._refresh_pd(),
            "pd.configure": self._configure_pd,
            "pd.start": self._start_pd,
            "pd.stop": lambda _p: self._stop_pd(),
            "charts.reset": lambda _p: self._reset_charts(),
            "app.stopAll": lambda _p: self._stop_all(),
            "app.shutdown": lambda _p: self._shutdown(),
        }
        action = actions.get(method)
        if action is None:
            raise KeyError(method)
        result = action(params)
        if method not in {"automatic.references", "shippingReport.inspect", "shippingReport.batchInspect"}:
            self._persist_configuration()
        return self.snapshot() if result is None else result

    def dispatch_file_task(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "automatic.references":
            return self._list_automatic_references(params)
        if method == "shippingReport.inspect":
            return self._inspect_shipping_report(params)
        if method == "shippingReport.batchInspect":
            return self._inspect_shipping_report_batch(params)
        raise KeyError(method)

    def _configure(self, params: dict[str, Any]) -> None:
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

            if self.power_supply_controller is not None:
                if kind != self.power_supply_kind:
                    raise ValueError("请先断开电源，再切换控制器类型")

            if kind != self.power_supply_kind:
                # Reset opposite controller's scan status
                self.power_supply_resources = []
                self.ch341_discovery_state = "not_scanned"
                self.ch341_descriptors = []
                self.ch341_scan_error = ""

            for key in DEFAULT_CONFIGURATION:
                if key in params:
                    self.configuration[key] = params[key]
            mode = str(self.configuration["currentPlanMode"]).lower()
            if mode not in {"range", "custom"}:
                raise ValueError("currentPlanMode must be range or custom")
            points = self.configuration["customCurrentPoints"]
            if not isinstance(points, list):
                raise ValueError("customCurrentPoints must be a list")
            self.power_supply_kind = kind

    def _refresh_device(self, params: dict[str, Any]) -> None:
        device = str(params.get("device", "all"))
        if device in {"all", "powerSupply"}:
            if self.power_supply_kind == "tdk":
                self.power_supply_resources = list_tdk_serial_resources()
                self.ch341_discovery_state = "not_scanned"
                self.ch341_descriptors = []
                self.ch341_scan_error = ""
                self.status_message = f"找到 {len(self.power_supply_resources)} 个可用 RS-232 串口"
            else:
                self.power_supply_resources = []
                try:
                    descriptors = scan_ch341_devices()
                    self.ch341_descriptors = descriptors
                    self.ch341_scan_error = ""
                    if descriptors:
                        self.ch341_discovery_state = "ready"
                        self.status_message = f"扫描到 {len(descriptors)} 个 CH341 端口"
                    else:
                        self.ch341_discovery_state = "empty"
                        self.status_message = "未检测到在线的 CH341 设备"
                except Exception as exc:
                    self.ch341_descriptors = []
                    self.ch341_scan_error = str(exc)
                    self.ch341_discovery_state = "error"
                    self.status_message = f"CH341 扫描失败：{exc}"
                    _LOGGER.warning("CH341 扫描异常: %s", exc)
                    self.notify("error", "CH341 扫描失败", str(exc))
        if device in {"all", "powerMeter"}:
            self._detect_power_meters()
        if device in {"all", "spectrometer"}:
            self._detect_spectrometers()

    def _power_supply(self) -> ControllerPowerSupply | None:
        controller = self.power_supply_controller
        if controller is None or not bool(getattr(controller, "is_connected", False)):
            return None
        return ControllerPowerSupply(controller)

    def _connect_power_supply(self) -> None:
        if self._power_supply() is not None:
            return
        if self.power_supply_kind == "tdk":
            controller = TdkLambdaPowerSupply(str(self.configuration["tdkResource"]))
            connected, detail = controller.connect_device(0)
            if not connected:
                raise RuntimeError(str(detail))
            self.power_supply_controller = controller
            self.connected_ch341_port = None
            self.power_supply_identity_error = ""
            self.active_current_a = 0.0
            self.status_message = f"电源已连接：{detail}"
        else:
            selected_loc = str(self.configuration.get("ch341SelectedLocationPath", "")).strip()
            if not selected_loc:
                raise RuntimeError("请先选择要连接的 CH341 工位端口")

            bindings = {b["locationPath"]: b["name"] for b in self.configuration.get("ch341PortBindings", []) if isinstance(b, dict)}
            bound_name = bindings.get(selected_loc)
            if not bound_name:
                raise RuntimeError("未命名的端口不能连接，请先绑定工位名称")

            # Perform fresh live scan
            try:
                descriptors = scan_ch341_devices()
                self.ch341_descriptors = descriptors
                self.ch341_scan_error = ""
                self.ch341_discovery_state = "ready" if descriptors else "empty"
            except Exception as exc:
                self.ch341_descriptors = []
                self.ch341_scan_error = str(exc)
                self.ch341_discovery_state = "error"
                raise RuntimeError(f"连接前扫描 CH341 端口失败：{exc}")

            matching_desc = next((d for d in descriptors if d.location_path == selected_loc), None)
            if matching_desc is None:
                raise RuntimeError(f"工位 '{bound_name}' ({selected_loc}) 当前未检测到或已离线，请检查硬件连接并重新扫描")

            target_index = matching_desc.device_index
            controller = create_ch341_controller(device_index=target_index)
            controller.set_i2c_speed(0)
            connected, detail = controller.connect_device(target_index)
            if not connected:
                raise RuntimeError(str(detail))

            # Post-open verification: verify opened index still matches target locationPath
            try:
                verified_iface = controller.get_device_interface_path(target_index)
                verified_loc = get_location_path_from_interface_path(verified_iface)
                if verified_loc != selected_loc:
                    raise RuntimeError(f"端口复核不一致（期望 {selected_loc}，实际 {verified_loc}）")
            except Exception as exc:
                disconnect_error = ""
                try:
                    disconnected = bool(controller.disconnect_device())
                except Exception as close_exc:
                    disconnected = False
                    disconnect_error = str(close_exc)
                if not disconnected:
                    detail_text = f"连接后端口校验失败：{exc}；设备关闭失败"
                    if disconnect_error:
                        detail_text += f"：{disconnect_error}"
                    self.power_supply_controller = controller
                    self.connected_ch341_port = {
                        "locationPath": selected_loc,
                        "name": bound_name,
                        "deviceIndex": target_index,
                    }
                    self.power_supply_identity_error = detail_text
                    self.status_message = detail_text
                    raise RuntimeError(detail_text) from exc
                raise RuntimeError(f"连接后端口校验失败：{exc}")

            self.power_supply_controller = controller
            self.active_current_a = 0.0
            self.power_supply_identity_error = ""
            self.connected_ch341_port = {
                "locationPath": selected_loc,
                "name": bound_name,
                "deviceIndex": target_index,
            }
            self.notify("info", "电源已连接", f"{bound_name}（当前 Device {target_index}）")
            _LOGGER.info("已连接 CH341 电源: name=%s, locationPath=%s, deviceIndex=%d", bound_name, selected_loc, target_index)

    def _disconnect_power_supply(self) -> None:
        if self.power_supply_identity_error and self.power_supply_controller is not None:
            try:
                disconnected = bool(self.power_supply_controller.disconnect_device())
            except Exception as exc:
                raise RuntimeError(f"身份校验失败的 CH341 仍无法关闭：{exc}") from exc
            if not disconnected:
                raise RuntimeError("身份校验失败的 CH341 仍无法关闭，请检查 USB 连接")
            self.power_supply_controller = None
            self.connected_ch341_port = None
            self.power_supply_identity_error = ""
            self.active_current_a = 0.0
            self.status_message = "身份校验失败的 CH341 已断开"
            return
        supply = self._power_supply()
        if supply is None:
            self.power_supply_controller = None
            self.connected_ch341_port = None
            self.power_supply_identity_error = ""
            return
        bound_name = self.connected_ch341_port.get("name", "") if self.connected_ch341_port else ""
        supply.set_current(0.0)
        if self.power_supply_kind == "tdk":
            supply.set_output_enabled(False)
        self.active_current_a = 0.0
        supply.disconnect()
        self.power_supply_controller = None
        self.connected_ch341_port = None
        self.power_supply_identity_error = ""
        self.status_message = f"电源已安全断开（{bound_name}）" if bound_name else "电源已安全断开"

    def _require_power_supply(self) -> ControllerPowerSupply:
        if self.power_supply_identity_error:
            raise RuntimeError("CH341 端口身份校验失败，只允许安全断开")
        supply = self._power_supply()
        if supply is None:
            raise RuntimeError("电源未连接")
        return supply

    def _set_current(self, params: dict[str, Any]) -> None:
        current_a = float(params["currentA"])
        if not math.isfinite(current_a) or current_a < 0.0:
            raise ValueError("电流必须是大于或等于 0 A 的有限数值")
        if self.power_supply_kind != "tdk" and current_a > 20.0:
            raise ValueError("CH341 最大电流不能超过 20 A")
        self._require_power_supply().set_current(current_a)
        with self._lock:
            self.configuration["setCurrentA"] = current_a
            self.active_current_a = current_a
        reader = self.power_meter_reader
        if reader is not None:
            reader.reset_stability_window()
        self.status_message = f"输出电流已设为 {current_a:g} A"

    def _set_voltage(self, params: dict[str, Any]) -> None:
        voltage_v = float(params["voltageV"])
        if self.power_supply_kind != "tdk":
            raise ValueError("只有 TDK 电源支持设置输出电压")
        self._require_power_supply().set_voltage(voltage_v)
        self.configuration["tdkVoltageV"] = voltage_v

    def _set_output(self, params: dict[str, Any]) -> None:
        if self.power_supply_kind != "tdk":
            raise ValueError("I2C 控制器没有独立输出开关")
        supply = self._require_power_supply()
        enabled = bool(params.get("enabled"))
        if enabled:
            supply.set_current(0.0)
            measured = supply.read_output_current()
            if not math.isfinite(measured) or abs(measured) > 0.01:
                raise RuntimeError("无法确认 TDK 输出电流已归零")
            self.active_current_a = 0.0
        supply.set_output_enabled(enabled)
        if not enabled:
            self.active_current_a = 0.0

    def _read_power_supply(self, params: dict[str, Any]) -> None:
        supply = self._require_power_supply()
        name = str(params.get("value", ""))
        if name == "outputVoltage":
            self.latest_tdk_voltage_v = supply.read_output_voltage()
        elif name == "outputCurrent":
            self.latest_tdk_current_a = supply.read_output_current()
        else:
            raise ValueError("当前无界面后台只支持读取输出电压和输出电流")

    def _detect_power_meters(self) -> None:
        if self.power_meter_detect_thread is not None:
            return
        detector = PowerMeterDetectThread(str(self.configuration["powerMeterResource"]))
        self.power_meter_detect_thread = detector
        detector.detected.connect(self._on_power_meters_detected)
        detector.failed.connect(self._on_power_meter_detect_failed)
        detector.status.connect(self._on_status)
        detector.finished.connect(self._on_power_meter_detect_finished)
        detector.start()

    def _on_power_meters_detected(self, options: list[PowerMeterOption]) -> None:
        with self._lock:
            self.power_meter_options = list(options)
            if options:
                selected = options[0]
                self.configuration["powerMeterResource"] = selected.resource
                self.configuration["powerMeterDriverKind"] = selected.driver_kind

    def _on_power_meter_detect_failed(self, message: str) -> None:
        self.power_meter_error = str(message)
        self.notify("error", "功率计检测", message)

    def _on_power_meter_detect_finished(self) -> None:
        self.power_meter_detect_thread = None

    def _power_meter_settings(self) -> PowerMeterSettings:
        resource = extract_power_resource_name(str(self.configuration["powerMeterResource"]))
        if not resource:
            raise ValueError("功率计资源不能为空")
        return PowerMeterSettings(
            resource=resource,
            wavelength_nm=float(self.configuration["powerMeterWavelengthNm"]),
            software_gain=float(self.configuration["softwareGain"]),
            interval_ms=int(self.configuration["powerMeterIntervalMs"]),
            stable_window_s=float(self.configuration["stableWindowS"]),
            stable_tolerance_w=float(self.configuration["stableToleranceW"]),
            driver_kind=str(self.configuration.get("powerMeterDriverKind", "caihuang")),
        )

    def _start_power_meter(self) -> None:
        if self.power_meter_reader is not None:
            return
        reader = PowerMeterReaderThread(self._power_meter_settings())
        self.power_meter_reader = reader
        self.power_meter_error = ""
        reader.reading.connect(self._on_power_reading)
        reader.ready.connect(self._on_power_meter_ready)
        reader.failed.connect(self._on_power_meter_failed)
        reader.status.connect(self._on_status)
        reader.finished.connect(self._on_power_meter_finished)
        reader.start()

    def _stop_power_meter(self) -> None:
        reader = self.power_meter_reader
        if reader is not None:
            reader.stop()
            reader.wait(3_000)

    def _on_power_reading(self, reading: PowerMeterReading) -> None:
        with self._lock:
            self.latest_power = reading
            self.power_points.append({"elapsedS": reading.elapsed_s, "powerW": reading.power_w})
            self.series_revisions["power"] += 1

    def _on_power_meter_ready(self) -> None:
        self.automatic.mark_power_meter_ready()

    def _on_power_meter_failed(self, message: str) -> None:
        self.power_meter_error = str(message)
        self.notify("error", "功率计", message)
        if self.automatic.state not in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED}:
            self.automatic.pause(str(message))

    def _on_power_meter_finished(self) -> None:
        self.power_meter_reader = None

    def _set_relative_zero(self, params: dict[str, Any]) -> None:
        power_meter_mvp = __import__("tools.power_meter_mvp", fromlist=["CaihuangPowerMeter"])
        kind = str(self.configuration.get("powerMeterDriverKind", "caihuang"))
        classes = {
            "caihuang": getattr(power_meter_mvp, "CaihuangPowerMeter"),
            "coherent": getattr(power_meter_mvp, "CoherentPowerMeter", None),
        }
        meter_class = classes.get(kind)
        if meter_class is None:
            raise ValueError("当前功率计不支持相对调零")
        meter = meter_class(str(self.configuration["powerMeterResource"]))
        try:
            meter.set_relative_zero(bool(params.get("enabled")))
        finally:
            meter.close()

    def _detect_spectrometers(self) -> None:
        spectrometer_class, _ = load_spectrometer_components(None)
        device_ids = [int(value) for value in spectrometer_class.detect()]
        self.spectrometer_resources = [
            {"label": f"Ocean Insight | 设备 ID {device_id}", "deviceId": device_id}
            for device_id in device_ids
        ]

    def _spectrometer_settings(self) -> SpectrometerSettings:
        device_id = self.configuration.get("spectrometerDeviceId")
        return SpectrometerSettings(
            integration_time_us=int(self.configuration["integrationTimeUs"]),
            interval_ms=int(self.configuration["spectrometerIntervalMs"]),
            device_id=None if device_id in {None, ""} else int(device_id),
            auto_integration_enabled=bool(self.configuration["autoIntegration"]),
            average_frames=int(self.configuration["spectrumAverageFrames"]),
        )

    def _start_spectrometer(self) -> None:
        if self.spectrometer_reader is not None:
            return
        reader = SpectrometerReaderThread(self._spectrometer_settings(), headless=True)
        self.spectrometer_reader = reader
        self.spectrometer_error = ""
        reader.reading.connect(self._on_spectrometer_reading)
        reader.ready.connect(self._on_spectrometer_ready)
        reader.failed.connect(self._on_spectrometer_failed)
        reader.status.connect(self._on_status)
        reader.integration_time_changed.connect(self._on_integration_changed)
        reader.finished.connect(self._on_spectrometer_finished)
        reader.start()

    def _stop_spectrometer(self) -> None:
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

    def _save_spectrum_csv(self, params: dict[str, Any]) -> None:
        frame = self.latest_spectrum
        if frame is None:
            raise ValueError("当前没有可保存的光谱数据")
        requested = str(params.get("path", "")).strip()
        path = Path(requested) if requested else Path(str(self.configuration["outputDir"])) / f"spectrum_{datetime.now():%Y%m%d_%H%M%S}.csv"
        save_spectrum_curve(path, frame.wavelength, frame.intensity)
        self.status_message = f"光谱已保存：{path}"

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

    def _start_automatic(self, params: dict[str, Any]) -> None:
        if self._automatic_thread is not None and self._automatic_thread.is_alive():
            raise ValueError("自动测试已在运行")
        self._configure(params)
        supply = self._require_power_supply()
        if self.power_supply_kind == "tdk" and not supply.output_enabled:
            raise ValueError("请先开启 TDK 电源输出")
        if float(self.active_current_a or 0.0) > 0.0:
            raise ValueError("开始自动测试前必须先将输出电流归零")
        settings = self._automatic_settings()
        if self.power_meter_reader is None:
            self._start_power_meter()
        if settings.use_spectrometer and self.spectrometer_reader is None:
            self._start_spectrometer()
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
            name="automatic-test",
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
                self._set_current({"currentA": current_a})
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
                efficiency = stable_reading.power_w / current_a / voltage_v * 100.0 if current_a > 0.0 and voltage_v > 0.0 else math.nan
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
                    pib=math.nan if frame is None else calculate_pib(frame.wavelength, frame.intensity),
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
            if records:
                self.automatic.set_state(AutomaticTestState.SAVING_POINT)
                output = Path(str(self.configuration["outputDir"])).expanduser()
                path = build_test_workbook_path(output, str(self.configuration["sn"]), datetime.now(), str(self.configuration["station"]))
                save_test_records(path, records)
                self.status_message = f"测试记录已保存：{path}"
            self._safe_ramp_down()
            self.automatic.complete()
            self.automatic_terminal_outcome = "succeeded" if not self._automatic_stop.is_set() else "stopped_by_operator"
            self.automatic_detail = "自动测试已安全完成"
        except Exception as exc:
            _LOGGER.exception("Headless automatic test failed")
            self.automatic.pause(str(exc))
            self.automatic_detail = str(exc)
            self.automatic_terminal_outcome = "aborted_safely"
            self.automatic_terminal_reason = str(exc)
            try:
                self._safe_ramp_down()
            except Exception as shutdown_error:
                self.automatic_output_shutdown_unconfirmed = True
                self.notify("error", "安全下电失败", str(shutdown_error))

    def _safe_ramp_down(self) -> None:
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

    def _pause_automatic(self) -> None:
        if self.automatic.state in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED, AutomaticTestState.PAUSED}:
            raise ValueError("当前状态不能暂停")
        self.automatic.pause("操作员暂停")
        self._automatic_resume.clear()

    def _resume_automatic(self) -> None:
        if self.automatic.state != AutomaticTestState.PAUSED:
            raise ValueError("当前状态不能重试")
        self.automatic.set_state(self.automatic.paused_from_state)
        self.automatic.pause_reason = ""
        self._automatic_resume.set()

    def _end_automatic(self) -> None:
        self._automatic_stop.set()
        self._automatic_resume.set()
        thread = self._automatic_thread
        if thread is not None:
            thread.join(15.0)

    def _reset_automatic(self) -> None:
        if self._automatic_thread is not None and self._automatic_thread.is_alive():
            raise ValueError("自动测试运行中，不能重置")
        self.automatic = AutomaticTestOrchestrator()
        self.automatic_detail = "准备测试"
        self.automatic_terminal_outcome = None
        self.automatic_terminal_reason = ""
        self._test_reference = None

    def _list_automatic_references(self, params: dict[str, Any]) -> dict[str, Any]:
        from combined_test.test_history import find_test_history

        output = str(params.get("outputDir", self.configuration["outputDir"])).strip()
        sn = str(params.get("sn", self.configuration["sn"])).strip()
        records = find_test_history(output, sn) if output and sn else ()
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
        target, request = report.generate_shipping_report_batch_item(params["sourcePath"], params["outputDirectory"], params)
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
            output_dir = Path(str(values["outputDir"] or self.configuration["outputDir"])).expanduser()
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

    def _reset_charts(self) -> None:
        with self._lock:
            self.power_points.clear()
            self.stable_power_points.clear()
            self.efficiency_points.clear()
            self.latest_spectrum = None
            for key in self.series_revisions:
                self.series_revisions[key] += 1

    def _stop_all(self) -> None:
        self._end_automatic()
        self._stop_power_meter()
        self._stop_spectrometer()
        self._stop_pd()
        self._safe_ramp_down()

    def _shutdown(self) -> dict[str, bool]:
        self._stop_all()
        supply = self._power_supply()
        if supply is not None:
            if self.power_supply_kind == "tdk" and supply.output_enabled:
                supply.set_output_enabled(False)
            measured = supply.read_output_current()
            if not math.isfinite(measured) or abs(measured) > 0.01:
                self.automatic_output_shutdown_unconfirmed = True
                raise RuntimeError("无法确认电源输出电流已归零")
        return {"shutdownConfirmed": True}

    def _on_status(self, message: str) -> None:
        self.status_message = str(message)

    def snapshot(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked(params or {})

    def _snapshot_locked(self, params: dict[str, Any]) -> dict[str, Any]:
        view = str(params.get("view", "full"))
        include_live = view in {"automatic", "manual", "full"}
        include_pd = view in {"pd", "full"}
        supply = self._power_supply()
        frame = self.latest_spectrum
        spectrum_saturated = bool(frame and frame.saturation and frame.saturation.saturated)
        spectrum_stats = None if frame is None else frame.stats
        wavelength = [] if frame is None else [float(value) for value in frame.wavelength]
        intensity = [] if frame is None else [float(value) for value in frame.intensity]
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
        currents = list(self.automatic.currents)
        current_index = int(self.automatic.current_index)
        progress = max(0.0, min(1.0, (current_index + 1) / len(currents))) if currents else 0.0
        dwell_elapsed = None
        if self.automatic_hold_started_at is not None and self.automatic.current_dwell_s > 0.0:
            dwell_elapsed = min(self.automatic.current_dwell_s, max(0.0, time.monotonic() - self.automatic_hold_started_at))
        bindings_map = {
            b["locationPath"]: b["name"]
            for b in self.configuration.get("ch341PortBindings", [])
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
            "connectedPort": self.connected_ch341_port,
        }
        if self.power_supply_identity_error:
            power_supply_detail = self.power_supply_identity_error
        elif supply is not None and self.power_supply_kind == "ch341" and self.connected_ch341_port:
            power_supply_detail = (
                f"{self.connected_ch341_port['name']} · 已连接 · "
                f"Device {self.connected_ch341_port['deviceIndex']}"
            )
        else:
            power_supply_detail = "已连接" if supply is not None else "未连接"

        return {
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "seriesRevisions": dict(self.series_revisions),
            "backend": {
                "connected": True,
                "mode": self.mode,
                "pythonVersion": platform.python_version(),
                "runtimeLogPath": str(runtime_log_path() or ""),
                "notices": list(self.notices),
            },
            "configuration": dict(self.configuration),
            "devices": {
                "powerSupply": {
                    "state": _device_state(supply is not None, False, self.power_supply_identity_error),
                    "label": "TDK RS232" if self.power_supply_kind == "tdk" else "CH341 I²C",
                    "detail": power_supply_detail,
                    "connected": supply is not None,
                    "outputEnabled": bool(supply and supply.output_enabled),
                    "activeCurrentA": _finite(self.active_current_a),
                    "resources": list(self.power_supply_resources),
                    "ch341": ch341_snapshot,
                },
                "powerMeter": {
                    "state": "connecting" if self.power_meter_detect_thread is not None else _device_state(False, self.power_meter_reader is not None, self.power_meter_error),
                    "label": "功率计",
                    "detail": self.power_meter_error or ("采集中" if self.power_meter_reader else "已停止"),
                    "running": self.power_meter_reader is not None,
                    "detecting": self.power_meter_detect_thread is not None,
                    "ready": bool(self.power_meter_reader and self.power_meter_reader.is_ready),
                    "powerW": _finite(None if self.latest_power is None else self.latest_power.power_w),
                    "stable": bool(self.latest_power and self.latest_power.stable),
                    "resources": [item.label() for item in self.power_meter_options],
                },
                "spectrometer": {
                    "state": _device_state(False, self.spectrometer_reader is not None, self.spectrometer_error),
                    "label": "Ocean Insight",
                    "detail": self.spectrometer_error or ("采集中" if self.spectrometer_reader else "已停止"),
                    "running": self.spectrometer_reader is not None,
                    "ready": bool(self.spectrometer_reader and self.spectrometer_reader.is_ready),
                    "peakWavelengthNm": None if spectrum_saturated else _finite(getattr(spectrum_stats, "peak_wavelength_nm", None)),
                    "centroidNm": None if spectrum_saturated else _finite(getattr(spectrum_stats, "centroid_nm", None)),
                    "fwhmNm": None if spectrum_saturated else _finite(getattr(spectrum_stats, "fwhm_nm", None)),
                    "smsrDb": None if frame is None or frame.smsr is None else _finite(frame.smsr.smsr_db),
                    "saturated": spectrum_saturated,
                    "spectrumWindow": None,
                    "resources": [item["label"] for item in self.spectrometer_resources],
                },
            },
            "automaticTest": {
                "state": self.automatic.state.value,
                "detail": self.automatic_detail,
                "controlsEnabled": True,
                "canStart": supply is not None and not self.power_supply_identity_error and float(self.active_current_a or 0.0) <= 0.0,
                "canRetry": self.automatic.state == AutomaticTestState.PAUSED,
                "canPause": self.automatic.state not in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED, AutomaticTestState.PAUSED, AutomaticTestState.RAMPING_DOWN},
                "canEnd": self.automatic.state not in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED, AutomaticTestState.RAMPING_DOWN},
                "settingsError": "",
                "currents": currents,
                "dwellTimesS": list(self.automatic.dwell_times_s),
                "currentIndex": current_index,
                "currentA": _finite(self.automatic.current_a),
                "currentDwellS": self.automatic.current_dwell_s,
                "currentDwellElapsedS": dwell_elapsed,
                "samplePath": self.automatic_sample_path,
                "progress": progress,
                "pauseReason": self.automatic.pause_reason,
                "terminalOutcome": self.automatic_terminal_outcome,
                "terminalReason": self.automatic_terminal_reason,
                "reference": None,
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
