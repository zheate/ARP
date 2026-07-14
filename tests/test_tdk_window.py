from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import combined_test.window as window_module
from combined_test.window import MainWindow


class FakeTdkController:
    def __init__(self, resource: str) -> None:
        self.resource = resource
        self.is_connected = False
        self.output_enabled = False
        self.voltages: list[float] = []

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
        self.output_enabled = enabled


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
            window.apply_tdk_output_voltage()
            self.assertEqual(controller.voltages, [24.5])

            window.last_power_supply_command_monotonic_s = None
            window.toggle_tdk_output()
            self.assertTrue(controller.output_enabled)
            self.assertEqual(window.tdk_output_status_label.text(), "输出开启")
            self.assertEqual(window.tdk_output_button.text(), "关闭输出")

            window.close()
            self.assertFalse(controller.output_enabled)
            self.assertFalse(controller.is_connected)
        finally:
            window_module.TdkLambdaPowerSupply = old_controller

    def test_controller_mode_shows_only_its_relevant_rows(self) -> None:
        window = self.make_window()
        form = window.power_supply_form

        self.assertFalse(form.isRowVisible(window.tdk_resource_row))
        self.assertFalse(form.isRowVisible(window.tdk_voltage_row))
        self.assertFalse(form.isRowVisible(window.tdk_output_row))
        self.assertTrue(form.isRowVisible(window.power_supply_read_row))

        window.power_supply_controller_combo.setCurrentIndex(
            window.power_supply_controller_combo.findData("tdk")
        )

        self.assertTrue(form.isRowVisible(window.tdk_resource_row))
        self.assertTrue(form.isRowVisible(window.tdk_voltage_row))
        self.assertTrue(form.isRowVisible(window.tdk_output_row))
        self.assertFalse(form.isRowVisible(window.power_supply_read_row))
        for button in (
            window.read_input_voltage_button,
            window.read_output_voltage_button,
            window.read_output_current_button,
            window.read_temperature_button,
        ):
            self.assertTrue(button.isHidden())

        window.close()

    def test_switching_controller_disconnects_existing_power_supply(self) -> None:
        window = self.make_window()
        controller = FakeTdkController("USB0::1::INSTR")
        controller.is_connected = True
        controller.output_enabled = True
        window.manual_ch341_controller = controller
        window.power_supply_controller_kind = "tdk"
        window.power_supply_controller_combo.setCurrentIndex(
            window.power_supply_controller_combo.findData("tdk")
        )

        window.power_supply_controller_combo.setCurrentIndex(
            window.power_supply_controller_combo.findData("ch341")
        )

        self.assertFalse(controller.is_connected)
        self.assertFalse(controller.output_enabled)
        self.assertIsNone(window.manual_ch341_controller)
        self.assertEqual(window.connect_i2c_button.text(), "连接 CH341")
        window.close()


if __name__ == "__main__":
    unittest.main()
