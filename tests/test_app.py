import tempfile
import sys
import os
import re
import types
import unittest
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QSettings
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QFormLayout, QGroupBox, QLabel, QMessageBox, QWidget

from combined_test import devices as combined_test_devices
from combined_test import spectrum as combined_test_spectrum
from combined_test import window as combined_test_window
from combined_test.automation import AutomaticTestState
from combined_test.excel_export import ExcelTestRecord
from combined_test.models import (
    LiveReading,
    PowerMeterOption,
    PowerMeterReading,
    PowerMeterSettings,
    SpectrometerOption,
    SpectrometerReading,
)
from combined_test.persistence import (
    build_spectrum_csv_path,
    save_spectrum_curve,
)
from combined_test.window import MainWindow, POWER_SUPPLY_COMMAND_MIN_INTERVAL_S
from tools import power_meter_mvp


class SpectrumCurveFileTests(unittest.TestCase):
    def test_build_spectrum_csv_path_uses_main_csv_sibling_directory(self) -> None:
        path = build_spectrum_csv_path(Path("records/main.csv"), datetime(2026, 7, 8, 12, 1, 2, 3456))

        self.assertEqual(path, Path("records/main_spectra/spectrum_20260708_120102_003456.csv"))

    def test_save_spectrum_curve_writes_full_wavelength_curve(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "curve.csv"

            save_spectrum_curve(path, [975.1, 975.2], [100, 200.5])

            self.assertEqual(
                path.read_text(encoding="utf-8").splitlines(),
                [
                    "wavelength_nm,intensity",
                    "975.100000,100.000000",
                    "975.200000,200.500000",
                ],
            )


class MainWindowTests(unittest.TestCase):
    def _group(self, window: MainWindow, title: str) -> QGroupBox:
        for group in window.findChildren(QGroupBox):
            if group.title() == title:
                return group
        raise AssertionError(f"{title} group not found")

    def _spectrometer_form(self, window: MainWindow) -> QFormLayout:
        for group in window.findChildren(QGroupBox):
            if group.title() == "光谱仪":
                form = group.layout()
                self.assertIsInstance(form, QFormLayout)
                return form
        raise AssertionError("光谱仪分组未找到")

    def _form_row_containing_widget(self, form: QFormLayout, widget: object) -> int:
        for row in range(form.rowCount()):
            for role in (
                QFormLayout.ItemRole.LabelRole,
                QFormLayout.ItemRole.FieldRole,
                QFormLayout.ItemRole.SpanningRole,
            ):
                item = form.itemAt(row, role)
                if item is not None and self._layout_item_contains_widget(item, widget):
                    return row
        raise AssertionError(f"Widget {widget!r} not found in form")

    def _layout_item_contains_widget(self, item: object, widget: object) -> bool:
        found_widget = item.widget()
        if found_widget is widget:
            return True
        layout = item.layout()
        if layout is None:
            return False
        for index in range(layout.count()):
            child = layout.itemAt(index)
            if child is not None and self._layout_item_contains_widget(child, widget):
                return True
        return False

    def test_main_window_can_be_constructed(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertIsNotNone(window.log_text)
        window.close()

    def test_automatic_test_controls_use_safe_defaults(self) -> None:
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = QSettings(str(Path(temp_dir) / "defaults.ini"), QSettings.Format.IniFormat)
            window = MainWindow(settings)

            self.assertEqual(window.automatic_test_toggle.text(), "自动测试")
            self.assertFalse(window.automatic_test_toggle.isChecked())
            self.assertTrue(window.automatic_test_content.isHidden())
            self.assertEqual(window.auto_initial_current_spin.value(), 1.0)
            self.assertEqual(window.auto_target_current_spin.value(), 20.0)
            self.assertEqual(window.auto_current_step_spin.value(), 1.0)
            self.assertEqual(window.auto_point_timeout_spin.value(), 120.0)
            self.assertEqual(window.auto_ramp_down_step_spin.value(), 5.0)
            self.assertEqual(window.auto_ramp_down_interval_spin.value(), 1.1)
            self.assertTrue(window.start_automatic_test_button.isEnabled())
            self.assertFalse(window.retry_automatic_test_button.isEnabled())
            self.assertFalse(window.end_automatic_test_button.isEnabled())
            window.automatic_test_toggle.click()
            self.assertTrue(window.automatic_test_toggle.isChecked())
            self.assertFalse(window.automatic_test_content.isHidden())
            window.close()

    def test_automatic_test_waits_for_both_acquisition_devices_before_setting_initial_current(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakeController:
            is_connected = True

            def __init__(self) -> None:
                self.writes: list[list[int]] = []

            def i2c_write(self, _address: int, command: list[int]) -> tuple[bool, str]:
                self.writes.append(command)
                return True, "OK"

        class ReaderStub:
            def reset_stability_window(self) -> int:
                return 1

            def stop(self) -> None:
                pass

            def wait(self, _timeout: int) -> None:
                pass

        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(QSettings(str(Path(temp_dir) / "inputs.ini"), QSettings.Format.IniFormat))
            controller = FakeController()
            window.manual_ch341_controller = controller
            window.power_meter_reader = ReaderStub()  # type: ignore[assignment]
            window.spectrometer_reader = ReaderStub()  # type: ignore[assignment]
            window.sn_field.setText("AUTO-001")
            window.output_dir_field.setText(temp_dir)

            window.start_automatic_test()

            self.assertEqual(window.automatic_test_state, AutomaticTestState.STARTING)
            self.assertEqual(controller.writes, [])
            window.on_power_meter_ready()
            self.assertEqual(controller.writes, [])
            window.on_spectrometer_ready()
            self.assertEqual(controller.writes, [[0xB4, 0xFF, 0x01, 0x00]])
            self.assertEqual(window.automatic_test_state, AutomaticTestState.WAITING_STABLE)
            self.assertIn("1/20", window.automatic_test_status_label.text())
            self.assertIn("1.0 A", window.automatic_test_status_label.text())

            window.automatic_test_state = AutomaticTestState.IDLE
            window.close()

    def test_stability_controls_update_the_running_power_meter_reader(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        class ReaderStub:
            def __init__(self) -> None:
                self.updates: list[tuple[float, float]] = []

            def update_stability_settings(self, window_s: float, tolerance_w: float) -> None:
                self.updates.append((window_s, tolerance_w))

        reader = ReaderStub()
        window.power_meter_reader = reader  # type: ignore[assignment]
        new_window_s = window.stable_window_spin.value() + 1.0
        window.stable_window_spin.setValue(new_window_s)

        self.assertEqual(reader.updates[-1], (new_window_s, window.stable_tolerance_spin.value()))
        window.power_meter_reader = None
        window.close()

    def test_power_reading_selects_and_displays_automatic_allowed_span(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        window.on_power_meter_reading(PowerMeterReading(1.0, 150.0, False, 0.2, 1.0))

        self.assertTrue(window.stable_tolerance_spin.isReadOnly())
        self.assertEqual(window.stable_tolerance_spin.value(), 0.25)
        self.assertIn("≤ 0.2500 W", window.live_plots.stability_detail_text.get_text())

        window.on_power_meter_reading(PowerMeterReading(4.0, 150.1, True, 0.1, 3.0))

        self.assertEqual(window.live_plots.stability_status_text.get_text(), "STABLE")
        self.assertIsNotNone(window.live_plots._stable_region_artist)
        self.assertEqual(window.live_plots.power_curve_line.get_color(), "#2f8f46")
        window.close()

    def test_input_parameters_are_restored_in_next_window(self) -> None:
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = QSettings(str(Path(temp_dir) / "operator-inputs.ini"), QSettings.Format.IniFormat)
            first_window = MainWindow(settings)
            first_window.set_current_spin.setValue(12)
            first_window.power_wavelength_spin.setValue(973.125)
            first_window.integration_spin.setValue(25000)
            first_window.stable_window_spin.setValue(5.0)
            first_window.stable_tolerance_spin.setValue(0.0123)
            first_window.auto_initial_current_spin.setValue(2.0)
            first_window.auto_target_current_spin.setValue(18.0)
            first_window.auto_current_step_spin.setValue(2.0)
            first_window.auto_point_timeout_spin.setValue(180.0)
            first_window.auto_ramp_down_step_spin.setValue(4.0)
            first_window.auto_ramp_down_interval_spin.setValue(1.5)
            first_window.sn_field.setText("SN-001")
            first_window.output_dir_field.setText(str(Path(temp_dir) / "records"))
            first_window.save_input_settings()
            first_window.close()

            restored_window = MainWindow(settings)
            self.assertEqual(restored_window.set_current_spin.value(), 12)
            self.assertAlmostEqual(restored_window.power_wavelength_spin.value(), 973.125)
            self.assertEqual(restored_window.integration_spin.value(), 25000)
            self.assertAlmostEqual(restored_window.stable_window_spin.value(), 5.0)
            self.assertAlmostEqual(restored_window.stable_tolerance_spin.value(), 0.15)
            self.assertEqual(restored_window.auto_initial_current_spin.value(), 2.0)
            self.assertEqual(restored_window.auto_target_current_spin.value(), 18.0)
            self.assertEqual(restored_window.auto_current_step_spin.value(), 2.0)
            self.assertEqual(restored_window.auto_point_timeout_spin.value(), 180.0)
            self.assertEqual(restored_window.auto_ramp_down_step_spin.value(), 4.0)
            self.assertEqual(restored_window.auto_ramp_down_interval_spin.value(), 1.5)
            self.assertEqual(restored_window.sn_field.text(), "SN-001")
            self.assertEqual(restored_window.output_dir_field.text(), str(Path(temp_dir) / "records"))
            restored_window.close()

    def test_excel_test_point_saves_liv_and_spectrum_in_one_workbook(self) -> None:
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(QSettings(str(Path(temp_dir) / "inputs.ini"), QSettings.Format.IniFormat))
            window.excel_workbook_path = Path(temp_dir) / "SN001_2026_07_10_14_30_25.xlsx"
            window.latest_spectrum_wavelength = [974.0, 975.0, 976.0, 977.0, 978.0]
            window.latest_spectrum_intensity = [0.0, 5.0, 10.0, 5.0, 0.0]

            window.queue_excel_test_point(3.0, 50.5, 33.0, 33.0 / 3.0 / 50.5)
            window.save_pending_excel_records()
            save_thread = window.excel_save_thread
            self.assertIsNotNone(save_thread)
            self.assertFalse(window.save_excel_button.isEnabled())
            self.assertTrue(save_thread.wait(5000))
            app.processEvents()

            self.assertTrue(window.excel_workbook_path.exists())
            self.assertEqual(window.excel_recorded_currents, {3.0})
            self.assertFalse(window.save_excel_button.isEnabled())
            self.assertEqual(window.save_excel_button.text(), "保存 Excel")
            window.close()

    def test_automatic_test_saves_each_complete_point_immediately(self) -> None:
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(QSettings(str(Path(temp_dir) / "inputs.ini"), QSettings.Format.IniFormat))
            window.excel_workbook_path = Path(temp_dir) / "AUTO_2026_07_11_12_00_00.xlsx"
            window.latest_spectrum_wavelength = [974.0, 975.0, 976.0, 977.0, 978.0]
            window.latest_spectrum_intensity = [0.0, 5.0, 10.0, 5.0, 0.0]
            window.active_output_current_a = 3.0
            window.stable_power_points[3.0] = 33.0
            window.automatic_test_state = AutomaticTestState.WAITING_VOLTAGE
            window.automatic_test_currents = (3.0, 4.0)
            window.automatic_test_current_index = 0

            window.record_efficiency_from_vout(50.5)

            self.assertEqual(window.automatic_test_state, AutomaticTestState.SAVING_POINT)
            self.assertIsNotNone(window.excel_save_thread)
            self.assertTrue(window.excel_save_thread.wait(5000))
            window.automatic_test_state = AutomaticTestState.IDLE
            window.close()

    def test_successful_point_save_advances_to_the_next_automatic_current(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakeController:
            is_connected = True

            def i2c_write(self, _address: int, _command: list[int]) -> tuple[bool, str]:
                return True, "OK"

        class ReaderStub:
            def reset_stability_window(self) -> int:
                return 2

        record = ExcelTestRecord(
            current_a=3.0,
            voltage_v=50.5,
            power_w=33.0,
            efficiency=33.0 / 3.0 / 50.5,
            peak_wavelength_nm=976.0,
            centroid_nm=976.0,
            fwhm_nm=1.0,
            pib=0.99,
            wavelength=[975.0, 976.0, 977.0],
            intensity=[1.0, 10.0, 1.0],
        )
        window = MainWindow()
        window.manual_ch341_controller = FakeController()
        window.power_meter_reader = ReaderStub()  # type: ignore[assignment]
        window.pending_excel_records[3.0] = record
        window.excel_save_thread = types.SimpleNamespace(records=[record], path=Path("auto.xlsx"))  # type: ignore[assignment]
        window.automatic_test_state = AutomaticTestState.SAVING_POINT
        window.automatic_test_currents = (3.0, 4.0)
        window.automatic_test_current_index = 0
        window.automatic_test_settings = window.collect_automatic_test_settings()
        window.last_power_supply_command_monotonic_s = combined_test_window.time.monotonic()

        window.on_excel_save_succeeded(0.1)

        self.assertEqual(window.excel_recorded_currents, {3.0})
        self.assertEqual(window.automatic_test_current_index, 1)
        self.assertEqual(window.automatic_test_state, AutomaticTestState.SETTING_CURRENT)
        self.assertTrue(window.automatic_command_timer.isActive())
        window.automatic_command_timer.stop()
        window.automatic_test_state = AutomaticTestState.IDLE
        window.excel_save_thread = None
        window.power_meter_reader = None
        window.close()

    def test_successful_target_save_starts_configured_ramp_down_without_recording_down_steps(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakeController:
            is_connected = True

            def __init__(self) -> None:
                self.writes: list[list[int]] = []

            def i2c_write(self, _address: int, command: list[int]) -> tuple[bool, str]:
                self.writes.append(command)
                return True, "OK"

        record = ExcelTestRecord(
            current_a=20.0,
            voltage_v=50.5,
            power_w=200.0,
            efficiency=200.0 / 20.0 / 50.5,
            peak_wavelength_nm=976.0,
            centroid_nm=976.0,
            fwhm_nm=1.0,
            pib=0.99,
            wavelength=[975.0, 976.0, 977.0],
            intensity=[1.0, 10.0, 1.0],
        )
        window = MainWindow()
        controller = FakeController()
        window.manual_ch341_controller = controller
        window.pending_excel_records[20.0] = record
        window.excel_save_thread = types.SimpleNamespace(records=[record], path=Path("auto.xlsx"))  # type: ignore[assignment]
        window.automatic_test_state = AutomaticTestState.SAVING_POINT
        window.automatic_test_currents = (20.0,)
        window.automatic_test_current_index = 0
        window.automatic_test_settings = window.collect_automatic_test_settings()
        window.active_output_current_a = 20.0
        window.last_power_supply_command_monotonic_s = (
            combined_test_window.time.monotonic() - POWER_SUPPLY_COMMAND_MIN_INTERVAL_S - 0.1
        )

        window.on_excel_save_succeeded(0.1)

        self.assertEqual(window.automatic_test_state, AutomaticTestState.RAMPING_DOWN)
        self.assertEqual(controller.writes, [[0xB4, 0xFF, 0x0F, 0x00]])
        self.assertEqual(window.active_output_current_a, 15.0)
        self.assertEqual(set(window.pending_excel_records), {20.0})
        self.assertTrue(window.automatic_ramp_down_timer.isActive())
        window.automatic_ramp_down_timer.stop()
        window.automatic_test_state = AutomaticTestState.IDLE
        window.excel_save_thread = None
        window.close()

    def test_controlled_ramp_down_reaches_zero_and_completes_the_test(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakeController:
            is_connected = True

            def __init__(self) -> None:
                self.writes: list[list[int]] = []

            def i2c_write(self, _address: int, command: list[int]) -> tuple[bool, str]:
                self.writes.append(command)
                return True, "OK"

        window = MainWindow()
        controller = FakeController()
        window.manual_ch341_controller = controller
        window.automatic_test_settings = window.collect_automatic_test_settings()
        window.active_output_current_a = 20.0
        window.last_power_supply_command_monotonic_s = (
            combined_test_window.time.monotonic() - POWER_SUPPLY_COMMAND_MIN_INTERVAL_S - 0.1
        )

        window.begin_automatic_ramp_down()
        for _ in range(3):
            window.automatic_ramp_down_timer.stop()
            window.last_power_supply_command_monotonic_s = (
                combined_test_window.time.monotonic() - POWER_SUPPLY_COMMAND_MIN_INTERVAL_S - 0.1
            )
            window.schedule_next_automatic_ramp_down_current()

        self.assertEqual(
            controller.writes,
            [
                [0xB4, 0xFF, 0x0F, 0x00],
                [0xB4, 0xFF, 0x0A, 0x00],
                [0xB4, 0xFF, 0x05, 0x00],
                [0xB4, 0xFF, 0x00, 0x00],
            ],
        )
        self.assertEqual(window.active_output_current_a, 0.0)
        self.assertEqual(window.automatic_test_state, AutomaticTestState.COMPLETED)
        window.close()

    def test_retry_after_ramp_down_write_failure_resumes_downward_sequence(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakeController:
            is_connected = True

            def __init__(self) -> None:
                self.fail = True
                self.writes: list[list[int]] = []

            def i2c_write(self, _address: int, command: list[int]) -> tuple[bool, str]:
                self.writes.append(command)
                return (not self.fail), ("I2C error" if self.fail else "OK")

        class ReadyReaderStub:
            is_ready = True

        window = MainWindow()
        controller = FakeController()
        window.manual_ch341_controller = controller
        window.power_meter_reader = ReadyReaderStub()  # type: ignore[assignment]
        window.spectrometer_reader = ReadyReaderStub()  # type: ignore[assignment]
        window.automatic_test_settings = window.collect_automatic_test_settings()
        window.active_output_current_a = 15.0
        window.last_power_supply_command_monotonic_s = (
            combined_test_window.time.monotonic() - POWER_SUPPLY_COMMAND_MIN_INTERVAL_S - 0.1
        )

        window.begin_automatic_ramp_down()
        self.assertEqual(window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertEqual(window.active_output_current_a, 15.0)

        controller.fail = False
        window.last_power_supply_command_monotonic_s = (
            combined_test_window.time.monotonic() - POWER_SUPPLY_COMMAND_MIN_INTERVAL_S - 0.1
        )
        window.retry_automatic_test()

        self.assertEqual(window.automatic_test_state, AutomaticTestState.RAMPING_DOWN)
        self.assertEqual(window.active_output_current_a, 10.0)
        window.automatic_ramp_down_timer.stop()
        window.automatic_test_state = AutomaticTestState.IDLE
        window.power_meter_reader = None
        window.spectrometer_reader = None
        window.close()

    def test_stop_all_routes_active_automatic_test_through_controlled_ramp_down(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakeController:
            is_connected = True

            def __init__(self) -> None:
                self.writes: list[list[int]] = []

            def i2c_write(self, _address: int, command: list[int]) -> tuple[bool, str]:
                self.writes.append(command)
                return True, "OK"

        window = MainWindow()
        controller = FakeController()
        window.manual_ch341_controller = controller
        window.automatic_test_settings = window.collect_automatic_test_settings()
        window.active_output_current_a = 10.0
        window.automatic_test_state = AutomaticTestState.WAITING_STABLE
        window.last_power_supply_command_monotonic_s = (
            combined_test_window.time.monotonic() - POWER_SUPPLY_COMMAND_MIN_INTERVAL_S - 0.1
        )

        window.stop_all()

        self.assertEqual(window.automatic_test_state, AutomaticTestState.RAMPING_DOWN)
        self.assertEqual(controller.writes, [[0xB4, 0xFF, 0x05, 0x00]])
        window.automatic_ramp_down_timer.stop()
        window.automatic_test_state = AutomaticTestState.IDLE
        window.close()

    def test_closing_during_automatic_test_defers_exit_until_controlled_ramp_down(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakeController:
            is_connected = True

            def __init__(self) -> None:
                self.writes: list[list[int]] = []

            def i2c_write(self, _address: int, command: list[int]) -> tuple[bool, str]:
                self.writes.append(command)
                return True, "OK"

        window = MainWindow()
        controller = FakeController()
        window.manual_ch341_controller = controller
        window.automatic_test_settings = window.collect_automatic_test_settings()
        window.active_output_current_a = 10.0
        window.automatic_test_state = AutomaticTestState.PAUSED
        window.last_power_supply_command_monotonic_s = (
            combined_test_window.time.monotonic() - POWER_SUPPLY_COMMAND_MIN_INTERVAL_S - 0.1
        )
        event = QCloseEvent()

        window.closeEvent(event)

        self.assertFalse(event.isAccepted())
        self.assertTrue(window.close_after_automatic_ramp_down)
        self.assertEqual(window.automatic_test_state, AutomaticTestState.RAMPING_DOWN)
        self.assertEqual(controller.writes, [[0xB4, 0xFF, 0x05, 0x00]])
        window.automatic_ramp_down_timer.stop()
        window.close_after_automatic_ramp_down = False
        window.automatic_test_state = AutomaticTestState.IDLE
        window.close()

    def test_excel_save_failure_pauses_automatic_test_at_current_output(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.active_output_current_a = 8.0
        window.automatic_test_state = AutomaticTestState.SAVING_POINT

        old_critical = QMessageBox.critical
        try:
            QMessageBox.critical = lambda *args, **kwargs: QMessageBox.StandardButton.Ok  # type: ignore[method-assign]
            window.on_excel_save_failed("文件被占用")
        finally:
            QMessageBox.critical = old_critical  # type: ignore[method-assign]

        self.assertEqual(window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertEqual(window.active_output_current_a, 8.0)
        self.assertTrue(window.retry_automatic_test_button.isEnabled())
        self.assertTrue(window.end_automatic_test_button.isEnabled())
        window.automatic_test_state = AutomaticTestState.IDLE
        window.close()

    def test_retry_after_excel_failure_saves_buffered_point_without_remeasuring(self) -> None:
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp_dir:
            record = ExcelTestRecord(
                current_a=8.0,
                voltage_v=50.5,
                power_w=80.0,
                efficiency=80.0 / 8.0 / 50.5,
                peak_wavelength_nm=976.0,
                centroid_nm=976.0,
                fwhm_nm=1.0,
                pib=0.99,
                wavelength=[975.0, 976.0, 977.0],
                intensity=[1.0, 10.0, 1.0],
            )
            window = MainWindow()
            window.excel_workbook_path = Path(temp_dir) / "retry.xlsx"
            window.pending_excel_records[8.0] = record
            window.automatic_test_currents = (8.0, 10.0)
            window.automatic_test_current_index = 0
            window.automatic_test_state = AutomaticTestState.SAVING_POINT
            window.pause_automatic_test("Excel 保存失败")

            old_warning = QMessageBox.warning
            try:
                QMessageBox.warning = lambda *args, **kwargs: QMessageBox.StandardButton.Ok  # type: ignore[method-assign]
                window.retry_automatic_test()
            finally:
                QMessageBox.warning = old_warning  # type: ignore[method-assign]

            self.assertEqual(window.automatic_test_state, AutomaticTestState.SAVING_POINT)
            self.assertIsNotNone(window.excel_save_thread)
            self.assertTrue(window.excel_save_thread.wait(5000))
            window.automatic_test_state = AutomaticTestState.IDLE
            window.close()

    def test_acquisition_failure_pauses_automatic_test_at_current_output(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.active_output_current_a = 6.0
        window.automatic_test_state = AutomaticTestState.WAITING_STABLE

        old_critical = QMessageBox.critical
        try:
            QMessageBox.critical = lambda *args, **kwargs: QMessageBox.StandardButton.Ok  # type: ignore[method-assign]
            window.on_power_meter_failed("串口断开")
        finally:
            QMessageBox.critical = old_critical  # type: ignore[method-assign]

        self.assertEqual(window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertEqual(window.active_output_current_a, 6.0)
        self.assertIn("功率计", window.automatic_test_status_label.text())
        window.automatic_test_state = AutomaticTestState.IDLE
        window.close()

    def test_unexpected_acquisition_stop_pauses_automatic_current_point(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.active_output_current_a = 6.0
        window.automatic_test_state = AutomaticTestState.WAITING_STABLE

        window.on_spectrometer_finished()

        self.assertEqual(window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertIn("光谱仪", window.automatic_test_status_label.text())
        window.automatic_test_state = AutomaticTestState.IDLE
        window.close()

    def test_retry_current_point_restarts_missing_acquisition_devices_before_reapplying_current(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakeController:
            is_connected = True

            def __init__(self) -> None:
                self.writes: list[list[int]] = []

            def i2c_write(self, _address: int, command: list[int]) -> tuple[bool, str]:
                self.writes.append(command)
                return True, "OK"

        window = MainWindow()
        controller = FakeController()
        window.manual_ch341_controller = controller
        window.automatic_test_currents = (6.0, 8.0)
        window.automatic_test_current_index = 0
        window.automatic_test_settings = window.collect_automatic_test_settings()
        window.automatic_test_state = AutomaticTestState.WAITING_STABLE
        window.pause_automatic_test("功率计错误")
        starts: list[str] = []
        window.start_power_meter = lambda: starts.append("power")  # type: ignore[method-assign]
        window.start_spectrometer = lambda: starts.append("spectrum")  # type: ignore[method-assign]

        window.retry_automatic_test()

        self.assertEqual(starts, ["power", "spectrum"])
        self.assertEqual(controller.writes, [])
        self.assertEqual(window.automatic_test_state, AutomaticTestState.STARTING)
        window.automatic_device_start_timer.stop()
        window.automatic_test_state = AutomaticTestState.IDLE
        window.close()

    def test_main_window_uses_workflow_layout_without_scroll_area(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertIsInstance(window.centralWidget(), QWidget)
        self.assertFalse(hasattr(window, "scroll_area"))
        self.assertTrue(hasattr(window, "left_control_panel"))
        self.assertTrue(hasattr(window, "monitor_panel"))
        self.assertGreaterEqual(window.left_control_panel.minimumWidth(), 320)
        self.assertLessEqual(window.left_control_panel.maximumWidth(), 360)
        window.close()

    def test_main_window_integrates_metrics_into_their_related_plots(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        for attribute in (
            "global_status_label",
            "global_psu_status_indicator",
            "global_power_meter_status_indicator",
            "global_spectrometer_status_indicator",
            "sn_field",
            "output_dir_field",
            "save_excel_button",
            "curves_layout",
        ):
            self.assertTrue(hasattr(window, attribute), attribute)

        window.live_plots.relayout(1000)
        self.assertEqual(window.curves_layout.getItemPosition(window.curves_layout.indexOf(window.power_curve_canvas))[:2], (0, 0))
        self.assertEqual(window.curves_layout.getItemPosition(window.curves_layout.indexOf(window.stable_power_canvas))[:2], (0, 1))
        self.assertEqual(
            window.curves_layout.getItemPosition(window.curves_layout.indexOf(window.spectrum_curve_canvas)),
            (1, 0, 1, 2),
        )
        self.assertEqual([window.curves_layout.columnStretch(column) for column in range(2)], [1, 1])
        self.assertEqual([window.curves_layout.rowStretch(row) for row in range(2)], [3, 2])
        self.assertAlmostEqual(window.power_curve_axis.get_position().width, 0.77)
        self.assertAlmostEqual(window.stable_power_axis.get_position().width, 0.77)
        self.assertFalse(hasattr(window, "kpi_panel"))
        self.assertEqual(window.live_plots.power_value_text.get_text(), "-- W")
        self.assertEqual(window.live_plots.power_value_text.get_position(), (0.975, 0.95))
        self.assertEqual(window.live_plots.power_value_text.get_ha(), "right")
        self.assertEqual(window.live_plots.stability_status_text.get_position(), (0.025, 0.95))
        self.assertEqual(window.live_plots.stability_status_text.get_ha(), "left")
        self.assertIn("Center wavelength", window.live_plots.spectrum_centroid_text.get_text())
        self.assertIn("FWHM", window.live_plots.spectrum_fwhm_text.get_text())
        self.assertIn("PIB", window.live_plots.spectrum_pib_text.get_text())
        self.assertFalse(window.log_text.isHidden())
        self.assertIsInstance(window.log_text, QLabel)
        self.assertFalse(hasattr(window, "toggle_log_button"))
        self.assertFalse(hasattr(window, "clear_log_button"))
        window.close()

    def test_global_device_indicators_follow_connection_state(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertIn("#dc2626", window.global_psu_status_indicator.styleSheet())
        self.assertIn("#dc2626", window.global_power_meter_status_indicator.styleSheet())
        self.assertIn("#dc2626", window.global_spectrometer_status_indicator.styleSheet())

        class ConnectedController:
            is_connected = True

        class ReadyReader:
            is_ready = True

        window.manual_ch341_controller = ConnectedController()
        window.power_meter_reader = ReadyReader()  # type: ignore[assignment]
        window.spectrometer_reader = ReadyReader()  # type: ignore[assignment]
        window.update_global_status()

        self.assertIn("#16a34a", window.global_psu_status_indicator.styleSheet())
        self.assertIn("#16a34a", window.global_power_meter_status_indicator.styleSheet())
        self.assertIn("#16a34a", window.global_spectrometer_status_indicator.styleSheet())
        window.power_meter_reader = None
        window.spectrometer_reader = None
        window.close()

    def test_log_shows_only_the_latest_line(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        window.add_log("first message")
        window.add_log("latest message")

        self.assertIn("latest message", window.log_text.text())
        self.assertNotIn("first message", window.log_text.text())
        self.assertFalse(window.log_text.wordWrap())
        window.close()

    def test_plot_layout_stacks_only_below_the_dashboard_width(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.live_plots.relayout(1000)
        self.assertEqual(
            [
                window.curves_layout.getItemPosition(window.curves_layout.indexOf(canvas))[:2]
                for canvas in (window.power_curve_canvas, window.stable_power_canvas, window.spectrum_curve_canvas)
            ],
            [(0, 0), (0, 1), (1, 0)],
        )

        window.live_plots.relayout(700)
        self.assertEqual(
            [
                window.curves_layout.getItemPosition(window.curves_layout.indexOf(canvas))[:2]
                for canvas in (window.power_curve_canvas, window.stable_power_canvas, window.spectrum_curve_canvas)
            ],
            [(0, 0), (1, 0), (2, 0)],
        )
        window.close()

    def test_common_1280_by_800_window_does_not_expand_vertically(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.resize(1280, 800)
        window.show()
        app.processEvents()

        self.assertLessEqual(window.height(), 800)
        self.assertFalse(hasattr(window, "kpi_layout"))
        window.close()

    def test_record_controls_are_grouped_before_device_controls(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        record_form = self._group(window, "测试记录").layout()
        self.assertIsInstance(record_form, QFormLayout)

        for widget in (
            window.sn_field,
            window.output_dir_field,
            window.save_excel_button,
        ):
            self._form_row_containing_widget(record_form, widget)

        self.assertLess(
            window.left_control_content.layout().indexOf(self._group(window, "测试记录")),
            window.left_control_content.layout().indexOf(self._group(window, "电源")),
        )
        window.close()

    def test_button_roles_use_native_default_and_one_destructive_color(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertTrue(window.start_all_button.isDefault())
        self.assertEqual(window.stop_all_button.styleSheet(), window.stop_power_meter_button.styleSheet())
        self.assertEqual(window.stop_all_button.styleSheet(), window.stop_spectrometer_button.styleSheet())
        self.assertIn("color:", window.stop_all_button.styleSheet())
        for button in (
            window.apply_current_button,
            window.connect_i2c_button,
            window.detect_power_meter_button,
            window.start_power_meter_button,
            window.detect_spectrometer_button,
            window.start_spectrometer_button,
            window.save_excel_button,
        ):
            self.assertGreaterEqual(button.minimumHeight(), 28)
        window.close()

    def test_centroid_display_uses_short_median_window(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        for value in (976.000, 976.002, 980.000, 976.001, 976.003):
            window.update_centroid_display(value)

        self.assertEqual(window.live_plots.spectrum_centroid_text.get_text(), "Center wavelength   976.002 nm")
        window.close()

    def test_spin_boxes_and_combos_ignore_mouse_wheel_events(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        wheel_event = QEvent(QEvent.Type.Wheel)

        self.assertTrue(window.eventFilter(window.set_current_spin, wheel_event))
        self.assertTrue(window.eventFilter(window.power_meter_combo, wheel_event))
        window.close()

    def test_left_control_groups_reserve_enough_height_for_their_contents(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        for title in ("测试记录", "电源", "功率计", "光谱仪", "稳定性"):
            group = self._group(window, title)
            self.assertGreaterEqual(group.minimumHeight(), group.sizeHint().height(), title)

        window.close()

    def test_no_advanced_group_and_device_settings_stay_with_their_device(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertNotIn("Advanced", [group.title() for group in window.findChildren(QGroupBox)])

        power_supply_form = self._group(window, "电源").layout()
        self.assertIsInstance(power_supply_form, QFormLayout)
        self.assertFalse(hasattr(window, "i2c_addr_field"))
        self.assertFalse(hasattr(window, "i2c_speed_combo"))
        self.assertEqual(combined_test_window.DEFAULT_I2C_ADDRESS, 0x41)
        self.assertEqual(combined_test_window.DEFAULT_I2C_SPEED, 0)

        power_meter_form = self._group(window, "功率计").layout()
        self.assertIsInstance(power_meter_form, QFormLayout)
        self._form_row_containing_widget(power_meter_form, window.software_gain_spin)
        self._form_row_containing_widget(power_meter_form, window.power_meter_interval_spin)

        spectrometer_form = self._group(window, "光谱仪").layout()
        self.assertIsInstance(spectrometer_form, QFormLayout)
        self._form_row_containing_widget(spectrometer_form, window.interval_spin)
        window.close()

    def test_left_control_panel_does_not_need_horizontal_scrolling(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.resize(2048, 1152)
        window.show()
        app.processEvents()

        self.assertLessEqual(window.left_control_content.width(), window.left_control_panel.viewport().width())
        window.close()

    def test_main_window_exposes_realtime_curve_widgets(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        for attribute in (
            "power_curve_canvas",
            "spectrum_curve_canvas",
            "power_curve_line",
            "spectrum_curve_line",
        ):
            self.assertTrue(hasattr(window, attribute), attribute)

        window.close()

    def test_realtime_curves_have_readable_initial_ranges(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertEqual(tuple(window.power_curve_axis.get_xlim()), (0.0, 10.0))
        self.assertEqual(tuple(window.power_curve_axis.get_ylim()), (-0.01, 0.01))
        self.assertEqual(tuple(window.spectrum_curve_axis.get_xlim()), (0.0, 1.0))
        self.assertEqual(tuple(window.spectrum_curve_axis.get_ylim()), (0.0, 1.0))
        self.assertEqual(window.power_curve_axis.get_title(), "")
        self.assertEqual(window.stable_power_axis.get_title(), "")
        self.assertEqual(window.spectrum_curve_axis.get_title(), "")
        self.assertEqual(window.spectrum_curve_axis.get_xlabel(), "")
        self.assertAlmostEqual(window.power_curve_axis.get_position().height, 0.81)
        self.assertAlmostEqual(window.stable_power_axis.get_position().height, 0.81)
        self.assertAlmostEqual(window.spectrum_curve_axis.get_position().height, 0.76)
        self.assertLessEqual(
            len([tick for tick in window.power_curve_axis.get_yticks() if -0.01 <= tick <= 0.01]),
            5,
        )
        self.assertEqual(
            [tick for tick in window.efficiency_axis.get_yticks() if 20.0 <= tick <= 60.0],
            [20.0, 30.0, 40.0, 50.0, 60.0],
        )
        self.assertEqual(len(window.power_curve_axis.xaxis.get_minorticklocs()), 0)
        self.assertEqual(window.power_curve_axis.get_xticklabels()[0].get_fontsize(), 11.0)
        for axis in (window.power_curve_axis, window.stable_power_axis):
            axis.figure.canvas.draw()
            for label in (tick.get_text() for tick in axis.get_yticklabels()):
                number = re.search(r"-?\d+(?:\.(\d+))?", label)
                self.assertIsNotNone(number)
                self.assertLessEqual(len(number.group(1) or ""), 1)
        self.assertGreaterEqual(window.power_curve_canvas.minimumHeight(), 180)
        self.assertGreaterEqual(window.spectrum_curve_canvas.minimumHeight(), 180)
        window.close()

    def test_live_reading_and_spectrum_update_curve_data(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        reading = LiveReading(
            elapsed_s=1.5,
            power_w=2.25,
            peak_wavelength_nm=976.1,
            centroid_nm=976.2,
            fwhm_nm=1.1,
            stable=False,
            stable_span_w=0.02,
            stable_window_s=1.5,
        )
        window.on_live_reading(reading)
        window.on_spectrum_curve([975.0, 976.0], [10.0, 20.0])

        self.assertEqual(list(window.power_curve_line.get_xdata()), [1.5])
        self.assertEqual(list(window.power_curve_line.get_ydata()), [2.25])
        self.assertEqual(list(window.spectrum_curve_line.get_xdata()), [975.0, 976.0])
        self.assertEqual(list(window.spectrum_curve_line.get_ydata()), [10.0, 20.0])
        window.close()

    def test_power_curve_discards_samples_outside_the_visible_history(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        window.update_power_curve(0.0, 1.0)
        window.update_power_curve(61.0, 2.0)

        self.assertEqual(list(window.power_curve_times), [61.0])
        self.assertEqual(list(window.power_curve_line.get_xdata()), [61.0])
        window.close()

    def test_saturated_spectrum_warns_and_is_not_queued_for_excel(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        wavelength = [973.0, 973.5, 974.0, 974.5, 975.0]
        saturated_intensity = [0.0, 16000.0, 16020.0, 16010.0, 0.0]

        window.on_spectrum_curve(wavelength, saturated_intensity)
        window.on_spectrometer_reading(SpectrometerReading(974.0, 974.0, 1.0))
        window.queue_excel_test_point(10.0, 50.0, 200.0, 0.4)

        self.assertTrue(window.latest_spectrum_saturated)
        self.assertTrue(window.live_plots.spectrum_saturation_text.get_visible())
        self.assertEqual(window.live_plots.spectrum_centroid_text.get_text(), "Center wavelength   -- nm")
        self.assertNotIn(10.0, window.pending_excel_records)
        self.assertIn("未加入保存队列", window.save_status_label.text())

        window.on_spectrum_curve(wavelength, [0.0, 100.0, 200.0, 100.0, 0.0])
        self.assertFalse(window.latest_spectrum_saturated)
        self.assertFalse(window.live_plots.spectrum_saturation_text.get_visible())
        self.assertNotEqual(window.live_plots.spectrum_pib_text.get_text(), "PIB   -- %")
        window.close()

    def test_saturated_spectrum_pauses_automatic_test_at_the_current_point(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.latest_spectrum_wavelength = [973.0, 973.5, 974.0, 974.5, 975.0]
        window.latest_spectrum_intensity = [0.0, 16000.0, 16020.0, 16010.0, 0.0]
        window.active_output_current_a = 10.0
        window.stable_power_points[10.0] = 200.0
        window.automatic_test_state = AutomaticTestState.WAITING_VOLTAGE

        window.record_efficiency_from_vout(50.0)

        self.assertEqual(window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertEqual(window.active_output_current_a, 10.0)
        self.assertIn("光谱饱和", window.automatic_test_status_label.text())
        window.automatic_test_state = AutomaticTestState.IDLE
        window.close()

    def test_spectrum_x_axis_locks_to_dominant_peak_plus_minus_20_after_stable_readings(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        for _ in range(combined_test_spectrum.SPECTRUM_CENTER_LOCK_REQUIRED_SAMPLES):
            window.on_spectrometer_reading(
                SpectrometerReading(
                    peak_wavelength_nm=975.8,
                    centroid_nm=976.2,
                    fwhm_nm=1.1,
                )
            )
        window.on_spectrum_curve([900.0, 946.2, 976.2, 1006.2, 1100.0], [1.0, 2.0, 10.0, 2.0, 1.0])

        x_min, x_max = window.spectrum_curve_axis.get_xlim()
        self.assertAlmostEqual(x_min, 955.8)
        self.assertAlmostEqual(x_max, 995.8)
        window.close()

    def test_spectrum_x_axis_ignores_unstable_whole_spectrum_centroid(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        for centroid_nm in (940.0, 1000.0, 930.0, 1010.0, 950.0):
            window.on_spectrometer_reading(
                SpectrometerReading(
                    peak_wavelength_nm=973.0,
                    centroid_nm=centroid_nm,
                    fwhm_nm=1.1,
                )
            )

        window.on_spectrum_curve([900.0, 943.0, 973.0, 1003.0, 1100.0], [1.0, 2.0, 10.0, 2.0, 1.0])
        x_min, x_max = window.spectrum_curve_axis.get_xlim()

        self.assertAlmostEqual(x_min, 953.0)
        self.assertAlmostEqual(x_max, 993.0)
        window.close()

    def test_spectrum_curve_marks_top_three_peak_centroids(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        wavelength = [850.0, 851.0, 852.0, 853.0, 854.0, 855.0, 856.0, 857.0, 858.0, 859.0, 860.0, 861.0, 862.0]
        intensity = [0.0, 5.0, 80.0, 5.0, 0.0, 10.0, 300.0, 10.0, 0.0, 20.0, 200.0, 20.0, 0.0]

        window.update_spectrum_curve(wavelength, intensity)

        self.assertEqual(
            [(item.label, round(item.centroid_nm, 3)) for item in window.spectrum_peak_annotations],
            [("P1", 856.0), ("P2", 860.0), ("P3", 852.0)],
        )
        annotation_text = "\n".join(
            artist.get_text() for artist in window.spectrum_peak_annotation_artists if hasattr(artist, "get_text")
        )
        self.assertIn("P1 856.000 nm", annotation_text)
        self.assertIn("P2 860.000 nm", annotation_text)
        self.assertIn("P3 852.000 nm", annotation_text)
        window.close()

    def test_spectrum_peak_labels_stay_inside_plot_area_for_tall_peaks(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        wavelength = [877.0, 878.0, 879.0, 880.0, 881.0]
        intensity = [0.0, 15000.0, 50.0, 1000.0, 0.0]

        window.update_spectrum_curve(wavelength, intensity)

        _, y_max = window.spectrum_curve_axis.get_ylim()
        label_artists = [artist for artist in window.spectrum_peak_annotation_artists if hasattr(artist, "get_text")]
        self.assertTrue(label_artists)
        for artist in label_artists:
            _x, y = artist.get_position()
            self.assertLessEqual(y, y_max * 0.92)
            self.assertEqual(artist.get_bbox_patch(), None)
        window.close()

    def test_spectrum_y_axis_rescales_when_peak_intensity_drops(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        wavelength = [850.0, 878.5, 906.0]

        window.update_spectrum_curve(wavelength, [0.0, 15000.0, 0.0])
        first_limits = tuple(window.spectrum_curve_axis.get_ylim())
        window.update_spectrum_curve(wavelength, [0.0, 12000.0, 0.0])
        second_limits = tuple(window.spectrum_curve_axis.get_ylim())

        self.assertEqual(second_limits[0], 0.0)
        self.assertLess(second_limits[1], first_limits[1])
        window.close()

    def test_spectrum_y_axis_expands_when_peak_increases(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        wavelength = [850.0, 878.5, 906.0]

        window.update_spectrum_curve(wavelength, [0.0, 10000.0, 0.0])
        _first_min, first_max = window.spectrum_curve_axis.get_ylim()
        window.update_spectrum_curve(wavelength, [0.0, 16000.0, 0.0])
        _second_min, second_max = window.spectrum_curve_axis.get_ylim()

        self.assertGreater(second_max, first_max)
        window.close()

    def test_spectrum_y_axis_starts_at_zero_for_nonnegative_intensities(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        wavelength = [850.0, 878.5, 906.0]

        window.update_spectrum_curve(wavelength, [0.0, 15000.0, 0.0])
        y_min, y_max = window.spectrum_curve_axis.get_ylim()

        self.assertEqual(y_min, 0.0)
        self.assertGreater(y_max, 15000.0)
        window.close()

    def test_spectrum_peak_labels_are_staggered_when_wavelengths_are_close(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        wavelength = [868.0, 869.0, 870.0, 871.0, 871.7, 872.4, 877.5, 878.5, 879.5]
        intensity = [0.0, 0.0, 1200.0, 0.0, 900.0, 0.0, 0.0, 15000.0, 0.0]

        window.update_spectrum_curve(wavelength, intensity)

        y_min, y_max = window.spectrum_curve_axis.get_ylim()
        y_span = y_max - y_min
        text_positions = {
            artist.get_text().split()[0]: artist.get_position()
            for artist in window.spectrum_peak_annotation_artists
            if hasattr(artist, "get_text")
        }
        self.assertIn("P2", text_positions)
        self.assertIn("P3", text_positions)
        self.assertGreaterEqual(abs(text_positions["P2"][1] - text_positions["P3"][1]), y_span * 0.07)
        window.close()

    def test_spectrum_peak_labels_spread_horizontally_when_low_peaks_are_close(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        wavelength = [848.0, 868.8, 869.272, 869.8, 869.9, 870.247, 870.8, 877.5, 878.518, 879.5, 908.0]
        intensity = [0.0, 0.0, 300.0, 0.0, 0.0, 400.0, 0.0, 0.0, 7800.0, 0.0, 0.0]

        window.update_spectrum_curve(wavelength, intensity)

        x_min, x_max = window.spectrum_curve_axis.get_xlim()
        x_span = x_max - x_min
        text_positions = {
            artist.get_text().split()[0]: artist.get_position()
            for artist in window.spectrum_peak_annotation_artists
            if hasattr(artist, "get_text")
        }
        self.assertIn("P2", text_positions)
        self.assertIn("P3", text_positions)
        self.assertGreaterEqual(abs(text_positions["P2"][0] - text_positions["P3"][0]), x_span * 0.035)
        window.close()

    def test_spectrum_peak_labels_split_left_and_right_for_adjacent_peaks(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        wavelength = [848.0, 868.8, 869.272, 869.8, 869.9, 870.247, 870.8, 877.5, 878.518, 879.5, 908.0]
        intensity = [0.0, 0.0, 300.0, 0.0, 0.0, 400.0, 0.0, 0.0, 7800.0, 0.0, 0.0]

        window.update_spectrum_curve(wavelength, intensity)

        centroids = {item.label: item.centroid_nm for item in window.spectrum_peak_annotations}
        text_positions = {
            artist.get_text().split()[0]: artist.get_position()
            for artist in window.spectrum_peak_annotation_artists
            if hasattr(artist, "get_text")
        }
        text_alignments = {
            artist.get_text().split()[0]: artist.get_ha()
            for artist in window.spectrum_peak_annotation_artists
            if hasattr(artist, "get_text")
        }
        self.assertLess(centroids["P3"], centroids["P2"])
        self.assertLess(text_positions["P3"][0], centroids["P3"])
        self.assertGreater(text_positions["P2"][0], centroids["P2"])
        self.assertEqual(text_alignments["P3"], "right")
        self.assertEqual(text_alignments["P2"], "left")
        window.close()


    def test_collect_settings_uses_selected_detected_devices(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        power_option = PowerMeterOption("ASRL9::INSTR", "Caihuang CHLP-P", "OK")
        spectrometer_option = SpectrometerOption(321)
        window.power_meter_combo.clear()
        window.power_meter_combo.addItem(power_option.label(), power_option)
        window.spectrometer_combo.clear()
        window.spectrometer_combo.addItem(spectrometer_option.label(), spectrometer_option)

        settings = window.collect_settings()

        self.assertEqual(settings.power_resource, "ASRL9::INSTR")
        self.assertEqual(settings.spectrometer_device_id, 321)
        window.close()

    def test_stable_power_schedules_one_automatic_vout_read_after_five_seconds(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.active_output_current_a = 3.0
        window.pending_stable_point_current_a = 3.0
        window.pending_stable_point_generation = 7

        window.on_power_meter_reading(
            PowerMeterReading(1.0, 10.0, True, 0.01, 3.0, stability_generation=7)
        )

        self.assertEqual(window.pending_auto_vout_current_a, 3.0)
        self.assertEqual(window.pending_auto_vout_generation, 7)
        self.assertTrue(window.auto_vout_timer.isActive())
        self.assertGreaterEqual(window.auto_vout_timer.remainingTime(), 4900)
        window.close()

    def test_automatic_test_moves_to_voltage_wait_after_power_becomes_stable(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.automatic_test_state = AutomaticTestState.WAITING_STABLE
        window.active_output_current_a = 3.0
        window.pending_stable_point_current_a = 3.0
        window.pending_stable_point_generation = 7

        window.on_power_meter_reading(
            PowerMeterReading(1.0, 10.0, True, 0.01, 3.0, stability_generation=7)
        )

        self.assertEqual(window.automatic_test_state, AutomaticTestState.WAITING_VOLTAGE)
        self.assertTrue(window.auto_vout_timer.isActive())
        window.automatic_test_state = AutomaticTestState.IDLE
        window.close()

    def test_point_timeout_pauses_automatic_test_without_changing_current(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.active_output_current_a = 7.0
        window.automatic_test_state = AutomaticTestState.WAITING_STABLE

        window.on_automatic_point_timeout()

        self.assertEqual(window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertEqual(window.active_output_current_a, 7.0)
        self.assertIn("超时", window.automatic_test_status_label.text())
        window.automatic_test_state = AutomaticTestState.IDLE
        window.close()

    def test_automatic_vout_read_is_cancelled_when_power_becomes_unstable(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.active_output_current_a = 3.0
        window.pending_stable_point_current_a = 3.0
        window.pending_stable_point_generation = 7
        window.on_power_meter_reading(
            PowerMeterReading(1.0, 10.0, True, 0.01, 3.0, stability_generation=7)
        )

        window.on_power_meter_reading(
            PowerMeterReading(1.2, 10.5, False, 0.20, 3.0, stability_generation=7)
        )

        self.assertIsNone(window.pending_auto_vout_current_a)
        self.assertFalse(window.auto_vout_timer.isActive())
        window.close()

    def test_automatic_vout_read_is_rescheduled_when_power_becomes_stable_again(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.active_output_current_a = 3.0
        window.pending_stable_point_current_a = 3.0
        window.pending_stable_point_generation = 7
        window.on_power_meter_reading(
            PowerMeterReading(1.0, 10.0, True, 0.01, 3.0, stability_generation=7)
        )
        window.on_power_meter_reading(
            PowerMeterReading(1.2, 10.5, False, 0.20, 3.0, stability_generation=7)
        )

        window.on_power_meter_reading(
            PowerMeterReading(4.5, 10.0, True, 0.01, 3.0, stability_generation=7)
        )

        self.assertEqual(window.pending_auto_vout_current_a, 3.0)
        self.assertEqual(window.pending_auto_vout_generation, 7)
        self.assertTrue(window.auto_vout_timer.isActive())
        window.close()

    def test_automatic_vout_read_runs_only_for_the_active_stable_point(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.active_output_current_a = 3.0
        window.recorded_stable_point_current_a = 3.0
        window.recorded_stable_point_generation = 7
        window.pending_auto_vout_current_a = 3.0
        window.pending_auto_vout_generation = 7
        window.latest_power_meter_reading = PowerMeterReading(1.0, 10.0, True, 0.01, 3.0, stability_generation=7)
        automatic_calls: list[bool] = []
        window.read_output_voltage = lambda automatic=False: automatic_calls.append(automatic)

        window.on_auto_vout_timer_timeout()

        self.assertEqual(automatic_calls, [True])
        window.close()

    def test_automatic_voltage_read_failure_pauses_current_point(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakeController:
            is_connected = True

            def i2c_write_read(
                self,
                _address: int,
                _command: list[int],
                _length: int,
            ) -> tuple[bool, str]:
                return False, "I2C error"

        window = MainWindow()
        window.manual_ch341_controller = FakeController()
        window.active_output_current_a = 5.0
        window.automatic_test_state = AutomaticTestState.WAITING_VOLTAGE
        window.last_power_supply_command_monotonic_s = (
            combined_test_window.time.monotonic() - POWER_SUPPLY_COMMAND_MIN_INTERVAL_S - 0.1
        )
        old_critical = QMessageBox.critical
        try:
            QMessageBox.critical = lambda *args, **kwargs: QMessageBox.StandardButton.Ok  # type: ignore[method-assign]
            window.read_output_voltage(automatic=True)
        finally:
            QMessageBox.critical = old_critical  # type: ignore[method-assign]

        self.assertEqual(window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertEqual(window.active_output_current_a, 5.0)
        window.automatic_test_state = AutomaticTestState.IDLE
        window.close()

    def test_invalid_voltage_value_pauses_automatic_current_point(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.active_output_current_a = 5.0
        window.stable_power_points[5.0] = 50.0
        window.automatic_test_state = AutomaticTestState.WAITING_VOLTAGE

        window.record_efficiency_from_vout(0.0)

        self.assertEqual(window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertIn("大于 0", window.automatic_test_status_label.text())
        window.automatic_test_state = AutomaticTestState.IDLE
        window.close()

    def test_power_supply_commands_are_blocked_for_at_least_one_second(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakeController:
            is_connected = True

            def __init__(self) -> None:
                self.read_count = 0

            def i2c_write_read(self, _address: int, _command: list[int], _length: int) -> tuple[bool, list[int]]:
                self.read_count += 1
                return True, [0, 0, 0, 0]

        window = MainWindow()
        controller = FakeController()
        window.manual_ch341_controller = controller
        window.last_power_supply_command_monotonic_s = combined_test_window.time.monotonic()

        self.assertIsNone(window.execute_i2c_read([0xB4, 0x88, 0x00, 0x00], "Input voltage", "V"))
        self.assertEqual(controller.read_count, 0)

        window.last_power_supply_command_monotonic_s = (
            combined_test_window.time.monotonic() - POWER_SUPPLY_COMMAND_MIN_INTERVAL_S - 0.1
        )
        self.assertEqual(window.execute_i2c_read([0xB4, 0x88, 0x00, 0x00], "Input voltage", "V"), 0.0)
        self.assertEqual(controller.read_count, 1)
        window.close()

    def test_automatic_current_command_waits_for_guard_instead_of_pausing(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakeController:
            is_connected = True

            def __init__(self) -> None:
                self.writes: list[list[int]] = []

            def i2c_write(self, _address: int, command: list[int]) -> tuple[bool, str]:
                self.writes.append(command)
                return True, "OK"

        class ReaderStub:
            def reset_stability_window(self) -> int:
                return 2

        window = MainWindow()
        controller = FakeController()
        window.manual_ch341_controller = controller
        window.power_meter_reader = ReaderStub()  # type: ignore[assignment]
        window.automatic_test_currents = (4.0,)
        window.automatic_test_current_index = 0
        window.automatic_test_settings = window.collect_automatic_test_settings()
        window.last_power_supply_command_monotonic_s = combined_test_window.time.monotonic()

        window.begin_automatic_current_point()

        self.assertEqual(window.automatic_test_state, AutomaticTestState.SETTING_CURRENT)
        self.assertEqual(controller.writes, [])
        self.assertTrue(window.automatic_command_timer.isActive())

        window.last_power_supply_command_monotonic_s = (
            combined_test_window.time.monotonic() - POWER_SUPPLY_COMMAND_MIN_INTERVAL_S - 0.1
        )
        window.on_automatic_command_timer_timeout()
        self.assertEqual(controller.writes, [[0xB4, 0xFF, 0x04, 0x00]])
        self.assertEqual(window.automatic_test_state, AutomaticTestState.WAITING_STABLE)
        window.automatic_test_state = AutomaticTestState.IDLE
        window.power_meter_reader = None
        window.close()

    def test_temperature_read_uses_lpower_temperature_command(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        calls: list[tuple[list[int], str, str]] = []
        window.execute_i2c_read = lambda command, name, unit: calls.append((command, name, unit))

        window.read_temperature()

        self.assertEqual(calls, [([0xB4, 0x8D, 0x00, 0x00], "模块温度", "°C")])
        window.close()

    def test_auto_detect_spectrometers_keeps_auto_select_as_current_choice(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakeOceanSpectrometer:
            @staticmethod
            def detect() -> list[int]:
                return [41]

        old_loader = combined_test_window.load_spectrometer_components
        try:
            combined_test_window.load_spectrometer_components = lambda root: (FakeOceanSpectrometer, None)
            window = MainWindow()

            window.auto_detect_spectrometers()

            self.assertIsNone(window.spectrometer_combo.itemData(0))
            self.assertEqual(window.spectrometer_combo.itemText(0), "自动选择第一台 Ocean Insight")
            self.assertIsInstance(window.spectrometer_combo.itemData(1), SpectrometerOption)
            self.assertIsNone(window.spectrometer_combo.currentData())
            self.assertIsNone(window.collect_spectrometer_settings().device_id)
            window.close()
        finally:
            combined_test_window.load_spectrometer_components = old_loader

    def test_main_window_exposes_manual_device_action_buttons(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        for attribute in (
            "connect_i2c_button",
            "read_input_voltage_button",
            "read_output_voltage_button",
            "read_output_current_button",
            "read_temperature_button",
            "apply_current_button",
            "refresh_power_meter_button",
            "rel_zero_check",
            "copy_spectrum_button",
            "save_spectrum_button",
        ):
            self.assertTrue(hasattr(window, attribute), attribute)

        window.close()

    def test_power_meter_common_action_buttons_stay_in_power_meter_group(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        form = self._group(window, "功率计").layout()
        self.assertIsInstance(form, QFormLayout)

        for widget in (
            window.refresh_power_meter_button,
            window.rel_zero_check,
        ):
            self._form_row_containing_widget(form, widget)

        window.close()

    def test_main_window_exposes_independent_acquisition_buttons(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        for attribute in (
            "start_power_meter_button",
            "stop_power_meter_button",
            "start_spectrometer_button",
            "stop_spectrometer_button",
        ):
            self.assertTrue(hasattr(window, attribute), attribute)

        self.assertFalse(hasattr(window, "start_button"))
        self.assertFalse(hasattr(window, "stop_button"))
        window.close()

    def test_manual_power_supply_controls_stay_enabled_during_acquisition(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        window.set_power_meter_running_state(True)
        window.set_spectrometer_running_state(True)

        for widget in (
            window.connect_i2c_button,
            window.read_input_voltage_button,
            window.read_output_voltage_button,
            window.read_output_current_button,
            window.read_temperature_button,
            window.apply_current_button,
        ):
            self.assertTrue(widget.isEnabled())

        self.assertTrue(window.start_power_meter_button.isHidden())
        self.assertFalse(window.stop_power_meter_button.isHidden())
        self.assertTrue(window.start_spectrometer_button.isHidden())
        self.assertFalse(window.stop_spectrometer_button.isHidden())
        window.close()

    def test_automatic_test_locks_manual_power_commands_but_allows_reconnect_while_paused(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        window.set_automatic_test_state(AutomaticTestState.WAITING_STABLE, "等待稳定")

        for widget in (
            window.connect_i2c_button,
            window.apply_current_button,
            window.read_input_voltage_button,
            window.read_output_voltage_button,
            window.read_output_current_button,
            window.read_temperature_button,
            window.stable_window_spin,
            window.start_all_button,
        ):
            self.assertFalse(widget.isEnabled(), widget.objectName())

        window.set_automatic_test_state(AutomaticTestState.PAUSED, "连接中断")
        self.assertTrue(window.connect_i2c_button.isEnabled())
        self.assertFalse(window.apply_current_button.isEnabled())
        window.automatic_test_state = AutomaticTestState.IDLE
        window.close()

    def test_start_stop_buttons_show_only_current_action(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertFalse(window.start_power_meter_button.isHidden())
        self.assertTrue(window.stop_power_meter_button.isHidden())
        self.assertFalse(window.start_spectrometer_button.isHidden())
        self.assertTrue(window.stop_spectrometer_button.isHidden())

        window.set_power_meter_running_state(True)
        window.set_spectrometer_running_state(True)

        self.assertTrue(window.start_power_meter_button.isHidden())
        self.assertFalse(window.stop_power_meter_button.isHidden())
        self.assertTrue(window.start_spectrometer_button.isHidden())
        self.assertFalse(window.stop_spectrometer_button.isHidden())
        window.close()

    def test_normal_acquisition_stop_requests_thread_exit_without_blocking_the_ui(self) -> None:
        app = QApplication.instance() or QApplication([])

        class ReaderStub:
            def __init__(self) -> None:
                self.stopped = False

            def stop(self) -> None:
                self.stopped = True

            def wait(self, _timeout: int) -> None:
                raise AssertionError("normal UI stop must not wait")

        window = MainWindow()
        reader = ReaderStub()
        window.power_meter_reader = reader  # type: ignore[assignment]

        window.stop_power_meter()

        self.assertTrue(reader.stopped)
        window.power_meter_reader = None
        window.close()

    def test_power_meter_wavelength_accepts_decimal_values(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertIsInstance(window.power_wavelength_spin, QDoubleSpinBox)
        self.assertGreaterEqual(window.power_wavelength_spin.decimals(), 1)

        window.power_wavelength_spin.setValue(976.5)

        self.assertAlmostEqual(window.collect_settings().power_meter_wavelength_nm, 976.5)
        self.assertAlmostEqual(window.collect_power_meter_settings().wavelength_nm, 976.5)
        window.close()

    def test_spectrometer_start_stop_buttons_are_below_integration(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        form = self._spectrometer_form(window)

        integration_row = self._form_row_containing_widget(form, window.integration_spin)
        start_row = self._form_row_containing_widget(form, window.start_spectrometer_button)
        stop_row = self._form_row_containing_widget(form, window.stop_spectrometer_button)

        self.assertGreater(start_row, integration_row)
        self.assertEqual(start_row, stop_row)
        window.close()

    def test_spectrometer_default_integration_time_is_10000_us(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertEqual(window.integration_spin.value(), 10000)
        self.assertEqual(window.collect_spectrometer_settings().integration_time_us, 10000)
        window.close()

    def test_power_meter_detecting_state_does_not_block_manual_power_supply_controls(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        window.set_power_meter_detecting_state(True)

        self.assertFalse(window.detect_power_meter_button.isEnabled())
        self.assertFalse(window.refresh_power_meter_button.isEnabled())
        self.assertFalse(window.start_power_meter_button.isEnabled())
        for widget in (
            window.connect_i2c_button,
            window.read_input_voltage_button,
            window.read_output_voltage_button,
            window.read_output_current_button,
            window.read_temperature_button,
            window.apply_current_button,
        ):
            self.assertTrue(widget.isEnabled())

        window.set_power_meter_detecting_state(False)
        self.assertTrue(window.detect_power_meter_button.isEnabled())
        self.assertTrue(window.refresh_power_meter_button.isEnabled())
        self.assertTrue(window.start_power_meter_button.isEnabled())
        window.close()


class SpectrumPeakAnnotationTests(unittest.TestCase):
    def test_find_spectrum_peak_annotations_returns_top_three_centroids_by_peak_height(self) -> None:
        wavelength = [850.0, 851.0, 852.0, 853.0, 854.0, 855.0, 856.0, 857.0, 858.0, 859.0, 860.0, 861.0, 862.0]
        intensity = [0.0, 5.0, 80.0, 5.0, 0.0, 10.0, 300.0, 10.0, 0.0, 20.0, 200.0, 20.0, 0.0]

        annotations = combined_test_spectrum.find_spectrum_peak_annotations(list(zip(wavelength, intensity)))

        self.assertEqual(
            [(item.label, round(item.centroid_nm, 3), round(item.peak_intensity, 1)) for item in annotations],
            [("P1", 856.0, 300.0), ("P2", 860.0, 200.0), ("P3", 852.0, 80.0)],
        )

    def test_saturation_detector_requires_a_consecutive_near_full_scale_plateau(self) -> None:
        saturated = combined_test_spectrum.detect_spectrum_saturation([0.0, 16000.0, 16020.0, 16010.0, 0.0])
        spike = combined_test_spectrum.detect_spectrum_saturation([0.0, 17000.0, 0.0])

        self.assertTrue(saturated.saturated)
        self.assertEqual(saturated.consecutive_pixels, 3)
        self.assertFalse(spike.saturated)


class PowerMeterDetectThreadTests(unittest.TestCase):
    def test_power_meter_detect_thread_probes_selected_port_first_with_short_timeout(self) -> None:
        calls: list[tuple[str, int]] = []

        class FakeResourceManager:
            def list_resources(self) -> tuple[str, str]:
                return ("ASRL1::INSTR", "ASRL2::INSTR")

            def close(self) -> None:
                pass

        class FakeCaihuangPowerMeter:
            @staticmethod
            def probe(resource: str, timeout_ms: int = 1000) -> object | None:
                calls.append((resource, timeout_ms))
                if resource == "ASRL2::INSTR":
                    return types.SimpleNamespace(
                        resource=resource,
                        device_type="Caihuang CHLP-P",
                        detail="OK",
                    )
                return None

        old_modules = dict(sys.modules)
        try:
            sys.modules["pyvisa"] = types.SimpleNamespace(ResourceManager=lambda: FakeResourceManager())
            sys.modules["tools.power_meter_mvp"] = types.SimpleNamespace(CaihuangPowerMeter=FakeCaihuangPowerMeter)
            thread = combined_test_devices.PowerMeterDetectThread("ASRL2::INSTR")
            detected: list[PowerMeterOption] = []
            statuses: list[str] = []
            thread.detected.connect(lambda options: detected.extend(options))
            thread.status.connect(statuses.append)

            thread.run()

            self.assertEqual(calls[0], ("ASRL2::INSTR", combined_test_devices.POWER_METER_PROBE_TIMEOUT_MS))
            self.assertEqual(calls[1], ("ASRL1::INSTR", combined_test_devices.POWER_METER_PROBE_TIMEOUT_MS))
            self.assertEqual([option.resource for option in detected], ["ASRL2::INSTR"])
            self.assertIn("检测功率计", statuses[0])
        finally:
            sys.modules.clear()
            sys.modules.update(old_modules)


class AcquisitionReadySignalTests(unittest.TestCase):
    def test_power_meter_reader_reports_ready_after_device_configuration(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakePowerMeter:
            def __init__(self, _resource: str) -> None:
                pass

            def test(self) -> str:
                return "OK"

            def set_wavelength(self, _wavelength_nm: float) -> None:
                pass

            def read_power_w(self) -> float:
                raise RuntimeError("stop test loop")

            def close(self) -> None:
                pass

        old_meter_class = power_meter_mvp.CaihuangPowerMeter
        try:
            power_meter_mvp.CaihuangPowerMeter = FakePowerMeter
            reader = combined_test_devices.PowerMeterReaderThread(
                PowerMeterSettings("ASRL1::INSTR", 976.0, 1.0, 300, 3.0, 0.15)
            )
            ready_events: list[bool] = []
            reader.ready.connect(lambda: ready_events.append(True))

            reader.run()

            self.assertEqual(ready_events, [True])
        finally:
            power_meter_mvp.CaihuangPowerMeter = old_meter_class


class SpectrometerDeviceOpeningTests(unittest.TestCase):
    def test_open_spectrometer_device_falls_back_when_selected_runtime_id_changed(self) -> None:
        class FakeControl:
            def __init__(self) -> None:
                self.opened_device_id: int | None = None

            def find_usb_devices(self) -> int:
                return 0

            def get_device_ids(self) -> list[int]:
                return [8]

            def open_device(self, device_id: int) -> int:
                self.opened_device_id = device_id
                return 0

        spectrometer = types.SimpleNamespace(control=FakeControl(), device_id=None)

        device_id = combined_test_devices.open_spectrometer_device(spectrometer, selected_device_id=7)

        self.assertEqual(device_id, 8)
        self.assertEqual(spectrometer.device_id, 8)
        self.assertEqual(spectrometer.control.opened_device_id, 8)


class PowerMeterCommandFormattingTests(unittest.TestCase):
    def test_format_wavelength_keeps_decimal_only_when_needed(self) -> None:
        self.assertEqual(power_meter_mvp.format_wavelength_nm(976.0), "976")
        self.assertEqual(power_meter_mvp.format_wavelength_nm(976.5), "976.5")
        self.assertEqual(power_meter_mvp.format_wavelength_nm(976.125), "976.125")


class DeviceOptionTests(unittest.TestCase):
    def test_power_meter_option_label_includes_model_resource_and_detail(self) -> None:
        option = PowerMeterOption(
            resource="ASRL4::INSTR",
            device_type="Caihuang CHLP-P",
            detail="OK, version 1.2",
        )

        self.assertEqual(option.label(), "Caihuang CHLP-P | ASRL4::INSTR | OK, version 1.2")

    def test_spectrometer_option_label_includes_ocean_model_and_device_id(self) -> None:
        option = SpectrometerOption(device_id=123)

        self.assertEqual(option.label(), "Ocean Insight | 设备 ID 123")


class LocalSpectrometerLoadingTests(unittest.TestCase):
    def test_sth_eb314_launcher_uses_named_conda_environment(self) -> None:
        launcher = Path(__file__).resolve().parents[1] / "run_combined_test_sth_eb314.bat"

        self.assertTrue(launcher.exists())
        content = launcher.read_text(encoding="utf-8")
        self.assertIn("sth_eb314", content)
        self.assertIn("main.py", content)

    def test_load_spectrometer_components_ignores_legacy_root_and_does_not_import_application(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            application_dir = root / "application"
            application_dir.mkdir()
            (application_dir / "__init__.py").write_text("raise AssertionError('application imported')\n", encoding="utf-8")

            old_path = list(sys.path)
            old_modules = dict(sys.modules)
            old_cwd = Path.cwd()
            try:
                ocean_spectrometer, calculate_stats = combined_test_devices.load_spectrometer_components(root)
                cached_spectrometer, cached_calculate_stats = combined_test_devices.load_spectrometer_components(root)

                self.assertIn("combined_local_spectrometer_mvp", ocean_spectrometer.__module__)
                self.assertEqual(calculate_stats.__module__, "combined_test.spectrum_math")
                self.assertIs(cached_spectrometer, ocean_spectrometer)
                self.assertIs(cached_calculate_stats, calculate_stats)
                self.assertEqual(sys.path, old_path)
                self.assertEqual(Path.cwd(), Path(__file__).resolve().parents[1])
                self.assertNotIn(str(root.resolve()), sys.path)
                self.assertNotIn("application", sys.modules)
            finally:
                os.chdir(old_cwd)
                sys.path[:] = old_path
                for name in list(sys.modules):
                    if name.startswith("application") or "combined_local_spectrometer_mvp" in name:
                        sys.modules.pop(name, None)
                sys.modules.update(
                    {
                        key: value
                        for key, value in old_modules.items()
                        if key.startswith("application") or "combined_local_spectrometer_mvp" in key
                    }
                )


if __name__ == "__main__":
    unittest.main()
