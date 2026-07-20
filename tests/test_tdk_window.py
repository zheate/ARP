from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

import combined_test.window as window_module
from combined_test.window import MainWindow


class FakeTdkController:
    def __init__(self, resource: str) -> None:
        self.resource = resource
        self.is_connected = False
        self.output_enabled = False
        self.voltages: list[float] = []
        self.currents: list[float] = []
        self.measured_current_a = 0.0
        self.command_history: list[str] = []

    def set_i2c_speed(self, _speed: int) -> bool:
        return True

    def connect_device(self, _index: int = 0) -> tuple[bool, str]:
        self.is_connected = True
        return True, f"TDK-LAMBDA,FAKE | {self.resource}"

    def disconnect_device(self) -> bool:
        self.is_connected = False
        self.output_enabled = False
        return True

    def set_output_voltage(self, value: float) -> None:
        self.voltages.append(value)

    def set_output_enabled(self, enabled: bool) -> None:
        self.command_history.append(f"output:{int(enabled)}")
        self.output_enabled = enabled

    def set_output_current(self, value: float) -> None:
        self.command_history.append(f"current:{value:g}")
        self.currents.append(value)

    def read_output_current(self) -> float:
        self.command_history.append("read_current")
        return self.measured_current_a


class TdkWindowTests(unittest.TestCase):
    def make_window(self) -> MainWindow:
        QApplication.instance() or QApplication([])
        self.temp_dir = tempfile.TemporaryDirectory()
        settings = QSettings(str(Path(self.temp_dir.name) / "settings.ini"), QSettings.Format.IniFormat)
        return MainWindow(settings)

    def tearDown(self) -> None:
        if hasattr(self, "temp_dir"):
            self.temp_dir.cleanup()

    def test_tdk_mode_connects_programs_voltage_and_toggles_output(self) -> None:
        old_controller = window_module.TdkLambdaPowerSupply
        window_module.TdkLambdaPowerSupply = FakeTdkController  # type: ignore[assignment]
        try:
            window = self.make_window()
            window.power_supply_controller_combo.setCurrentIndex(
                window.power_supply_controller_combo.findData("tdk")
            )
            window.tdk_resource_combo.setEditText("ASRL3::INSTR")

            window.connect_i2c_device()

            controller = window.manual_ch341_controller
            self.assertIsInstance(controller, FakeTdkController)
            self.assertTrue(controller.is_connected)
            self.assertEqual(window.connect_i2c_button.text(), "断开 TDK")
            self.assertFalse(window.read_temperature_button.isEnabled())

            window.tdk_voltage_spin.setValue(24.5)
            window.last_power_supply_command_monotonic_s = window_module.time.monotonic()
            window.apply_tdk_output_voltage()
            self.assertEqual(controller.voltages, [24.5])
            self.assertEqual(window.power_supply_command_interval_remaining_s(), 0.0)

            window.toggle_tdk_output()
            self.assertTrue(controller.output_enabled)
            self.assertEqual(controller.currents, [0.0])
            self.assertEqual(
                controller.command_history[:3],
                ["current:0", "read_current", "output:1"],
            )
            self.assertEqual(window.set_current_spin.value(), 0.0)
            self.assertEqual(window.active_output_current_a, 0.0)
            self.assertEqual(window.tdk_output_status_label.text(), "输出开启")
            self.assertEqual(window.tdk_output_button.text(), "关闭输出")
            self.assertEqual(window.prepare_tdk_output_button.text(), "关闭输出")

            window.active_output_current_a = 5.0
            window.toggle_tdk_output()
            self.assertFalse(controller.output_enabled)
            self.assertEqual(window.active_output_current_a, 0.0)

            window.active_output_current_a = 5.0
            window.connect_i2c_device()
            self.assertEqual(window.active_output_current_a, 0.0)

            window.close()
            self.assertFalse(controller.output_enabled)
            self.assertFalse(controller.is_connected)
        finally:
            window_module.TdkLambdaPowerSupply = old_controller

    def test_manual_tdk_output_locks_other_tabs_until_output_is_closed(self) -> None:
        old_controller = window_module.TdkLambdaPowerSupply
        window_module.TdkLambdaPowerSupply = FakeTdkController  # type: ignore[assignment]
        try:
            window = self.make_window()
            window.power_supply_controller_combo.setCurrentIndex(
                window.power_supply_controller_combo.findData("tdk")
            )
            window.tdk_resource_combo.setEditText("ASRL3::INSTR")
            window.connect_i2c_device()
            window.main_tabs.setCurrentIndex(window.manual_tab_index)

            window.last_power_supply_command_monotonic_s = None
            window.toggle_tdk_output()

            self.assertTrue(window.manual_power_tab_lock_active)
            self.assertTrue(window.main_tabs.isTabEnabled(window.manual_tab_index))
            for index in (
                window.automatic_tab_index,
                window.records_tab_index,
            ):
                self.assertFalse(window.main_tabs.isTabEnabled(index))
            self.assertTrue(window.main_tabs.isTabEnabled(window.pd_tab_index))
            window.main_tabs.setCurrentIndex(window.pd_tab_index)
            self.assertEqual(window.main_tabs.currentIndex(), window.pd_tab_index)

            window.last_power_supply_command_monotonic_s = None
            window.toggle_tdk_output()

            self.assertFalse(window.manual_power_tab_lock_active)
            for index in range(window.main_tabs.count()):
                self.assertTrue(window.main_tabs.isTabEnabled(index))
            window.close()
        finally:
            window_module.TdkLambdaPowerSupply = old_controller

    def test_tdk_output_stays_off_when_current_does_not_confirm_zero(self) -> None:
        old_controller = window_module.TdkLambdaPowerSupply
        old_critical = window_module.QMessageBox.critical
        window_module.TdkLambdaPowerSupply = FakeTdkController  # type: ignore[assignment]
        errors: list[tuple[str, str]] = []
        window_module.QMessageBox.critical = (  # type: ignore[method-assign]
            lambda _parent, title, message: errors.append((title, message))
        )
        try:
            window = self.make_window()
            window.power_supply_controller_combo.setCurrentIndex(
                window.power_supply_controller_combo.findData("tdk")
            )
            window.tdk_resource_combo.setEditText("ASRL3::INSTR")
            window.connect_i2c_device()
            controller = window.manual_ch341_controller
            controller.measured_current_a = 0.2
            window.set_current_spin.setValue(5.0)

            window.last_power_supply_command_monotonic_s = None
            window.toggle_tdk_output()

            self.assertFalse(controller.output_enabled)
            self.assertEqual(controller.currents, [0.0])
            self.assertNotIn("output:1", controller.command_history)
            self.assertEqual(window.set_current_spin.value(), 0.0)
            self.assertEqual(errors[0][0], "TDK 输出")
            self.assertIn("电流未归零", errors[0][1])
            window.close()
        finally:
            window_module.QMessageBox.critical = old_critical  # type: ignore[method-assign]
            window_module.TdkLambdaPowerSupply = old_controller

    def test_controller_mode_shows_only_its_relevant_rows(self) -> None:
        window = self.make_window()
        form = window.power_supply_form
        details_form = window.power_supply_details_form

        self.assertFalse(details_form.isRowVisible(window.tdk_resource_row))
        self.assertFalse(details_form.isRowVisible(window.tdk_voltage_row))
        self.assertFalse(form.isRowVisible(window.tdk_output_row))
        self.assertTrue(details_form.isRowVisible(window.power_supply_read_row))

        window.power_supply_controller_combo.setCurrentIndex(
            window.power_supply_controller_combo.findData("tdk")
        )

        self.assertTrue(details_form.isRowVisible(window.tdk_resource_row))
        self.assertTrue(details_form.isRowVisible(window.tdk_voltage_row))
        self.assertTrue(form.isRowVisible(window.tdk_output_row))
        self.assertFalse(details_form.isRowVisible(window.power_supply_read_row))
        for button in (
            window.read_input_voltage_button,
            window.read_output_voltage_button,
            window.read_output_current_button,
            window.read_temperature_button,
        ):
            self.assertTrue(button.isHidden())

        window.close()

    def test_switching_from_disconnected_ch341_back_to_tdk_uses_a_new_tdk_controller(self) -> None:
        class FakeCh341Controller:
            def __init__(self) -> None:
                self.is_connected = False
                self.was_disconnected = False

            def set_i2c_speed(self, _speed: int) -> bool:
                if self.was_disconnected:
                    raise RuntimeError("无法设置 I2C 速度")
                return True

            def connect_device(self, _index: int = 0) -> tuple[bool, str]:
                self.is_connected = True
                return True, "CH341 connected"

            def disconnect_device(self) -> bool:
                self.is_connected = False
                self.was_disconnected = True
                return True

        old_tdk_controller = window_module.TdkLambdaPowerSupply
        old_ch341_loader = window_module.load_legacy_ch341_controller_class
        window_module.TdkLambdaPowerSupply = FakeTdkController  # type: ignore[assignment]
        window_module.load_legacy_ch341_controller_class = lambda: FakeCh341Controller  # type: ignore[assignment]
        try:
            window = self.make_window()
            tdk_index = window.power_supply_controller_combo.findData("tdk")
            ch341_index = window.power_supply_controller_combo.findData("ch341")
            window.power_supply_controller_combo.setCurrentIndex(tdk_index)
            window.power_supply_controller_combo.setCurrentIndex(ch341_index)

            window.connect_i2c_device()
            stale_ch341 = window.manual_ch341_controller
            window.connect_i2c_device()
            self.assertFalse(stale_ch341.is_connected)
            self.assertIs(window.manual_ch341_controller, stale_ch341)

            window.power_supply_controller_combo.setCurrentIndex(tdk_index)

            self.assertIsNone(window.manual_ch341_controller)
            window.tdk_resource_combo.setEditText("ASRL4::INSTR")
            window.connect_i2c_device()

            self.assertIsInstance(window.manual_ch341_controller, FakeTdkController)
            self.assertTrue(window.manual_ch341_controller.is_connected)
            self.assertEqual(window.i2c_status_label.text(), "已连接")
            window.close()
        finally:
            window_module.TdkLambdaPowerSupply = old_tdk_controller
            window_module.load_legacy_ch341_controller_class = old_ch341_loader

    def test_tdk_connection_locks_controller_and_serial_selection(self) -> None:
        window = self.make_window()
        controller = FakeTdkController("USB0::1::INSTR")
        controller.is_connected = True
        controller.output_enabled = True
        window.manual_ch341_controller = controller
        window.power_supply_controller_kind = "tdk"
        window.power_supply_controller_combo.setCurrentIndex(
            window.power_supply_controller_combo.findData("tdk")
        )
        window.update_global_status()

        self.assertFalse(window.power_supply_controller_combo.isEnabled())
        self.assertFalse(window.prepare_power_supply_combo.isEnabled())
        self.assertFalse(window.tdk_resource_combo.isEnabled())
        self.assertFalse(window.prepare_tdk_resource_combo.isEnabled())
        self.assertFalse(window.refresh_tdk_resources_button.isEnabled())

        window.power_supply_controller_combo.setCurrentIndex(
            window.power_supply_controller_combo.findData("ch341")
        )

        self.assertEqual(window.power_supply_controller_combo.currentData(), "tdk")
        self.assertEqual(window.prepare_power_supply_combo.currentData(), "tdk")
        self.assertTrue(controller.is_connected)
        self.assertTrue(controller.output_enabled)
        self.assertIs(window.manual_ch341_controller, controller)

        window.connect_i2c_device()

        self.assertTrue(window.power_supply_controller_combo.isEnabled())
        self.assertTrue(window.prepare_power_supply_combo.isEnabled())
        self.assertTrue(window.tdk_resource_combo.isEnabled())
        self.assertTrue(window.prepare_tdk_resource_combo.isEnabled())
        self.assertTrue(window.refresh_tdk_resources_button.isEnabled())
        window.close()

    def test_ch341_connection_locks_controller_selection(self) -> None:
        class ConnectedCh341Controller:
            is_connected = True

            def disconnect_device(self) -> None:
                self.is_connected = False

        window = self.make_window()
        controller = ConnectedCh341Controller()
        window.manual_ch341_controller = controller
        window.power_supply_controller_kind = "ch341"
        window.update_global_status()

        self.assertFalse(window.power_supply_controller_combo.isEnabled())
        self.assertFalse(window.prepare_power_supply_combo.isEnabled())

        window.prepare_power_supply_combo.setCurrentIndex(
            window.prepare_power_supply_combo.findData("tdk")
        )

        self.assertEqual(window.power_supply_controller_combo.currentData(), "ch341")
        self.assertEqual(window.prepare_power_supply_combo.currentData(), "ch341")
        self.assertTrue(controller.is_connected)
        self.assertIs(window.manual_ch341_controller, controller)
        window.manual_ch341_controller = None
        window.close()

    def test_tdk_mode_removes_software_current_limit_and_ch341_restores_it(self) -> None:
        window = self.make_window()

        window.power_supply_controller_combo.setCurrentIndex(
            window.power_supply_controller_combo.findData("tdk")
        )

        for widget in (
            window.set_current_spin,
            window.auto_initial_current_spin,
            window.auto_target_current_spin,
            window.auto_current_step_spin,
            window.auto_ramp_down_step_spin,
        ):
            self.assertTrue(math.isinf(widget.maximum()))
        window.set_current_spin.setValue(30.0)
        window.auto_initial_current_spin.setValue(25.0)
        window.auto_target_current_spin.setValue(30.0)
        settings = window.collect_automatic_test_settings()
        self.assertEqual(window.set_current_spin.value(), 30.0)
        self.assertIsNone(settings.maximum_current_a)

        window.power_supply_controller_combo.setCurrentIndex(
            window.power_supply_controller_combo.findData("ch341")
        )

        self.assertEqual(window.set_current_spin.maximum(), 20.0)
        self.assertEqual(window.auto_target_current_spin.maximum(), 20.0)
        self.assertEqual(window.set_current_spin.value(), 20.0)
        window.close()

    def test_close_retries_tdk_output_off_then_exits(self) -> None:
        class RetryController(FakeTdkController):
            def __init__(self) -> None:
                super().__init__("ASRL4::INSTR")
                self.is_connected = True
                self.output_enabled = True
                self.output_off_attempts = 0

            def set_output_enabled(self, enabled: bool) -> None:
                self.output_off_attempts += 1
                if self.output_off_attempts == 1:
                    raise RuntimeError("temporary serial failure")
                super().set_output_enabled(enabled)

        window = self.make_window()
        controller = RetryController()
        window.manual_ch341_controller = controller
        window.power_supply_controller_kind = "tdk"
        actions: list[str] = []
        window._ask_tdk_shutdown_failure_action = (  # type: ignore[method-assign]
            lambda _error: actions.append("retry") or "retry"
        )
        event = QCloseEvent()

        window.closeEvent(event)

        self.assertTrue(event.isAccepted())
        self.assertEqual(actions, ["retry"])
        self.assertEqual(controller.output_off_attempts, 2)
        self.assertFalse(controller.is_connected)
        self.assertIsNone(window.manual_ch341_controller)
        window.close()

    def test_close_sends_output_off_even_when_cached_state_is_false(self) -> None:
        class StateTrackingController(FakeTdkController):
            def __init__(self) -> None:
                super().__init__("ASRL4::INSTR")
                self.is_connected = True
                self.output_enabled = False
                self.output_commands: list[bool] = []

            def set_output_enabled(self, enabled: bool) -> None:
                self.output_commands.append(enabled)
                super().set_output_enabled(enabled)

        window = self.make_window()
        controller = StateTrackingController()
        window.manual_ch341_controller = controller
        window.power_supply_controller_kind = "tdk"
        event = QCloseEvent()

        window.closeEvent(event)

        self.assertTrue(event.isAccepted())
        self.assertEqual(controller.output_commands, [False])
        self.assertFalse(controller.is_connected)
        self.assertIsNone(window.manual_ch341_controller)
        window.close()

    def test_close_can_force_exit_when_tdk_does_not_answer(self) -> None:
        class FailedController(FakeTdkController):
            def __init__(self) -> None:
                super().__init__("ASRL4::INSTR")
                self.is_connected = True
                self.output_enabled = True
                self.output_off_attempts = 0

            def set_output_enabled(self, _enabled: bool) -> None:
                self.output_off_attempts += 1
                raise RuntimeError("serial link lost")

        window = self.make_window()
        controller = FailedController()
        window.manual_ch341_controller = controller
        window.power_supply_controller_kind = "tdk"
        window._ask_tdk_shutdown_failure_action = lambda _error: "force"  # type: ignore[method-assign]
        event = QCloseEvent()

        window.closeEvent(event)

        self.assertTrue(event.isAccepted())
        self.assertEqual(controller.output_off_attempts, 1)
        self.assertFalse(controller.is_connected)
        self.assertIsNone(window.manual_ch341_controller)
        self.assertIn("强制退出", window.log_text.text())
        window.close()

    def test_cancel_after_tdk_shutdown_failure_keeps_window_open(self) -> None:
        class FailedController(FakeTdkController):
            def set_output_enabled(self, _enabled: bool) -> None:
                raise RuntimeError("serial link lost")

        window = self.make_window()
        controller = FailedController("ASRL4::INSTR")
        controller.is_connected = True
        controller.output_enabled = True
        window.manual_ch341_controller = controller
        window.power_supply_controller_kind = "tdk"
        window._ask_tdk_shutdown_failure_action = lambda _error: "cancel"  # type: ignore[method-assign]
        event = QCloseEvent()

        window.closeEvent(event)

        self.assertFalse(event.isAccepted())
        self.assertTrue(controller.is_connected)
        self.assertIs(window.manual_ch341_controller, controller)
        window.manual_ch341_controller = None
        window.close()


if __name__ == "__main__":
    unittest.main()
