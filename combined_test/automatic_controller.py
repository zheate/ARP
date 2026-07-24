"""Automatic test lifecycle controller kept outside the main window module.

The controller coordinates the device ports and record store while delegating
operator-facing UI effects to its host.
"""

from __future__ import annotations

import math
from collections import deque
from enum import Enum
from typing import Any, Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from .automation import (
    AutomaticTestSettings,
    AutomaticTestState,
    PowerDropProtectionDetector,
    build_ramp_up_currents,
    validate_automatic_test_settings,
)
from .device_interfaces import PowerMeter, PowerSupply, SpectrumMeter
from .record_store import AttemptValidity, RecordStore, SessionStatus

AUTO_VOUT_AFTER_STABLE_S = 5.0
AUTOMATIC_DEVICE_START_TIMEOUT_S = 15.0


class AutomaticTestTerminalOutcome(str, Enum):
    """Why an automatic-test lifecycle reached its safe terminal state."""

    SUCCEEDED = "succeeded"
    STOPPED_BY_OPERATOR = "stopped_by_operator"
    ABORTED_SAFELY = "aborted_safely"

AUTOMATIC_CONTROLLER_METHODS = (
    "collect_automatic_test_settings",
    "start_automatic_test",
    "on_power_meter_ready",
    "on_spectrometer_ready",
    "maybe_start_automatic_current_sequence",
    "begin_automatic_current_point",
    "schedule_next_automatic_ramp_up_current",
    "schedule_automatic_current_command",
    "on_automatic_command_timer_timeout",
    "write_automatic_current",
    "set_automatic_test_state",
    "pause_automatic_test",
    "on_automatic_pause_safety_timeout",
    "automatic_measurement_is_active",
    "retry_automatic_test",
    "end_automatic_test",
    "on_automatic_device_start_timeout",
    "on_automatic_point_timeout",
    "on_automatic_ramp_down_current_applied",
    "begin_automatic_ramp_down",
    "schedule_next_automatic_ramp_down_current",
    "complete_automatic_test",
    "reset_automatic_test",
)


class AutomaticTestController:
    def __init__(
        self,
        host: Any,
        *,
        power_supply_provider: Callable[[], PowerSupply | None],
        power_meter_provider: Callable[[], PowerMeter | None],
        spectrum_meter_provider: Callable[[], SpectrumMeter | None],
        record_store: RecordStore,
        error_formatter: Callable[[Any], str] = str,
    ) -> None:
        object.__setattr__(self, "_host", host)
        object.__setattr__(self, "_power_supply_provider", power_supply_provider)
        object.__setattr__(self, "_power_meter_provider", power_meter_provider)
        object.__setattr__(self, "_spectrum_meter_provider", spectrum_meter_provider)
        object.__setattr__(self, "_error_formatter", error_formatter)
        object.__setattr__(self, "record_store", record_store)
        object.__setattr__(self, "_terminal_outcome", None)
        object.__setattr__(self, "_terminal_reason", "")
        object.__setattr__(self, "_pending_terminal_outcome", None)
        object.__setattr__(self, "_pending_terminal_reason", "")
        object.__setattr__(self, "_output_shutdown_unconfirmed", False)
        object.__setattr__(self, "_pause_was_operator_requested", False)
        object.__setattr__(self, "_save_completed_while_paused", False)
        object.__setattr__(self, "_current_write_retry_key", None)
        object.__setattr__(self, "_current_write_retry_count", 0)
        object.__setattr__(self, "_power_drop_protection", PowerDropProtectionDetector())

    def bind_to_host(self) -> None:
        for name in AUTOMATIC_CONTROLLER_METHODS:
            setattr(self._host, name, getattr(self, name))

    def __getattr__(self, name: str) -> Any:
        if name == "power_meter_reader":
            return self._power_meter_provider()
        if name == "spectrometer_reader":
            return self._spectrum_meter_provider()
        return getattr(self._host, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_") or name == "record_store":
            object.__setattr__(self, name, value)
            return
        setattr(self._host, name, value)

    def get_power_supply(self) -> PowerSupply | None:
        return self._power_supply_provider()

    @property
    def terminal_outcome(self) -> AutomaticTestTerminalOutcome | None:
        return self._terminal_outcome

    @property
    def terminal_reason(self) -> str:
        return self._terminal_reason

    @property
    def output_shutdown_unconfirmed(self) -> bool:
        """Whether a direct TDK OUT 0 command failed or could not be sent."""

        return self._output_shutdown_unconfirmed

    def _clear_terminal_outcome(self) -> None:
        object.__setattr__(self, "_terminal_outcome", None)
        object.__setattr__(self, "_terminal_reason", "")
        object.__setattr__(self, "_pending_terminal_outcome", None)
        object.__setattr__(self, "_pending_terminal_reason", "")
        object.__setattr__(self, "_output_shutdown_unconfirmed", False)
        object.__setattr__(self, "_pause_was_operator_requested", False)
        object.__setattr__(self, "_save_completed_while_paused", False)

    def _set_terminal_outcome(
        self,
        outcome: AutomaticTestTerminalOutcome,
        reason: str,
    ) -> None:
        object.__setattr__(self, "_terminal_outcome", outcome)
        object.__setattr__(self, "_terminal_reason", str(reason).strip())
        object.__setattr__(self, "_pending_terminal_outcome", None)
        object.__setattr__(self, "_pending_terminal_reason", "")

    def _set_pending_terminal_outcome(
        self,
        outcome: AutomaticTestTerminalOutcome,
        reason: str,
    ) -> None:
        """Remember the planned result until hardware shutdown is confirmed."""

        object.__setattr__(self, "_pending_terminal_outcome", outcome)
        object.__setattr__(self, "_pending_terminal_reason", str(reason).strip())

    def _confirm_terminal_outcome(self) -> None:
        """Publish the planned result only after the safe shutdown boundary."""

        pending_outcome = self._pending_terminal_outcome
        if pending_outcome is None:
            if self._terminal_outcome is not None:
                return
            pending_outcome = AutomaticTestTerminalOutcome.SUCCEEDED
            pending_reason = "所有计划测试点均已保存"
        else:
            pending_reason = self._pending_terminal_reason
        self._set_terminal_outcome(pending_outcome, pending_reason)

    def _mark_output_shutdown_unconfirmed(self) -> None:
        object.__setattr__(self, "_output_shutdown_unconfirmed", True)

    def _mark_output_shutdown_confirmed(self) -> None:
        object.__setattr__(self, "_output_shutdown_unconfirmed", False)

    def _clear_current_write_retry(self) -> None:
        object.__setattr__(self, "_current_write_retry_key", None)
        object.__setattr__(self, "_current_write_retry_count", 0)

    def _can_retry_legacy_current_write(self, current_a: float, kind: str) -> bool:
        """Allow one delayed retry for an idempotent CH341 current command."""

        if self.power_supply_controller_kind == "tdk" or kind == "ramp_down":
            return False
        retry_key = (round(float(current_a), 9), str(kind))
        if self._current_write_retry_key != retry_key:
            object.__setattr__(self, "_current_write_retry_key", retry_key)
            object.__setattr__(self, "_current_write_retry_count", 0)
        if self._current_write_retry_count >= 1:
            return False
        object.__setattr__(
            self,
            "_current_write_retry_count",
            self._current_write_retry_count + 1,
        )
        return True

    def _completion_message(self) -> str:
        outcome = self.terminal_outcome
        reason = self.terminal_reason
        if outcome == AutomaticTestTerminalOutcome.STOPPED_BY_OPERATOR:
            return "操作者提前结束测试，已安全下电至 0 A"
        if outcome == AutomaticTestTerminalOutcome.ABORTED_SAFELY:
            reason_text = f"（{reason}）" if reason else ""
            return f"自动测试异常中止{reason_text}，已安全下电至 0 A"
        return "自动测试完整完成，输出电流已降至 0 A"

    def on_voltage_record_ready(self, current_a: float, queued: bool, error: str) -> None:
        if self.automatic_test_state != AutomaticTestState.WAITING_VOLTAGE:
            return
        if not queued:
            self.pause_automatic_test(error or "当前测试点未能生成有效记录")
            return
        self.automatic_point_timer.stop()
        object.__setattr__(self, "_save_completed_while_paused", False)
        self.set_automatic_test_state(
            AutomaticTestState.SAVING_POINT,
            f"正在保存 {current_a:.1f} A 测试点",
        )
        self.save_pending_excel_records()

    def on_record_saved(self) -> None:
        paused_during_save = (
            self.automatic_test_state == AutomaticTestState.PAUSED
            and self.automatic_paused_from_state == AutomaticTestState.SAVING_POINT
        )
        if self.automatic_test_state != AutomaticTestState.SAVING_POINT and not paused_during_save:
            return
        expected_current = self.automatic_test_currents[self.automatic_test_current_index]
        if expected_current not in self.record_store.recorded_currents:
            if not paused_during_save:
                self.pause_automatic_test(f"{expected_current:.1f} A 测试点未确认写入 Excel")
            return
        if paused_during_save:
            object.__setattr__(self, "_save_completed_while_paused", True)
            message = (
                f"{expected_current:.1f} A 测试点已成功保存；"
                "排除故障后点击“重试”继续"
            )
            self.automatic_test_status_label.setText(message)
            self.statusBar().showMessage(message)
            self.add_log(message)
            return
        object.__setattr__(self, "_save_completed_while_paused", False)
        if self.automatic_orchestrator.advance():
            self.begin_automatic_current_point()
        else:
            self.automatic_completion_record = self.record_store.pending_records.get(expected_current)
            self.begin_automatic_ramp_down(
                terminal_outcome=AutomaticTestTerminalOutcome.SUCCEEDED,
                terminal_reason="所有计划测试点均已保存",
            )

    def on_record_save_failed(self, message: str) -> None:
        if self.automatic_test_state == AutomaticTestState.SAVING_POINT:
            self.pause_automatic_test(f"Excel 保存失败：{message}")

    def on_stable_power_captured(self, current_a: float) -> None:
        if self.automatic_test_state == AutomaticTestState.WAITING_STABLE:
            stable_label = (
                "功率及波长已稳定" if self.automatic_uses_spectrometer() else "功率已稳定"
            )
            self.set_automatic_test_state(
                AutomaticTestState.WAITING_VOLTAGE,
                f"{current_a:.1f} A {stable_label}，等待读取输出电压",
            )

    def on_acquisition_failed(self, device_label: str, message: str) -> None:
        if device_label == "光谱仪" and not self.automatic_uses_spectrometer():
            return
        if self.automatic_measurement_is_active():
            recorder = getattr(self.record_store, "record_invalid_attempt", None)
            if callable(recorder):
                try:
                    recorder(
                        float(self.active_output_current_a or 0.0),
                        AttemptValidity.DEVICE_ERROR,
                        f"{device_label}错误：{self._error_formatter(message)}",
                    )
                except Exception as exc:
                    self.add_log(f"设备异常尝试归档失败：{self._error_formatter(exc)}")
            self.pause_automatic_test(f"{device_label}错误：{self._error_formatter(message)}")

    def on_acquisition_stopped(self, device_label: str) -> None:
        if device_label == "光谱仪" and not self.automatic_uses_spectrometer():
            return
        if self.automatic_measurement_is_active():
            self.pause_automatic_test(f"{device_label}采集已停止")

    def on_automatic_power_sample(
        self,
        power_w: float,
        elapsed_s: float | None = None,
    ) -> bool:
        """Check the rolling five-second power change and trip shutdown."""

        if self.automatic_test_state not in (
            AutomaticTestState.SETTING_CURRENT,
            AutomaticTestState.WAITING_STABLE,
            AutomaticTestState.WAITING_VOLTAGE,
            AutomaticTestState.SAVING_POINT,
        ):
            return False
        if float(self.active_output_current_a or 0.0) <= 0.0:
            return False

        result = self._power_drop_protection.observe(power_w, elapsed_s)
        if not result.triggered or result.reference_power_w is None:
            return False

        drop_percent = result.drop_w / result.reference_power_w * 100.0
        reason = (
            "功率异常下降："
            f"5 秒前 {result.reference_power_w:.3f} W，当前 {result.observed_power_w:.3f} W，"
            f"下降 {result.drop_w:.3f} W（{drop_percent:.1f}%），"
            "超过 30% 保护阈值"
        )
        recorder = getattr(self.record_store, "record_invalid_attempt", None)
        if callable(recorder):
            try:
                recorder(
                    float(self.active_output_current_a or 0.0),
                    AttemptValidity.DEVICE_ERROR,
                    reason,
                )
            except Exception as exc:
                self.add_log(f"功率保护触发记录失败：{self._error_formatter(exc)}")
        self.add_log(f"功率保护触发，正在立即断电：{reason}")
        self._host.emergency_stop_for_power_protection(reason)
        return True

    def automatic_uses_spectrometer(self) -> bool:
        settings = self.automatic_test_settings
        return settings is None or settings.use_spectrometer

    def collect_automatic_test_settings(self) -> AutomaticTestSettings:
        settings = AutomaticTestSettings(
            initial_current_a=self.auto_initial_current_spin.value(),
            target_current_a=self.auto_target_current_spin.value(),
            current_step_a=self.auto_current_step_spin.value(),
            point_timeout_s=self.auto_point_timeout_spin.value(),
            ramp_down_step_a=self.auto_ramp_down_step_spin.value(),
            ramp_down_interval_s=self.auto_ramp_down_interval_spin.value(),
            pause_ramp_down_timeout_s=self.auto_pause_ramp_down_timeout_spin.value(),
            use_spectrometer=self.auto_use_spectrometer_check.isChecked(),
            maximum_current_a=(
                None if self._selected_power_supply_kind() == "tdk" else 20.0
            ),
        )
        return validate_automatic_test_settings(
            settings,
            stable_window_s=self.stable_window_spin.value(),
            post_stable_delay_s=AUTO_VOUT_AFTER_STABLE_S,
        )

    def start_automatic_test(self) -> None:
        if self.automatic_test_state not in (AutomaticTestState.IDLE, AutomaticTestState.COMPLETED):
            return
        if self.excel_save_thread is not None:
            self.statusBar().showMessage("请等待当前 Excel 保存完成")
            return
        if not self._manual_i2c_connected():
            label = "TDK" if self._selected_power_supply_kind() == "tdk" else "CH341"
            QMessageBox.warning(self._host, "自动测试", f"请先连接 {label}。")
            return
        if (
            self.power_supply_controller_kind == "tdk"
            and not bool(getattr(self.manual_ch341_controller, "output_enabled", False))
        ):
            QMessageBox.warning(self._host, "自动测试", "请先开启 TDK 电源输出。")
            return
        try:
            settings = self.collect_automatic_test_settings()
            self.begin_test_session(require_station=True)
        except Exception as exc:
            QMessageBox.warning(self._host, "自动测试", self._error_formatter(exc))
            return

        self._clear_terminal_outcome()
        self._clear_current_write_retry()
        self._power_drop_protection.reset()
        self.reset_power_curve()
        self.reset_stable_power_curve()
        self.reset_spectrum_curve()
        self.automatic_completion_record = None

        self.automatic_orchestrator.start(
            settings,
            power_meter_ready=bool(
                self.power_meter_reader is not None and getattr(self.power_meter_reader, "is_ready", False)
            ),
            spectrum_meter_ready=bool(
                self.spectrometer_reader is not None and getattr(self.spectrometer_reader, "is_ready", False)
            ),
        )
        configure_sequence = getattr(self.record_store, "configure_sequence", None)
        if callable(configure_sequence):
            configure_sequence(self.automatic_test_currents)
        self.set_automatic_test_state(AutomaticTestState.STARTING, "正在启动并确认采集设备")

        if self.power_meter_reader is None:
            self.start_power_meter()
        if settings.use_spectrometer and self.spectrometer_reader is None:
            self.start_spectrometer()
        self.pending_stable_point_current_a = None
        self.pending_stable_point_generation = None
        self.automatic_device_start_timer.start(round(AUTOMATIC_DEVICE_START_TIMEOUT_S * 1000.0))
        self.maybe_start_automatic_current_sequence()

    def on_power_meter_ready(self) -> None:
        self.automatic_orchestrator.mark_power_meter_ready()
        self.update_global_status()
        self.maybe_start_automatic_current_sequence()

    def on_spectrometer_ready(self) -> None:
        self.automatic_orchestrator.mark_spectrum_meter_ready()
        self.update_global_status()
        self.maybe_start_automatic_current_sequence()

    def maybe_start_automatic_current_sequence(self) -> None:
        if self.automatic_test_state != AutomaticTestState.STARTING:
            return
        if not self.automatic_orchestrator.acquisition_ready:
            return
        self.automatic_device_start_timer.stop()
        self.begin_automatic_current_point()

    def begin_automatic_current_point(self) -> None:
        if not (0 <= self.automatic_test_current_index < len(self.automatic_test_currents)):
            self.pause_automatic_test("自动测试电流序列无效")
            return
        current_a = self.automatic_test_currents[self.automatic_test_current_index]
        self.set_automatic_test_state(AutomaticTestState.SETTING_CURRENT, f"正在设置 {current_a:.1f} A")
        start_current_a = max(0.0, float(self.active_output_current_a or 0.0))
        if current_a > start_current_a:
            settings = self.automatic_test_settings
            maximum_current_a = settings.maximum_current_a if settings is not None else 20.0
            self.automatic_ramp_up_currents = deque(
                build_ramp_up_currents(
                    start_current_a,
                    current_a,
                    maximum_current_a=maximum_current_a,
                )
            )
            self.schedule_next_automatic_ramp_up_current()
        else:
            self.automatic_ramp_up_currents.clear()
            self.schedule_automatic_current_command(current_a, "test")

    def schedule_next_automatic_ramp_up_current(self) -> None:
        if self.automatic_test_state != AutomaticTestState.SETTING_CURRENT:
            return
        if not self.automatic_ramp_up_currents:
            self.pause_automatic_test("自动升流序列无效")
            return
        self.schedule_automatic_current_command(self.automatic_ramp_up_currents.popleft(), "ramp_up")

    def schedule_automatic_current_command(self, current_a: float, kind: str) -> None:
        self.pending_automatic_current_a = float(current_a)
        self.pending_automatic_command_kind = kind
        remaining_s = self.power_supply_command_interval_remaining_s()
        if remaining_s > 0.0:
            self.automatic_command_timer.start(max(1, math.ceil(remaining_s * 1000.0)))
            return
        self.on_automatic_command_timer_timeout()

    def on_automatic_command_timer_timeout(self) -> None:
        current_a = self.pending_automatic_current_a
        kind = self.pending_automatic_command_kind
        if current_a is None or kind is None:
            return
        remaining_s = self.power_supply_command_interval_remaining_s()
        if remaining_s > 0.0:
            self.automatic_command_timer.start(max(1, math.ceil(remaining_s * 1000.0)))
            return
        self.pending_automatic_current_a = None
        self.pending_automatic_command_kind = None
        self.write_automatic_current(current_a, kind)

    def write_automatic_current(self, current_a: float, kind: str) -> None:
        if not self._manual_i2c_connected():
            label = "TDK" if self._selected_power_supply_kind() == "tdk" else "CH341"
            self.pause_automatic_test(f"{label} 未连接")
            return
        if (
            self.power_supply_controller_kind == "tdk"
            and current_a > 0.0
            and not bool(getattr(self.manual_ch341_controller, "output_enabled", False))
        ):
            self.pause_automatic_test("TDK 电源输出未开启")
            return
        if not self.begin_power_supply_command("自动设置输出电流"):
            self.schedule_automatic_current_command(current_a, kind)
            return
        try:
            power_supply = self.get_power_supply()
            if power_supply is None:
                raise RuntimeError("电源未连接")
            power_supply.set_current(current_a)
        except Exception as exc:
            raw_error = str(exc).strip()
            display_error = self._error_formatter(exc)
            if raw_error and raw_error != display_error:
                display_error = f"{display_error}（原始错误：{raw_error}）"
            self.add_log(f"设置 {current_a:.1f} A 原始错误：{raw_error or type(exc).__name__}")
            if self._can_retry_legacy_current_write(current_a, kind):
                self.add_log(
                    f"设置 {current_a:.1f} A 首次失败，将在命令安全间隔后自动重试一次"
                )
                self.set_automatic_test_state(
                    self.automatic_test_state,
                    f"设置 {current_a:.1f} A 失败，正在自动重试",
                )
                self.schedule_automatic_current_command(current_a, kind)
                return
            self._clear_current_write_retry()
            if kind == "ramp_down" and self.power_supply_controller_kind == "tdk":
                self.add_log("TDK 分段降流失败，正在尝试直接关闭输出")
                try:
                    power_supply = self.get_power_supply()
                    if power_supply is None:
                        raise RuntimeError("TDK 电源未连接")
                    power_supply.set_output_enabled(False)
                except Exception as shutdown_exc:
                    shutdown_raw = str(shutdown_exc).strip()
                    shutdown_display = self._error_formatter(shutdown_exc)
                    if shutdown_raw and shutdown_raw != shutdown_display:
                        shutdown_display = (
                            f"{shutdown_display}（原始错误：{shutdown_raw}）"
                        )
                    self._mark_output_shutdown_unconfirmed()
                    self.pause_automatic_test(
                        f"分段下电失败：{display_error}；直接关闭 TDK 输出也失败：{shutdown_display}"
                    )
                    return
                self.add_log("分段降流失败，但已确认 TDK 输出直接关闭")
                fallback_reason = (
                    f"分段下电失败（原始错误：{raw_error or type(exc).__name__}），"
                    "已直接关闭 TDK 输出"
                )
                self._set_pending_terminal_outcome(
                    AutomaticTestTerminalOutcome.ABORTED_SAFELY,
                    fallback_reason,
                )
                self.complete_automatic_test(
                    tdk_output_already_disabled=True,
                )
                return
            self.pause_automatic_test(
                f"设置 {current_a:.1f} A 失败：{display_error}"
            )
            return

        self._clear_current_write_retry()
        if kind == "ramp_down":
            self.on_automatic_ramp_down_current_applied(current_a)
            return
        if kind == "ramp_up":
            self.active_output_current_a = current_a
            self.set_current_spin.setValue(current_a)
            if self.automatic_ramp_up_currents:
                self.set_automatic_test_state(
                    AutomaticTestState.SETTING_CURRENT,
                    f"安全升流：输出电流已设为 {current_a:.1f} A",
                )
                self.schedule_next_automatic_ramp_up_current()
                return

        self.cancel_auto_vout_read()
        self.set_current_spin.setValue(current_a)
        self.active_output_current_a = current_a
        self.pending_stable_point_current_a = current_a
        self.recorded_stable_point_current_a = None
        self.recorded_stable_point_generation = None
        if self.power_meter_reader is not None:
            self.pending_stable_point_generation = self.power_meter_reader.reset_stability_window()
        else:
            self.pending_stable_point_generation = None
        if self.automatic_uses_spectrometer():
            self.reset_wavelength_stability_window()
        stability_label = "功率及波长稳定" if self.automatic_uses_spectrometer() else "功率稳定"
        self.set_automatic_test_state(
            AutomaticTestState.WAITING_STABLE,
            f"等待 {current_a:.1f} A {stability_label}",
        )
        if self.automatic_test_settings is not None:
            self.automatic_point_timer.start(round(self.automatic_test_settings.point_timeout_s * 1000.0))
        self.add_log(
            f"自动测试 {self.automatic_test_current_index + 1}/{len(self.automatic_test_currents)}："
            f"输出电流已设为 {current_a:.1f} A"
        )

    def set_automatic_test_state(self, state: AutomaticTestState, detail: str = "") -> None:
        self.automatic_orchestrator.set_state(state)
        active = state not in (AutomaticTestState.IDLE, AutomaticTestState.COMPLETED)
        paused = state == AutomaticTestState.PAUSED
        self.start_automatic_test_button.setEnabled(not active)
        self.retry_automatic_test_button.setEnabled(paused)
        self.end_automatic_test_button.setEnabled(active and state != AutomaticTestState.RAMPING_DOWN)
        for widget in (
            self.auto_initial_current_spin,
            self.auto_target_current_spin,
            self.auto_current_step_spin,
            self.auto_point_timeout_spin,
            self.auto_ramp_down_step_spin,
            self.auto_ramp_down_interval_spin,
            self.auto_pause_ramp_down_timeout_spin,
            self.auto_use_spectrometer_check,
            self.sn_field,
            self.product_model_field,
            self.batch_field,
            self.output_dir_field,
            self.browse_button,
            self.apply_current_button,
            self.set_current_spin,
            self.read_input_voltage_button,
            self.read_output_voltage_button,
            self.read_output_current_button,
            self.read_temperature_button,
            self.stable_window_spin,
            self.power_supply_controller_combo,
            self.tdk_resource_combo,
            self.refresh_tdk_resources_button,
            self.tdk_voltage_spin,
            self.apply_tdk_voltage_button,
            self.tdk_output_button,
        ):
            widget.setEnabled(not active)
        for name in ("shipping_report_button", "generate_result_report_button"):
            widget = getattr(self._host, name, None)
            if widget is not None:
                widget.setEnabled(not active)
        if self._selected_power_supply_kind() != "tdk":
            for widget in (
                self.tdk_resource_combo,
                self.refresh_tdk_resources_button,
                self.tdk_voltage_spin,
                self.apply_tdk_voltage_button,
                self.tdk_output_button,
            ):
                widget.setEnabled(False)
        legacy_power_controller = self._selected_power_supply_kind() != "tdk"
        self.read_input_voltage_button.setEnabled(not active and legacy_power_controller)
        self.read_temperature_button.setEnabled(not active and legacy_power_controller)
        self.connect_i2c_button.setEnabled(not active or (paused and not self._manual_i2c_connected()))
        if detail:
            if (
                state
                in (
                    AutomaticTestState.SETTING_CURRENT,
                    AutomaticTestState.WAITING_STABLE,
                    AutomaticTestState.WAITING_VOLTAGE,
                    AutomaticTestState.SAVING_POINT,
                    AutomaticTestState.PAUSED,
                )
                and 0 <= self.automatic_test_current_index < len(self.automatic_test_currents)
            ):
                current_a = self.automatic_test_currents[self.automatic_test_current_index]
                detail = (
                    f"{self.automatic_test_current_index + 1}/{len(self.automatic_test_currents)} · "
                    f"{current_a:.1f} A · {detail}"
                )
            self.automatic_test_status_label.setText(detail)
        state_ui_handler = getattr(self._host, "on_automatic_state_ui_changed", None)
        if callable(state_ui_handler):
            state_ui_handler(state, detail)
        self.update_global_status()

    def pause_automatic_test(
        self,
        reason: str,
        *,
        operator_requested: bool = False,
    ) -> None:
        if operator_requested and self.automatic_test_state == AutomaticTestState.SAVING_POINT:
            message = "当前测试点正在保存，保存完成后再暂停"
            self.automatic_test_status_label.setText(message)
            self.statusBar().showMessage(message)
            self.add_log(f"已忽略暂停请求：{message}")
            return
        object.__setattr__(self, "_pause_was_operator_requested", bool(operator_requested))
        self.automatic_orchestrator.pause(reason)
        self.automatic_device_start_timer.stop()
        self.automatic_point_timer.stop()
        self.automatic_command_timer.stop()
        self.automatic_ramp_down_timer.stop()
        self.automatic_pause_safety_timer.stop()
        self.automatic_ramp_up_currents.clear()
        self._clear_current_write_retry()
        self.pending_automatic_current_a = None
        self.pending_automatic_command_kind = None
        self.cancel_auto_vout_read()
        self.set_automatic_test_state(AutomaticTestState.PAUSED, f"已暂停：{reason}")
        self.statusBar().showMessage(f"自动测试已暂停：{reason}")
        self.add_log(f"自动测试已暂停并保持当前电流：{reason}")
        settings = self.automatic_test_settings
        if settings is not None and settings.pause_ramp_down_timeout_s > 0.0:
            self.automatic_pause_safety_timer.start(
                max(1, math.ceil(settings.pause_ramp_down_timeout_s * 1000.0))
            )

    def on_automatic_pause_safety_timeout(self) -> None:
        if self.automatic_test_state != AutomaticTestState.PAUSED:
            return
        self.add_log("暂停等待超时，开始自动安全下电")
        if self._pause_was_operator_requested:
            terminal_outcome = AutomaticTestTerminalOutcome.STOPPED_BY_OPERATOR
            terminal_reason = "操作者暂停后等待超时"
        else:
            terminal_outcome = AutomaticTestTerminalOutcome.ABORTED_SAFELY
            terminal_reason = self.automatic_pause_reason or "异常暂停后等待超时"
        self.begin_automatic_ramp_down(
            terminal_outcome=terminal_outcome,
            terminal_reason=terminal_reason,
        )

    def automatic_measurement_is_active(self) -> bool:
        return self.automatic_test_state in (
            AutomaticTestState.STARTING,
            AutomaticTestState.SETTING_CURRENT,
            AutomaticTestState.WAITING_STABLE,
            AutomaticTestState.WAITING_VOLTAGE,
            AutomaticTestState.SAVING_POINT,
        )

    def retry_automatic_test(self) -> None:
        if self.automatic_test_state != AutomaticTestState.PAUSED:
            return
        paused_from_state = self.automatic_paused_from_state
        pause_reason = self.automatic_pause_reason
        if paused_from_state == AutomaticTestState.SAVING_POINT:
            if self.excel_save_thread is not None:
                self.automatic_test_status_label.setText("请等待当前 Excel 保存线程结束后再重试")
                return
            self.automatic_pause_safety_timer.stop()
            self.automatic_pause_reason = ""
            object.__setattr__(self, "_pause_was_operator_requested", False)
            if self._save_completed_while_paused:
                object.__setattr__(self, "_save_completed_while_paused", False)
                expected_current = self.automatic_test_currents[self.automatic_test_current_index]
                if self.automatic_orchestrator.advance():
                    self.set_automatic_test_state(
                        AutomaticTestState.STARTING,
                        f"{expected_current:.1f} A 已保存，正在恢复下一测试点",
                    )
                else:
                    self.automatic_completion_record = self.record_store.pending_records.get(
                        expected_current
                    )
                    self.begin_automatic_ramp_down(
                        terminal_outcome=AutomaticTestTerminalOutcome.ABORTED_SAFELY,
                        terminal_reason=pause_reason or "保存期间发生异常",
                    )
                    return
            else:
                self.set_automatic_test_state(
                    AutomaticTestState.SAVING_POINT,
                    "正在重试保存当前测试点",
                )
                self.save_pending_excel_records()
                return
        else:
            self.automatic_pause_safety_timer.stop()
            self.automatic_pause_reason = ""
            object.__setattr__(self, "_pause_was_operator_requested", False)
        if paused_from_state == AutomaticTestState.RAMPING_DOWN:
            self.begin_automatic_ramp_down()
            return
        if not self._manual_i2c_connected():
            label = "TDK" if self._selected_power_supply_kind() == "tdk" else "CH341"
            self.pause_automatic_test(f"{label} 未连接")
            return
        if (
            self.power_supply_controller_kind == "tdk"
            and not bool(getattr(self.manual_ch341_controller, "output_enabled", False))
        ):
            self.pause_automatic_test("TDK 电源输出未开启")
            return
        self.automatic_power_meter_ready = bool(
            self.power_meter_reader is not None and getattr(self.power_meter_reader, "is_ready", False)
        )
        use_spectrometer = self.automatic_uses_spectrometer()
        self.automatic_spectrometer_ready = bool(
            not use_spectrometer
            or (self.spectrometer_reader is not None and getattr(self.spectrometer_reader, "is_ready", False))
        )
        if not (self.automatic_power_meter_ready and self.automatic_spectrometer_ready):
            self.set_automatic_test_state(AutomaticTestState.STARTING, "正在重新启动并确认采集设备")
            if self.power_meter_reader is None:
                self.start_power_meter()
            if use_spectrometer and self.spectrometer_reader is None:
                self.start_spectrometer()
            self.pending_stable_point_current_a = None
            self.pending_stable_point_generation = None
            self.automatic_device_start_timer.start(round(AUTOMATIC_DEVICE_START_TIMEOUT_S * 1000.0))
            self.maybe_start_automatic_current_sequence()
            return
        self.begin_automatic_current_point()

    def end_automatic_test(self) -> None:
        if self.automatic_test_state in (AutomaticTestState.IDLE, AutomaticTestState.COMPLETED):
            return
        if self.automatic_test_state == AutomaticTestState.RAMPING_DOWN:
            return
        self.statusBar().showMessage("已收到结束指令，正在安全下电")
        self.add_log("操作者请求结束自动测试，开始安全下电")
        if self.automatic_test_state == AutomaticTestState.PAUSED and not self._pause_was_operator_requested:
            terminal_outcome = AutomaticTestTerminalOutcome.ABORTED_SAFELY
            terminal_reason = self.automatic_pause_reason or "异常暂停后由操作者结束"
        else:
            terminal_outcome = AutomaticTestTerminalOutcome.STOPPED_BY_OPERATOR
            terminal_reason = "操作者提前结束测试"
        self.begin_automatic_ramp_down(
            terminal_outcome=terminal_outcome,
            terminal_reason=terminal_reason,
        )

    def on_automatic_device_start_timeout(self) -> None:
        if self.automatic_test_state == AutomaticTestState.STARTING:
            self.pause_automatic_test("采集设备在 15 秒内未全部就绪")

    def on_automatic_point_timeout(self) -> None:
        if self.automatic_test_state in (AutomaticTestState.WAITING_STABLE, AutomaticTestState.WAITING_VOLTAGE):
            current_a = self.active_output_current_a or 0.0
            recorder = getattr(self.record_store, "record_invalid_attempt", None)
            if callable(recorder):
                try:
                    recorder(current_a, AttemptValidity.TIMEOUT, f"{current_a:.1f} A 单点测试超时")
                except Exception as exc:
                    self.add_log(f"超时尝试归档失败：{self._error_formatter(exc)}")
            self.pause_automatic_test(f"{current_a:.1f} A 单点测试超时")

    def on_automatic_ramp_down_current_applied(self, current_a: float) -> None:
        self.active_output_current_a = current_a
        self.set_current_spin.setValue(current_a)
        self.set_automatic_test_state(
            AutomaticTestState.RAMPING_DOWN,
            f"分段下电：输出电流已设为 {current_a:.1f} A",
        )
        self.add_log(f"自动下电：输出电流已设为 {current_a:.1f} A")
        if current_a <= 0.0:
            self.complete_automatic_test()
            return
        settings = self.automatic_test_settings
        if settings is None:
            self.pause_automatic_test("自动下电参数不可用")
            return
        self.automatic_ramp_down_timer.start(max(1, math.ceil(settings.ramp_down_interval_s * 1000.0)))

    def begin_automatic_ramp_down(
        self,
        *,
        terminal_outcome: AutomaticTestTerminalOutcome | None = None,
        terminal_reason: str = "",
    ) -> None:
        if terminal_outcome is not None:
            self._set_pending_terminal_outcome(terminal_outcome, terminal_reason)
        elif self._pending_terminal_outcome is None and self.terminal_outcome is None:
            if self.automatic_test_state == AutomaticTestState.PAUSED and not self._pause_was_operator_requested:
                self._set_pending_terminal_outcome(
                    AutomaticTestTerminalOutcome.ABORTED_SAFELY,
                    self.automatic_pause_reason or "异常暂停后安全下电",
                )
            else:
                self._set_pending_terminal_outcome(
                    AutomaticTestTerminalOutcome.STOPPED_BY_OPERATOR,
                    "操作者提前结束测试",
                )
        settings = self.automatic_test_settings
        if settings is None:
            try:
                settings = self.collect_automatic_test_settings()
            except ValueError as exc:
                self.pause_automatic_test(self._error_formatter(exc))
                return
            self.automatic_test_settings = settings
        self.automatic_device_start_timer.stop()
        self.automatic_point_timer.stop()
        self.automatic_command_timer.stop()
        self.automatic_ramp_down_timer.stop()
        self.automatic_pause_safety_timer.stop()
        self.automatic_ramp_up_currents.clear()
        self.cancel_auto_vout_read()
        start_current_a = max(0.0, float(self.active_output_current_a or 0.0))
        try:
            self.automatic_ramp_down_currents = deque(self.automatic_orchestrator.begin_ramp_down(start_current_a))
        except ValueError as exc:
            self.pause_automatic_test(self._error_formatter(exc))
            return
        self.set_automatic_test_state(AutomaticTestState.RAMPING_DOWN, "正在分段下电")
        self.schedule_next_automatic_ramp_down_current()

    def schedule_next_automatic_ramp_down_current(self) -> None:
        if self.automatic_test_state != AutomaticTestState.RAMPING_DOWN:
            return
        if not self.automatic_ramp_down_currents:
            self.complete_automatic_test()
            return
        current_a = self.automatic_ramp_down_currents.popleft()
        self.schedule_automatic_current_command(current_a, "ramp_down")

    def complete_automatic_test(
        self,
        *,
        tdk_output_already_disabled: bool = False,
        completion_detail: str = "",
    ) -> None:
        self.automatic_device_start_timer.stop()
        self.automatic_point_timer.stop()
        self.automatic_command_timer.stop()
        self.automatic_ramp_down_timer.stop()
        self.automatic_pause_safety_timer.stop()
        self.pending_automatic_current_a = None
        self.pending_automatic_command_kind = None
        self._clear_current_write_retry()
        if self._pending_terminal_outcome is None and self.terminal_outcome is None:
            self._set_pending_terminal_outcome(
                AutomaticTestTerminalOutcome.SUCCEEDED,
                "所有计划测试点均已保存",
            )
        if (
            self.power_supply_controller_kind == "tdk"
            and not tdk_output_already_disabled
        ):
            try:
                power_supply = self.get_power_supply()
                if power_supply is None:
                    raise RuntimeError("TDK 电源未连接")
                power_supply.set_output_enabled(False)
            except Exception as exc:
                self._mark_output_shutdown_unconfirmed()
                self.automatic_paused_from_state = AutomaticTestState.RAMPING_DOWN
                self.pause_automatic_test(f"TDK 输出关闭失败：{self._error_formatter(exc)}")
                return
            self._mark_output_shutdown_confirmed()
        elif self.power_supply_controller_kind == "tdk":
            self._mark_output_shutdown_confirmed()
        if self.power_supply_controller_kind == "tdk":
            self.sync_tdk_output_controls(False)
        self.active_output_current_a = 0.0
        self.set_current_spin.setValue(0.0)
        self._confirm_terminal_outcome()
        self.automatic_orchestrator.complete()
        completed_message = completion_detail or self._completion_message()
        session_status = {
            AutomaticTestTerminalOutcome.SUCCEEDED: SessionStatus.COMPLETED,
            AutomaticTestTerminalOutcome.STOPPED_BY_OPERATOR: SessionStatus.STOPPED_BY_OPERATOR,
            AutomaticTestTerminalOutcome.ABORTED_SAFELY: SessionStatus.ABORTED_SAFELY,
        }.get(self.terminal_outcome, SessionStatus.ABORTED_SAFELY)
        complete_session = getattr(self.record_store, "complete_session", None)
        if callable(complete_session):
            try:
                complete_session(
                    session_status,
                    self.terminal_reason or completed_message,
                    shutdown_confirmed=not self.output_shutdown_unconfirmed,
                )
            except Exception as exc:
                self.add_log(f"测试结束状态更新失败：{self._error_formatter(exc)}")
        completion_record = self.automatic_completion_record
        self.automatic_completion_record = None
        finalizer = getattr(self._host, "finalize_automatic_session_workbook", None)
        if callable(finalizer) and finalizer(completion_record, completed_message):
            self.set_automatic_test_state(AutomaticTestState.COMPLETED, "正在写入最终测试状态")
            self.statusBar().showMessage("正在写入 Excel 最终测试状态")
            self.add_log("正在后台写入 Excel 最终测试状态")
            return
        self.finish_automatic_test_presentation(completion_record, completed_message)

    def finish_automatic_test_presentation(
        self,
        completion_record: Any | None,
        completed_message: str,
    ) -> None:
        """Present the terminal state after final workbook metadata is durable."""
        self.set_automatic_test_state(AutomaticTestState.COMPLETED, completed_message)
        self.statusBar().showMessage(completed_message)
        self.add_log(completed_message)
        # Keep measurement devices in their current state after a normal test.
        # An optional spectrometer that was never started therefore stays off,
        # while devices used by the test remain available for the next run.
        if not self.close_after_automatic_ramp_down:
            result_handler = getattr(self._host, "show_automatic_result", None)
            if callable(result_handler):
                result_handler(completion_record, completed_message)
        if self.close_after_automatic_ramp_down:
            self.close_after_automatic_ramp_down = False
            QTimer.singleShot(0, self.close)

    def reset_automatic_test(self) -> None:
        """Return a completed workflow to preparation without bypassing lifecycle ownership."""
        if self.automatic_test_state not in (AutomaticTestState.IDLE, AutomaticTestState.COMPLETED):
            return
        self.automatic_test_settings = None
        self.automatic_test_currents = ()
        self.automatic_test_current_index = -1
        self.automatic_completion_record = None
        self.automatic_run_started_monotonic_s = None
        self._clear_current_write_retry()
        self._clear_terminal_outcome()
        self._power_drop_protection.reset()
        self.set_automatic_test_state(AutomaticTestState.IDLE, "准备测试")
