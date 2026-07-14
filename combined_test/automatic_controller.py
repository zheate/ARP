"""Automatic test lifecycle controller kept outside the main window module.

The controller coordinates the device ports and record store while delegating
operator-facing UI effects to its host.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any, Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from .automation import (
    AutomaticTestSettings,
    AutomaticTestState,
    build_ramp_up_currents,
    validate_automatic_test_settings,
)
from .device_interfaces import PowerMeter, PowerSupply, SpectrumMeter
from .record_store import RecordStore

AUTO_VOUT_AFTER_STABLE_S = 5.0
AUTOMATIC_DEVICE_START_TIMEOUT_S = 15.0

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

    def on_voltage_record_ready(self, current_a: float, queued: bool, error: str) -> None:
        if self.automatic_test_state != AutomaticTestState.WAITING_VOLTAGE:
            return
        if not queued:
            self.pause_automatic_test(error or "当前测试点未能生成有效记录")
            return
        self.automatic_point_timer.stop()
        self.set_automatic_test_state(
            AutomaticTestState.SAVING_POINT,
            f"正在保存 {current_a:.1f} A 测试点",
        )
        self.save_pending_excel_records()

    def on_record_saved(self) -> None:
        if self.automatic_test_state != AutomaticTestState.SAVING_POINT:
            return
        expected_current = self.automatic_test_currents[self.automatic_test_current_index]
        if expected_current not in self.record_store.recorded_currents:
            self.pause_automatic_test(f"{expected_current:.1f} A 测试点未确认写入 Excel")
        elif self.automatic_orchestrator.advance():
            self.begin_automatic_current_point()
        else:
            self.automatic_completion_record = self.record_store.pending_records.get(expected_current)
            self.begin_automatic_ramp_down()

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
            self.pause_automatic_test(f"{device_label}错误：{self._error_formatter(message)}")

    def on_acquisition_stopped(self, device_label: str) -> None:
        if device_label == "光谱仪" and not self.automatic_uses_spectrometer():
            return
        if self.automatic_measurement_is_active():
            self.pause_automatic_test(f"{device_label}采集已停止")

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
            self.begin_test_session()
        except ValueError as exc:
            QMessageBox.warning(self._host, "自动测试", self._error_formatter(exc))
            return

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
                    self.pause_automatic_test(
                        f"分段下电失败：{display_error}；直接关闭 TDK 输出也失败：{shutdown_display}"
                    )
                    return
                self.add_log("分段降流失败，但已确认 TDK 输出直接关闭")
                self.complete_automatic_test(
                    tdk_output_already_disabled=True,
                    completion_detail=(
                        f"分段下电失败（原始错误：{raw_error or type(exc).__name__}），"
                        "已直接关闭 TDK 输出"
                    ),
                )
                return
            self.pause_automatic_test(
                f"设置 {current_a:.1f} A 失败：{display_error}"
            )
            return

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
            self.output_dir_field,
            self.browse_button,
            self.apply_current_button,
            self.set_current_spin,
            self.read_input_voltage_button,
            self.read_output_voltage_button,
            self.read_output_current_button,
            self.read_temperature_button,
            self.stable_window_spin,
            self.start_all_button,
            self.power_supply_controller_combo,
            self.tdk_resource_combo,
            self.refresh_tdk_resources_button,
            self.tdk_voltage_spin,
            self.apply_tdk_voltage_button,
            self.tdk_output_button,
        ):
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
        self.update_global_status()

    def pause_automatic_test(self, reason: str) -> None:
        self.automatic_orchestrator.pause(reason)
        self.automatic_device_start_timer.stop()
        self.automatic_point_timer.stop()
        self.automatic_command_timer.stop()
        self.automatic_ramp_down_timer.stop()
        self.automatic_pause_safety_timer.stop()
        self.automatic_ramp_up_currents.clear()
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
        self.begin_automatic_ramp_down()

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
        self.automatic_pause_safety_timer.stop()
        self.automatic_pause_reason = ""
        if self.automatic_paused_from_state == AutomaticTestState.SAVING_POINT:
            if self.excel_save_thread is not None:
                self.automatic_test_status_label.setText("请等待当前 Excel 保存线程结束后再重试")
                return
            self.set_automatic_test_state(AutomaticTestState.SAVING_POINT, "正在重试保存当前测试点")
            self.save_pending_excel_records()
            return
        if self.automatic_paused_from_state == AutomaticTestState.RAMPING_DOWN:
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
        self.statusBar().showMessage("已收到结束指令，正在安全下电")
        self.add_log("操作者请求结束自动测试，开始安全下电")
        self.begin_automatic_ramp_down()

    def on_automatic_device_start_timeout(self) -> None:
        if self.automatic_test_state == AutomaticTestState.STARTING:
            self.pause_automatic_test("采集设备在 15 秒内未全部就绪")

    def on_automatic_point_timeout(self) -> None:
        if self.automatic_test_state in (AutomaticTestState.WAITING_STABLE, AutomaticTestState.WAITING_VOLTAGE):
            current_a = self.active_output_current_a or 0.0
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

    def begin_automatic_ramp_down(self) -> None:
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
        if (
            self.power_supply_controller_kind == "tdk"
            and self.manual_ch341_controller is not None
            and not tdk_output_already_disabled
        ):
            try:
                power_supply = self.get_power_supply()
                if power_supply is None:
                    raise RuntimeError("TDK 电源未连接")
                power_supply.set_output_enabled(False)
            except Exception as exc:
                self.automatic_paused_from_state = AutomaticTestState.RAMPING_DOWN
                self.pause_automatic_test(f"TDK 输出关闭失败：{self._error_formatter(exc)}")
                return
        self.active_output_current_a = 0.0
        self.set_current_spin.setValue(0.0)
        self.automatic_orchestrator.complete()
        completed_message = completion_detail or "自动测试完成，输出电流已降至 0 A"
        self.set_automatic_test_state(AutomaticTestState.COMPLETED, completed_message)
        self.statusBar().showMessage(completed_message)
        self.add_log(completed_message)
        self.stop_power_meter()
        self.stop_spectrometer()
        completion_record = self.automatic_completion_record
        self.automatic_completion_record = None
        if completion_record is not None and not self.close_after_automatic_ramp_down:
            summary_lines = [
                "测试完成",
                "",
                f"目标电流：{completion_record.current_a:.1f} A",
                f"功率：{completion_record.power_w:.3f} W",
                f"效率：{completion_record.efficiency * 100.0:.2f} %",
            ]
            if self.automatic_uses_spectrometer():
                summary_lines.extend(
                    (
                        f"中心波长：{completion_record.peak_wavelength_nm:.3f} nm",
                        f"FWHM：{completion_record.fwhm_nm:.3f} nm",
                        f"PIB：{completion_record.pib * 100.0:.2f} %",
                    )
                )
            QMessageBox.information(
                self._host,
                "自动测试完成",
                "\n".join(summary_lines),
            )
        if self.close_after_automatic_ramp_down:
            self.close_after_automatic_ramp_down = False
            QTimer.singleShot(0, self.close)
