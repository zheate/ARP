from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from combined_test.automatic_controller import AutomaticTestTerminalOutcome
from combined_test.automation import AutomaticTestState
from combined_test.models import PowerMeterReading
from combined_test.window import MainWindow


class AutomaticTestControllerLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        settings_path = Path(self.temp_dir.name) / "controller.ini"
        self.window = MainWindow(QSettings(str(settings_path), QSettings.Format.IniFormat))
        self.power_protection_alerts: list[str] = []
        self.window.show_power_protection_alert = (  # type: ignore[method-assign]
            lambda reason: self.power_protection_alerts.append(reason)
        )

    def tearDown(self) -> None:
        self.window.automatic_pause_safety_timer.stop()
        self.window.automatic_test_state = AutomaticTestState.IDLE
        self.window.close()
        self.temp_dir.cleanup()

    def _prepare_zero_current_terminal_path(self, state: AutomaticTestState) -> list[str]:
        self.window.automatic_test_settings = self.window.collect_automatic_test_settings()
        self.window.automatic_test_currents = (8.0,)
        self.window.automatic_test_current_index = 0
        self.window.active_output_current_a = 0.0
        self.window.automatic_test_state = state
        result_details: list[str] = []
        self.window.show_automatic_result = (  # type: ignore[method-assign]
            lambda _record, detail: result_details.append(detail)
        )
        return result_details

    def _retry_without_modal(self) -> None:
        original_information = QMessageBox.information
        QMessageBox.information = (  # type: ignore[method-assign]
            lambda *_args, **_kwargs: QMessageBox.StandardButton.Ok
        )
        try:
            self.window.retry_automatic_test()
        finally:
            QMessageBox.information = original_information  # type: ignore[method-assign]

    def test_operator_pause_during_save_is_rejected_and_save_can_complete(self) -> None:
        result_details = self._prepare_zero_current_terminal_path(AutomaticTestState.SAVING_POINT)
        self.window.record_store.recorded_currents.add(8.0)

        self.window.pause_automatic_test("操作者暂停", operator_requested=True)

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.SAVING_POINT)
        self.assertFalse(self.window.automatic_pause_safety_timer.isActive())
        self.assertIn("保存", self.window.statusBar().currentMessage())

        self.window.automatic_controller.on_record_saved()

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.COMPLETED)
        self.assertEqual(
            self.window.automatic_controller.terminal_outcome,
            AutomaticTestTerminalOutcome.SUCCEEDED,
        )
        self.assertIn("完整完成", result_details[-1])

    def test_save_failure_can_still_pause_for_retry(self) -> None:
        self._prepare_zero_current_terminal_path(AutomaticTestState.SAVING_POINT)

        self.window.automatic_controller.on_record_save_failed("文件被占用")

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertEqual(
            self.window.automatic_paused_from_state,
            AutomaticTestState.SAVING_POINT,
        )
        self.assertTrue(self.window.automatic_pause_safety_timer.isActive())
        self.assertIn("Excel 保存失败", self.window.automatic_pause_reason)

    def test_acquisition_fault_during_save_retries_from_the_next_point_after_save(self) -> None:
        self._prepare_zero_current_terminal_path(AutomaticTestState.SAVING_POINT)
        self.window.automatic_test_currents = (8.0, 10.0)
        self.window.record_store.recorded_currents.add(8.0)

        self.window.automatic_controller.on_acquisition_failed("功率计", "通信中断")

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.PAUSED)

        self.window.automatic_controller.on_record_saved()

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertEqual(self.window.automatic_test_current_index, 0)
        self.assertIn("已成功保存", self.window.statusBar().currentMessage())
        self.assertIn("功率计错误", self.window.automatic_pause_reason)
        self.assertIsNone(self.window.automatic_controller.terminal_outcome)

        self._retry_without_modal()

        self.assertEqual(self.window.automatic_test_current_index, 1)
        self.assertNotEqual(self.window.automatic_test_state, AutomaticTestState.SAVING_POINT)
        self.assertIn("未连接", self.window.automatic_pause_reason)

    def test_acquisition_fault_after_final_save_aborts_safely(self) -> None:
        result_details = self._prepare_zero_current_terminal_path(AutomaticTestState.SAVING_POINT)
        self.window.record_store.recorded_currents.add(8.0)

        self.window.automatic_controller.on_acquisition_failed("功率计", "通信中断")
        self.window.automatic_controller.on_record_saved()
        self._retry_without_modal()

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.COMPLETED)
        self.assertEqual(
            self.window.automatic_controller.terminal_outcome,
            AutomaticTestTerminalOutcome.ABORTED_SAFELY,
        )
        self.assertIn("功率计错误", self.window.automatic_controller.terminal_reason)
        self.assertIn("异常中止", result_details[-1])

    def test_operator_end_reports_stopped_by_operator_after_safe_ramp_down(self) -> None:
        result_details = self._prepare_zero_current_terminal_path(AutomaticTestState.WAITING_STABLE)

        self.window.end_automatic_test()

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.COMPLETED)
        self.assertEqual(
            self.window.automatic_controller.terminal_outcome,
            AutomaticTestTerminalOutcome.STOPPED_BY_OPERATOR,
        )
        self.assertIn("操作者提前结束", self.window.automatic_controller.terminal_reason)
        self.assertIn("安全下电", result_details[-1])

    def test_power_drop_over_five_seconds_trips_legacy_supply(self) -> None:
        class ConnectedLegacyController:
            is_connected = True

            def __init__(self) -> None:
                self.writes: list[list[int]] = []

            def i2c_write(self, _address: int, command: list[int]) -> tuple[bool, str]:
                self.writes.append(command)
                return True, "OK"

        result_details = self._prepare_zero_current_terminal_path(
            AutomaticTestState.SETTING_CURRENT
        )
        controller = ConnectedLegacyController()
        self.window.manual_ch341_controller = controller
        self.window.active_output_current_a = 8.0
        self.window.set_current_spin.setValue(8.0)

        self.window.on_power_meter_reading(
            PowerMeterReading(1.0, 100.0, False, 5.0, 0.1)
        )
        self.window.on_power_meter_reading(
            PowerMeterReading(6.0, 69.0, False, 31.0, 5.0)
        )

        self.assertEqual(controller.writes, [[0xB4, 0xFF, 0x00, 0x00]])
        self.assertEqual(self.window.active_output_current_a, 0.0)
        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.COMPLETED)
        self.assertEqual(
            self.window.automatic_controller.terminal_outcome,
            AutomaticTestTerminalOutcome.ABORTED_SAFELY,
        )
        self.assertIn("5 秒前 100.000 W", self.window.automatic_controller.terminal_reason)
        self.assertIn("功率保护已触发", result_details[-1])
        self.assertEqual(len(self.power_protection_alerts), 1)
        self.assertIn("当前 69.000 W", self.power_protection_alerts[0])
        self.window.manual_ch341_controller = None

    def test_power_drop_immediately_sets_zero_and_disables_tdk_output(self) -> None:
        class ConnectedTdkController:
            is_connected = True
            output_enabled = True

            def __init__(self) -> None:
                self.commands: list[str] = []

            def set_output_current(self, current_a: float) -> None:
                self.commands.append(f"current:{current_a:g}")

            def set_output_enabled(self, enabled: bool) -> None:
                self.commands.append(f"output:{int(enabled)}")
                self.output_enabled = enabled

        self._prepare_zero_current_terminal_path(AutomaticTestState.WAITING_VOLTAGE)
        controller = ConnectedTdkController()
        self.window.manual_ch341_controller = controller
        self.window.power_supply_controller_kind = "tdk"
        self.window.active_output_current_a = 5.0
        self.window.set_current_spin.setValue(5.0)

        self.window.on_power_meter_reading(
            PowerMeterReading(1.0, 50.0, False, 5.0, 0.1)
        )
        self.window.on_power_meter_reading(
            PowerMeterReading(6.0, 34.0, False, 16.0, 5.0)
        )

        self.assertEqual(controller.commands, ["current:0", "output:0"])
        self.assertFalse(controller.output_enabled)
        self.assertEqual(self.window.active_output_current_a, 0.0)
        self.assertEqual(
            self.window.automatic_controller.terminal_outcome,
            AutomaticTestTerminalOutcome.ABORTED_SAFELY,
        )
        self.assertEqual(len(self.power_protection_alerts), 1)
        self.assertIn("当前 34.000 W", self.power_protection_alerts[0])
        self.window.manual_ch341_controller = None

    def test_power_drop_protection_is_inactive_during_normal_ramp_down(self) -> None:
        class ConnectedLegacyController:
            is_connected = True

            def __init__(self) -> None:
                self.writes: list[list[int]] = []

            def i2c_write(self, _address: int, command: list[int]) -> tuple[bool, str]:
                self.writes.append(command)
                return True, "OK"

        controller = ConnectedLegacyController()
        self.window.manual_ch341_controller = controller
        self.window.active_output_current_a = 8.0
        self.window.automatic_test_state = AutomaticTestState.WAITING_STABLE
        self.assertFalse(
            self.window.automatic_controller.on_automatic_power_sample(100.0)
        )

        self.window.automatic_test_state = AutomaticTestState.RAMPING_DOWN
        self.assertFalse(
            self.window.automatic_controller.on_automatic_power_sample(0.0)
        )

        self.assertEqual(controller.writes, [])
        self.assertEqual(self.window.active_output_current_a, 8.0)
        self.window.manual_ch341_controller = None

    def test_fault_pause_timeout_reports_aborted_safely(self) -> None:
        result_details = self._prepare_zero_current_terminal_path(AutomaticTestState.WAITING_STABLE)

        self.window.pause_automatic_test("功率计通信中断")
        self.window.automatic_pause_safety_timer.stop()
        self.window.on_automatic_pause_safety_timeout()

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.COMPLETED)
        self.assertEqual(
            self.window.automatic_controller.terminal_outcome,
            AutomaticTestTerminalOutcome.ABORTED_SAFELY,
        )
        self.assertIn("功率计通信中断", self.window.automatic_controller.terminal_reason)
        self.assertIn("异常中止", result_details[-1])
        self.assertIn("安全下电", result_details[-1])

    def test_terminal_outcome_is_hidden_while_ramp_down_is_in_progress(self) -> None:
        class ConnectedLegacyController:
            is_connected = True

            def i2c_write(self, _address: int, _command: list[int]) -> tuple[bool, str]:
                return True, "OK"

        result_details = self._prepare_zero_current_terminal_path(
            AutomaticTestState.WAITING_STABLE
        )
        self.window.manual_ch341_controller = ConnectedLegacyController()
        self.window.active_output_current_a = 8.0
        self.window.last_power_supply_command_monotonic_s = time.monotonic() - 1.0

        self.window.end_automatic_test()

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.RAMPING_DOWN)
        self.assertIsNone(self.window.automatic_controller.terminal_outcome)
        self.assertEqual(self.window.automatic_controller.terminal_reason, "")
        self.assertEqual(result_details, [])
        self.window.automatic_ramp_down_timer.stop()

    def test_two_amp_single_point_retries_transient_second_i2c_write(self) -> None:
        class FlakyLegacyController:
            is_connected = True

            def __init__(self) -> None:
                self.writes: list[list[int]] = []
                self.two_amp_attempts = 0

            def i2c_write(self, _address: int, command: list[int]) -> tuple[bool, str]:
                self.writes.append(command)
                if command == [0xB4, 0xFF, 0x02, 0x00]:
                    self.two_amp_attempts += 1
                    if self.two_amp_attempts == 1:
                        return False, "写入失败"
                return True, "OK"

        class ReaderStub:
            def reset_stability_window(self) -> int:
                return 1

        controller = FlakyLegacyController()
        self.window.manual_ch341_controller = controller
        self.window.power_meter_reader = ReaderStub()  # type: ignore[assignment]
        self.window.automatic_test_settings = self.window.collect_automatic_test_settings()
        self.window.automatic_test_currents = (2.0,)
        self.window.automatic_test_current_index = 0

        self.window.begin_automatic_current_point()

        self.assertEqual(controller.writes, [[0xB4, 0xFF, 0x01, 0x00]])
        self.window.automatic_command_timer.stop()
        self.window.last_power_supply_command_monotonic_s = time.monotonic() - 2.0
        self.window.on_automatic_command_timer_timeout()

        self.assertEqual(
            controller.writes,
            [[0xB4, 0xFF, 0x01, 0x00], [0xB4, 0xFF, 0x02, 0x00]],
        )
        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.SETTING_CURRENT)
        self.assertTrue(self.window.automatic_command_timer.isActive())

        self.window.automatic_command_timer.stop()
        self.window.last_power_supply_command_monotonic_s = time.monotonic() - 2.0
        self.window.on_automatic_command_timer_timeout()

        self.assertEqual(
            controller.writes,
            [
                [0xB4, 0xFF, 0x01, 0x00],
                [0xB4, 0xFF, 0x02, 0x00],
                [0xB4, 0xFF, 0x02, 0x00],
            ],
        )
        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.WAITING_STABLE)
        self.window.power_meter_reader = None

    def test_missing_tdk_controller_never_reports_safe_shutdown(self) -> None:
        result_details = self._prepare_zero_current_terminal_path(AutomaticTestState.RAMPING_DOWN)
        self.window.power_supply_controller_kind = "tdk"
        self.window.manual_ch341_controller = None
        self.window.active_output_current_a = 4.0
        self.window.set_current_spin.setValue(4.0)

        self.window.complete_automatic_test()
        self.window.manual_ch341_controller = None

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertIsNone(self.window.automatic_controller.terminal_outcome)
        self.assertEqual(self.window.automatic_controller.terminal_reason, "")
        self.assertTrue(self.window.automatic_controller.output_shutdown_unconfirmed)
        self.assertEqual(self.window.active_output_current_a, 4.0)
        self.assertEqual(result_details, [])
        self.assertIn("TDK 输出关闭失败", self.window.automatic_pause_reason)

    def test_tdk_output_timeout_keeps_original_current_and_result_pending(self) -> None:
        class TimedOutOutputController:
            is_connected = True
            output_enabled = True

            def set_output_enabled(self, _enabled: bool) -> None:
                raise RuntimeError("OUT 0 timeout")

        result_details = self._prepare_zero_current_terminal_path(
            AutomaticTestState.RAMPING_DOWN
        )
        self.window.power_supply_controller_kind = "tdk"
        self.window.manual_ch341_controller = TimedOutOutputController()
        self.window.active_output_current_a = 4.0
        self.window.set_current_spin.setValue(4.0)

        self.window.complete_automatic_test()
        self.window.manual_ch341_controller = None

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertIsNone(self.window.automatic_controller.terminal_outcome)
        self.assertEqual(self.window.automatic_controller.terminal_reason, "")
        self.assertTrue(self.window.automatic_controller.output_shutdown_unconfirmed)
        self.assertEqual(self.window.active_output_current_a, 4.0)
        self.assertEqual(self.window.set_current_spin.value(), 4.0)
        self.assertEqual(result_details, [])
        self.assertIn("TDK 输出关闭失败", self.window.automatic_pause_reason)

    def test_retrying_tdk_output_shutdown_promotes_original_pending_outcome(self) -> None:
        class FlakyOutputController:
            is_connected = True
            output_enabled = True

            def __init__(self) -> None:
                self.shutdown_attempts = 0

            def set_output_enabled(self, enabled: bool) -> None:
                self.shutdown_attempts += 1
                if self.shutdown_attempts == 1:
                    raise RuntimeError("OUT 0 timeout")
                self.output_enabled = enabled

        result_details = self._prepare_zero_current_terminal_path(
            AutomaticTestState.RAMPING_DOWN
        )
        controller = FlakyOutputController()
        self.window.power_supply_controller_kind = "tdk"
        self.window.manual_ch341_controller = controller

        self.window.begin_automatic_ramp_down(
            terminal_outcome=AutomaticTestTerminalOutcome.SUCCEEDED,
            terminal_reason="所有计划测试点均已保存",
        )

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertIsNone(self.window.automatic_controller.terminal_outcome)
        self.assertTrue(self.window.automatic_controller.output_shutdown_unconfirmed)
        self.assertEqual(result_details, [])

        self._retry_without_modal()

        self.assertEqual(controller.shutdown_attempts, 2)
        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.COMPLETED)
        self.assertEqual(
            self.window.automatic_controller.terminal_outcome,
            AutomaticTestTerminalOutcome.SUCCEEDED,
        )
        self.assertEqual(
            self.window.automatic_controller.terminal_reason,
            "所有计划测试点均已保存",
        )
        self.assertFalse(self.window.automatic_controller.output_shutdown_unconfirmed)
        self.assertIn("完整完成", result_details[-1])

    def test_automatic_tdk_shutdown_syncs_manual_and_prepare_output_buttons(self) -> None:
        class ConnectedTdkController:
            is_connected = True
            output_enabled = True

            def set_output_enabled(self, enabled: bool) -> None:
                self.output_enabled = enabled

        self.window.power_supply_controller_combo.setCurrentIndex(
            self.window.power_supply_controller_combo.findData("tdk")
        )
        controller = ConnectedTdkController()
        self.window.manual_ch341_controller = controller
        self.window.power_supply_controller_kind = "tdk"
        self.window.sync_tdk_output_controls(True)
        self.window.automatic_test_state = AutomaticTestState.RAMPING_DOWN

        self.window.complete_automatic_test()

        self.assertFalse(controller.output_enabled)
        self.assertEqual(self.window.tdk_output_status_label.text(), "输出关闭")
        self.assertEqual(self.window.tdk_output_button.text(), "开启输出")
        self.assertEqual(self.window.prepare_tdk_output_button.text(), "开启输出")

    def test_completion_keeps_running_measurement_devices_open(self) -> None:
        class ReaderStub:
            is_ready = True

            def __init__(self) -> None:
                self.stop_calls = 0

            def stop(self) -> None:
                self.stop_calls += 1

        power_meter = ReaderStub()
        spectrometer = ReaderStub()
        self.window.power_meter_reader = power_meter  # type: ignore[assignment]
        self.window.spectrometer_reader = spectrometer  # type: ignore[assignment]
        self.window.automatic_test_state = AutomaticTestState.RAMPING_DOWN

        self.window.complete_automatic_test()

        self.assertIs(self.window.power_meter_reader, power_meter)
        self.assertIs(self.window.spectrometer_reader, spectrometer)
        self.assertEqual(power_meter.stop_calls, 0)
        self.assertEqual(spectrometer.stop_calls, 0)

    def test_completion_leaves_unused_spectrometer_off(self) -> None:
        self.window.auto_use_spectrometer_check.setChecked(False)
        self.window.automatic_test_settings = self.window.collect_automatic_test_settings()
        self.window.spectrometer_reader = None
        spectrometer_starts: list[bool] = []
        self.window.start_spectrometer = (  # type: ignore[method-assign]
            lambda: spectrometer_starts.append(True)
        )
        self.window.automatic_test_state = AutomaticTestState.RAMPING_DOWN

        self.window.complete_automatic_test()

        self.assertIsNone(self.window.spectrometer_reader)
        self.assertEqual(spectrometer_starts, [])


if __name__ == "__main__":
    unittest.main()
