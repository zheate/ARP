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
from typing import Any, Callable, Literal, Sequence

from combined_test.automation import (
    AutomaticCurrentPoint,
    AutomaticTestOrchestrator,
    AutomaticTestSettings,
    AutomaticTestState,
    MIN_POWER_SUPPLY_COMMAND_INTERVAL_S,
    PowerDropProtectionDetector,
    build_custom_hold_ramp_currents,
    build_ramp_down_currents,
    validate_automatic_test_settings,
)
from combined_test.automatic_samples import (
    AutomaticSampleRecorder,
    AutomaticSecondSample,
    automatic_sample_path,
)
from combined_test.core import WavelengthStabilityDetector
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
from combined_test.concurrency import Signal, WorkerThread
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
from combined_test.record_store import (
    AttemptValidity,
    DeviceSnapshot,
    SessionRecordStore,
    SessionStatus,
)
from combined_test.runtime_logging import get_runtime_logger, runtime_log_path
from combined_test.spectrum import (
    SPECTRUM_CENTER_LOCK_HALF_RANGE_NM,
    SPECTRUM_CENTER_LOCK_REQUIRED_SAMPLES,
    SPECTRUM_CENTER_LOCK_TOLERANCE_NM,
)
from combined_test.spectrum_math import calculate_pib
from combined_test.tdk_power_supply import (
    TdkLambdaPowerSupply,
    compensate_tdk_output_voltage,
    list_tdk_serial_resources,
)

from .settings import JsonSettings


_LOGGER = get_runtime_logger("headless_backend")
MIN_SPECTRUM_PEAK_INTENSITY = 500.0
AGING_TEST_SCRIPT_ID = "aging"
AGING_START_CURRENT_A = 2.0
AGING_TARGET_CURRENT_A = 20.0
AGING_CURRENT_STEP_A = 2.0
AGING_RAMP_INTERVAL_S = 1.0
AGING_HOLD_DURATION_S = 2.0 * 60.0 * 60.0


class _DeviceDiscoveryThread(WorkerThread):
    """Run one blocking hardware discovery task without blocking snapshots."""

    completed = Signal(object)
    status = Signal(str)
    failed = Signal(str)

    def __init__(self, discover: Callable[[Callable[[str], None]], object]) -> None:
        super().__init__()
        self._discover = discover

    def run(self) -> None:
        try:
            self.completed.emit(self._discover(self.status.emit))
        except Exception as exc:
            _LOGGER.exception("设备识别线程失败")
            self.failed.emit(str(exc))


class _AutomaticPointRetry(RuntimeError):
    """Internal control-flow signal used to restart the active current point."""


class _AutomaticPointQualityError(RuntimeError):
    def __init__(self, validity: AttemptValidity, message: str, *, hold_current: bool = False) -> None:
        super().__init__(message)
        self.validity = validity
        self.hold_current = bool(hold_current)


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
    "powerMeterResource": "",
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
    "centerWavelengthSpecNm": 976.0,
    "centerWavelengthToleranceNm": 1.5,
    "fwhmMaximumNm": 0.6,
    "pibMinimumPercent": 95.0,
    "stableWindowS": 3.0,
    "stableToleranceW": 0.15,
    "initialCurrentA": 1.0,
    "targetCurrentA": 20.0,
    "currentStepA": 3.0,
    "selectedScriptId": "standard",
    "currentPlanMode": "range",
    "customCurrentPoints": [],
    "pointTimeoutS": 120.0,
    "rampDownStepA": 5.0,
    "rampDownIntervalS": 1.1,
    "pauseRampDownTimeoutS": 30.0,
}

BUILTIN_TEST_SCRIPTS: tuple[dict[str, Any], ...] = (
    {"id": "standard", "name": "标准测试", "builtin": True, "currentPlanMode": "range"},
    {"id": "hold-points", "name": "增加停留点", "builtin": True, "currentPlanMode": "custom"},
    {
        "id": AGING_TEST_SCRIPT_ID,
        "name": "AU 老化测试",
        "builtin": True,
        "currentPlanMode": "custom",
        "powerSupplyKinds": ["ch341"],
    },
)


def _apply_aging_script_configuration(configuration: dict[str, Any]) -> None:
    configuration.update(
        {
            "initialCurrentA": AGING_START_CURRENT_A,
            "targetCurrentA": AGING_TARGET_CURRENT_A,
            "currentStepA": AGING_CURRENT_STEP_A,
            "currentPlanMode": "custom",
            "customCurrentPoints": [
                {"currentA": AGING_TARGET_CURRENT_A, "dwellS": AGING_HOLD_DURATION_S}
            ],
        }
    )


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


def _default_pd_state() -> dict[str, Any]:
    """Create an isolated PD acquisition state for one station."""
    return {
        "state": "idle",
        "status": "未启动",
        "detecting": False,
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


@dataclass
class ExportTask:
    station_id: str
    target_path: Path
    records: list[ExcelTestRecord]
    callback: Callable[[str | None, Exception | None], None] | None = None
    session: Any | None = None
    attempts: tuple[Any, ...] = ()
    automatic_samples_path: Path | None = None


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
                saved_path = save_test_records(
                    task.target_path,
                    task.records,
                    session=task.session,
                    attempts=task.attempts,
                    automatic_samples_path=task.automatic_samples_path,
                )
                out_path = str(saved_path)
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


def _normalize_power_meter_configuration(
    configuration: dict[str, Any],
    options: Sequence[PowerMeterOption] = (),
) -> bool:
    before = (
        configuration.get("powerMeterResource"),
        configuration.get("powerMeterDriverKind"),
    )
    resource = extract_power_resource_name(str(before[0] or ""))
    configuration["powerMeterResource"] = resource
    if resource.startswith("OPHIR::USB::"):
        configuration["powerMeterDriverKind"] = "ophir"
    else:
        matched = next((item for item in options if item.resource == resource), None)
        if matched is not None:
            configuration["powerMeterDriverKind"] = matched.driver_kind
        elif str(configuration.get("powerMeterDriverKind", "")).lower() == "ophir":
            configuration["powerMeterDriverKind"] = "caihuang"
    after = (
        configuration.get("powerMeterResource"),
        configuration.get("powerMeterDriverKind"),
    )
    return after != before


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
        self.power_points: deque[dict[str, float]] = deque(maxlen=1_200)
        self.power_drop_protection = PowerDropProtectionDetector()
        self._expected_power_meter_stop = False

        self.spectrometer_reader: SpectrometerReaderThread | None = None
        self.spectrometer_error = ""
        self.latest_spectrometer_reading: SpectrometerReading | None = None
        self.latest_spectrum: SpectrumFrame | None = None
        self.latest_spectrum_received_monotonic_s: float | None = None
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
        self.series_revisions = {"power": 0, "stable": 0, "spectrum": 0, "pd": 0}
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
        self.automatic_output_shutdown_unconfirmed = False
        self.automatic_sample_path = ""
        self.automatic_sample_recorder: AutomaticSampleRecorder | None = None
        self.record_store = SessionRecordStore()
        self._automatic_thread: threading.Thread | None = None
        self._automatic_stop = threading.Event()
        self._automatic_resume = threading.Event()
        self._automatic_resume.set()
        self._automatic_retry_requested = False
        self._automatic_retry_reason = ""
        self._automatic_pause_holds_current = False
        self._automatic_pause_generation = 0
        self._emergency_ramp_thread: threading.Thread | None = None
        self._safe_ramp_lock = threading.Lock()
        self._supply_io_lock = threading.RLock()
        self._expected_spectrometer_stop = False
        self._test_reference: Any | None = None
        self.export_status: str = "idle"
        self.export_path: str = ""
        self.export_error: str = ""

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

    def _begin_recording_session(self, settings: AutomaticTestSettings) -> Path:
        started_at = datetime.now()
        workbook_path = self.record_store.begin_session(
            Path(str(self.configuration["outputDir"])),
            str(self.configuration["sn"]),
            started_at,
            test_station=str(self.configuration["station"]),
            mode="automatic",
            product_model=str(self.configuration.get("productModel", "")),
            batch=str(self.configuration.get("batch", "")),
            settings=dict(self.configuration),
            devices=self._device_snapshots(),
        )
        self.backend.check_workbook_path_available(self.station_id, workbook_path)
        self.record_store.configure_sequence(self.automatic.currents)
        self._close_automatic_sample_recorder()
        self.automatic_sample_path = ""
        # The one-second trace belongs only to TDK custom-hold plans. CH341
        # must never run this extra polling path.
        if self.power_supply_kind != "tdk" or settings.current_plan_mode != "custom":
            return workbook_path
        sample_path = automatic_sample_path(workbook_path)
        self.automatic_sample_recorder = AutomaticSampleRecorder(sample_path)
        self.automatic_sample_path = str(sample_path)
        return workbook_path

    def _close_automatic_sample_recorder(self) -> None:
        recorder = self.automatic_sample_recorder
        self.automatic_sample_recorder = None
        if recorder is not None:
            recorder.close()

    def _start_emergency_ramp_down(self, reason: str) -> None:
        """Start physical shutdown independently of Excel or acquisition callbacks."""
        with self._lock:
            current = self._emergency_ramp_thread
            if current is not None and current.is_alive():
                return

            def run() -> None:
                try:
                    self.safe_ramp_down()
                except Exception as exc:
                    self.automatic_output_shutdown_unconfirmed = True
                    self.notify("error", "安全下电失败", f"{reason}：{exc}")

            self._emergency_ramp_thread = threading.Thread(
                target=run,
                name=f"emergency-ramp-station-{self.station_id}",
                daemon=True,
            )
            self._emergency_ramp_thread.start()

    def _schedule_pause_safety_timeout(self) -> None:
        settings = self.automatic.settings
        timeout_s = 0.0 if settings is None else settings.pause_ramp_down_timeout_s
        if timeout_s <= 0.0 or self._automatic_pause_holds_current:
            return
        self._automatic_pause_generation += 1
        generation = self._automatic_pause_generation

        def wait_for_timeout() -> None:
            if self._automatic_resume.wait(timeout_s):
                return
            if (
                generation != self._automatic_pause_generation
                or self.automatic.state != AutomaticTestState.PAUSED
                or self._automatic_pause_holds_current
            ):
                return
            self.automatic_terminal_reason = self.automatic.pause_reason or "暂停等待超时"
            self._automatic_stop.set()
            self._automatic_resume.set()
            self._start_emergency_ramp_down("暂停等待超时")

        threading.Thread(
            target=wait_for_timeout,
            name=f"pause-safety-station-{self.station_id}",
            daemon=True,
        ).start()

    def _request_automatic_point_retry(
        self,
        validity: AttemptValidity,
        reason: str,
        *,
        hold_current: bool = False,
    ) -> None:
        if self.automatic.state in {
            AutomaticTestState.IDLE,
            AutomaticTestState.COMPLETED,
            AutomaticTestState.RAMPING_DOWN,
        }:
            return
        if not self._automatic_retry_requested:
            current_a = self.automatic.current_a
            if current_a is not None:
                self.record_store.record_invalid_attempt(
                    current_a,
                    validity,
                    reason,
                    integration_time_us=int(self.configuration["integrationTimeUs"]),
                )
        self._automatic_retry_requested = True
        self._automatic_retry_reason = str(reason)
        self._automatic_pause_holds_current = bool(hold_current)
        self.automatic.pause(reason)
        self.automatic_detail = f"已暂停：{reason}"
        self._automatic_resume.clear()
        self._schedule_pause_safety_timeout()

    def _write_automatic_second_sample(
        self,
        *,
        point_index: int,
        elapsed_s: float,
        target_current_a: float,
    ) -> None:
        recorder = self.automatic_sample_recorder
        if recorder is None or self.power_supply_kind != "tdk":
            return
        errors: list[str] = []
        output_voltage_raw_v: float | None = None
        output_voltage_v: float | None = None
        supply = self._require_power_supply()
        try:
            with self._supply_io_lock:
                output_current_a = float(supply.read_output_current())
                output_voltage_raw_v = float(supply.read_output_voltage())
            output_voltage_v = compensate_tdk_output_voltage(
                output_voltage_raw_v,
                output_current_a,
            )
        except Exception as exc:
            output_current_a = None
            errors.append(f"电流/电压读取失败：{exc}")
        with self._lock:
            power = self.latest_power
            frame = self.latest_spectrum
        stats = None if frame is None else frame.stats
        pib = None
        if frame is not None:
            try:
                pib = calculate_pib(
                    frame.wavelength,
                    frame.intensity,
                    center_nm=float(self.configuration.get("pibCenterWavelengthNm", 976.0)),
                    half_range_nm=float(self.configuration.get("pibHalfRangeNm", 1.5)),
                )
            except Exception as exc:
                errors.append(f"PIB 计算失败：{exc}")
        recorder.write(
            AutomaticSecondSample(
                point_index=point_index,
                point_elapsed_s=elapsed_s,
                target_current_a=target_current_a,
                output_current_a=output_current_a,
                output_voltage_raw_v=output_voltage_raw_v,
                output_voltage_v=output_voltage_v,
                optical_power_w=None if power is None else power.power_w,
                peak_wavelength_nm=None if stats is None else stats.peak_wavelength_nm,
                centroid_nm=None if stats is None else stats.centroid_nm,
                fwhm_nm=None if stats is None else stats.fwhm_nm,
                pib=pib,
                spectrum_wavelengths_nm=() if frame is None else tuple(float(v) for v in frame.wavelength),
                spectrum_intensities=() if frame is None else tuple(float(v) for v in frame.intensity),
                error="；".join(errors),
            )
        )

    def _await_automatic_resume(self) -> float | None:
        if self._automatic_stop.is_set():
            return None
        if self._automatic_resume.is_set():
            if self._automatic_retry_requested:
                self._automatic_retry_requested = False
                raise _AutomaticPointRetry(
                    self._automatic_retry_reason or "重试当前测试点"
                )
            return 0.0
        paused_at = time.monotonic()
        while not self._automatic_resume.is_set():
            if self._automatic_stop.wait(0.05):
                return None
        paused_duration = time.monotonic() - paused_at
        if self._automatic_stop.is_set():
            return None
        if self._automatic_retry_requested:
            self._automatic_retry_requested = False
            raise _AutomaticPointRetry(self._automatic_retry_reason or "重试当前测试点")
        return paused_duration

    def _wait_automatic_duration(
        self,
        duration_s: float,
        *,
        sample_point_index: int | None = None,
        target_current_a: float = 0.0,
        on_tick: Callable[[], None] | None = None,
    ) -> bool:
        active_elapsed = 0.0
        next_sample_s = 1.0
        last_tick = time.monotonic()
        while active_elapsed < max(0.0, duration_s):
            paused_duration = self._await_automatic_resume()
            if paused_duration is None or self._automatic_stop.is_set():
                return False
            last_tick += paused_duration
            remaining = duration_s - active_elapsed
            if self._automatic_stop.wait(min(0.05, remaining)):
                return False
            now = time.monotonic()
            active_elapsed += max(0.0, now - last_tick)
            last_tick = now
            if on_tick is not None:
                on_tick()
            while sample_point_index is not None and next_sample_s <= min(active_elapsed, duration_s) + 0.05:
                self._write_automatic_second_sample(
                    point_index=sample_point_index,
                    elapsed_s=min(next_sample_s, duration_s),
                    target_current_a=target_current_a,
                )
                next_sample_s += 1.0
        return True

    def _save_record_store(self) -> Path | None:
        records = tuple(self.record_store.snapshot())
        session = self.record_store.current_session
        path = self.record_store.workbook_path
        if not records or session is None or path is None:
            return None
        recorder = self.automatic_sample_recorder
        if recorder is not None:
            recorder.flush_now()
        attempts = self.record_store.list_attempts(session.session_id)
        worker = getattr(self.backend, "export_worker", None)
        if worker is None:
            saved_path = save_test_records(
                path,
                records,
                session=session,
                attempts=attempts,
                automatic_samples_path=(
                    Path(self.automatic_sample_path) if self.automatic_sample_path else None
                ),
            )
        else:
            completed = threading.Event()
            result: dict[str, Any] = {}

            def on_export_done(out_path: str | None, error: Exception | None) -> None:
                result["path"] = out_path
                result["error"] = error
                completed.set()

            submitted = worker.submit(
                ExportTask(
                    station_id=self.station_id,
                    target_path=path,
                    records=list(records),
                    callback=on_export_done,
                    session=session,
                    attempts=attempts,
                    automatic_samples_path=(
                        Path(self.automatic_sample_path) if self.automatic_sample_path else None
                    ),
                )
            )
            if not submitted:
                raise RuntimeError("Excel 保存队列已满，请稍后重试")
            safety_started = False
            while not completed.wait(0.05):
                if self._automatic_stop.is_set() and not safety_started:
                    safety_started = True
                    self._start_emergency_ramp_down(
                        self.automatic_terminal_reason or "保存期间收到停止请求"
                    )
            error = result.get("error")
            if error is not None:
                raise error
            out_path = result.get("path")
            if not out_path:
                raise RuntimeError("Excel 保存未返回目标路径")
            saved_path = Path(str(out_path))
        self.record_store.mark_saved(records)
        self.export_path = str(saved_path)
        return saved_path

    def _delete_embedded_automatic_samples(self, saved_path: Path | None) -> None:
        """Remove the temporary CSV only after its final workbook was saved."""
        if saved_path is None or not self.automatic_sample_path:
            return
        sample_path = Path(self.automatic_sample_path)
        expected_path = automatic_sample_path(Path(saved_path))
        if sample_path.resolve(strict=False) != expected_path.resolve(strict=False):
            self.notify("error", "逐秒记录", f"临时 CSV 路径不匹配，未删除：{sample_path}")
            return
        sample_path.unlink(missing_ok=True)
        self.automatic_sample_path = ""

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
                    float(self.configuration["stableToleranceW"]),
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
        self.record_store.begin_session(
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
            stable_tolerance_w=float(self.configuration["stableToleranceW"]),
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
        if (
            not self._aging_test_selected()
            and self.automatic.state not in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED}
        ):
            self.automatic.power_meter_ready = False
            self._request_automatic_point_retry(
                AttemptValidity.DEVICE_ERROR,
                f"功率计错误：{message}",
            )

    def _on_power_finished(self) -> None:
        self.power_meter_reader = None
        self.automatic.power_meter_ready = False
        if not self._expected_power_meter_stop and not self._aging_test_selected():
            self._request_automatic_point_retry(
                AttemptValidity.DEVICE_ERROR,
                "功率计采集已停止",
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
                self.spectrum_revision += 1
                self.series_revisions["spectrum"] += 1
                saturated = bool(frame.saturation and frame.saturation.saturated)
            else:
                saturated = False
        if saturated and not self._aging_test_selected() and self.automatic.state in {
            AutomaticTestState.WAITING_STABLE,
            AutomaticTestState.WAITING_VOLTAGE,
        }:
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
            not self._aging_test_selected()
            and self.automatic.state not in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED}
        ):
            self.automatic.spectrum_meter_ready = False
            self._request_automatic_point_retry(
                AttemptValidity.DEVICE_ERROR,
                f"光谱仪错误：{message}",
            )

    def _on_spectrometer_finished(self) -> None:
        self.spectrometer_reader = None
        self.automatic.spectrum_meter_ready = False
        if not self._expected_spectrometer_stop and self.automatic.settings is not None and self.automatic.settings.use_spectrometer:
            self._request_automatic_point_retry(
                AttemptValidity.DEVICE_ERROR,
                "光谱仪采集已停止",
            )

    def _on_spectrometer_device_opened(self, device_id: int) -> None:
        actual_device_id = int(device_id)
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
            label = f"Ocean Insight | 设备 ID {actual_device_id}"
            self.configuration["spectrometerDeviceId"] = actual_device_id
            self.configuration["spectrometerResource"] = label

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
                {"label": label, "deviceId": actual_device_id}
            )

        if previous_device_id is not None and previous_device_id != actual_device_id:
            self.notify(
                "warning",
                "光谱仪设备已重绑定",
                f"原设备 ID {previous_device_id} 已失效，已自动切换并打开当前唯一在线设备 ID {actual_device_id}。",
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
            use_spectrometer=(
                self.spectrometer_reader is not None and not self._aging_test_selected()
            ),
            maximum_current_a=None if self.power_supply_kind == "tdk" else 20.0,
            current_plan_mode=str(self.configuration["currentPlanMode"]),
            custom_current_points=points,
            custom_hold_ramp_enabled=(
                str(self.configuration["currentPlanMode"]).lower() == "custom"
                and self.power_supply_kind == "tdk"
            ),
        )
        return validate_automatic_test_settings(
            settings,
            stable_window_s=float(self.configuration["stableWindowS"]),
            post_stable_delay_s=10.0,
        )

    def _aging_test_selected(self) -> bool:
        return str(self.configuration.get("selectedScriptId", "standard")) == AGING_TEST_SCRIPT_ID

    def start_automatic(self, params: dict[str, Any]) -> None:
        if self._automatic_thread is not None and self._automatic_thread.is_alive():
            raise ValueError(f"工位 {self.station_id} 自动测试已在运行")
        self.configure(params)
        aging_test = self._aging_test_selected()
        if not aging_test:
            self._select_latest_test_reference(params)
        supply = self._require_power_supply()
        if self.power_supply_kind == "tdk" and not supply.output_enabled:
            raise ValueError("请先开启 TDK 电源输出")
        if float(self.active_current_a or 0.0) > 0.0:
            raise ValueError("开始自动测试前必须先将输出电流归零")

        settings = self._automatic_settings()
        self.reset_charts()
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
        self.power_drop_protection.reset()
        self.wavelength_stability_detector = WavelengthStabilityDetector(
            float(self.configuration["stableWindowS"]),
            0.2,
        )
        self.latest_wavelength_stable = False
        self.latest_wavelength_span_nm = math.inf
        self.export_status = "recording"
        self.export_path = ""
        self.export_error = ""
        self._automatic_retry_requested = False
        self._automatic_retry_reason = ""
        self._automatic_pause_holds_current = False
        try:
            if aging_test:
                self.record_store = SessionRecordStore()
            else:
                self._begin_recording_session(settings)
        except Exception as exc:
            self.automatic = AutomaticTestOrchestrator()
            self.record_store = SessionRecordStore()
            self.export_status = "failed"
            self.export_error = str(exc)
            self._close_automatic_sample_recorder()
            raise
        started_power_reader = not aging_test and self.power_meter_reader is None
        started_spectrum_reader = (
            not aging_test and settings.use_spectrometer and self.spectrometer_reader is None
        )
        try:
            if not aging_test and self.power_meter_reader is None:
                self.start_power_meter()
            elif not aging_test:
                self.power_meter_reader.update_stability_settings(
                    float(self.configuration["stableWindowS"]),
                    float(self.configuration["stableToleranceW"]),
                )
            if not aging_test and settings.use_spectrometer:
                self.configuration["autoIntegration"] = False
                if self.spectrometer_reader is None:
                    self.start_spectrometer()
                if self.spectrometer_reader is not None:
                    self.spectrometer_reader.use_fixed_integration_time(
                        int(self.configuration["integrationTimeUs"])
                    )
        except Exception as exc:
            if started_power_reader:
                self.stop_power_meter()
            if started_spectrum_reader:
                self.stop_spectrometer()
            self._close_automatic_sample_recorder()
            self.record_store = SessionRecordStore()
            self.automatic = AutomaticTestOrchestrator()
            self.export_status = "failed"
            self.export_error = str(exc)
            raise
        self._automatic_thread = threading.Thread(
            target=self._run_automatic,
            name=f"automatic-test-station-{self.station_id}",
            daemon=True,
        )
        self._automatic_thread.start()

    def _begin_automatic_measurement_window(self) -> tuple[int, int]:
        """Return boundaries that exclude samples from the previous setpoint."""
        reader = self.power_meter_reader
        if reader is None:
            raise RuntimeError("功率计采集线程未运行")
        stability_generation = reader.reset_stability_window()
        with self._lock:
            spectrum_revision = self.spectrum_revision
        return stability_generation, spectrum_revision

    def _fresh_automatic_measurement(
        self,
        *,
        stability_generation: int,
        spectrum_revision: int,
        use_spectrometer: bool,
        minimum_received_monotonic_s: float | None = None,
    ) -> tuple[PowerMeterReading, Any] | None:
        """Return a stable sample only when every input is from this setpoint."""
        with self._lock:
            reading = self.latest_power
            frame = self.latest_spectrum
            current_spectrum_revision = self.spectrum_revision
            power_received_at = self.latest_power_received_monotonic_s
            spectrum_received_at = self.latest_spectrum_received_monotonic_s

        if (
            reading is None
            or reading.stability_generation != stability_generation
            or not reading.stable
        ):
            return None
        if (
            minimum_received_monotonic_s is not None
            and (power_received_at is None or power_received_at < minimum_received_monotonic_s)
        ):
            return None
        if not use_spectrometer:
            return reading, None
        if (
            frame is None
            or current_spectrum_revision <= spectrum_revision
        ):
            return None
        if (
            minimum_received_monotonic_s is not None
            and (spectrum_received_at is None or spectrum_received_at < minimum_received_monotonic_s)
        ):
            return None
        if bool(getattr(frame.saturation, "saturated", False)):
            return None
        if not self.latest_wavelength_stable:
            return None
        return reading, frame

    def _wait_for_automatic_stability(
        self,
        settings: AutomaticTestSettings,
        current_a: float,
        *,
        retry_reason: str = "",
    ) -> tuple[PowerMeterReading, Any, int, int]:
        """Wait for a fresh joint-stability latch for the active setpoint."""
        self.wavelength_stability_detector = WavelengthStabilityDetector(
            float(self.configuration["stableWindowS"]),
            0.2,
        )
        self.latest_wavelength_stable = False
        self.latest_wavelength_span_nm = math.inf
        stability_generation, spectrum_revision = self._begin_automatic_measurement_window()
        self.automatic.set_state(AutomaticTestState.WAITING_STABLE)
        self.automatic_detail = (
            f"{retry_reason}，正在重新判稳" if retry_reason else f"正在等待 {current_a:g} A 稳定"
        )
        point_deadline = time.monotonic() + settings.point_timeout_s
        while time.monotonic() < point_deadline:
            if self._automatic_stop.is_set():
                raise RuntimeError(self.automatic_terminal_reason or "自动测试已停止")
            paused_duration = self._await_automatic_resume()
            if paused_duration is None:
                raise RuntimeError(self.automatic_terminal_reason or "自动测试已停止")
            point_deadline += paused_duration
            measurement = self._fresh_automatic_measurement(
                stability_generation=stability_generation,
                spectrum_revision=spectrum_revision,
                use_spectrometer=settings.use_spectrometer,
            )
            if measurement is not None:
                reading, frame = measurement
                return reading, frame, stability_generation, spectrum_revision
            time.sleep(0.05)
        raise TimeoutError(f"{current_a:g} A 测试点等待稳定超时")

    @staticmethod
    def _stable_power_w(reading: PowerMeterReading) -> float:
        """Use the stability-window median for LIV points, matching main."""
        median_w = float(reading.stable_median_w)
        return median_w if math.isfinite(median_w) else float(reading.power_w)

    def _wait_for_automatic_acquisition(self, timeout_s: float = 15.0) -> None:
        deadline = time.monotonic() + timeout_s
        while not self.automatic.acquisition_ready:
            if self._automatic_stop.wait(0.05):
                raise RuntimeError(self.automatic_terminal_reason or "自动测试已停止")
            self._await_automatic_resume()
            if time.monotonic() >= deadline:
                raise TimeoutError("采集设备在 15 秒内未全部就绪")

    def _set_automatic_current(self, current_a: float) -> None:
        try:
            self.set_current({"currentA": current_a}, wait_for_slot=True)
        except Exception:
            if self.power_supply_kind != "ch341" or self._automatic_stop.is_set():
                raise
            # main retries one legacy CH341 write after the mandatory command
            # interval; transient I2C failures must not lose the whole point.
            if not self._wait_automatic_duration(MIN_POWER_SUPPLY_COMMAND_INTERVAL_S):
                raise RuntimeError(self.automatic_terminal_reason or "自动测试已停止")
            self.set_current({"currentA": current_a}, wait_for_slot=True)

    def _validate_automatic_spectrum(self, frame: SpectrumFrame | None) -> None:
        if frame is None:
            return
        peak = float(frame.peak_intensity)
        if not math.isfinite(peak) or peak < MIN_SPECTRUM_PEAK_INTENSITY:
            raise _AutomaticPointQualityError(
                AttemptValidity.WEAK_SIGNAL,
                f"光谱信号过弱：峰值 {peak:.0f} counts，必须至少达到 "
                f"{MIN_SPECTRUM_PEAK_INTENSITY:.0f} counts",
            )
        if bool(frame.saturation and frame.saturation.saturated):
            raise _AutomaticPointQualityError(
                AttemptValidity.SATURATED,
                f"光谱饱和（峰值 {frame.saturation.peak_intensity:.0f} counts）；"
                "请缩短积分时间或调整光路后重试当前测试点",
                hold_current=True,
            )

    def _run_automatic_point(
        self,
        index: int,
        current_a: float,
        settings: AutomaticTestSettings,
    ) -> None:
        self._wait_for_automatic_acquisition()
        self.automatic.set_state(AutomaticTestState.SETTING_CURRENT)
        self.automatic_detail = f"正在设置 {current_a:g} A"
        if settings.custom_hold_ramp_enabled:
            ramp_currents = build_custom_hold_ramp_currents(
                max(0.0, float(self.active_current_a or 0.0)),
                current_a,
            )
            for ramp_index, ramp_current_a in enumerate(ramp_currents):
                self._set_automatic_current(ramp_current_a)
                if ramp_index < len(ramp_currents) - 1 and not self._wait_automatic_duration(0.5):
                    raise RuntimeError(self.automatic_terminal_reason or "自动测试已停止")
        else:
            self._set_automatic_current(current_a)

        (
            stable_reading,
            stable_frame,
            stability_generation,
            spectrum_revision,
        ) = self._wait_for_automatic_stability(settings, current_a)

        dwell_s = self.automatic.current_dwell_s
        self.automatic_hold_started_at = time.monotonic()
        stable_power_w = self._stable_power_w(stable_reading)
        with self._lock:
            self.stable_power_points[current_a] = stable_power_w
            self.efficiency_points.pop(current_a, None)
            self.series_revisions["stable"] += 1

        def refresh_stable_point() -> None:
            nonlocal stable_reading, stable_frame, stable_power_w
            measurement = self._fresh_automatic_measurement(
                stability_generation=stability_generation,
                spectrum_revision=spectrum_revision,
                use_spectrometer=settings.use_spectrometer,
            )
            if measurement is None:
                return
            stable_reading, stable_frame = measurement
            next_power_w = self._stable_power_w(stable_reading)
            if next_power_w == stable_power_w:
                return
            stable_power_w = next_power_w
            with self._lock:
                self.stable_power_points[current_a] = stable_power_w
                self.series_revisions["stable"] += 1

        self.automatic.set_state(AutomaticTestState.WAITING_VOLTAGE)
        delay_started_at = time.monotonic()
        with self._lock:
            delay_spectrum_revision = self.spectrum_revision
        if dwell_s > 0.0 and not self._wait_automatic_duration(
            dwell_s,
            sample_point_index=(index + 1 if self.automatic_sample_recorder is not None else None),
            target_current_a=current_a,
            on_tick=refresh_stable_point,
        ):
            raise RuntimeError(self.automatic_terminal_reason or "自动测试已停止")
        post_stable_delay_s = 10.0 if index == len(self.automatic.currents) - 1 else 5.0
        remaining_post_stable_s = max(0.0, post_stable_delay_s - dwell_s)
        if remaining_post_stable_s > 0.0 and not self._wait_automatic_duration(
            remaining_post_stable_s,
            on_tick=refresh_stable_point,
        ):
            raise RuntimeError(self.automatic_terminal_reason or "自动测试已停止")

        final_measurement = self._fresh_automatic_measurement(
            stability_generation=stability_generation,
            spectrum_revision=max(spectrum_revision, delay_spectrum_revision),
            use_spectrometer=settings.use_spectrometer,
            minimum_received_monotonic_s=delay_started_at,
        )
        if dwell_s > 0.0:
            # Designated holds latch the first stable point, but both readers
            # must still have produced data during the hold.
            with self._lock:
                power_progressed = bool(
                    self.latest_power_received_monotonic_s is not None
                    and self.latest_power_received_monotonic_s >= delay_started_at
                )
                spectrum_progressed = bool(
                    not settings.use_spectrometer
                    or (
                        self.latest_spectrum_received_monotonic_s is not None
                        and self.latest_spectrum_received_monotonic_s >= delay_started_at
                        and self.spectrum_revision > delay_spectrum_revision
                    )
                )
            if self.power_meter_reader is None or not power_progressed:
                raise RuntimeError("读取电压前功率计数据未继续更新")
            if settings.use_spectrometer and (self.spectrometer_reader is None or not spectrum_progressed):
                raise RuntimeError("读取电压前光谱仪数据未继续更新")
            if final_measurement is not None:
                stable_reading, stable_frame = final_measurement
        else:
            # No designated hold: silently return to joint stability detection.
            while final_measurement is None:
                (
                    stable_reading,
                    stable_frame,
                    stability_generation,
                    spectrum_revision,
                ) = self._wait_for_automatic_stability(
                    settings,
                    current_a,
                    retry_reason="读取电压前测试点波动",
                )
                self.automatic.set_state(AutomaticTestState.WAITING_VOLTAGE)
                delay_started_at = time.monotonic()
                with self._lock:
                    delay_spectrum_revision = self.spectrum_revision
                if not self._wait_automatic_duration(post_stable_delay_s, on_tick=refresh_stable_point):
                    raise RuntimeError(self.automatic_terminal_reason or "自动测试已停止")
                final_measurement = self._fresh_automatic_measurement(
                    stability_generation=stability_generation,
                    spectrum_revision=delay_spectrum_revision,
                    use_spectrometer=settings.use_spectrometer,
                    minimum_received_monotonic_s=delay_started_at,
                )
            stable_reading, stable_frame = final_measurement

        self._validate_automatic_spectrum(stable_frame)
        stable_power_w = self._stable_power_w(stable_reading)
        with self._supply_io_lock:
            self._reserve_power_supply_command(wait=True)
            raw_voltage_v = self._require_power_supply().read_output_voltage()
        self.latest_power_supply_output_voltage_v = float(raw_voltage_v)
        voltage_v = (
            compensate_tdk_output_voltage(raw_voltage_v, current_a)
            if self.power_supply_kind == "tdk"
            else raw_voltage_v
        )
        efficiency = (
            stable_power_w / current_a / voltage_v * 100.0
            if current_a > 0.0 and voltage_v > 0.0
            else math.nan
        )
        if self.automatic_sample_recorder is not None:
            self._write_automatic_second_sample(
                point_index=index + 1,
                elapsed_s=max(dwell_s, time.monotonic() - (self.automatic_hold_started_at or time.monotonic())),
                target_current_a=current_a,
            )
        frame = stable_frame
        stats = None if frame is None else frame.stats
        record = ExcelTestRecord(
            current_a=current_a,
            voltage_v=voltage_v,
            power_w=stable_power_w,
            efficiency=efficiency / 100.0,
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
        self.record_store.queue(
            record,
            actual_current_a=float(self.active_current_a or current_a),
            voltage_raw_v=raw_voltage_v,
            stable_span_w=stable_reading.stable_span_w,
            stable_window_s=stable_reading.stable_window_s,
            stable_tolerance_w=float(self.configuration["stableToleranceW"]),
            integration_time_us=int(self.configuration["integrationTimeUs"]),
        )
        with self._lock:
            self.stable_power_points[current_a] = stable_power_w
            self.efficiency_points[current_a] = efficiency
            self.series_revisions["stable"] += 1
        self.automatic.set_state(AutomaticTestState.SAVING_POINT)
        while True:
            self.export_status = "exporting"
            try:
                saved_path = self._save_record_store()
                break
            except Exception as exc:
                reason = f"Excel 保存失败：{exc}"
                self.export_status = "failed"
                self.export_error = str(exc)
                self.automatic.pause(reason)
                self.automatic_detail = f"已暂停：{reason}"
                self._automatic_pause_holds_current = False
                self._automatic_resume.clear()
                self._schedule_pause_safety_timeout()
                self.notify("error", "导出失败", f"{reason}；请重试当前保存")
                try:
                    if self._await_automatic_resume() is None:
                        raise RuntimeError(self.automatic_terminal_reason or "自动测试已停止")
                except _AutomaticPointRetry:
                    # A device callback may also arrive while saving. The
                    # measurement is already buffered, so reconnect devices
                    # but retry this exact record instead of re-measuring it.
                    pass
                self.automatic.set_state(AutomaticTestState.SAVING_POINT)
        self.export_status = "recording"
        self.automatic_detail = f"{current_a:g} A 已保存：{saved_path}"
        if self.automatic.state == AutomaticTestState.PAUSED:
            # The point is durably saved. A device callback that arrived while
            # Excel was writing must not cause this current to be measured twice.
            self._automatic_retry_requested = False
            if self._await_automatic_resume() is None:
                raise RuntimeError(self.automatic_terminal_reason or "自动测试已停止")

    def _run_aging_cycle(self) -> None:
        currents = self.automatic.currents
        for index, current_a in enumerate(currents):
            self.automatic.current_index = index
            self.automatic.set_state(AutomaticTestState.SETTING_CURRENT)
            self.automatic_detail = f"老化升流：正在设置 {current_a:g} A"
            self._set_automatic_current(current_a)
            if index < len(currents) - 1 and not self._wait_automatic_duration(
                AGING_RAMP_INTERVAL_S
            ):
                raise RuntimeError(self.automatic_terminal_reason or "AU 老化测试已停止")

        self.automatic.set_state(AutomaticTestState.WAITING_VOLTAGE)
        self.automatic_hold_started_at = time.monotonic()
        self.automatic_detail = "已到达 20 A，不判稳，正在老化计时 2 小时"
        if not self._wait_automatic_duration(AGING_HOLD_DURATION_S):
            raise RuntimeError(self.automatic_terminal_reason or "AU 老化测试已停止")

    def _run_automatic(self) -> None:
        try:
            settings = self.automatic.settings
            if settings is None:
                raise RuntimeError("自动测试配置不可用")

            if self._aging_test_selected():
                self._run_aging_cycle()
                self.safe_ramp_down()
                self.automatic.complete()
                self.automatic_terminal_outcome = "succeeded"
                self.automatic_terminal_reason = "AU 老化测试已完成并安全下电"
                self.automatic_detail = "AU 老化测试已完成并安全下电"
                self.export_status = "idle"
                self.notify("info", "AU 老化测试", "2 小时老化已完成，电源已安全下电")
                return

            for index, current_a in enumerate(self.automatic.currents):
                self.automatic.current_index = index
                while True:
                    try:
                        self._run_automatic_point(index, current_a, settings)
                        break
                    except _AutomaticPointRetry:
                        continue
                    except _AutomaticPointQualityError as exc:
                        self._request_automatic_point_retry(
                            exc.validity,
                            str(exc),
                            hold_current=exc.hold_current,
                        )
                    except TimeoutError as exc:
                        self._request_automatic_point_retry(AttemptValidity.TIMEOUT, str(exc))
                    except Exception as exc:
                        if self._automatic_stop.is_set():
                            raise
                        self._request_automatic_point_retry(AttemptValidity.DEVICE_ERROR, str(exc))
                    try:
                        self._await_automatic_resume()
                    except _AutomaticPointRetry:
                        continue

            self.safe_ramp_down()
            self.record_store.complete_session(
                SessionStatus.COMPLETED,
                "所有计划测试点均已保存",
                shutdown_confirmed=True,
            )
            self.export_status = "exporting"
            saved_path = self._save_record_store()
            self._close_automatic_sample_recorder()
            self._delete_embedded_automatic_samples(saved_path)
            self.export_status = "completed"
            self.export_path = "" if saved_path is None else str(saved_path)
            self.automatic.complete()
            self.automatic_terminal_outcome = "succeeded"
            self.automatic_terminal_reason = "所有计划测试点均已保存"
            self.automatic_detail = "自动测试已安全完成"
            self.notify("info", "导出完成", f"测试记录已保存：{self.export_path}")
        except Exception as exc:
            _LOGGER.exception("工位 %s 自动测试异常", self.station_id)
            reason = self.automatic_terminal_reason or str(exc)
            operator_stopped = reason in {"操作者提前结束测试", "自动测试已停止"}
            self.automatic_detail = reason
            self.automatic_terminal_outcome = (
                "stopped_by_operator" if operator_stopped else "aborted_safely"
            )
            self.automatic_terminal_reason = reason
            shutdown_confirmed = False
            try:
                self.safe_ramp_down()
                shutdown_confirmed = True
            except Exception as shutdown_error:
                self.automatic_output_shutdown_unconfirmed = True
                self.notify("error", "安全下电失败", str(shutdown_error))
                reason = f"{reason}；安全下电失败：{shutdown_error}"
                self.automatic_terminal_reason = reason
            current_a = self.automatic.current_a
            if current_a is not None:
                validity = (
                    AttemptValidity.TIMEOUT
                    if isinstance(exc, TimeoutError)
                    else AttemptValidity.DEVICE_ERROR
                )
                self.record_store.record_invalid_attempt(current_a, validity, reason)
            self.record_store.complete_session(
                SessionStatus.STOPPED_BY_OPERATOR if operator_stopped else SessionStatus.ABORTED_SAFELY,
                reason,
                shutdown_confirmed=shutdown_confirmed,
            )
            saved_path: Path | None = None
            try:
                self.export_status = "exporting"
                saved_path = self._save_record_store()
                self.export_path = "" if saved_path is None else str(saved_path)
                self.export_status = "completed" if saved_path is not None else "idle"
            except Exception as export_error:
                self.export_status = "failed"
                self.export_error = str(export_error)
                self.notify("error", "导出失败", str(export_error))
            try:
                self._close_automatic_sample_recorder()
                self._delete_embedded_automatic_samples(saved_path)
            except Exception as sample_error:
                self.notify("error", "逐秒记录失败", str(sample_error))
            self.automatic.complete()

    def safe_ramp_down(self) -> None:
        with self._safe_ramp_lock:
            with self._supply_io_lock:
                self._safe_ramp_down_impl()

    def _safe_ramp_down_impl(self) -> None:
        if self.power_supply_identity_error:
            raise RuntimeError("CH341 端口身份校验失败，禁止下发电流指令；请先安全断开")
        supply = self._power_supply()
        if supply is None:
            return
        settings = self.automatic.settings
        if settings is None:
            try:
                configured_step = float(self.configuration.get("rampDownStepA", 5.0))
            except (TypeError, ValueError):
                configured_step = 5.0
            if not math.isfinite(configured_step) or configured_step <= 0.0:
                configured_step = 5.0
            try:
                configured_interval = float(self.configuration.get("rampDownIntervalS", 1.1))
            except (TypeError, ValueError):
                configured_interval = 1.1
            if not math.isfinite(configured_interval) or configured_interval < 0.0:
                configured_interval = 1.1
            if self.power_supply_kind == "ch341":
                configured_interval = max(
                    configured_interval,
                    MIN_POWER_SUPPLY_COMMAND_INTERVAL_S,
                )
            settings = AutomaticTestSettings(
                ramp_down_step_a=configured_step,
                ramp_down_interval_s=configured_interval,
                maximum_current_a=None if self.power_supply_kind == "tdk" else 20.0,
            )
        start = max(0.0, float(self.active_current_a or 0.0))
        automatic_active = self.automatic.state not in {
            AutomaticTestState.IDLE,
            AutomaticTestState.COMPLETED,
        }
        if automatic_active:
            self.automatic.set_state(AutomaticTestState.RAMPING_DOWN)
        ramp_currents = build_ramp_down_currents(
            start,
            settings.ramp_down_step_a,
            maximum_current_a=settings.maximum_current_a,
        )
        for current_a in ramp_currents:
            if self.power_supply_kind == "ch341":
                previous = self._last_power_supply_command_at
                if previous is not None:
                    remaining = MIN_POWER_SUPPLY_COMMAND_INTERVAL_S - (time.monotonic() - previous)
                    if remaining > 0.0:
                        time.sleep(remaining)
                self._last_power_supply_command_at = time.monotonic()
            try:
                supply.set_current(current_a)
            except Exception:
                if self.power_supply_kind == "tdk":
                    supply.set_output_enabled(False)
                    measured_current_a = float(supply.read_output_current())
                    if not math.isfinite(measured_current_a) or abs(measured_current_a) > 0.01:
                        raise RuntimeError(
                            f"无法确认电源输出电流已归零：{measured_current_a:.3f} A"
                        )
                    self.active_current_a = 0.0
                    self.configuration["setCurrentA"] = 0.0
                    return
                raise
            self.active_current_a = current_a
            if current_a > 0.0:
                time.sleep(settings.ramp_down_interval_s)
        if not ramp_currents or ramp_currents[-1] != 0.0:
            supply.set_current(0.0)
        self.active_current_a = 0.0
        self.configuration["setCurrentA"] = 0.0
        if self.power_supply_kind == "tdk":
            # Always send OUT 0; the cached flag cannot prove the physical
            # state immediately after connecting to an already-running supply.
            supply.set_output_enabled(False)
        if self.power_supply_kind == "tdk":
            measured_current_a = float(supply.read_output_current())
            if not math.isfinite(measured_current_a) or abs(measured_current_a) > 0.01:
                raise RuntimeError(f"无法确认电源输出电流已归零：{measured_current_a:.3f} A")
        self.automatic_output_shutdown_unconfirmed = False

    def pause_automatic(self) -> None:
        if self.automatic.state in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED, AutomaticTestState.PAUSED}:
            raise ValueError("当前状态不能暂停")
        if self.automatic.state == AutomaticTestState.SAVING_POINT:
            self.automatic_detail = "当前测试点正在保存，保存完成后再暂停"
            return
        self.automatic.pause("操作员暂停")
        self._automatic_pause_holds_current = False
        self._automatic_resume.clear()
        self._schedule_pause_safety_timeout()

    def resume_automatic(self) -> None:
        if self.automatic.state != AutomaticTestState.PAUSED:
            raise ValueError("当前状态不能重试")
        settings = self.automatic.settings
        if self.power_meter_reader is None:
            self.start_power_meter()
        if settings is not None and settings.use_spectrometer:
            if self.spectrometer_reader is None:
                self.start_spectrometer()
            if self.spectrometer_reader is not None:
                self.spectrometer_reader.use_fixed_integration_time(
                    int(self.configuration["integrationTimeUs"])
                )
        self.automatic.set_state(self.automatic.paused_from_state)
        self.automatic.pause_reason = ""
        self._automatic_pause_holds_current = False
        self._automatic_pause_generation += 1
        self._automatic_resume.set()

    def end_automatic(self) -> None:
        if self._automatic_thread is not None and self._automatic_thread.is_alive():
            if not self.automatic_terminal_reason:
                self.automatic_terminal_reason = "操作者提前结束测试"
        self._automatic_stop.set()
        self._automatic_resume.set()
        thread = self._automatic_thread
        if thread is not None:
            thread.join(30.0)
            if thread.is_alive():
                raise RuntimeError("自动测试安全下电超时，后台线程仍在运行")

    def reset_automatic(self) -> None:
        if self._automatic_thread is not None and self._automatic_thread.is_alive():
            raise ValueError("自动测试运行中，不能重置")
        self.automatic = AutomaticTestOrchestrator()
        self.automatic_detail = "准备测试"
        self.automatic_terminal_outcome = None
        self.automatic_terminal_reason = ""
        self._test_reference = None
        self.export_status = "idle"
        self.export_error = ""

    def reset_charts(self) -> None:
        with self._lock:
            self.power_points.clear()
            self.stable_power_points.clear()
            self.efficiency_points.clear()
            self.latest_spectrum = None
            self.spectrum_center_candidate_nm = None
            self.spectrum_center_candidate_count = 0
            self.spectrum_centroid_candidate_nm = None
            self.spectrum_centroid_candidate_count = 0
            self.spectrum_center_locked_nm = None
            # PD 曲线是独立采集链路；普通图表重置不应让 PD 视图误判为有新数据。
            for key in ("power", "stable", "spectrum"):
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
            self.automatic_output_shutdown_unconfirmed = True
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
                    "centroidNm": _finite(item.centroid_nm),
                    "peakWavelengthNm": _finite(item.peak_wavelength_nm),
                    "peakIntensity": _finite(item.peak_intensity),
                }
                for item in (() if frame is None else frame.peak_annotations)
            ]
            return spectrum, peaks


class HeadlessBackend:
    """Own dual-station hardware, automation, and serializable application state."""

    mode = "active"

    def __init__(self, settings: JsonSettings | None = None) -> None:
        self._lock = threading.RLock()
        self._snapshot_lock = threading.RLock()
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
        self.power_meter_detection_detail: str = ""
        self.power_meter_detection_station_id: str = ""
        self.power_supply_detect_thread: _DeviceDiscoveryThread | None = None
        self.power_supply_detection_detail: str = ""
        self.power_supply_detection_kind: Literal["ch341", "tdk"] | None = None
        self.power_supply_detection_station_id: str = ""
        self.spectrometer_detect_thread: _DeviceDiscoveryThread | None = None
        self.spectrometer_detection_detail: str = ""
        self.spectrometer_detection_station_id: str = ""
        self.spectrometer_resources: list[dict[str, Any]] = []

        # Initialize Stations
        station_configs = self.settings.get("runtime/stations", {})
        raw_st1_config = station_configs.get("1", stored_dict) if isinstance(station_configs, dict) else stored_dict
        raw_st2_config = station_configs.get("2", {}) if isinstance(station_configs, dict) else {}
        st1_config = dict(raw_st1_config) if isinstance(raw_st1_config, dict) else {}
        st2_config = dict(raw_st2_config) if isinstance(raw_st2_config, dict) else {}

        discarded_device_persistence = any(
            config.get("ch341SelectedLocationPath")
            or config.get("ch341PortBindings")
            or config.get("powerMeterResource")
            or config.get("powerMeterDriverKind", "caihuang") != "caihuang"
            or config.get("spectrometerResource")
            or config.get("spectrometerDeviceId") not in {None, ""}
            for config in (st1_config, st2_config)
        )
        for config in (st1_config, st2_config):
            config["ch341SelectedLocationPath"] = ""
            config["ch341PortBindings"] = []
            config["powerMeterResource"] = ""
            config["powerMeterDriverKind"] = "caihuang"
            config["spectrometerResource"] = ""
            config["spectrometerDeviceId"] = None

        for key in ("tdkResource",):
            station_one_resource = st1_config.get(key)
            if station_one_resource not in {None, ""} and st2_config.get(key) == station_one_resource:
                st2_config[key] = None if key == "spectrometerDeviceId" else ""

        self.stations: dict[str, StationRuntime] = {
            "1": StationRuntime("1", self, st1_config),
            "2": StationRuntime("2", self, st2_config),
        }
        if discarded_device_persistence or any(
            station.power_meter_configuration_migrated
            for station in self.stations.values()
        ):
            self._persist_configuration()

        self.notices: deque[dict[str, Any]] = deque(maxlen=8)
        self._notice_sequence = 0
        self.status_message = "后台已就绪"

    def _station(self, station_id: str | None = None) -> StationRuntime:
        key = str(station_id) if station_id is not None else "1"
        return self.stations.get(key, self.stations["1"])

    def _station_id_from_params(self, params: dict[str, Any]) -> str:
        """Resolve routing without confusing the human-readable station label."""
        value = params.get("stationId")
        if value is None:
            legacy_value = params.get("station")
            value = legacy_value if str(legacy_value) in self.stations else "1"
        station_id = str(value)
        if station_id not in self.stations:
            raise ValueError(f"未知工位 ID：{station_id}")
        return station_id

    # Forwarding properties for Station 1 backwards compatibility.  Older
    # tests/integrations used these names before PD became station-scoped.
    @property
    def pd_state(self) -> dict[str, Any]:
        return self.stations["1"].pd_state

    @property
    def series_revisions(self) -> dict[str, int]:
        return self.stations["1"].series_revisions

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

    @property
    def connected_ch341_port(self) -> dict[str, Any] | None:
        return self.stations["1"].connected_ch341_port

    @connected_ch341_port.setter
    def connected_ch341_port(self, value: dict[str, Any] | None) -> None:
        self.stations["1"].connected_ch341_port = value

    @property
    def power_supply_identity_error(self) -> str:
        return self.stations["1"].power_supply_identity_error

    @power_supply_identity_error.setter
    def power_supply_identity_error(self, value: str) -> None:
        self.stations["1"].power_supply_identity_error = str(value)

    @property
    def _test_reference(self) -> Any | None:
        return self.stations["1"]._test_reference

    @_test_reference.setter
    def _test_reference(self, value: Any | None) -> None:
        self.stations["1"]._test_reference = value

    def _connect_power_supply(self) -> None:
        self.stations["1"].connect_power_supply()

    def _disconnect_power_supply(self) -> None:
        self.stations["1"].disconnect_power_supply()

    def _history_records(self, params: dict[str, Any]) -> tuple[Any, ...]:
        return self.stations["1"]._history_records(params)

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

    def check_workbook_path_available(self, target_station_id: str, workbook_path: Path) -> None:
        """Prevent two live station sessions from exporting to the same workbook."""
        target = Path(workbook_path).resolve(strict=False)
        with self._lock:
            for other_id, other_st in self.stations.items():
                if other_id == str(target_station_id):
                    continue
                other_path = other_st.record_store.workbook_path
                if other_path is not None and Path(other_path).resolve(strict=False) == target:
                    raise RuntimeError(
                        f"工位 {target_station_id} 与工位 {other_id} 的测试文件路径相同：{target}；"
                        "请修改 SN、测试站别或输出目录"
                    )

    def notify(
        self,
        level: str,
        title: str,
        message: str,
        *,
        station_id: str = "",
        code: str = "",
    ) -> None:
        with self._lock:
            self._notice_sequence += 1
            notice: dict[str, Any] = {
                "id": str(self._notice_sequence),
                "level": str(level),
                "title": str(title),
                "message": str(message),
            }
            if station_id:
                notice["stationId"] = str(station_id)
            if code:
                notice["code"] = str(code)
            self.notices.append(notice)
            self.status_message = str(message)
            if station_id and str(station_id) in self.stations:
                self.stations[str(station_id)].status_message = f"[工位 {station_id}] {message}"

    def set_status(self, message: str, *, station_id: str | None = None) -> None:
        with self._lock:
            self.status_message = str(message)
            if station_id is not None and str(station_id) in self.stations:
                self.stations[str(station_id)].status_message = str(message)

    def _persist_configuration(self) -> None:
        def persistent_configuration(station: StationRuntime) -> dict[str, Any]:
            configuration = dict(station.configuration)
            configuration["ch341SelectedLocationPath"] = ""
            configuration["ch341PortBindings"] = []
            configuration["powerMeterResource"] = ""
            configuration["powerMeterDriverKind"] = "caihuang"
            configuration["spectrometerResource"] = ""
            configuration["spectrometerDeviceId"] = None
            return configuration

        self.settings.set("runtime/configuration", persistent_configuration(self.stations["1"]))
        self.settings.set(
            "runtime/stations",
            {sid: persistent_configuration(st) for sid, st in self.stations.items()},
        )

    def ping(self) -> dict[str, Any]:
        return {"status": "ok", "mode": self.mode}

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        station_id = self._station_id_from_params(params)
        st = self._station(station_id)

        actions: dict[str, Callable[[dict[str, Any]], Any]] = {
            "app.configure": lambda p: self._configure(p),
            "device.refresh": self._refresh_device,
            "powerSupply.connect": lambda _p: st.connect_power_supply(),
            "powerSupply.disconnect": lambda _p: st.disconnect_power_supply(),
            "powerSupply.setCurrent": lambda p: st.set_current(p),
            "powerSupply.setVoltage": lambda p: st.set_voltage(p),
            "powerSupply.setOutput": lambda p: st.set_output(p),
            "powerSupply.read": lambda p: st.read_power_supply(p),
            "powerMeter.start": lambda _p: st.start_power_meter(),
            "powerMeter.stop": lambda _p: st.stop_power_meter(),
            "powerMeter.setRelativeZero": lambda p: st.set_relative_zero(p),
            "manual.saveExcel": lambda _p: st.save_manual_records(),
            "spectrometer.start": lambda _p: st.start_spectrometer(),
            "spectrometer.stop": lambda _p: st.stop_spectrometer(),
            "spectrometer.saveCsv": lambda p: st.save_spectrum_csv(p),
            "automatic.start": lambda p: st.start_automatic(p),
            "automatic.pause": lambda _p: st.pause_automatic(),
            "automatic.retry": lambda _p: st.resume_automatic(),
            "automatic.end": lambda _p: st.end_automatic(),
            "automatic.reset": lambda _p: st.reset_automatic(),
            "shippingReport.inspect": self._inspect_shipping_report,
            "shippingReport.batchInspect": self._inspect_shipping_report_batch,
            "shippingReport.batchGenerateItem": self._generate_shipping_report_batch_item,
            "shippingReport.preview": self._preview_shipping_report,
            "shippingReport.generate": self._generate_shipping_report,
            "pd.refresh": lambda _p: self._refresh_pd(st),
            "pd.configure": lambda p: self._configure_pd(st, p),
            "pd.start": lambda p: self._start_pd(st, p),
            "pd.stop": lambda _p: self._stop_pd(st),
            "charts.reset": lambda _p: st.reset_charts(),
            "app.stopAll": lambda _p: self._stop_all(),
            "app.shutdown": lambda _p: self._shutdown(),
        }
        action = actions.get(method)
        if action is None:
            raise KeyError(method)
        result = action(params)
        if method not in {"shippingReport.inspect", "shippingReport.batchInspect"}:
            self._persist_configuration()
        return self.snapshot(params) if result is None else result

    def dispatch_file_task(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "shippingReport.inspect":
            return self._inspect_shipping_report(params)
        if method == "shippingReport.batchInspect":
            return self._inspect_shipping_report_batch(params)
        raise KeyError(method)

    def _configure(self, params: dict[str, Any]) -> None:
        target_station_id = self._station_id_from_params(params)
        target_station = self.stations[target_station_id]
        previous_kind = target_station.power_supply_kind
        target_station.configure(params)
        current_kind = target_station.power_supply_kind
        if previous_kind == "ch341" and current_kind == "tdk" and not any(
            st.power_supply_kind == "ch341" and st.power_supply_controller is not None
            for st in self.stations.values()
        ):
            with self._lock:
                self.ch341_discovery_state = "not_scanned"
                self.ch341_scan_error = ""
                self.ch341_descriptors = []

    def scan_ch341(self) -> None:
        try:
            descriptors = scan_ch341_devices()
            with self._lock:
                self.ch341_descriptors = descriptors
                self.ch341_discovery_state = "ready" if descriptors else "empty"
                self.ch341_scan_error = ""
        except Exception as exc:
            with self._lock:
                self.ch341_discovery_state = "error"
                self.ch341_scan_error = str(exc)
                self.ch341_descriptors = []
            self.notify("error", "CH341 扫描失败", str(exc))

    def _refresh_device(self, params: dict[str, Any]) -> None:
        device = str(params.get("device", "all"))
        target_station = self._station(self._station_id_from_params(params))
        if device in {"all", "powerSupply"}:
            self._refresh_power_supply(target_station)

        if device in {"all", "powerMeter"}:
            self._refresh_power_meter(params)

        if device in {"all", "spectrometer"}:
            self._refresh_spectrometer(target_station)

    def _refresh_power_supply(self, station: StationRuntime) -> None:
        with self._lock:
            if self.power_supply_detect_thread is not None:
                return
            controller_kind = station.power_supply_kind
            self.power_supply_detection_detail = "正在准备电源识别"
            self.power_supply_detection_kind = controller_kind
            self.power_supply_detection_station_id = station.station_id

        def discover(report: Callable[[str], None]) -> tuple[str, list[Any]]:
            if controller_kind == "tdk":
                report("正在枚举 TDK RS232 串口")
                return controller_kind, list_tdk_serial_resources()
            report("正在扫描 CH341 设备")
            return controller_kind, scan_ch341_devices()

        thread = _DeviceDiscoveryThread(discover)
        with self._lock:
            self.power_supply_detect_thread = thread

        def on_completed(result: tuple[str, list[Any]]) -> None:
            kind, devices = result
            with self._lock:
                if kind == "tdk":
                    self.power_supply_resources = [str(resource) for resource in devices]
                    self.power_supply_detection_detail = f"识别完成：发现 {len(devices)} 个 TDK 串口"
                else:
                    self.ch341_descriptors = list(devices)
                    self.ch341_discovery_state = "ready" if devices else "empty"
                    self.ch341_scan_error = ""
                    self.power_supply_detection_detail = f"识别完成：发现 {len(devices)} 个 CH341 设备"
            if kind == "ch341":
                self.notify(
                    "info",
                    "CH341 识别",
                    self.power_supply_detection_detail,
                    station_id=station.station_id,
                )

        def on_status(message: str) -> None:
            with self._lock:
                self.power_supply_detection_detail = str(message)
            self.set_status(str(message), station_id=station.station_id)

        def on_failed(message: str) -> None:
            with self._lock:
                if controller_kind == "ch341":
                    self.ch341_discovery_state = "error"
                    self.ch341_scan_error = str(message)
                    self.ch341_descriptors = []
                self.power_supply_detection_detail = f"识别失败：{message}"
            self.notify("error", "CH341 扫描失败" if controller_kind == "ch341" else "TDK 串口识别", message)

        def on_finished() -> None:
            with self._lock:
                if self.power_supply_detect_thread is thread:
                    self.power_supply_detect_thread = None

        thread.completed.connect(on_completed)
        thread.status.connect(on_status)
        thread.failed.connect(on_failed)
        thread.finished.connect(on_finished)
        thread.start()

    def _refresh_power_meter(self, params: dict[str, Any]) -> None:
        with self._lock:
            if self.power_meter_detect_thread is not None:
                return
        target_station_id = self._station_id_from_params(params)
        thread = PowerMeterDetectThread(preferred_resource="")
        with self._lock:
            self.power_meter_detect_thread = thread
            self.power_meter_detection_detail = "正在准备功率计识别"
            self.power_meter_detection_station_id = target_station_id

        def on_detected(options: list[PowerMeterOption]) -> None:
            with self._lock:
                self.power_meter_options = list(options)
                self.power_meter_detection_detail = (
                    f"识别完成：发现 {len(options)} 台兼容功率计"
                    if options
                    else "识别完成：未找到兼容功率计，请检查 USB/串口连接与驱动"
                )
        def on_finished() -> None:
            with self._lock:
                self.power_meter_detect_thread = None

        def on_status(message: str) -> None:
            with self._lock:
                self.power_meter_detection_detail = str(message)
            self.set_status(str(message), station_id=target_station_id)

        def on_failed(message: str) -> None:
            with self._lock:
                self.power_meter_detection_detail = f"识别失败：{message}"
            self.notify("error", "功率计扫描", message)

        thread.detected.connect(on_detected)
        thread.status.connect(on_status)
        thread.failed.connect(on_failed)
        thread.finished.connect(on_finished)
        thread.start()

    def _refresh_spectrometer(self, station: StationRuntime | None = None) -> None:
        target_station_id = station.station_id if station is not None else "1"
        with self._lock:
            if self.spectrometer_detect_thread is not None:
                return
            self.spectrometer_detection_detail = "正在准备光谱仪识别"
            self.spectrometer_detection_station_id = target_station_id

        def discover(report: Callable[[str], None]) -> list[int]:
            report("正在加载 Ocean Insight 驱动")
            spectrometer_class, _ = load_spectrometer_components(None)
            report("正在扫描 Ocean Insight USB 设备")
            return [int(value) for value in spectrometer_class.detect()]

        thread = _DeviceDiscoveryThread(discover)
        with self._lock:
            self.spectrometer_detect_thread = thread

        def on_completed(device_ids: list[int]) -> None:
            resources = [
                {"label": f"Ocean Insight | 设备 ID {device_id}", "deviceId": device_id}
                for device_id in device_ids
            ]
            with self._lock:
                self.spectrometer_resources = resources
                self.spectrometer_detection_detail = f"识别完成：发现 {len(device_ids)} 台光谱仪"

        def on_status(message: str) -> None:
            with self._lock:
                self.spectrometer_detection_detail = str(message)
            if station is not None:
                self.set_status(str(message), station_id=station.station_id)

        def on_failed(message: str) -> None:
            with self._lock:
                self.spectrometer_detection_detail = f"识别失败：{message}"
            self.notify("error", "光谱仪识别", message)

        def on_finished() -> None:
            with self._lock:
                if self.spectrometer_detect_thread is thread:
                    self.spectrometer_detect_thread = None

        thread.completed.connect(on_completed)
        thread.status.connect(on_status)
        thread.failed.connect(on_failed)
        thread.finished.connect(on_finished)
        thread.start()

    def _set_current(self, params: dict[str, Any]) -> None:
        st = self._station(self._station_id_from_params(params))
        st.set_current(params)

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

    def _refresh_pd(self, station: StationRuntime) -> None:
        with station._pd_lock:
            if station._pd_detect_thread is not None:
                return
            station.pd_state["detecting"] = True
            station.pd_state["status"] = "正在准备 NI-DAQ 采集卡识别"

        def discover(report: Callable[[str], None]) -> tuple[str, list[Any], Callable[[Any, str], list[str]]]:
            report("正在加载 NI-DAQmx 驱动")
            from tools.pd_daq_mvp import channels_for_terminal_mode, discover_ni_daq_devices

            report("正在枚举 NI-DAQ 采集卡")
            driver, devices = discover_ni_daq_devices()
            return driver, list(devices), channels_for_terminal_mode

        thread = _DeviceDiscoveryThread(discover)
        with station._pd_lock:
            station._pd_detect_thread = thread

        def on_completed(result: tuple[str, list[Any], Callable[[Any, str], list[str]]]) -> None:
            driver, devices, channels_for_terminal_mode = result
            with station._pd_lock:
                station._pd_devices = devices
                station.pd_state["devices"] = [device.label() for device in devices]
                if devices:
                    selected = devices[0]
                    terminal = str(station.pd_state["settings"]["terminal"])
                    station.pd_state["settings"]["device"] = selected.label()
                    station.pd_state["channels"] = list(channels_for_terminal_mode(selected, terminal))
                    station.pd_state["ranges"] = [
                        {"label": f"±{value:g} V", "value": value}
                        for value in selected.voltage_ranges
                    ]
                    if station.pd_state["channels"] and not station.pd_state["settings"]["channel"]:
                        station.pd_state["settings"]["channel"] = station.pd_state["channels"][0]
                station.pd_state["status"] = f"识别完成：{driver}，发现 {len(devices)} 台采集卡"

        def on_status(message: str) -> None:
            with station._pd_lock:
                station.pd_state["status"] = str(message)

        def on_failed(message: str) -> None:
            with station._pd_lock:
                station.pd_state["status"] = f"识别失败：{message}"
            self.notify("error", "PD 采集", message)

        def on_finished() -> None:
            with station._pd_lock:
                if station._pd_detect_thread is thread:
                    station._pd_detect_thread = None
                    station.pd_state["detecting"] = False

        thread.completed.connect(on_completed)
        thread.status.connect(on_status)
        thread.failed.connect(on_failed)
        thread.finished.connect(on_finished)
        thread.start()

    def _configure_pd(self, station: StationRuntime, params: dict[str, Any]) -> None:
        with station._pd_lock:
            for key in station.pd_state["settings"]:
                if key in params:
                    station.pd_state["settings"][key] = params[key]

    def _start_pd(self, station: StationRuntime, params: dict[str, Any]) -> None:
        self._configure_pd(station, params)
        with station._pd_lock:
            if station._pd_reader is not None:
                return
            has_devices = bool(station._pd_devices)
        if not has_devices:
            raise RuntimeError("请先完成 NI-DAQ 采集卡识别")
        with station._pd_lock:
            devices = list(station._pd_devices)
        from tools.pd_daq_mvp import (
            PdAcquisitionThread,
            PdDaqSettings,
            default_csv_path,
        )

        with station._pd_lock:
            values = dict(station.pd_state["settings"])
        requested_device = str(values["device"])
        device = next(
            (item for item in devices if item.label() == requested_device or item.name == requested_device),
            devices[0],
        )
        channel = str(values["channel"]).strip()
        if not channel:
            channel = device.ai_channels[0]
        save_path = None
        if bool(values["save"]):
            output_dir = Path(str(values["outputDir"] or station.configuration["outputDir"])).expanduser()
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
        with station._pd_lock:
            if station._pd_reader is not None:
                return
            station._pd_reader = reader
            reader.block_ready.connect(lambda block: self._on_pd_block(station, block))
            reader.acquisition_ready.connect(lambda value: self._on_pd_ready(station, value))
            reader.acquisition_failed.connect(lambda message: self._on_pd_failed(station, message))
            reader.recording_stopped.connect(lambda path: self._on_pd_recording_stopped(station, path))
            reader.finished.connect(lambda: self._on_pd_finished(station))
            station.pd_state["state"] = "running"
            station.pd_state["status"] = "正在启动"
        reader.start()

    def _on_pd_block(self, station: StationRuntime, block: Any) -> None:
        with station._pd_lock:
            points = station.pd_state["points"]
            points.extend(
                {"elapsedS": float(elapsed), "value": float(value)}
                for elapsed, value in zip(block.plot_times_s, block.plot_values)
            )
            if len(points) > 13_000:
                del points[: len(points) - 13_000]
            calibrated = block.calibrated
            voltage = block.voltage
            station.pd_state["currentValue"] = f"{calibrated.latest:.6g} {station.pd_state['settings']['unit']}"
            station.pd_state["voltage"] = f"{voltage.latest:.6g} V"
            station.pd_state["mean"] = f"{calibrated.mean:.6g}"
            station.pd_state["standardDeviation"] = f"{calibrated.standard_deviation:.6g}"
            station.pd_state["rangeText"] = f"{calibrated.minimum:.6g} – {calibrated.maximum:.6g}"
            station.pd_state["sampleCount"] = str(block.sample_count)
            station.series_revisions["pd"] += 1

    def _on_pd_ready(self, station: StationRuntime, channel: str) -> None:
        with station._pd_lock:
            station.pd_state["state"] = "running"
            station.pd_state["status"] = f"正在采集 {channel}"

    def _on_pd_failed(self, station: StationRuntime, message: str) -> None:
        with station._pd_lock:
            station.pd_state["state"] = "error"
            station.pd_state["status"] = str(message)
        station.notify("error", "PD 采集", message)

    def _on_pd_recording_stopped(self, station: StationRuntime, path: str) -> None:
        if path:
            with station._pd_lock:
                station.pd_state["status"] = f"记录已保存：{path}"

    def _on_pd_finished(self, station: StationRuntime) -> None:
        with station._pd_lock:
            station._pd_reader = None
            if station.pd_state["state"] != "error":
                station.pd_state["state"] = "idle"

    def _stop_pd(self, station: StationRuntime) -> None:
        with station._pd_lock:
            reader = station._pd_reader
        if reader is not None:
            reader.stop()
            reader.wait(5_000)
        with station._pd_lock:
            station.pd_state["state"] = "idle"
            station.pd_state["status"] = "已停止"

    def _stop_all(self) -> None:
        errors: list[str] = []
        for st in self.stations.values():
            try:
                st.stop_all()
            except Exception as exc:
                errors.append(f"工位 {st.station_id}：{exc}")
            try:
                self._stop_pd(st)
            except Exception as exc:
                errors.append(f"工位 {st.station_id} PD：{exc}")
        if errors:
            raise RuntimeError("停止未完全确认：" + "；".join(errors))

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
        # Do not hold the backend-wide lock while reading a station.  Worker
        # callbacks may hold a station lock and then publish a notice (which
        # takes the backend lock); keeping this order one-way avoids a
        # station/backend lock inversion during live acquisition.
        with self._snapshot_lock:
            return self._snapshot_locked(params or {})

    def _snapshot_locked(self, params: dict[str, Any]) -> dict[str, Any]:
        view = str(params.get("view", "full"))
        active_station_id = (
            "2"
            if view in {"automatic_2", "manual_2", "pd_2"}
            else self._station_id_from_params(params)
        )
        active_st = self._station(active_station_id)
        with self._lock:
            shared = {
                "notices": list(self.notices),
                "status_message": self.status_message,
                "power_supply_resources": list(self.power_supply_resources),
                "ch341_discovery_state": self.ch341_discovery_state,
                "ch341_scan_error": self.ch341_scan_error,
                "ch341_descriptors": list(self.ch341_descriptors),
                "power_meter_options": list(self.power_meter_options),
                "power_meter_detecting": self.power_meter_detect_thread is not None,
                "power_meter_detection_detail": self.power_meter_detection_detail,
                "power_meter_detection_station_id": self.power_meter_detection_station_id,
                "power_supply_detecting": self.power_supply_detect_thread is not None,
                "power_supply_detection_detail": self.power_supply_detection_detail,
                "power_supply_detection_kind": self.power_supply_detection_kind,
                "power_supply_detection_station_id": self.power_supply_detection_station_id,
                "spectrometer_detecting": self.spectrometer_detect_thread is not None,
                "spectrometer_detection_detail": self.spectrometer_detection_detail,
                "spectrometer_detection_station_id": self.spectrometer_detection_station_id,
                "spectrometer_resources": [dict(item) for item in self.spectrometer_resources],
            }
        with active_st._lock:
            return self._snapshot_locked_with_station_lock(params, shared)

    def _snapshot_locked_with_station_lock(
        self,
        params: dict[str, Any],
        shared: dict[str, Any],
    ) -> dict[str, Any]:
        view = str(params.get("view", "full"))
        active_station_id = (
            "2"
            if view in {"automatic_2", "manual_2", "pd_2"}
            else self._station_id_from_params(params)
        )
        active_st = self._station(active_station_id)

        notices = shared["notices"]
        status_message = shared["status_message"]
        power_supply_resources = shared["power_supply_resources"]
        ch341_discovery_state = shared["ch341_discovery_state"]
        ch341_scan_error = shared["ch341_scan_error"]
        ch341_descriptors = shared["ch341_descriptors"]
        power_meter_options = shared["power_meter_options"]
        power_meter_detection_matches = (
            shared["power_meter_detection_station_id"] == active_station_id
        )
        power_meter_detecting = shared["power_meter_detecting"] and power_meter_detection_matches
        power_meter_detection_detail = (
            shared["power_meter_detection_detail"]
            if power_meter_detection_matches
            else ""
        )
        power_supply_detection_matches = (
            shared["power_supply_detection_kind"] == active_st.power_supply_kind
            and shared["power_supply_detection_station_id"] == active_station_id
        )
        power_supply_detecting = shared["power_supply_detecting"] and power_supply_detection_matches
        power_supply_detection_detail = (
            shared["power_supply_detection_detail"]
            if power_supply_detection_matches
            else ""
        )
        spectrometer_detection_matches = (
            shared["spectrometer_detection_station_id"] == active_station_id
        )
        spectrometer_detecting = shared["spectrometer_detecting"] and spectrometer_detection_matches
        spectrometer_detection_detail = (
            shared["spectrometer_detection_detail"]
            if spectrometer_detection_matches
            else ""
        )
        spectrometer_resources = shared["spectrometer_resources"]

        with active_st._pd_lock:
            pd_state = {
                key: (list(value) if key == "points" else dict(value) if key == "settings" else list(value) if isinstance(value, list) else value)
                for key, value in active_st.pd_state.items()
            }
            pd_revision = active_st.series_revisions["pd"]
        status_message = active_st.status_message or status_message

        include_live = view in {"automatic", "automatic_1", "automatic_2", "manual", "manual_1", "manual_2", "full"}
        include_pd = view in {"pd", "pd_1", "pd_2", "full"}

        supply = active_st._power_supply()
        frame = active_st.latest_spectrum
        spectrum_saturated = bool(frame and frame.saturation and frame.saturation.saturated)
        spectrum_stats = None if frame is None else frame.stats

        raw_since = params.get("since")
        since_revisions = raw_since if isinstance(raw_since, dict) else None
        raw_cursors = params.get("cursors")
        series_cursors = raw_cursors if isinstance(raw_cursors, dict) else {}
        merged_revisions = {
            "power": active_st.series_revisions["power"],
            "stable": active_st.series_revisions["stable"],
            "spectrum": active_st.series_revisions["spectrum"],
            "pd": pd_revision,
        }

        def include_series(name: str, active_for_view: bool) -> bool:
            if not active_for_view:
                return False
            if since_revisions is None:
                return True
            previous = since_revisions.get(name)
            return not isinstance(previous, (int, float)) or int(previous) != merged_revisions[name]

        def append_patch(
            name: str,
            points: list[dict[str, Any]],
            x_key: str,
        ) -> dict[str, Any] | None:
            cursor = _finite(series_cursors.get(name))
            if since_revisions is None or cursor is None or not points:
                return None
            cursor_index = next(
                (
                    index
                    for index, point in enumerate(points)
                    if math.isclose(float(point[x_key]), cursor, rel_tol=0.0, abs_tol=1e-9)
                ),
                None,
            )
            if cursor_index is None or cursor_index >= len(points) - 1:
                return None
            return {
                "startX": float(points[0][x_key]),
                "points": points[cursor_index + 1 :],
            }

        measurements: dict[str, Any] = {}
        series_patches: dict[str, Any] = {}
        if include_series("power", include_live):
            power_points = list(active_st.power_points)
            power_patch = append_patch("power", power_points, "elapsedS")
            if power_patch is None:
                measurements["power"] = power_points
            else:
                series_patches["power"] = power_patch
        if include_series("stable", include_live):
            measurements["stable"] = active_st.build_stable_snapshot()
        if include_series("spectrum", include_live):
            spectrum, spectrum_peaks = active_st.build_spectrum_snapshot(
                allow_cached_grid=since_revisions is not None,
            )
            measurements["spectrum"] = spectrum
            measurements["spectrumPeaks"] = spectrum_peaks

        pd_snapshot = {key: value for key, value in pd_state.items() if key != "points"}
        if include_series("pd", include_pd):
            pd_points = list(pd_state["points"])
            pd_patch = append_patch("pd", pd_points, "elapsedS")
            if pd_patch is None:
                pd_snapshot["points"] = pd_points
            else:
                series_patches["pd"] = pd_patch

        # CH341 device ports map
        online_map = {d.location_path: d.device_index for d in ch341_descriptors}
        occupied_paths = {
            str(other_st.configuration.get("ch341SelectedLocationPath", "")).strip()
            for other_id, other_st in self.stations.items()
            if other_id != active_st.station_id
            and other_st.power_supply_kind == "ch341"
            and other_st.power_supply_controller is not None
            and str(other_st.configuration.get("ch341SelectedLocationPath", "")).strip()
        }
        all_paths = [
            loc
            for loc in online_map
            if loc not in occupied_paths
        ]
        ports = [
            {
                "locationPath": loc,
                "name": None,
                "online": True,
                "deviceIndex": online_map[loc],
            }
            for loc in all_paths
        ]
        occupied_power_meter_resources = {
            str(other_st.configuration.get("powerMeterResource", "")).strip()
            for other_id, other_st in self.stations.items()
            if other_id != active_st.station_id
            and other_st.power_meter_reader is not None
            and str(other_st.configuration.get("powerMeterResource", "")).strip()
        }
        available_power_meter_options = [
            option
            for option in power_meter_options
            if option.resource not in occupied_power_meter_resources
        ]
        occupied_spectrometer_ids = {
            int(other_st.configuration["spectrometerDeviceId"])
            for other_id, other_st in self.stations.items()
            if other_id != active_st.station_id
            and other_st.spectrometer_reader is not None
            and other_st.configuration.get("spectrometerDeviceId") not in {None, ""}
        }
        available_spectrometer_resources = [
            resource
            for resource in spectrometer_resources
            if int(resource["deviceId"]) not in occupied_spectrometer_ids
        ]
        ch341_snapshot = {
            "discoveryState": ch341_discovery_state,
            "error": ch341_scan_error,
            "ports": ports,
            "connectedPort": active_st.connected_ch341_port,
        }

        if active_st.power_supply_identity_error:
            power_supply_detail = active_st.power_supply_identity_error
        elif supply is not None and active_st.power_supply_kind == "ch341" and active_st.connected_ch341_port:
            power_supply_detail = f"Device {active_st.connected_ch341_port['deviceIndex']} · 已连接"
        else:
            power_supply_detail = "已连接" if supply is not None else "未连接"

        currents = list(active_st.automatic.currents)
        current_index = int(active_st.automatic.current_index)
        progress = max(0.0, min(1.0, (current_index + 1) / len(currents))) if currents else 0.0
        dwell_elapsed = None
        if active_st.automatic_hold_started_at is not None and active_st.automatic.current_dwell_s > 0.0:
            elapsed_s = min(
                active_st.automatic.current_dwell_s,
                max(0.0, time.monotonic() - active_st.automatic_hold_started_at),
            )
            # The UI displays whole seconds. Quantizing this transport field
            # prevents the 200 ms live snapshot loop from becoming a timer
            # stream while the device data itself is unchanged.
            dwell_elapsed = (
                active_st.automatic.current_dwell_s
                if elapsed_s >= active_st.automatic.current_dwell_s
                else float(math.floor(elapsed_s))
            )

        stations_summary = {sid: st.summary_snapshot() for sid, st in self.stations.items()}

        return {
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "seriesRevisions": merged_revisions,
            **({"seriesPatches": series_patches} if series_patches else {}),
            "activeStation": active_station_id,
            "stations": stations_summary,
            "backend": {
                "connected": True,
                "mode": self.mode,
                "pythonVersion": platform.python_version(),
                "runtimeLogPath": str(runtime_log_path() or ""),
                "notices": notices,
            },
            "configuration": dict(active_st.configuration),
            "configurations": {sid: dict(st.configuration) for sid, st in self.stations.items()},
            "testScripts": [dict(item) for item in BUILTIN_TEST_SCRIPTS],
            "devices": {
                "powerSupply": {
                    "state": "connecting" if power_supply_detecting else _device_state(supply is not None, False, active_st.power_supply_identity_error),
                    "label": "TDK RS232" if active_st.power_supply_kind == "tdk" else "CH341 I²C",
                    "detail": power_supply_detection_detail if power_supply_detecting or (supply is None and power_supply_detection_detail) else power_supply_detail,
                    "connected": supply is not None,
                    "detecting": power_supply_detecting,
                    "outputEnabled": bool(supply and supply.output_enabled),
                    "activeCurrentA": _finite(active_st.active_current_a),
                    "inputVoltageV": _finite(active_st.latest_power_supply_input_voltage_v),
                    "outputVoltageV": _finite(active_st.latest_power_supply_output_voltage_v),
                    "outputCurrentA": _finite(active_st.latest_power_supply_output_current_a),
                    "temperatureC": _finite(active_st.latest_power_supply_temperature_c),
                    "resources": power_supply_resources,
                    "ch341": ch341_snapshot,
                },
                "powerMeter": {
                    "state": "connecting" if power_meter_detecting else _device_state(False, active_st.power_meter_reader is not None, active_st.power_meter_error),
                    "label": "功率计",
                    "detail": active_st.power_meter_error or ("采集中" if active_st.power_meter_reader else power_meter_detection_detail or "已停止"),
                    "running": active_st.power_meter_reader is not None,
                    "detecting": power_meter_detecting,
                    "ready": bool(active_st.power_meter_reader and active_st.power_meter_reader.is_ready),
                    "powerW": _finite(None if active_st.latest_power is None else active_st.latest_power.power_w),
                    "stable": bool(active_st.latest_power and active_st.latest_power.stable),
                    "resources": [item.label() for item in available_power_meter_options],
                },
                "spectrometer": {
                    "state": "connecting" if spectrometer_detecting else _device_state(False, active_st.spectrometer_reader is not None, active_st.spectrometer_error),
                    "label": "Ocean Insight",
                    "detail": active_st.spectrometer_error or ("采集中" if active_st.spectrometer_reader else spectrometer_detection_detail or "已停止"),
                    "running": active_st.spectrometer_reader is not None,
                    "detecting": spectrometer_detecting,
                    "ready": bool(active_st.spectrometer_reader and active_st.spectrometer_reader.is_ready),
                    "peakWavelengthNm": None if spectrum_saturated else _finite(getattr(spectrum_stats, "peak_wavelength_nm", None)),
                    "centroidNm": None if spectrum_saturated else _finite(getattr(spectrum_stats, "centroid_nm", None)),
                    "fwhmNm": None if spectrum_saturated else _finite(getattr(spectrum_stats, "fwhm_nm", None)),
                    "pib": None if frame is None or not math.isfinite(getattr(frame, "pib", math.nan)) else _finite(frame.pib * 100.0 if frame.pib <= 1.0 else frame.pib),
                    "smsrDb": None if frame is None or frame.smsr is None else _finite(frame.smsr.smsr_db),
                    "saturated": spectrum_saturated,
                    "spectrumWindow": (
                        {
                            "centerNm": active_st.spectrum_center_locked_nm,
                            "halfRangeNm": SPECTRUM_CENTER_LOCK_HALF_RANGE_NM,
                        }
                        if active_st.spectrum_center_locked_nm is not None
                        else None
                    ),
                    "resources": [item["label"] for item in available_spectrometer_resources],
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
                "exportStatus": active_st.export_status,
                "exportPath": active_st.export_path,
                "exportError": active_st.export_error,
                "reference": (
                    active_st._test_reference.reference_payload()
                    if active_st._test_reference is not None
                    else None
                ),
            },
            "manualTest": {
                "pendingCount": (
                    len(active_st.record_store.unsaved_records())
                    if active_st.record_store.current_session is not None
                    and active_st.record_store.current_session.mode == "manual"
                    else 0
                ),
                "exportStatus": active_st.export_status,
                "exportPath": active_st.export_path,
                "exportError": active_st.export_error,
            },
            "measurements": measurements,
            "pd": pd_snapshot,
            "safety": {
                "hardwareAccess": True,
                "commandMode": "controller_owned",
                "detail": "设备命令、自动流程和安全下电由无界面 Python 后台执行",
                "outputShutdownUnconfirmed": self.automatic_output_shutdown_unconfirmed,
            },
            "status": {"message": status_message},
        }
