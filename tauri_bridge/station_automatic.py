"""Automatic-test lifecycle for a single station."""

from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from combined_test.automation import (
    AutomaticCurrentPoint,
    AutomaticTestOrchestrator,
    AutomaticTestSettings,
    AutomaticTestState,
    StableHoldTimer,
    FINAL_AUTO_POST_STABLE_DELAY_S,
    MIN_POWER_SUPPLY_COMMAND_INTERVAL_S,
    automatic_post_stable_delay_s,
    build_custom_hold_ramp_currents,
    build_ramp_down_currents,
    build_test_currents,
    validate_automatic_test_settings,
)
from combined_test.automatic_samples import (
    AutomaticSampleRecorder,
    AutomaticSecondSample,
    automatic_sample_path,
)
from combined_test.core import WavelengthStabilityDetector
from combined_test.excel_export import ExcelTestRecord, save_test_records
from combined_test.models import PowerMeterReading, SpectrumFrame
from combined_test.record_store import AttemptValidity, SessionStatus
from combined_test.runtime_logging import get_runtime_logger
from combined_test.session_journal import SessionJournalError
from combined_test.spectrum_math import calculate_pib
from combined_test.tdk_power_supply import compensate_tdk_output_voltage

from .export_queue import ExportTask
from .station_support import (
    AGING_HOLD_DURATION_S,
    AGING_POWER_SAMPLE_INTERVAL_S,
    AGING_RAMP_INTERVAL_S,
    AGING_TEST_SCRIPT_ID,
    AUTOMATIC_PAUSE_ACTIONS,
    BUILTIN_TEST_SCRIPTS,
    MIN_SPECTRUM_PEAK_INTENSITY,
    AutomaticPointQualityError,
    AutomaticPointRetry,
)


_LOGGER = get_runtime_logger("station_automatic")

STANDARD_WAVELENGTH_STABILITY_TOLERANCE_NM = 0.2
STANDARD_TEST_STABILITY_WINDOW_S = 5.0
RELAXED_STABILITY_CURRENT_A = 2.0
RELAXED_WAVELENGTH_STABILITY_TOLERANCE_NM = 0.5
RELAXED_POWER_STABILITY_SPAN_W = 0.2
LOW_POWER_STABILITY_SPAN_W = 0.1
LOW_POWER_STABILITY_WINDOW_S = 10.0


def automatic_wavelength_stability_tolerance_nm(current_a: float | None) -> float:
    """Use a wider centroid window only for the 2 A automatic point."""
    if current_a is not None and math.isclose(
        float(current_a),
        RELAXED_STABILITY_CURRENT_A,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        return RELAXED_WAVELENGTH_STABILITY_TOLERANCE_NM
    return STANDARD_WAVELENGTH_STABILITY_TOLERANCE_NM


def automatic_power_stability_span_limit_w(current_a: float | None) -> float | None:
    """Use 0.1 W peak-to-peak below 2 A and 0.2 W at 2 A."""
    if current_a is not None and 0.0 <= float(current_a) < RELAXED_STABILITY_CURRENT_A:
        return LOW_POWER_STABILITY_SPAN_W
    if current_a is not None and math.isclose(
        float(current_a),
        RELAXED_STABILITY_CURRENT_A,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        return RELAXED_POWER_STABILITY_SPAN_W
    return None


def automatic_stability_window_s(
    selected_script_id: Any,
    configured_window_s: Any,
) -> float:
    """Use a five-second power and wavelength window for the standard test."""
    if str(selected_script_id).strip() == "standard":
        return STANDARD_TEST_STABILITY_WINDOW_S
    return float(configured_window_s)


def automatic_power_stability_window_s(
    selected_script_id: Any,
    configured_window_s: Any,
    current_a: float | None,
) -> float:
    """Require a full ten-second power window for points below 2 A."""
    if current_a is not None and 0.0 <= float(current_a) < RELAXED_STABILITY_CURRENT_A:
        return LOW_POWER_STABILITY_WINDOW_S
    return automatic_stability_window_s(selected_script_id, configured_window_s)


class StationAutomaticMixin:
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
        with self._emergency_ramp_thread_lock:
            current = self._emergency_ramp_thread
            if current is not None and current.is_alive():
                return

            self.command_safety.begin_stop()

            def run() -> None:
                try:
                    self.safe_ramp_down()
                except Exception as exc:
                    self.notify("error", "安全下电失败", f"{reason}：{exc}")
                finally:
                    self.command_safety.end_stop()

            self._emergency_ramp_thread = threading.Thread(
                target=run,
                name="emergency-ramp",
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
            name="pause-safety",
            daemon=True,
        ).start()

    def _set_automatic_pause(
        self,
        reason: str,
        *,
        kind: str,
        hold_current: bool,
        retry_point: bool,
    ) -> None:
        now = datetime.now(timezone.utc)
        settings = self.automatic.settings
        timeout_s = 0.0 if settings is None else settings.pause_ramp_down_timeout_s
        self.automatic_pause_kind = kind
        self.automatic_pause_started_at_utc = now.isoformat(timespec="milliseconds")
        self.automatic_pause_deadline_at_utc = (
            None
            if hold_current or timeout_s <= 0.0
            else datetime.fromtimestamp(now.timestamp() + timeout_s, timezone.utc).isoformat(
                timespec="milliseconds"
            )
        )
        self.automatic_pause_actions = AUTOMATIC_PAUSE_ACTIONS.get(
            kind,
            ("retry_point", "safe_end"),
        )
        self._automatic_retry_requested = retry_point
        self._automatic_retry_reason = str(reason)
        self._automatic_pause_holds_current = bool(hold_current)
        self.automatic.pause(reason)
        self.automatic_detail = f"已暂停：{reason}"
        self._automatic_resume.clear()
        self._schedule_pause_safety_timeout()

    def _clear_automatic_pause(self) -> None:
        self.automatic_pause_kind = None
        self.automatic_pause_started_at_utc = None
        self.automatic_pause_deadline_at_utc = None
        self.automatic_pause_actions = ()

    def _request_automatic_point_retry(
        self,
        validity: AttemptValidity,
        reason: str,
        *,
        hold_current: bool = False,
        pause_kind: str | None = None,
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
                try:
                    self.record_store.record_invalid_attempt(
                        current_a,
                        validity,
                        reason,
                        integration_time_us=int(self.configuration["integrationTimeUs"]),
                    )
                except SessionJournalError as exc:
                    self._set_automatic_pause(
                        str(exc),
                        kind="record_persistence_failed",
                        hold_current=False,
                        retry_point=True,
                    )
                    return
        resolved_kind = pause_kind
        if resolved_kind is None:
            resolved_kind = (
                "spectrum_saturated"
                if validity == AttemptValidity.SATURATED
                else "measurement_timeout"
                if validity == AttemptValidity.TIMEOUT
                else "device_error"
            )
        self._set_automatic_pause(
            reason,
            kind=resolved_kind,
            hold_current=hold_current,
            retry_point=True,
        )

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
                self.latest_power_supply_output_current_a = None
                output_current_a = float(supply.read_output_current())
                self.latest_power_supply_output_current_a = (
                    output_current_a if math.isfinite(output_current_a) else None
                )
                self._last_live_current_read_at = time.monotonic()
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
        self._refresh_live_power_supply_current()
        if self._automatic_resume.is_set():
            if self._automatic_retry_requested:
                self._automatic_retry_requested = False
                raise AutomaticPointRetry(
                    self._automatic_retry_reason or "重试当前测试点"
                )
            return 0.0
        paused_at = time.monotonic()
        while not self._automatic_resume.is_set():
            if self._automatic_stop.wait(0.05):
                return None
            self._refresh_live_power_supply_current()
        paused_duration = time.monotonic() - paused_at
        if self._automatic_stop.is_set():
            return None
        if self._automatic_retry_requested:
            self._automatic_retry_requested = False
            raise AutomaticPointRetry(self._automatic_retry_reason or "重试当前测试点")
        return paused_duration

    def _wait_automatic_duration(
        self,
        duration_s: float,
        *,
        sample_point_index: int | None = None,
        target_current_a: float = 0.0,
        on_tick: Callable[[], None] | None = None,
        interval_s: float | None = None,
        on_interval: Callable[[float], None] | None = None,
    ) -> bool:
        active_elapsed = 0.0
        next_sample_s = 1.0
        next_interval_s = interval_s
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
            while (
                on_interval is not None
                and next_interval_s is not None
                and next_interval_s > 0.0
                and next_interval_s <= min(active_elapsed, duration_s) + 0.05
            ):
                on_interval(min(next_interval_s, duration_s))
                next_interval_s += interval_s or 0.0
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

    def _queue_automatic_export(self) -> None:
        store = self.record_store
        records = tuple(store.snapshot())
        session = store.current_session
        path = store.workbook_path
        if not records or session is None or path is None:
            self.export_status = "idle"
            return
        recorder = self.automatic_sample_recorder
        if recorder is not None:
            recorder.flush_now()
        attempts = store.list_attempts(session.session_id)
        sample_path = Path(self.automatic_sample_path) if self.automatic_sample_path else None
        self._export_generation += 1
        generation = self._export_generation
        self.export_status = "exporting"
        self.export_error = ""

        def on_export_done(out_path: str | None, error: Exception | None) -> None:
            final_error = error
            saved_path = None if not out_path else Path(out_path)
            if final_error is None and saved_path is not None:
                try:
                    store.mark_saved(records)
                except Exception as exc:
                    final_error = exc
            if final_error is None and saved_path is not None and sample_path is not None:
                expected = automatic_sample_path(saved_path)
                if sample_path.resolve(strict=False) == expected.resolve(strict=False):
                    try:
                        sample_path.unlink(missing_ok=True)
                    except OSError as exc:
                        self.notify("error", "逐秒记录", f"临时 CSV 删除失败：{exc}")
            if generation != self._export_generation:
                if final_error is not None:
                    self.notify(
                        "error",
                        "上一台 Excel 生成失败",
                        f"SN {session.sn} 的 JSONL 已保留：{final_error}",
                    )
                return
            if final_error is not None:
                self.export_status = "failed"
                self.export_error = str(final_error)
                self.notify(
                    "error",
                    "Excel 生成失败",
                    f"原始测试记录已保存在本地 JSONL：{final_error}",
                )
                return
            if saved_path is None:
                self.export_status = "failed"
                self.export_error = "Excel 生成未返回目标路径"
                return
            self.export_status = "completed"
            self.export_path = str(saved_path)
            if self.automatic_sample_path == str(sample_path or ""):
                self.automatic_sample_path = ""
            self.notify("info", "导出完成", f"测试记录已保存：{saved_path}")

        worker = getattr(self.backend, "export_worker", None)
        if worker is None:
            try:
                saved_path = save_test_records(
                    path,
                    records,
                    session=session,
                    attempts=attempts,
                    automatic_samples_path=sample_path,
                )
            except Exception as exc:
                on_export_done(None, exc)
            else:
                on_export_done(str(saved_path), None)
            return
        submitted = worker.submit(
            ExportTask(
                target_path=path,
                records=list(records),
                callback=on_export_done,
                session=session,
                attempts=attempts,
                automatic_samples_path=sample_path,
            )
        )
        if not submitted:
            on_export_done(None, RuntimeError("Excel 保存队列已满，请稍后重新生成"))

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
                bool(self.spectrometer_reader and self.spectrometer_reader.is_ready)
                and not self._aging_test_selected()
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
            stable_window_s=max(
                automatic_stability_window_s(
                    self.configuration.get("selectedScriptId", "standard"),
                    self.configuration["stableWindowS"],
                ),
                automatic_power_stability_window_s(
                    self.configuration.get("selectedScriptId", "standard"),
                    self.configuration["stableWindowS"],
                    build_test_currents(settings)[0],
                ),
            ),
            post_stable_delay_s=FINAL_AUTO_POST_STABLE_DELAY_S,
            minimum_ramp_down_interval_s=(
                0.0 if self.power_supply_kind == "tdk" else MIN_POWER_SUPPLY_COMMAND_INTERVAL_S
            ),
        )

    def automatic_readiness(self) -> dict[str, Any]:
        blockers: list[dict[str, str]] = []

        def block(code: str, message: str) -> None:
            blockers.append({"code": code, "message": message})

        if self.automatic.state not in {
            AutomaticTestState.IDLE,
            AutomaticTestState.COMPLETED,
        }:
            block("automatic_not_idle", "当前自动测试尚未重置")
        if not str(self.configuration.get("sn", "")).strip():
            block("missing_sn", "请填写产品 SN")
        if not str(self.configuration.get("outputDir", "")).strip():
            block("missing_output_dir", "请选择测试数据输出目录")

        script_id = str(self.configuration.get("selectedScriptId", "")).strip()
        script = next(
            (item for item in BUILTIN_TEST_SCRIPTS if str(item.get("id")) == script_id),
            None,
        )
        if script is None:
            block("invalid_script", "请选择有效的测试脚本")
        elif self.power_supply_kind not in tuple(
            script.get("powerSupplyKinds", ()) or (self.power_supply_kind,)
        ):
            block("script_power_supply_mismatch", "当前测试脚本与电源类型不兼容")

        supply = self._power_supply()
        if supply is None:
            block("power_supply_not_connected", "请先连接电源控制器")
        if self.power_supply_identity_error:
            block("power_supply_identity_error", f"电源状态异常：{self.power_supply_identity_error}")
        if float(self.active_current_a or 0.0) > 0.005:
            block("output_current_not_zero", "开始自动测试前必须先将输出电流归零")
        if supply is not None and self.power_supply_kind == "tdk" and not supply.output_enabled:
            block("tdk_output_disabled", "请先开启 TDK 电源输出")

        if not str(self.configuration.get("powerMeterResource", "")).strip():
            block("power_meter_not_selected", "请选择功率计资源")
        try:
            self.backend.power_meter_profiles.require_ready()
            self._automatic_settings()
        except (TypeError, ValueError) as exc:
            block("invalid_test_settings", str(exc))

        return {"ready": not blockers, "blockers": blockers}

    def _require_automatic_ready(self) -> AutomaticTestSettings:
        readiness = self.automatic_readiness()
        blockers = readiness["blockers"]
        if blockers:
            raise ValueError("；".join(str(item["message"]) for item in blockers))
        return self._automatic_settings()

    def _aging_test_selected(self) -> bool:
        return str(self.configuration.get("selectedScriptId", "standard")) == AGING_TEST_SCRIPT_ID

    def start_automatic(self, params: dict[str, Any]) -> None:
        with self.command_safety.command():
            self._start_automatic_impl(params)

    def _start_automatic_impl(self, params: dict[str, Any]) -> None:
        if self._automatic_thread is not None and self._automatic_thread.is_alive():
            raise ValueError("自动测试已在运行")
        self.configure(params)
        if self.automatic.state == AutomaticTestState.COMPLETED:
            self._reset_completed_automatic(clear_sn=False, require_shutdown=False)
        aging_test = self._aging_test_selected()
        if not aging_test:
            self._select_latest_test_reference(params)
        settings = self._require_automatic_ready()
        self.reset_charts()
        self.automatic.start(
            settings,
            power_meter_ready=bool(self.power_meter_reader and self.power_meter_reader.is_ready),
            spectrum_meter_ready=bool(self.spectrometer_reader and self.spectrometer_reader.is_ready),
        )
        self.automatic_started_at = time.monotonic()
        self.automatic_finished_at = None
        self.automatic_run_context = {
            key: str(self.configuration.get(key, ""))
            for key in ("sn", "station", "testTemperatureC", "selectedScriptId")
        }
        self.automatic_terminal_outcome = None
        self.automatic_terminal_reason = ""
        self._clear_shutdown_result()
        self.power_drop_protection.reset()
        self.wavelength_stability_detector = WavelengthStabilityDetector(
            automatic_stability_window_s(
                self.configuration.get("selectedScriptId", "standard"),
                self.configuration["stableWindowS"],
            ),
            STANDARD_WAVELENGTH_STABILITY_TOLERANCE_NM,
        )
        self.latest_wavelength_stable = False
        self.latest_wavelength_span_nm = math.inf
        self.export_status = "recording"
        self.export_path = ""
        self.export_error = ""
        self._automatic_retry_requested = False
        self._automatic_retry_reason = ""
        self._automatic_pause_holds_current = False
        self._clear_automatic_pause()
        self._export_generation += 1
        try:
            if aging_test:
                self.record_store = self._new_record_store()
            else:
                self._begin_recording_session(settings)
        except Exception as exc:
            self.automatic = AutomaticTestOrchestrator()
            self.record_store = self._new_record_store()
            self.export_status = "failed"
            self.export_error = str(exc)
            self._close_automatic_sample_recorder()
            raise
        started_power_reader = self.power_meter_reader is None
        started_spectrum_reader = (
            not aging_test and settings.use_spectrometer and self.spectrometer_reader is None
        )
        try:
            stable_window_s = automatic_stability_window_s(
                self.configuration.get("selectedScriptId", "standard"),
                self.configuration["stableWindowS"],
            )
            if self.power_meter_reader is None:
                self.start_power_meter(stable_window_s=stable_window_s)
            else:
                self.power_meter_reader.update_stability_settings(
                    stable_window_s,
                    float(self.configuration["stabilityLimitPercent"]),
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
            self.record_store = self._new_record_store()
            self.automatic = AutomaticTestOrchestrator()
            self.export_status = "failed"
            self.export_error = str(exc)
            raise
        # Starting the worker and resetting its stop event are one short
        # transition. An emergency invalidates this request before signaling
        # the stop event, so an old start cannot clear that signal afterwards.
        try:
            with self.command_safety.commit():
                self._automatic_command_generation = self.command_safety.token()
                self._automatic_stop.clear()
                self._automatic_resume.set()
                self._automatic_thread = threading.Thread(
                    target=self._run_automatic,
                    name="automatic-test",
                    daemon=True,
                )
                self._automatic_thread.start()
        except Exception:
            self._automatic_stop.set()
            self.automatic.complete()
            self.automatic_finished_at = time.monotonic()
            self.automatic_detail = "启动已取消，请等待安全停止完成"
            self._close_automatic_sample_recorder()
            raise

    def _begin_automatic_measurement_window(
        self,
        current_a: float | None = None,
    ) -> tuple[int, int]:
        """Return boundaries that exclude samples from the previous setpoint."""
        reader = self.power_meter_reader
        if reader is None:
            raise RuntimeError("功率计采集线程未运行")
        reader.update_stability_settings(
            automatic_power_stability_window_s(
                self.configuration.get("selectedScriptId", "standard"),
                self.configuration["stableWindowS"],
                current_a,
            ),
            float(self.configuration["stabilityLimitPercent"]),
            automatic_power_stability_span_limit_w(current_a),
        )
        stability_generation = reader.reset_stability_window()
        with self._lock:
            self.automatic_stability_generation = stability_generation
            self.automatic_stability_reading = None
            self.automatic_stable_hold_elapsed_s = 0.0
            spectrum_revision = self.spectrum_revision
        return stability_generation, spectrum_revision

    def _fresh_automatic_measurement(
        self,
        *,
        stability_generation: int,
        spectrum_revision: int,
        use_spectrometer: bool,
        minimum_received_monotonic_s: float | None = None,
        require_live: bool = False,
    ) -> tuple[PowerMeterReading, Any] | None:
        """Return a stable sample only when every input is from this setpoint."""
        with self._lock:
            reading = self.latest_power
            frame = self.latest_spectrum
            current_spectrum_revision = self.spectrum_revision
            power_received_at = self.latest_power_received_monotonic_s
            spectrum_received_at = self.latest_spectrum_received_monotonic_s
            if require_live:
                now = time.monotonic()
                power_gap_s = max(1.0, float(self.configuration["powerMeterIntervalMs"]) * 3.0 / 1000.0)
                spectrum_gap_s = max(1.0, float(self.configuration["spectrometerIntervalMs"]) * 3.0 / 1000.0)
                if (
                    self.power_meter_reader is None or not self.power_meter_reader.is_ready
                    or power_received_at is None or not 0 <= now - power_received_at <= power_gap_s
                    or reading is None or not math.isfinite(reading.power_w)
                ):
                    return None
                if use_spectrometer and (
                    self.spectrometer_reader is None or not self.spectrometer_reader.is_ready
                    or spectrum_received_at is None or not 0 <= now - spectrum_received_at <= spectrum_gap_s
                ):
                    return None

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
            automatic_stability_window_s(
                self.configuration.get("selectedScriptId", "standard"),
                self.configuration["stableWindowS"],
            ),
            automatic_wavelength_stability_tolerance_nm(current_a),
        )
        self.latest_wavelength_stable = False
        self.latest_wavelength_span_nm = math.inf
        stability_generation, spectrum_revision = self._begin_automatic_measurement_window(current_a)
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
                require_live=True,
            )
            if measurement is not None:
                reading, frame = measurement
                return reading, frame, stability_generation, spectrum_revision
            time.sleep(0.05)
        raise TimeoutError(f"{current_a:g} A 测试点等待稳定超时")

    def _wait_for_automatic_stable_hold(
        self,
        settings: AutomaticTestSettings,
        current_a: float,
        index: int,
        stability_generation: int,
        spectrum_revision: int,
    ) -> tuple[PowerMeterReading, Any]:
        """Require an uninterrupted stable interval before reading voltage."""
        required_s = automatic_post_stable_delay_s(index, len(self.automatic.currents))
        dwell_s = self.automatic.current_dwell_s
        timer = StableHoldTimer(required_s, max(1.0, float(self.configuration["powerMeterIntervalMs"]) * 3.0 / 1000.0))
        started_at = time.monotonic()
        self.automatic_hold_started_at = started_at
        deadline = started_at + dwell_s + settings.point_timeout_s
        next_sample_s = 1.0
        while True:
            paused_s = self._await_automatic_resume()
            if paused_s is None or self._automatic_stop.is_set():
                raise RuntimeError(self.automatic_terminal_reason or "自动测试已停止")
            if paused_s > 0.0:
                timer.reset()
                started_at += paused_s
                deadline += paused_s
                self.automatic_hold_started_at = started_at
            with self._lock:
                now = time.monotonic()
                if self._automatic_stop.is_set():
                    raise RuntimeError(self.automatic_terminal_reason or "自动测试已停止")
                if self.automatic.state == AutomaticTestState.PAUSED:
                    continue
                measurement = self._fresh_automatic_measurement(
                    stability_generation=stability_generation,
                    spectrum_revision=spectrum_revision,
                    use_spectrometer=settings.use_spectrometer,
                    require_live=True,
                )
                if measurement is None:
                    timer.reset()
                    self.automatic_stable_hold_elapsed_s = 0.0
                    self.automatic.set_state(AutomaticTestState.WAITING_STABLE)
                    self.automatic_detail = f"{current_a:g} A 波动超限或采样中断，重新判稳后保持 {required_s:g} 秒"
                else:
                    reading, frame = measurement
                    elapsed_s = timer.observe(reading.elapsed_s, self.latest_power_received_monotonic_s, now, stable=True)
                    self.automatic_stable_hold_elapsed_s = elapsed_s
                    self.automatic_stability_reading = reading
                    self.automatic.set_state(AutomaticTestState.WAITING_VOLTAGE)
                    self.automatic_detail = f"{current_a:g} A 已稳定，保持 {elapsed_s:.1f} / {required_s:g} 秒"
                    power_w = self._stable_power_w(reading)
                    if self.stable_power_points.get(current_a) != power_w:
                        self.stable_power_points[current_a] = power_w
                        self.efficiency_points.pop(current_a, None)
                        self.series_revisions["stable"] += 1
                    if elapsed_s >= required_s and now - started_at >= dwell_s:
                        return reading, frame
            if now >= deadline:
                raise TimeoutError(f"{current_a:g} A 测试点未能连续保持稳定 {required_s:g} 秒")
            while self.automatic_sample_recorder is not None and dwell_s > 0 and next_sample_s <= min(dwell_s, now - started_at):
                self._write_automatic_second_sample(point_index=index + 1, elapsed_s=next_sample_s, target_current_a=current_a)
                next_sample_s += 1.0
            if self._automatic_stop.wait(0.05):
                raise RuntimeError(self.automatic_terminal_reason or "自动测试已停止")

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

    def _wait_for_aging_power_meter(self, timeout_s: float = 15.0) -> None:
        deadline = time.monotonic() + timeout_s
        while not self.automatic.power_meter_ready:
            if self._automatic_stop.wait(0.05):
                raise RuntimeError(self.automatic_terminal_reason or "AU 老化测试已停止")
            if time.monotonic() >= deadline:
                raise TimeoutError("功率计在 15 秒内未就绪，无法开始 AU 老化测试")

    def _record_aging_power_sample(self, elapsed_s: float) -> None:
        with self._lock:
            reader = self.power_meter_reader
            reading = self.latest_power
            received_at = self.latest_power_received_monotonic_s
            freshness_limit_s = max(
                5.0,
                float(self.configuration["powerMeterIntervalMs"]) / 1_000.0 * 3.0,
            )
            if (
                reader is None
                or not reader.is_ready
                or reading is None
                or received_at is None
                or time.monotonic() - received_at > freshness_limit_s
                or not math.isfinite(float(reading.power_w))
            ):
                raise RuntimeError("AU 老化功率采样失败：功率计没有新鲜有效读数")
            elapsed_minutes = elapsed_s / 60.0
            power_w = float(reading.power_w)
            self.aging_power_points.append(
                {"elapsedMinutes": elapsed_minutes, "powerW": power_w}
            )
            self.series_revisions["aging"] += 1
        self.automatic_detail = (
            f"20 A 老化中：已记录 {elapsed_minutes:g} 分钟功率 {power_w:.6g} W"
        )

    def _set_automatic_current(self, current_a: float) -> None:
        generation = getattr(self, "_automatic_command_generation", self.command_safety.token())
        with self.command_safety.command(generation):
            self._set_automatic_current_impl(current_a)

    def _set_automatic_current_impl(self, current_a: float) -> None:
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
            raise AutomaticPointQualityError(
                AttemptValidity.WEAK_SIGNAL,
                f"光谱信号过弱：峰值 {peak:.0f} counts，必须至少达到 "
                f"{MIN_SPECTRUM_PEAK_INTENSITY:.0f} counts",
            )
        if bool(frame.saturation and frame.saturation.saturated):
            raise AutomaticPointQualityError(
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
        stable_reading, stable_frame = self._wait_for_automatic_stable_hold(
            settings, current_a, index, stability_generation, spectrum_revision,
        )

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
        self.automatic.set_state(AutomaticTestState.SAVING_POINT)
        with self._lock:
            self.automatic_stability_reading = stable_reading
        while True:
            try:
                self.record_store.queue(
                    record,
                    actual_current_a=float(self.active_current_a or current_a),
                    voltage_raw_v=raw_voltage_v,
                    stable_span_w=stable_reading.stable_span_w,
                    stable_window_s=stable_reading.stable_window_s,
                    stability_percent=stable_reading.stability_percent,
                    stability_limit_percent=float(self.configuration["stabilityLimitPercent"]),
                    integration_time_us=int(self.configuration["integrationTimeUs"]),
                )
                break
            except SessionJournalError as exc:
                self._set_automatic_pause(
                    str(exc),
                    kind="record_persistence_failed",
                    hold_current=False,
                    retry_point=False,
                )
                self.notify(
                    "error",
                    "本地记录失败",
                    f"{exc}；测试不会进入下一点",
                )
                if self._await_automatic_resume() is None:
                    raise RuntimeError(self.automatic_terminal_reason or "自动测试已停止")
                self.automatic.set_state(AutomaticTestState.SAVING_POINT)
        with self._lock:
            self.stable_power_points[current_a] = stable_power_w
            self.efficiency_points[current_a] = efficiency
            self.series_revisions["stable"] += 1
        self.export_status = "recording"
        self.automatic_detail = f"{current_a:g} A 已写入本地测试记录"
        if self.automatic.state == AutomaticTestState.PAUSED:
            self._automatic_retry_requested = False
            if self._await_automatic_resume() is None:
                raise RuntimeError(self.automatic_terminal_reason or "自动测试已停止")

    def _run_aging_cycle(self) -> None:
        self._wait_for_aging_power_meter()
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
        if not self._wait_automatic_duration(
            AGING_HOLD_DURATION_S,
            interval_s=AGING_POWER_SAMPLE_INTERVAL_S,
            on_interval=self._record_aging_power_sample,
        ):
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
                    except AutomaticPointRetry:
                        continue
                    except AutomaticPointQualityError as exc:
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
                    except AutomaticPointRetry:
                        continue

            self.safe_ramp_down()
            self.record_store.complete_session(
                SessionStatus.COMPLETED,
                "所有计划测试点均已保存",
                shutdown_confirmed=True,
            )
            self._close_automatic_sample_recorder()
            self._queue_automatic_export()
            self.automatic.complete()
            self._clear_automatic_pause()
            self.automatic_terminal_outcome = "succeeded"
            self.automatic_terminal_reason = "所有计划测试点均已保存"
            self.automatic_detail = "自动测试已安全完成，本地记录已保存"
        except Exception as exc:
            _LOGGER.exception("自动测试异常")
            reason = self.automatic_terminal_reason or str(exc)
            operator_stopped = reason in {
                "操作者提前结束测试",
                "急停",
                "自动测试已停止",
            }
            self.automatic_detail = reason
            self.automatic_terminal_reason = reason
            shutdown_confirmed = False
            try:
                self.safe_ramp_down()
                shutdown_confirmed = True
            except Exception as shutdown_error:
                self.notify("error", "安全下电失败", str(shutdown_error))
                reason = f"{reason}；安全下电失败：{shutdown_error}"
                self.automatic_terminal_reason = reason
            self.automatic_terminal_outcome = (
                "shutdown_unconfirmed"
                if not shutdown_confirmed
                else "stopped_shutdown_confirmed"
                if operator_stopped
                else "aborted_shutdown_confirmed"
            )
            current_a = self.automatic.current_a
            if current_a is not None:
                validity = (
                    AttemptValidity.TIMEOUT
                    if isinstance(exc, TimeoutError)
                    else AttemptValidity.DEVICE_ERROR
                )
            try:
                if current_a is not None:
                    self.record_store.record_invalid_attempt(current_a, validity, reason)
                self.record_store.complete_session(
                    SessionStatus.SHUTDOWN_UNCONFIRMED
                    if not shutdown_confirmed
                    else SessionStatus.STOPPED_BY_OPERATOR
                    if operator_stopped
                    else SessionStatus.ABORTED_SAFELY,
                    reason,
                    shutdown_confirmed=shutdown_confirmed,
                )
            except SessionJournalError as journal_error:
                self.notify("error", "本地记录失败", str(journal_error))
            try:
                self._close_automatic_sample_recorder()
                self._queue_automatic_export()
            except Exception as sample_error:
                self.notify("error", "逐秒记录失败", str(sample_error))
            self.automatic.complete()
            self._clear_automatic_pause()

        finally:
            self.automatic_finished_at = time.monotonic()

    def safe_ramp_down(self) -> None:
        with self.command_safety.stopping(), self._safe_ramp_lock:
            self._shutdown_measurement_candidate = None
            try:
                with self._supply_io_lock:
                    measured_current_a, output_off_confirmed = self._safe_ramp_down_impl()
            except Exception as exc:
                self._record_shutdown_result(
                    confirmed=False,
                    measured_current_a=self._shutdown_measurement_candidate,
                    output_off_confirmed=False,
                    reason=str(exc),
                )
                raise
            self._record_shutdown_result(
                confirmed=True,
                measured_current_a=measured_current_a,
                output_off_confirmed=output_off_confirmed,
                reason="电源输出已确认关闭" if output_off_confirmed else "电流已确认归零",
            )

    def _safe_ramp_down_impl(self) -> tuple[float | None, bool]:
        if self.power_supply_identity_error:
            raise RuntimeError("CH341 端口身份校验失败，禁止下发电流指令；请先安全断开")
        supply = self._power_supply()
        if supply is None:
            return None, True
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
        read_current_during_ramp = self.power_supply_kind == "tdk"
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
                    self._shutdown_measurement_candidate = measured_current_a
                    if not math.isfinite(measured_current_a) or abs(measured_current_a) > 0.01:
                        raise RuntimeError(
                            f"无法确认电源输出电流已归零：{measured_current_a:.3f} A"
                        )
                    self.active_current_a = 0.0
                    self.configuration["setCurrentA"] = 0.0
                    self.latest_power_supply_output_current_a = measured_current_a
                    return measured_current_a, True
                raise
            self.active_current_a = current_a
            self.latest_power_supply_output_current_a = None
            if current_a > 0.0:
                if read_current_during_ramp:
                    read_current_during_ramp = self._refresh_live_power_supply_current(force=True)
                time.sleep(settings.ramp_down_interval_s)
        if not ramp_currents or ramp_currents[-1] != 0.0:
            supply.set_current(0.0)
        self.active_current_a = 0.0
        self.latest_power_supply_output_current_a = None
        self.configuration["setCurrentA"] = 0.0
        if self.power_supply_kind == "tdk":
            # Always send OUT 0; the cached flag cannot prove the physical
            # state immediately after connecting to an already-running supply.
            supply.set_output_enabled(False)
        if self.power_supply_kind == "tdk":
            measured_current_a = float(supply.read_output_current())
            self._shutdown_measurement_candidate = measured_current_a
            self.latest_power_supply_output_current_a = measured_current_a
            if not math.isfinite(measured_current_a) or abs(measured_current_a) > 0.01:
                raise RuntimeError(f"无法确认电源输出电流已归零：{measured_current_a:.3f} A")
            return measured_current_a, True
        return None, True

    def pause_automatic(self) -> None:
        if self.automatic.state in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED, AutomaticTestState.PAUSED}:
            raise ValueError("当前状态不能暂停")
        if self.automatic.state == AutomaticTestState.SAVING_POINT:
            self.automatic_detail = "当前测试点正在保存，保存完成后再暂停"
            return
        self._set_automatic_pause(
            "操作员暂停",
            kind="operator",
            hold_current=False,
            retry_point=False,
        )

    def resume_automatic(
        self,
        action: str = "retry_point",
        params: dict[str, Any] | None = None,
    ) -> None:
        if self.automatic.state != AutomaticTestState.PAUSED:
            raise ValueError("当前状态不能重试")
        action_name = str(action)
        if action_name not in self.automatic_pause_actions:
            raise ValueError(f"当前暂停状态不允许执行：{action_name}")
        values = params or {}
        if action_name == "adjust_spectrum":
            from .command_safety import finite_number
            integration_time_us = finite_number(values.get("integrationTimeUs", 0), "integrationTimeUs")
            if not integration_time_us.is_integer():
                raise ValueError("积分时间必须是整数")
            integration_time_us = int(integration_time_us)
            if integration_time_us <= 0:
                raise ValueError("积分时间必须大于 0 μs")
            self.configuration["autoIntegration"] = False
            self.configuration["integrationTimeUs"] = integration_time_us
        self._automatic_retry_requested = action_name in {
            "adjust_spectrum",
            "recover_power_meter",
            "recover_spectrometer",
            "retry_point",
        }
        settings = self.automatic.settings
        if action_name == "recover_power_meter" and self.power_meter_reader is None:
            self.start_power_meter(
                stable_window_s=automatic_stability_window_s(
                    self.configuration.get("selectedScriptId", "standard"),
                    self.configuration["stableWindowS"],
                )
            )
        if action_name == "recover_spectrometer" and self.spectrometer_reader is None:
            self.start_spectrometer()
        if settings is not None and settings.use_spectrometer and action_name in {
            "adjust_spectrum",
            "recover_spectrometer",
            "retry_point",
        }:
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
        self._clear_automatic_pause()
        self._automatic_command_generation = self.command_safety.token()
        self._automatic_resume.set()

    def request_emergency_stop(self, reason: str = "操作者提前结束测试") -> None:
        self.command_safety.invalidate()
        if self._automatic_thread is not None and self._automatic_thread.is_alive():
            if not self.automatic_terminal_reason:
                self.automatic_terminal_reason = reason
        self._automatic_stop.set()
        self._automatic_resume.set()
        self._start_emergency_ramp_down(reason)

    def end_automatic(self) -> None:
        with self.command_safety.stopping():
            self._end_automatic_impl()

    def _end_automatic_impl(self) -> None:
        self.request_emergency_stop()
        thread = self._automatic_thread
        if thread is not None:
            thread.join(30.0)
            if thread.is_alive():
                raise RuntimeError("自动测试安全下电超时，后台线程仍在运行")
        ramp_thread = self._emergency_ramp_thread
        if ramp_thread is not None:
            ramp_thread.join(30.0)
            if ramp_thread.is_alive():
                raise RuntimeError("电源安全降流超时，后台线程仍在运行")
        if not self.shutdown_confirmed:
            self.safe_ramp_down()

    def _reset_completed_automatic(
        self,
        *,
        clear_sn: bool,
        require_shutdown: bool = True,
    ) -> None:
        if self._automatic_thread is not None and self._automatic_thread.is_alive():
            raise ValueError("自动测试仍在运行，不能进入下一台")
        if self.automatic.state != AutomaticTestState.COMPLETED:
            raise ValueError("当前测试尚未结束")
        if require_shutdown and not self.shutdown_confirmed:
            raise RuntimeError("尚未确认安全下电，不能进入下一台")
        self._export_generation += 1
        self.automatic = AutomaticTestOrchestrator()
        self.record_store = self._new_record_store()
        self.automatic_stability_generation = None
        self.automatic_stability_reading = None
        self.automatic_stable_hold_elapsed_s = 0.0
        if clear_sn:
            self.configuration["sn"] = ""
        self.automatic_detail = "请输入下一台产品 SN" if clear_sn else "准备重新测试"
        self.automatic_terminal_outcome = None
        self.automatic_terminal_reason = ""
        self.automatic_started_at = None
        self.automatic_finished_at = None
        self.automatic_run_context = None
        self._test_reference = None
        self.export_status = "idle"
        self.export_path = ""
        self.export_error = ""
        self.automatic_sample_path = ""
        self._clear_automatic_pause()
        self._clear_shutdown_result()
        self.reset_charts()
        self.backend.clear_transient_notices()

    def prepare_next_device(self) -> None:
        self._reset_completed_automatic(clear_sn=True)

    def retry_automatic_export(self, params: dict[str, Any]) -> None:
        if self.automatic.state != AutomaticTestState.COMPLETED:
            raise ValueError("测试结束后才能重新生成 Excel")
        output_dir = str(params.get("outputDir", "")).strip()
        if output_dir:
            self.configuration["outputDir"] = output_dir
            self.record_store.retarget_workbook(Path(output_dir))
        self._queue_automatic_export()
