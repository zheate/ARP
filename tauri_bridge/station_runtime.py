"""Single-station hardware and automatic-test runtime."""

from __future__ import annotations

import math
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from combined_test.automation import (
    AutomaticTestOrchestrator,
    AutomaticTestState,
    MIN_POWER_SUPPLY_COMMAND_INTERVAL_S,
    PowerDropProtectionDetector,
)
from combined_test.automatic_samples import AutomaticSampleRecorder
from combined_test.core import WavelengthStabilityDetector
from combined_test.device_interfaces import ControllerPowerSupply
from combined_test.devices import (
    PowerMeterDetectThread,
    PowerMeterReaderThread,
    SpectrometerReaderThread,
    create_ch341_controller,
)
from combined_test.ch341_ports import get_location_path_from_interface_path
from combined_test.excel_export import ExcelTestRecord
from combined_test.models import (
    PowerMeterReading,
    PowerMeterSettings,
    SpectrumFrame,
    SpectrometerReading,
    SpectrometerSettings,
)
from combined_test.persistence import save_spectrum_curve
from combined_test.record_store import (
    AttemptValidity,
    DeviceSnapshot,
    SessionRecordStore,
    SessionStatus,
)
from combined_test.runtime_logging import get_runtime_logger
from combined_test.spectrum import (
    SPECTRUM_CENTER_LOCK_HALF_RANGE_NM,
    SPECTRUM_CENTER_LOCK_REQUIRED_SAMPLES,
    SPECTRUM_CENTER_LOCK_TOLERANCE_NM,
)
from combined_test.spectrum_math import calculate_pib
from combined_test.tdk_power_supply import (
    TdkLambdaPowerSupply,
    compensate_tdk_output_voltage,
)


from .station_automatic import StationAutomaticMixin
from .station_support import (
    AGING_TEST_SCRIPT_ID,
    DEFAULT_CONFIGURATION,
    AutomaticPointQualityError as _AutomaticPointQualityError,
    apply_aging_script_configuration as _apply_aging_script_configuration,
    default_pd_state as _default_pd_state,
    finite as _finite,
    normalize_device_serial as _normalize_device_serial,
    normalize_power_meter_configuration as _normalize_power_meter_configuration,
    spectrometer_label as _spectrometer_label,
)


_LOGGER = get_runtime_logger("headless_backend")

class StationRuntime(StationAutomaticMixin):
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
                    "powerMeterSerialNumber": "",
                    "spectrometerResource": "",
                    "spectrometerDeviceId": None,
                    "spectrometerSerialNumber": "",
                }
            )
        if initial_config:
            self.configuration.update({k: v for k, v in initial_config.items() if k in DEFAULT_CONFIGURATION})
            self.configuration["testTemperatureC"] = str(
                self.configuration.get("testTemperatureC", "") or ""
            ).strip()
            # Preserve the old combined PIB/center-wavelength settings when
            # loading a configuration saved before editable result specs.
            if "centerWavelengthSpecNm" not in initial_config and "pibCenterWavelengthNm" in initial_config:
                self.configuration["centerWavelengthSpecNm"] = self.configuration["pibCenterWavelengthNm"]
            if "centerWavelengthToleranceNm" not in initial_config and "pibHalfRangeNm" in initial_config:
                self.configuration["centerWavelengthToleranceNm"] = self.configuration["pibHalfRangeNm"]
        self.power_meter_configuration_migrated = _normalize_power_meter_configuration(
            self.configuration
        )

        if str(self.configuration.get("selectedScriptId", "standard")) == AGING_TEST_SCRIPT_ID:
            if str(self.configuration.get("powerSupplyKind", "ch341")).lower() == "ch341":
                _apply_aging_script_configuration(self.configuration)
            else:
                self.configuration["selectedScriptId"] = "standard"
                self.configuration["currentPlanMode"] = "range"

        self.power_supply_controller: Any | None = None
        self.power_supply_kind = str(self.configuration["powerSupplyKind"])
        self.active_current_a: float | None = 0.0
        self.connected_ch341_port: dict[str, Any] | None = None
        self.power_supply_identity_error = ""
        self.latest_power_supply_input_voltage_v: float | None = None
        self.latest_power_supply_output_voltage_v: float | None = None
        self.latest_power_supply_output_current_a: float | None = None
        self.latest_power_supply_temperature_c: float | None = None
        self._last_power_supply_command_at: float | None = None

        self.power_meter_detect_thread: PowerMeterDetectThread | None = None
        self.power_meter_reader: PowerMeterReaderThread | None = None
        self.power_meter_error = ""
        self.latest_power: PowerMeterReading | None = None
        self.latest_power_received_monotonic_s: float | None = None
        self.latest_power_sampled_at_utc: str | None = None
        self.power_points: deque[dict[str, float]] = deque(maxlen=1_200)
        self.power_drop_protection = PowerDropProtectionDetector()
        self._expected_power_meter_stop = False

        self.spectrometer_reader: SpectrometerReaderThread | None = None
        self.spectrometer_error = ""
        self.latest_spectrometer_reading: SpectrometerReading | None = None
        self.latest_spectrum: SpectrumFrame | None = None
        self.latest_spectrum_received_monotonic_s: float | None = None
        self.latest_spectrum_sampled_at_utc: str | None = None
        self.spectrum_revision = 0
        self.spectrum_grid_revision = 0
        self._last_spectrum_grid: list[float] = []
        self.spectrum_center_candidate_nm: float | None = None
        self.spectrum_center_candidate_count = 0
        self.spectrum_centroid_candidate_nm: float | None = None
        self.spectrum_centroid_candidate_count = 0
        self.spectrum_center_locked_nm: float | None = None
        self.wavelength_stability_detector = WavelengthStabilityDetector(3.0, 0.2)
        self.latest_wavelength_stable = False
        self.latest_wavelength_span_nm = math.inf

        self.stable_power_points: dict[float, float] = {}
        self.efficiency_points: dict[float, float] = {}
        self.aging_power_points: list[dict[str, float]] = []
        self.series_revisions = {
            "power": 0,
            "stable": 0,
            "aging": 0,
            "spectrum": 0,
            "pd": 0,
        }
        self.pending_manual_stable_current_a: float | None = None
        self.pending_manual_stability_generation: int | None = None
        self.recorded_manual_stable_current_a: float | None = None
        self.recorded_manual_stability_generation: int | None = None
        self._manual_voltage_timer_generation = 0

        self.automatic = AutomaticTestOrchestrator()
        self.automatic_detail = "准备测试"
        self.status_message = "准备测试"
        self.automatic_started_at: float | None = None
        self.automatic_hold_started_at: float | None = None
        self.automatic_terminal_outcome: str | None = None
        self.automatic_terminal_reason = ""
        self.shutdown_requested = False
        self.shutdown_confirmed = False
        self.shutdown_measured_current_a: float | None = None
        self._shutdown_measurement_candidate: float | None = None
        self.shutdown_output_off_confirmed = False
        self.shutdown_reason = ""
        self.automatic_sample_path = ""
        self.automatic_sample_recorder: AutomaticSampleRecorder | None = None
        journal_root = (
            self.backend.settings.path.parent
            / "test-records"
            / f"station-{self.station_id}"
        )
        self._journal_root = journal_root
        self.record_store = self._new_record_store()
        self._automatic_thread: threading.Thread | None = None
        self._automatic_stop = threading.Event()
        self._automatic_resume = threading.Event()
        self._automatic_resume.set()
        self._automatic_retry_requested = False
        self._automatic_retry_reason = ""
        self._automatic_pause_holds_current = False
        self._automatic_pause_generation = 0
        self.automatic_pause_kind: str | None = None
        self.automatic_pause_started_at_utc: str | None = None
        self.automatic_pause_deadline_at_utc: str | None = None
        self.automatic_pause_actions: tuple[str, ...] = ()
        self._emergency_ramp_thread: threading.Thread | None = None
        self._emergency_ramp_thread_lock = threading.Lock()
        self._safe_ramp_lock = threading.Lock()
        self._supply_io_lock = threading.RLock()
        self._expected_spectrometer_stop = False
        self._test_reference: Any | None = None
        self.export_status: str = "idle"
        self.export_path: str = ""
        self.export_error: str = ""
        self._export_generation = 0

        # PD acquisition is a station-owned resource just like the power
        # meter and spectrometer.  Keep its mutable state behind its own lock
        # because acquisition callbacks run on a worker thread.
        self._pd_lock = threading.RLock()
        self.pd_state = _default_pd_state()
        self._pd_devices: list[Any] = []
        self._pd_reader: Any | None = None
        self._pd_detect_thread: _DeviceDiscoveryThread | None = None

    def notify(
        self,
        level: str,
        title: str,
        message: str,
        *,
        code: str = "",
    ) -> None:
        self.backend.notify(
            level,
            f"[工位 {self.station_id}] {title}",
            message,
            station_id=self.station_id,
            code=code,
        )

    def _new_record_store(self) -> SessionRecordStore:
        return SessionRecordStore(self._journal_root)

    def manual_controls_allowed(self) -> bool:
        return self.automatic.state in {
            AutomaticTestState.IDLE,
            AutomaticTestState.COMPLETED,
        }

    @staticmethod
    def measurement_freshness(
        *,
        received_at_monotonic_s: float | None,
        sampled_at_utc: str | None,
        running: bool,
        interval_ms: float,
        valid: bool,
        saturated: bool = False,
    ) -> dict[str, Any]:
        if received_at_monotonic_s is None or sampled_at_utc is None or not valid:
            return {"sampledAt": sampled_at_utc, "ageMs": None, "quality": "invalid"}
        # Whole-second age is sufficient for operators and avoids turning an
        # otherwise unchanged snapshot into a fresh WebView IPC message at 5 Hz.
        age_ms = int(max(0.0, time.monotonic() - received_at_monotonic_s)) * 1_000
        live_limit_ms = max(1_000, int(max(0.0, interval_ms) * 3.0))
        invalid_limit_ms = max(5_000, live_limit_ms * 3)
        if running and age_ms <= live_limit_ms:
            quality = "saturated" if saturated else "live"
        elif age_ms <= invalid_limit_ms:
            quality = "stale"
        else:
            quality = "invalid"
        return {"sampledAt": sampled_at_utc, "ageMs": age_ms, "quality": quality}

    def shutdown_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "requested": self.shutdown_requested,
                "confirmed": self.shutdown_confirmed,
                "measuredCurrentA": _finite(self.shutdown_measured_current_a),
                "outputOffConfirmed": self.shutdown_output_off_confirmed,
                "reason": self.shutdown_reason,
            }

    def _record_shutdown_result(
        self,
        *,
        confirmed: bool,
        measured_current_a: float | None,
        output_off_confirmed: bool,
        reason: str,
    ) -> None:
        with self._lock:
            self.shutdown_requested = True
            self.shutdown_confirmed = confirmed
            self.shutdown_measured_current_a = measured_current_a
            self.shutdown_output_off_confirmed = output_off_confirmed
            self.shutdown_reason = reason

    def _clear_shutdown_result(self) -> None:
        with self._lock:
            self.shutdown_requested = False
            self.shutdown_confirmed = False
            self.shutdown_measured_current_a = None
            self.shutdown_output_off_confirmed = False
            self.shutdown_reason = ""

    def _power_supply(self) -> ControllerPowerSupply | None:
        return ControllerPowerSupply(self.power_supply_controller) if self.power_supply_controller is not None else None

    def _require_power_supply(self) -> ControllerPowerSupply:
        supply = self._power_supply()
        if supply is None:
            raise RuntimeError("请先连接电源控制器")
        if self.power_supply_identity_error:
            raise RuntimeError(f"电源状态异常：{self.power_supply_identity_error}")
        return supply

    def _reserve_power_supply_command(self, *, wait: bool) -> None:
        """Keep CH341 commands outside the controller's unsafe 1.1 s window."""
        if self.power_supply_kind != "ch341":
            return
        now = time.monotonic()
        previous = self._last_power_supply_command_at
        remaining = (
            0.0
            if previous is None
            else MIN_POWER_SUPPLY_COMMAND_INTERVAL_S - (now - previous)
        )
        if remaining > 0.0:
            if not wait:
                raise RuntimeError(f"电源命令过快，请等待 {remaining:.1f} 秒后重试")
            automatic_active = self.automatic.state not in {
                AutomaticTestState.IDLE,
                AutomaticTestState.COMPLETED,
            }
            if automatic_active:
                if self._automatic_stop.wait(remaining):
                    raise RuntimeError("自动测试已停止")
            else:
                time.sleep(remaining)
        self._last_power_supply_command_at = time.monotonic()

    def _device_snapshots(self) -> tuple[DeviceSnapshot, ...]:
        ch341 = self.connected_ch341_port or {}
        return (
            DeviceSnapshot(
                role="电源",
                kind=self.power_supply_kind,
                resource=(
                    str(self.configuration.get("tdkResource", ""))
                    if self.power_supply_kind == "tdk"
                    else str(ch341.get("locationPath", self.configuration.get("ch341SelectedLocationPath", "")))
                ),
                detail=str(ch341.get("name", "")),
            ),
            DeviceSnapshot(
                role="功率计",
                kind=str(self.configuration.get("powerMeterDriverKind", "")),
                resource=str(self.configuration.get("powerMeterResource", "")),
            ),
            DeviceSnapshot(
                role="光谱仪",
                kind="Ocean Insight" if self.configuration.get("spectrometerResource") else "",
                resource=str(self.configuration.get("spectrometerResource", "")),
            ),
        )


    def configure(self, params: dict[str, Any]) -> None:
        with self._lock:
            kind = str(params.get("powerSupplyKind", self.configuration["powerSupplyKind"])).lower()
            if kind not in {"ch341", "tdk"}:
                raise ValueError("powerSupplyKind must be ch341 or tdk")
            selected_script_id = str(
                params.get("selectedScriptId", self.configuration["selectedScriptId"])
            )
            if selected_script_id == AGING_TEST_SCRIPT_ID and kind != "ch341":
                raise ValueError("AU 老化测试仅支持 CH341 电源，TDK RS232 不允许使用此模式")

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

            if "testTemperatureC" in params:
                params["testTemperatureC"] = str(params["testTemperatureC"] or "").strip()

            for key in DEFAULT_CONFIGURATION:
                if key in params:
                    self.configuration[key] = params[key]
            if selected_script_id == AGING_TEST_SCRIPT_ID:
                _apply_aging_script_configuration(self.configuration)

            if "powerMeterResource" in params or "powerMeterDriverKind" in params:
                _normalize_power_meter_configuration(
                    self.configuration,
                    self.backend.power_meter_options,
                )

            if self.power_meter_reader is not None:
                self.power_meter_reader.update_stability_settings(
                    float(self.configuration["stableWindowS"]),
                    float(self.configuration["stabilityLimitPercent"]),
                )

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
                supply = TdkLambdaPowerSupply(resource)
                connected, detail = supply.connect_device(0)
                if not connected:
                    raise RuntimeError(str(detail))
                self.power_supply_controller = supply
                self.power_supply_identity_error = ""
                try:
                    self.active_current_a = float(supply.read_output_current())
                except Exception:
                    supply.disconnect_device()
                    self.power_supply_controller = None
                    raise
                self.notify("info", "电源连接", f"TDK 电源已连接：{resource}")
                return

            # CH341 connection
            selected_loc = str(self.configuration.get("ch341SelectedLocationPath", "")).strip()
            if not selected_loc:
                raise ValueError("请先选择 CH341 物理端口")
            self.backend.check_device_conflicts(self.station_id, "ch341", selected_loc)
            # Always rescan immediately before opening: CH341 DLL indices are
            # enumeration-order handles and can swap after USB re-enumeration.
            self.backend.scan_ch341()
            if self.backend.ch341_discovery_state == "error":
                raise RuntimeError(self.backend.ch341_scan_error or "CH341 扫描失败")
            desc = next((d for d in self.backend.ch341_descriptors if d.location_path == selected_loc), None)
            if desc is None:
                raise RuntimeError(f"指定的 CH341 设备端口离线或未在线：{selected_loc}")

            target_index = desc.device_index
            name = f"Device {target_index}"
            controller = create_ch341_controller(device_index=target_index)
            # The power controller used by this application requires the same
            # 20 kHz mode as the legacy/main implementation.  Merely creating
            # the wrapper does not open the CH341 device.
            controller.set_i2c_speed(0)
            connected, detail = controller.connect_device(target_index)
            if not connected:
                raise RuntimeError(str(detail))

            # Verify the physical port again after opening.  DLL indices can
            # change when Windows re-enumerates multiple CH341 adapters.
            try:
                actual_iface = controller.get_device_interface_path(target_index)
                actual_loc = get_location_path_from_interface_path(actual_iface)
                if actual_loc != selected_loc:
                    raise RuntimeError(
                        f"端口复核不一致（期望 {selected_loc}，实际 {actual_loc}）"
                    )
            except Exception as exc:
                disconnect_error = ""
                try:
                    disconnected = bool(controller.disconnect_device())
                except Exception as close_exc:
                    disconnected = False
                    disconnect_error = str(close_exc)
                if not disconnected:
                    error = f"连接后端口校验失败：{exc}；设备关闭失败"
                    if disconnect_error:
                        error += f"：{disconnect_error}"
                    self.power_supply_controller = controller
                    self.connected_ch341_port = {
                        "locationPath": selected_loc,
                        "name": f"Device {target_index}",
                        "deviceIndex": target_index,
                    }
                    self.power_supply_identity_error = error
                    raise RuntimeError(error) from exc
                raise RuntimeError(f"连接后端口校验失败：{exc}") from exc

            self.connected_ch341_port = {
                "locationPath": selected_loc,
                "name": name,
                "deviceIndex": target_index,
            }
            self.power_supply_controller = controller
            self.power_supply_identity_error = ""
            self.active_current_a = 0.0
            self._last_power_supply_command_at = None
            self.notify("info", "电源连接", f"CH341 Device {target_index} 已连接")

    def disconnect_power_supply(self) -> None:
        with self._lock:
            if self.power_supply_controller is None:
                return
            if self.power_supply_kind == "ch341" and self.power_supply_identity_error:
                # A post-open port identity mismatch forbids current commands,
                # but must never make the retained handle impossible to close.
                ControllerPowerSupply(self.power_supply_controller).disconnect()
                self.power_supply_controller = None
                self.connected_ch341_port = None
                self.active_current_a = 0.0
                self.power_supply_identity_error = ""
                self._last_power_supply_command_at = None
                self._manual_voltage_timer_generation += 1
                self.notify("info", "电源断开", "身份异常的 CH341 控制器已断开")
                return
            supply = self._require_power_supply()
            with self._supply_io_lock:
                if self.power_supply_kind == "tdk":
                    # The cached output flag starts false after connecting and
                    # cannot prove the physical supply is off. Always send PC 0
                    # and OUT 0 before releasing the serial session.
                    supply.set_current(0.0)
                    supply.set_output_enabled(False)
                    measured_current_a = float(supply.read_output_current())
                    self.latest_power_supply_output_current_a = measured_current_a
                    if not math.isfinite(measured_current_a) or abs(measured_current_a) > 0.01:
                        raise RuntimeError(
                            f"安全断开已取消：无法确认 TDK 输出电流归零（{measured_current_a:.3f} A）"
                        )
                    self.active_current_a = 0.0
                    self.configuration["setCurrentA"] = 0.0
                elif self.active_current_a and abs(self.active_current_a) > 0.01:
                    self.safe_ramp_down()
                ControllerPowerSupply(self.power_supply_controller).disconnect()
            self.power_supply_controller = None
            self.connected_ch341_port = None
            self.active_current_a = 0.0
            self.power_supply_identity_error = ""
            self.latest_power_supply_input_voltage_v = None
            self.latest_power_supply_output_voltage_v = None
            self.latest_power_supply_output_current_a = None
            self.latest_power_supply_temperature_c = None
            self._last_power_supply_command_at = None
            self._manual_voltage_timer_generation += 1
            self.pending_manual_stable_current_a = None
            self.pending_manual_stability_generation = None
            self.notify("info", "电源断开", "电源控制器已安全断开")

    def set_current(self, params: dict[str, Any], *, wait_for_slot: bool = False) -> None:
        with self._lock:
            current_a = float(params["currentA"])
            if current_a < 0.0:
                raise ValueError("电流设定值不能为负数")
            if self.power_supply_kind == "ch341" and current_a > 20.0:
                raise ValueError("CH341 最大电流不能超过 20 A")
            supply = self._require_power_supply()
            with self._supply_io_lock:
                self._reserve_power_supply_command(wait=wait_for_slot)
                supply.set_current(current_a)
            self.active_current_a = current_a
            self.configuration["setCurrentA"] = current_a
            if self.automatic.state in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED}:
                self.pending_manual_stable_current_a = current_a
                self.recorded_manual_stable_current_a = None
                self.recorded_manual_stability_generation = None
                self.efficiency_points.pop(current_a, None)
                self._manual_voltage_timer_generation += 1
                self.pending_manual_stability_generation = (
                    self.power_meter_reader.reset_stability_window()
                    if self.power_meter_reader is not None
                    else None
                )

    def set_voltage(self, params: dict[str, Any]) -> None:
        with self._lock:
            voltage_v = float(params["voltageV"])
            if self.power_supply_kind != "tdk":
                raise ValueError("仅 TDK 电源支持设置电压")
            supply = self._require_power_supply()
            with self._supply_io_lock:
                supply.set_voltage(voltage_v)
            self.configuration["tdkVoltageV"] = voltage_v

    def set_output(self, params: dict[str, Any]) -> None:
        with self._lock:
            enabled = bool(params.get("enabled", False))
            supply = self._require_power_supply()
            with self._supply_io_lock:
                if enabled and self.power_supply_kind == "tdk":
                    supply.set_current(0.0)
                    self.active_current_a = 0.0
                    self.configuration["setCurrentA"] = 0.0
                    self._manual_voltage_timer_generation += 1
                    measured_current_a = float(supply.read_output_current())
                    if not math.isfinite(measured_current_a) or abs(measured_current_a) > 0.01:
                        raise RuntimeError(
                            "开启输出已取消：TDK 电流未归零，"
                            f"当前实测 {measured_current_a:.3f} A"
                        )
                supply.set_output_enabled(enabled)
                if not enabled and self.power_supply_kind == "tdk":
                    measured_current_a = float(supply.read_output_current())
                    if not math.isfinite(measured_current_a) or abs(measured_current_a) > 0.01:
                        raise RuntimeError(
                            f"TDK 输出已关闭，但无法确认输出电流归零：{measured_current_a:.3f} A"
                        )
                    self.active_current_a = 0.0
                    self.configuration["setCurrentA"] = 0.0
                    self._manual_voltage_timer_generation += 1

    def read_power_supply(self, params: dict[str, Any]) -> None:
        with self._lock:
            supply = self._require_power_supply()
            value_name = str(params.get("value", ""))
            readers: dict[str, tuple[Callable[[], float], str, str, str]] = {
                "inputVoltage": (
                    supply.read_input_voltage,
                    "输入电压",
                    "V",
                    "latest_power_supply_input_voltage_v",
                ),
                "outputVoltage": (
                    supply.read_output_voltage,
                    "输出电压",
                    "V",
                    "latest_power_supply_output_voltage_v",
                ),
                "outputCurrent": (
                    supply.read_output_current,
                    "输出电流",
                    "A",
                    "latest_power_supply_output_current_a",
                ),
                "temperature": (
                    supply.read_temperature,
                    "模块温度",
                    "°C",
                    "latest_power_supply_temperature_c",
                ),
            }
            operation = readers.get(value_name)
            if operation is None:
                raise ValueError("未知电源读取项")
            if self.power_supply_kind != "ch341" and value_name in {"inputVoltage", "temperature"}:
                raise ValueError("TDK 电源不支持该读取项")
            reader, label, unit, cache_name = operation
            with self._supply_io_lock:
                self._reserve_power_supply_command(wait=False)
                measured = float(reader())
            if not math.isfinite(measured):
                raise RuntimeError(f"电源返回的{label}不是有效数值")
            setattr(self, cache_name, measured)
            self.notify("info", "电源读取", f"{label}：{measured:.2f} {unit}")
        if value_name == "outputVoltage" and self.automatic.state in {
            AutomaticTestState.IDLE,
            AutomaticTestState.COMPLETED,
        }:
            self._record_manual_voltage_point(measured)

    def start_power_meter(self, *, stable_window_s: float | None = None) -> None:
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
                stable_window_s=(
                    float(self.configuration["stableWindowS"])
                    if stable_window_s is None
                    else float(stable_window_s)
                ),
                stability_limit_percent=float(self.configuration["stabilityLimitPercent"]),
                driver_kind=str(self.configuration.get("powerMeterDriverKind", "caihuang")),
                serial_number=str(self.configuration.get("powerMeterSerialNumber", "")),
            )
            reader = PowerMeterReaderThread(settings)
            if self.automatic.state in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED}:
                self.stable_power_points.clear()
                self.efficiency_points.clear()
                self.pending_manual_stable_current_a = None
                self.pending_manual_stability_generation = None
                self.recorded_manual_stable_current_a = None
                self.recorded_manual_stability_generation = None
                self.series_revisions["stable"] += 1
            self.power_meter_reader = reader
            self.power_meter_error = ""
            self.latest_power = None
            self.latest_power_received_monotonic_s = None
            self.latest_power_sampled_at_utc = None
            self.power_points.clear()
            self.series_revisions["power"] += 1
            reader.reading.connect(self._on_power_reading)
            reader.ready.connect(self._on_power_ready)
            reader.failed.connect(self._on_power_failed)
            reader.status.connect(
                lambda msg: self.backend.set_status(
                    f"[工位 {self.station_id}] {msg}", station_id=self.station_id
                )
            )
            reader.finished.connect(self._on_power_finished)
            reader.start()

    def stop_power_meter(self) -> None:
        reader = self.power_meter_reader
        if reader is not None:
            self._expected_power_meter_stop = True
            try:
                reader.stop()
                reader.wait(3_000)
            finally:
                self._expected_power_meter_stop = False

    def _on_power_reading(self, reading: PowerMeterReading) -> None:
        with self._lock:
            self.latest_power = reading
            self.latest_power_received_monotonic_s = time.monotonic()
            self.latest_power_sampled_at_utc = datetime.now(timezone.utc).isoformat(
                timespec="milliseconds"
            )
            self.power_points.append({"elapsedS": reading.elapsed_s, "powerW": reading.power_w})
            self.series_revisions["power"] += 1
            automatic_active = self.automatic.state in {
                AutomaticTestState.SETTING_CURRENT,
                AutomaticTestState.WAITING_STABLE,
                AutomaticTestState.WAITING_VOLTAGE,
                AutomaticTestState.SAVING_POINT,
            }
            current_a = float(self.active_current_a or 0.0)
        if automatic_active and current_a > 0.0:
            result = self.power_drop_protection.observe(reading.power_w, reading.elapsed_s)
            if result.triggered and result.reference_power_w is not None:
                drop_percent = result.drop_w / result.reference_power_w * 100.0
                reason = (
                    f"功率从 {result.reference_power_w:.6g} W 降至 {reading.power_w:.6g} W，"
                    f"下降 {drop_percent:.1f}%"
                )
                self.automatic_terminal_reason = reason
                self._automatic_stop.set()
                self._automatic_resume.set()
                self._start_emergency_ramp_down(reason)
                self.notify(
                    "error",
                    "功率保护",
                    f"检测到功率异常下降，正在立即安全下电：{reason}",
                    code="power_protection",
                )
        elif self.automatic.state in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED}:
            if reading.stable:
                self._capture_manual_stable_point(reading)
            else:
                current_a = self.recorded_manual_stable_current_a
                generation = self.recorded_manual_stability_generation
                if (
                    current_a is not None
                    and current_a == self.active_current_a
                    and generation == reading.stability_generation
                    and current_a not in self.efficiency_points
                ):
                    self._manual_voltage_timer_generation += 1
                    self.recorded_manual_stable_current_a = None
                    self.recorded_manual_stability_generation = None
                    self.pending_manual_stable_current_a = current_a
                    self.pending_manual_stability_generation = (
                        self.power_meter_reader.reset_stability_window()
                        if self.power_meter_reader is not None
                        else None
                    )

    def _capture_manual_stable_point(self, reading: PowerMeterReading) -> None:
        current_a = self.pending_manual_stable_current_a
        generation = self.pending_manual_stability_generation
        if current_a is None or current_a <= 0.0 or current_a != self.active_current_a:
            return
        if generation is not None and reading.stability_generation != generation:
            return
        power_w = self._stable_power_w(reading)
        with self._lock:
            self.stable_power_points[current_a] = power_w
            self.efficiency_points.pop(current_a, None)
            self.pending_manual_stable_current_a = None
            self.pending_manual_stability_generation = None
            self.recorded_manual_stable_current_a = current_a
            self.recorded_manual_stability_generation = reading.stability_generation
            self.series_revisions["stable"] += 1
            self._manual_voltage_timer_generation += 1
            timer_generation = self._manual_voltage_timer_generation

        def delayed_read() -> None:
            time.sleep(5.0)
            if (
                timer_generation != self._manual_voltage_timer_generation
                or self.automatic.state not in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED}
                or self.active_current_a != current_a
            ):
                return
            try:
                with self._supply_io_lock:
                    self._reserve_power_supply_command(wait=True)
                    raw_voltage_v = float(self._require_power_supply().read_output_voltage())
                self.latest_power_supply_output_voltage_v = raw_voltage_v
                self._record_manual_voltage_point(raw_voltage_v)
            except Exception as exc:
                self.notify("error", "手动效率记录", str(exc))

        threading.Thread(
            target=delayed_read,
            name=f"manual-voltage-station-{self.station_id}",
            daemon=True,
        ).start()

    def _ensure_manual_recording_session(self) -> None:
        session = self.record_store.current_session
        if session is not None and session.mode == "manual" and session.ended_at_utc is None:
            return
        workbook_path = self.record_store.begin_session(
            Path(str(self.configuration["outputDir"])),
            str(self.configuration["sn"]),
            datetime.now(),
            test_station=str(self.configuration["station"]),
            mode="manual",
            product_model=str(self.configuration.get("productModel", "")),
            batch=str(self.configuration.get("batch", "")),
            settings=dict(self.configuration),
            devices=self._device_snapshots(),
        )
        self.backend.check_workbook_path_available(self.station_id, workbook_path)

    def _record_manual_voltage_point(self, raw_voltage_v: float) -> None:
        current_a = self.recorded_manual_stable_current_a
        generation = self.recorded_manual_stability_generation
        reading = self.latest_power
        if (
            current_a is None
            or generation is None
            or reading is None
            or current_a <= 0.0
            or current_a != self.active_current_a
            or reading.stability_generation != generation
            or not reading.stable
        ):
            return
        power_w = self._stable_power_w(reading)
        voltage_v = (
            compensate_tdk_output_voltage(raw_voltage_v, current_a)
            if self.power_supply_kind == "tdk"
            else raw_voltage_v
        )
        if not math.isfinite(voltage_v) or voltage_v <= 0.0:
            raise RuntimeError("输出电压无效，未记录效率点")
        frame = self.latest_spectrum
        try:
            self._validate_automatic_spectrum(frame)
        except _AutomaticPointQualityError as exc:
            self.notify("warning", "手动测试点未记录", str(exc))
            return
        efficiency = power_w / current_a / voltage_v
        stats = None if frame is None else frame.stats
        self._ensure_manual_recording_session()
        self.record_store.queue(
            ExcelTestRecord(
                current_a=current_a,
                voltage_v=voltage_v,
                power_w=power_w,
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
            ),
            actual_current_a=float(self.active_current_a or current_a),
            voltage_raw_v=raw_voltage_v,
            stable_span_w=reading.stable_span_w,
            stable_window_s=reading.stable_window_s,
            stability_percent=reading.stability_percent,
            stability_limit_percent=float(self.configuration["stabilityLimitPercent"]),
            integration_time_us=int(self.configuration["integrationTimeUs"]),
        )
        with self._lock:
            self.stable_power_points[current_a] = power_w
            self.efficiency_points[current_a] = efficiency * 100.0
            self.series_revisions["stable"] += 1
        self.export_status = "recording"
        self.notify("info", "手动测试点", f"已暂存 {current_a:g} A 测试点，可保存 Excel")

    def save_manual_records(self) -> dict[str, Any]:
        session = self.record_store.current_session
        if session is None or session.mode != "manual" or not self.record_store.unsaved_records():
            raise ValueError("没有尚未保存的手动测试点")
        sn = str(self.configuration.get("sn", "")).strip()
        test_station = str(self.configuration.get("station", "")).strip()
        output_dir = str(self.configuration.get("outputDir", "")).strip()
        if not sn:
            raise ValueError("请填写产品 SN")
        if not test_station:
            raise ValueError("请填写测试站别")
        if not output_dir:
            raise ValueError("请选择测试数据输出目录")
        workbook_path = self.record_store.retarget_manual_session(
            Path(output_dir),
            sn,
            test_station,
            settings=dict(self.configuration),
        )
        self.backend.check_workbook_path_available(self.station_id, workbook_path)
        self.record_store.complete_session(
            SessionStatus.COMPLETED,
            "手动单点保存完成",
            shutdown_confirmed=None,
        )
        self.export_status = "exporting"
        try:
            saved_path = self._save_record_store()
        except Exception as exc:
            self.export_status = "failed"
            self.export_error = str(exc)
            raise
        self.export_status = "completed"
        self.export_path = "" if saved_path is None else str(saved_path)
        self.notify("info", "导出完成", f"手动测试记录已保存：{self.export_path}")
        return {"outputPath": self.export_path}

    def _on_power_ready(self) -> None:
        self.automatic.mark_power_meter_ready()

    def _on_power_failed(self, message: str) -> None:
        self.power_meter_error = str(message)
        self.notify("error", "功率计", message)
        if self.automatic.state not in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED}:
            self.automatic.power_meter_ready = False
            if self._aging_test_selected():
                reason = f"AU 老化功率采集失败：{message}"
                self.automatic_terminal_reason = reason
                self._automatic_stop.set()
                self._automatic_resume.set()
                return
            self._request_automatic_point_retry(
                AttemptValidity.DEVICE_ERROR,
                f"功率计错误：{message}",
                pause_kind="power_meter_disconnected",
            )

    def _on_power_finished(self) -> None:
        self.power_meter_reader = None
        self.automatic.power_meter_ready = False
        if self._expected_power_meter_stop:
            return
        if self._aging_test_selected() and self.automatic.state not in {
            AutomaticTestState.IDLE,
            AutomaticTestState.COMPLETED,
        }:
            self.automatic_terminal_reason = "AU 老化功率采集已停止"
            self._automatic_stop.set()
            self._automatic_resume.set()
            return
        if not self._aging_test_selected():
            self._request_automatic_point_retry(
                AttemptValidity.DEVICE_ERROR,
                "功率计采集已停止",
                pause_kind="power_meter_disconnected",
            )

    def set_relative_zero(self, params: dict[str, Any]) -> None:
        if self.power_meter_reader is not None:
            raise RuntimeError("请先停止功率采集，再切换相对调零")
        resource = str(self.configuration.get("powerMeterResource", "")).strip()
        if not resource:
            raise ValueError("请先选择功率计资源")
        enabled = bool(params.get("enabled", False))
        driver_kind = str(self.configuration.get("powerMeterDriverKind", "caihuang")).lower()
        if driver_kind == "ophir":
            raise RuntimeError("Ophir 不支持当前的相对调零指令")
        if driver_kind == "laserpoint":
            raise RuntimeError("LaserPoint 不支持彩煌的相对调零指令")
        from tools.power_meter_mvp import CaihuangPowerMeter, CoherentPowerMeter

        meter_class = CoherentPowerMeter if driver_kind == "coherent" else CaihuangPowerMeter
        meter = meter_class(resource)
        try:
            meter.set_relative_zero(enabled)
        finally:
            meter.close()
        state = "已启用" if enabled else "已停用"
        self.notify("info", "相对调零", f"功率计相对调零{state}")

    def start_spectrometer(self) -> None:
        with self._lock:
            if self.spectrometer_reader is not None:
                return
            device_id = self.configuration.get("spectrometerDeviceId")
            device_id_val = None if device_id in {None, ""} else int(device_id)
            if device_id_val is None:
                raise ValueError("请先选择光谱仪设备")
            spectrometer_serial = _normalize_device_serial(
                self.configuration.get("spectrometerSerialNumber")
            )
            if spectrometer_serial:
                self.backend.check_device_conflicts(
                    self.station_id,
                    "spectrometerSerial",
                    spectrometer_serial,
                )
            else:
                self.backend.check_device_conflicts(
                    self.station_id,
                    "spectrometer",
                    device_id_val,
                )

            settings = SpectrometerSettings(
                integration_time_us=int(self.configuration["integrationTimeUs"]),
                interval_ms=int(self.configuration["spectrometerIntervalMs"]),
                device_id=device_id_val,
                auto_integration_enabled=bool(self.configuration["autoIntegration"]),
                average_frames=int(self.configuration["spectrumAverageFrames"]),
                pib_center_wavelength_nm=float(self.configuration.get("pibCenterWavelengthNm", 976.0)),
                pib_half_range_nm=float(self.configuration.get("pibHalfRangeNm", 1.5)),
                serial_number=str(self.configuration.get("spectrometerSerialNumber", "")),
            )
            reader = SpectrometerReaderThread(settings, headless=True)
            self.spectrometer_reader = reader
            self.spectrometer_error = ""
            self.latest_spectrometer_reading = None
            self.latest_spectrum = None
            self.latest_spectrum_received_monotonic_s = None
            self.latest_spectrum_sampled_at_utc = None
            self.series_revisions["spectrum"] += 1
            reader.reading.connect(self._on_spectrometer_reading)
            reader.ready.connect(self._on_spectrometer_ready)
            reader.failed.connect(self._on_spectrometer_failed)
            reader.status.connect(
                lambda msg: self.backend.set_status(
                    f"[工位 {self.station_id}] {msg}", station_id=self.station_id
                )
            )
            reader.device_opened.connect(self._on_spectrometer_device_opened)
            reader.integration_time_changed.connect(self._on_integration_changed)
            reader.finished.connect(self._on_spectrometer_finished)
            reader.start()

    def stop_spectrometer(self) -> None:
        reader = self.spectrometer_reader
        if reader is not None:
            self._expected_spectrometer_stop = True
            try:
                reader.stop()
                reader.wait(3_000)
            finally:
                self._expected_spectrometer_stop = False

    def _on_spectrometer_reading(self, reading: SpectrometerReading) -> None:
        reader = self.spectrometer_reader
        frame = reader.take_latest_spectrum() if reader is not None else None
        with self._lock:
            self.latest_spectrometer_reading = reading
            self._update_spectrum_center_lock(reading)
            wavelength_stability = self.wavelength_stability_detector.add_sample(
                time.monotonic(),
                reading.centroid_nm,
            )
            self.latest_wavelength_stable = wavelength_stability.stable
            self.latest_wavelength_span_nm = wavelength_stability.span_w
            if frame is not None:
                self.latest_spectrum = frame
                self.latest_spectrum_received_monotonic_s = time.monotonic()
                self.latest_spectrum_sampled_at_utc = datetime.now(timezone.utc).isoformat(
                    timespec="milliseconds"
                )
                self.spectrum_revision += 1
                self.series_revisions["spectrum"] += 1
                saturated = bool(frame.saturation and frame.saturation.saturated)
            else:
                saturated = False
        if (
            saturated
            and self.automatic.settings is not None
            and self.automatic.settings.use_spectrometer
            and self.automatic.state in {
                AutomaticTestState.WAITING_STABLE,
                AutomaticTestState.WAITING_VOLTAGE,
            }
        ):
            reason = "光谱饱和；请缩短积分时间或调整光路后重试当前测试点"
            self._request_automatic_point_retry(
                AttemptValidity.SATURATED,
                reason,
                hold_current=True,
            )
            self.notify(
                "warning",
                "光谱饱和",
                f"{reason}；当前电流保持不变",
                code="spectrum_saturation",
            )

    def _update_spectrum_center_lock(self, reading: SpectrometerReading) -> None:
        locked_center: float | None = None
        lock_reference = ""
        current_lock = self.spectrum_center_locked_nm
        if current_lock is not None:
            peak_nm = float(reading.peak_wavelength_nm)
            if (
                not math.isfinite(peak_nm)
                or abs(peak_nm - current_lock) <= SPECTRUM_CENTER_LOCK_HALF_RANGE_NM
            ):
                self.spectrum_center_candidate_nm = None
                self.spectrum_center_candidate_count = 0
                return
            (
                self.spectrum_center_candidate_nm,
                self.spectrum_center_candidate_count,
            ) = self._advance_spectrum_center_candidate(
                peak_nm,
                self.spectrum_center_candidate_nm,
                self.spectrum_center_candidate_count,
            )
            if self.spectrum_center_candidate_count >= SPECTRUM_CENTER_LOCK_REQUIRED_SAMPLES:
                locked_center = self.spectrum_center_candidate_nm
                lock_reference = "新主峰"
        else:
            (
                self.spectrum_center_candidate_nm,
                self.spectrum_center_candidate_count,
            ) = self._advance_spectrum_center_candidate(
                reading.peak_wavelength_nm,
                self.spectrum_center_candidate_nm,
                self.spectrum_center_candidate_count,
            )
            (
                self.spectrum_centroid_candidate_nm,
                self.spectrum_centroid_candidate_count,
            ) = self._advance_spectrum_center_candidate(
                reading.centroid_nm,
                self.spectrum_centroid_candidate_nm,
                self.spectrum_centroid_candidate_count,
            )
            if self.spectrum_center_candidate_count >= SPECTRUM_CENTER_LOCK_REQUIRED_SAMPLES:
                locked_center = self.spectrum_center_candidate_nm
                lock_reference = "主峰"
            elif self.spectrum_centroid_candidate_count >= SPECTRUM_CENTER_LOCK_REQUIRED_SAMPLES:
                locked_center = self.spectrum_centroid_candidate_nm
                lock_reference = "中心波长"

        if locked_center is None:
            return
        self.spectrum_center_locked_nm = locked_center
        self.spectrum_center_candidate_nm = None
        self.spectrum_center_candidate_count = 0
        self.spectrum_centroid_candidate_nm = None
        self.spectrum_centroid_candidate_count = 0
        self.backend.set_status(
            f"[工位 {self.station_id}] 光谱横轴已按{lock_reference}锁定："
            f"{locked_center:.3f} nm ± {SPECTRUM_CENTER_LOCK_HALF_RANGE_NM:g} nm",
            station_id=self.station_id,
        )

    @staticmethod
    def _advance_spectrum_center_candidate(
        raw_center_nm: float,
        candidate_nm: float | None,
        candidate_count: int,
    ) -> tuple[float | None, int]:
        center = float(raw_center_nm)
        if not math.isfinite(center):
            return None, 0
        if candidate_nm is None or abs(center - candidate_nm) > SPECTRUM_CENTER_LOCK_TOLERANCE_NM:
            return center, 1
        next_count = candidate_count + 1
        return candidate_nm + (center - candidate_nm) / next_count, next_count

    def _on_spectrometer_ready(self) -> None:
        self.automatic.mark_spectrum_meter_ready()

    def _on_spectrometer_failed(self, message: str) -> None:
        self.spectrometer_error = str(message)
        self.notify("error", "光谱仪", message)
        if (
            self.automatic.settings is not None
            and self.automatic.settings.use_spectrometer
            and self.automatic.state not in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED}
        ):
            self.automatic.spectrum_meter_ready = False
            self._request_automatic_point_retry(
                AttemptValidity.DEVICE_ERROR,
                f"光谱仪错误：{message}",
                pause_kind="spectrometer_disconnected",
            )

    def _on_spectrometer_finished(self) -> None:
        self.spectrometer_reader = None
        self.automatic.spectrum_meter_ready = False
        if not self._expected_spectrometer_stop and self.automatic.settings is not None and self.automatic.settings.use_spectrometer:
            self._request_automatic_point_retry(
                AttemptValidity.DEVICE_ERROR,
                "光谱仪采集已停止",
                pause_kind="spectrometer_disconnected",
            )

    def _on_spectrometer_device_opened(self, device_id: int) -> None:
        actual_device_id = int(device_id)
        resource = next(
            (
                item
                for item in self.backend.spectrometer_resources
                if int(item.get("deviceId", -1)) == actual_device_id
            ),
            None,
        )
        serial_number = _normalize_device_serial(
            None if resource is None else resource.get("serialNumber")
        ) or _normalize_device_serial(self.configuration.get("spectrometerSerialNumber"))
        with self._lock:
            previous_value = self.configuration.get("spectrometerDeviceId")
            previous_device_id = (
                None if previous_value in {None, ""} else int(previous_value)
            )
            if previous_device_id != actual_device_id:
                self.backend.check_device_conflicts(
                    self.station_id,
                    "spectrometer",
                    actual_device_id,
                )
            label = _spectrometer_label(actual_device_id, serial_number)
            self.configuration["spectrometerDeviceId"] = actual_device_id
            self.configuration["spectrometerResource"] = label
            self.configuration["spectrometerSerialNumber"] = serial_number

        with self.backend._lock:
            self.backend.spectrometer_resources = [
                resource
                for resource in self.backend.spectrometer_resources
                if int(resource.get("deviceId", -1)) not in {
                    previous_device_id,
                    actual_device_id,
                }
            ]
            self.backend.spectrometer_resources.append(
                {
                    "label": label,
                    "deviceId": actual_device_id,
                    "serialNumber": serial_number,
                }
            )
        self.backend._persist_configuration()

        if previous_device_id is not None and previous_device_id != actual_device_id:
            # OceanDirect IDs are process-local enumeration handles and may
            # normally change after restarting the SDK or reconnecting USB.
            # A successful automatic rebind needs no operator action, so keep
            # it in the status stream instead of raising a warning toast.
            self.backend.set_status(
                f"[工位 {self.station_id}] 光谱仪设备 ID 已从 {previous_device_id} 自动更新为 {actual_device_id}",
                station_id=self.station_id,
            )

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


    def reset_charts(self) -> None:
        with self._lock:
            self.power_points.clear()
            self.stable_power_points.clear()
            self.efficiency_points.clear()
            self.aging_power_points.clear()
            self.latest_spectrum = None
            self.latest_spectrum_received_monotonic_s = None
            self.latest_spectrum_sampled_at_utc = None
            self.spectrum_center_candidate_nm = None
            self.spectrum_center_candidate_count = 0
            self.spectrum_centroid_candidate_nm = None
            self.spectrum_centroid_candidate_count = 0
            self.spectrum_center_locked_nm = None
            # PD 曲线是独立采集链路；普通图表重置不应让 PD 视图误判为有新数据。
            for key in ("power", "stable", "aging", "spectrum"):
                self.series_revisions[key] += 1

    def stop_all(self) -> None:
        errors: list[str] = []
        try:
            self.end_automatic()
        except Exception as exc:
            errors.append(f"自动测试停止失败：{exc}")
        try:
            self.stop_power_meter()
        except Exception as exc:
            errors.append(f"功率计停止失败：{exc}")
        try:
            self.stop_spectrometer()
        except Exception as exc:
            errors.append(f"光谱仪停止失败：{exc}")
        try:
            self.safe_ramp_down()
        except Exception as exc:
            errors.append(f"安全下电失败：{exc}")
        if errors:
            raise RuntimeError("；".join(errors))

    def _history_records(self, params: dict[str, Any]) -> tuple[Any, ...]:
        from combined_test.test_history import find_test_history

        output = str(params.get("outputDir", self.configuration["outputDir"])).strip()
        sn = str(params.get("sn", self.configuration["sn"])).strip()
        return find_test_history(output, sn) if output and sn else ()

    def _select_latest_test_reference(self, params: dict[str, Any]) -> None:
        records = self._history_records(params)
        self._test_reference = records[0] if records else None

    def summary_snapshot(self) -> dict[str, Any]:
        with self._lock:
            supply = self._power_supply()
            readiness = self.automatic_readiness()
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
                "canStart": readiness["ready"],
                "readiness": readiness,
                "canPause": self.automatic.state not in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED, AutomaticTestState.PAUSED, AutomaticTestState.RAMPING_DOWN},
                "canEnd": self.automatic.state not in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED, AutomaticTestState.RAMPING_DOWN},
                "terminalOutcome": self.automatic_terminal_outcome,
                "terminalReason": self.automatic_terminal_reason,
                "pauseKind": self.automatic_pause_kind,
                "pauseStartedAtUtc": self.automatic_pause_started_at_utc,
                "pauseDeadlineAtUtc": self.automatic_pause_deadline_at_utc,
                "pauseActions": list(self.automatic_pause_actions),
                "savedPointCount": len(self.record_store.recorded_currents),
                "exportedPointCount": len(self.record_store.exported_currents),
                "journalPath": "" if self.record_store.journal_path is None else str(self.record_store.journal_path),
                "canNextDevice": self.automatic.state == AutomaticTestState.COMPLETED and self.shutdown_confirmed,
                "canRetryExport": self.automatic.state == AutomaticTestState.COMPLETED and self.export_status == "failed",
                "controlOwner": "manual" if self.manual_controls_allowed() else "automatic",
                "manualControlsAllowed": self.manual_controls_allowed(),
                "shutdown": self.shutdown_snapshot(),
                "exportStatus": self.export_status,
                "exportPath": self.export_path,
                "exportError": self.export_error,
            }

    def build_stable_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "currentA": current,
                    "powerW": power,
                    "efficiencyPercent": self.efficiency_points.get(current),
                }
                for current, power in sorted(self.stable_power_points.items())
            ]

    def build_aging_power_snapshot(self) -> list[dict[str, float]]:
        with self._lock:
            return [dict(point) for point in self.aging_power_points]

    def build_spectrum_snapshot(
        self,
        *,
        allow_cached_grid: bool,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        with self._lock:
            frame = self.latest_spectrum
            wavelength = [] if frame is None else [float(v) for v in frame.wavelength]
            intensity = [] if frame is None else [float(v) for v in frame.intensity]
            grid_changed = wavelength != self._last_spectrum_grid
            if grid_changed:
                self._last_spectrum_grid = wavelength
                self.spectrum_grid_revision += 1

            spectrum: dict[str, Any] = {
                "gridRevision": self.spectrum_grid_revision,
                "intensity": intensity,
            }
            if not allow_cached_grid or grid_changed:
                spectrum["wavelengthNm"] = wavelength
            peaks = [
                {
                    "label": item.label,
                    "peakWavelengthNm": _finite(item.peak_wavelength_nm),
                    "peakIntensity": _finite(item.peak_intensity),
                }
                for item in (() if frame is None else frame.peak_annotations)
            ]
            return spectrum, peaks
