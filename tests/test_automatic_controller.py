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
from combined_test.window import MainWindow


class AutomaticTestControllerLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        settings_path = Path(self.temp_dir.name) / "controller.ini"
        self.window = MainWindow(QSettings(str(settings_path), QSettings.Format.IniFormat))

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


if __name__ == "__main__":
    unittest.main()
