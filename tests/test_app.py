import math
import tempfile
import sys
import os
import re
import threading
import types
import unittest
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QWidget,
)

from combined_test import devices as combined_test_devices
from combined_test import spectrum as combined_test_spectrum
from combined_test import window as combined_test_window
from combined_test.automatic_controller import AutomaticTestTerminalOutcome
from combined_test.automation import AutomaticTestState
from combined_test.excel_export import ExcelTestRecord
from combined_test.models import (
    LiveReading,
    PowerMeterOption,
    PowerMeterReading,
    PowerMeterSettings,
    SpectrometerOption,
    SpectrometerReading,
    SpectrometerSettings,
)
from combined_test.persistence import (
    build_spectrum_csv_path,
    save_spectrum_curve,
)
from combined_test.plots import PlotLayoutContext
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
    def setUp(self) -> None:
        """Keep GUI tests independent from the operator's registry settings."""
        self._settings_temp_dir = tempfile.TemporaryDirectory()
        self._settings_index = 0
        self._original_window_qsettings = combined_test_window.QSettings

        def isolated_settings(*_args: object, **_kwargs: object) -> QSettings:
            self._settings_index += 1
            path = Path(self._settings_temp_dir.name) / f"window-{self._settings_index}.ini"
            return QSettings(str(path), QSettings.Format.IniFormat)

        combined_test_window.QSettings = isolated_settings  # type: ignore[assignment]

    def tearDown(self) -> None:
        combined_test_window.QSettings = self._original_window_qsettings  # type: ignore[assignment]
        self._settings_temp_dir.cleanup()

    def test_tdk_efficiency_uses_line_compensated_load_voltage(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.power_supply_controller_kind = "tdk"
        window.active_output_current_a = 10.0
        window.stable_power_points[10.0] = 80.0
        queued_points: list[tuple[float, float, float, float]] = []
        window.queue_excel_test_point = (  # type: ignore[method-assign]
            lambda current, voltage, power, efficiency: queued_points.append(
                (current, voltage, power, efficiency)
            )
            or False
        )

        window.record_efficiency_from_vout(29.656)

        corrected_voltage = 29.400957142857145
        self.assertAlmostEqual(window.efficiency_voltage_points[10.0], corrected_voltage)
        self.assertAlmostEqual(
            window.efficiency_points[10.0],
            80.0 / 10.0 / corrected_voltage * 100.0,
        )
        self.assertAlmostEqual(queued_points[0][1], corrected_voltage)
        window.close()

    def test_ch341_efficiency_keeps_the_raw_voltage(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.power_supply_controller_kind = "ch341"
        window.active_output_current_a = 10.0
        window.stable_power_points[10.0] = 80.0

        window.record_efficiency_from_vout(29.656)

        self.assertEqual(window.efficiency_voltage_points[10.0], 29.656)
        window.close()

    def test_driver_errors_are_translated_for_operators(self) -> None:
        resource_missing = combined_test_window.user_facing_error_message(
            "VI_ERROR_RSRC_NFOUND (-1073807343): Insufficient location information "
            "or the requested device or resource is not present in the system."
        )
        self.assertIn("未找到指定的设备或通信资源", resource_missing)
        self.assertIn("请检查设备是否已连接", resource_missing)
        self.assertNotIn("Insufficient location information", resource_missing)

        self.assertIn(
            "设备通信超时",
            combined_test_window.user_facing_error_message("VI_ERROR_TMO: Timeout expired"),
        )
        self.assertIn(
            "其他程序占用",
            combined_test_window.user_facing_error_message("Access is denied"),
        )

    def test_power_meter_error_popup_uses_translated_message(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        captured: list[tuple[str, str]] = []
        old_critical = QMessageBox.critical
        try:
            QMessageBox.critical = (  # type: ignore[method-assign]
                lambda _parent, title, message: captured.append((title, message))
            )
            window.on_power_meter_failed(
                "VI_ERROR_RSRC_NFOUND (-1073807343): requested device or resource is not present"
            )
        finally:
            QMessageBox.critical = old_critical  # type: ignore[method-assign]

        self.assertEqual(captured[0][0], "功率计错误")
        self.assertIn("未找到指定的设备或通信资源", captured[0][1])
        self.assertNotIn("requested device", captured[0][1])
        window.close()

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
            self.assertTrue(window.automatic_test_toggle.isChecked())
            self.assertTrue(window.automatic_test_toggle.isHidden())
            self.assertFalse(window.automatic_test_content.isHidden())
            self.assertEqual(window.auto_initial_current_spin.value(), 1.0)
            self.assertEqual(window.auto_target_current_spin.value(), 20.0)
            self.assertEqual(window.auto_current_step_spin.value(), 1.0)
            self.assertEqual(window.auto_point_timeout_spin.value(), 120.0)
            self.assertEqual(window.auto_ramp_down_step_spin.value(), 5.0)
            self.assertEqual(window.auto_ramp_down_interval_spin.value(), 1.1)
            self.assertTrue(window.auto_use_spectrometer_check.isChecked())
            self.assertFalse(window.start_automatic_test_button.isEnabled())
            self.assertFalse(window.retry_automatic_test_button.isEnabled())
            self.assertFalse(window.end_automatic_test_button.isEnabled())
            self.assertTrue(window.safety_settings_content.isHidden())
            self.assertEqual(window.automatic_stack.currentIndex(), window.automatic_prepare_index)
            for spin_box in (
                window.auto_initial_current_spin,
                window.auto_target_current_spin,
                window.auto_current_step_spin,
                window.stable_window_spin,
            ):
                self.assertLessEqual(spin_box.maximumWidth(), 180)
                self.assertTrue(spin_box.accessibleName())
            window.close()

    def test_preflight_checklist_explains_blocker_and_enables_only_when_ready(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertFalse(window.start_automatic_test_button.isEnabled())
        self.assertIn("SN", window.preflight_blocker_label.text())

        class ConnectedController:
            is_connected = True

        window.manual_ch341_controller = ConnectedController()
        window.sn_field.setText("ARP-20260714-001")
        window.refresh_preflight_checklist()

        self.assertFalse(window.start_automatic_test_button.isEnabled())
        self.assertIn("测试站别", window.preflight_blocker_label.text())

        window.test_station_field.setText("老化站 1")
        window.refresh_preflight_checklist()

        self.assertTrue(window.start_automatic_test_button.isEnabled())
        self.assertIn("配置已完成", window.preflight_blocker_label.text())
        self.assertIn("共 20 点", window.preflight_sequence_label.text())
        window.manual_ch341_controller = None
        window.close()

    def test_automatic_state_switches_from_prepare_to_run_page(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.automatic_test_currents = (1.0, 2.0, 3.0)
        window.automatic_test_current_index = 1

        class ReadyReader:
            is_ready = True

        window.power_meter_reader = ReadyReader()  # type: ignore[assignment]
        window.spectrometer_reader = ReadyReader()  # type: ignore[assignment]

        window.set_automatic_test_state(AutomaticTestState.WAITING_STABLE, "等待功率稳定")

        self.assertEqual(window.automatic_stack.currentIndex(), window.automatic_run_index)
        self.assertEqual(window.main_tabs.currentIndex(), window.automatic_tab_index)
        self.assertEqual(window.run_progress_label.text(), "2 / 3 点")
        self.assertEqual(window.run_current_label.text(), "当前 2.0 A")
        self.assertEqual(window.run_stage_label.text(), "等待稳定")
        self.assertIn("2/3", window.global_progress_label.text())
        self.assertTrue(window.pause_automatic_test_button.isEnabled())
        self.assertFalse(window.main_tabs.isTabEnabled(window.manual_tab_index))
        self.assertTrue(window.main_tabs.isTabEnabled(window.pd_tab_index))
        window.main_tabs.setCurrentIndex(window.pd_tab_index)
        self.assertEqual(window.main_tabs.currentIndex(), window.pd_tab_index)
        window.set_automatic_test_state(AutomaticTestState.WAITING_VOLTAGE, "读取输出电压")
        self.assertEqual(window.main_tabs.currentIndex(), window.pd_tab_index)
        self.assertTrue(window.stop_power_meter_button.isHidden())
        self.assertTrue(window.stop_spectrometer_button.isHidden())

        window.toggle_automatic_pause()

        self.assertEqual(window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertTrue(window.pause_automatic_test_button.isHidden())
        self.assertFalse(window.retry_automatic_test_button.isHidden())
        self.assertEqual(window.retry_automatic_test_button.text(), "修复后重试当前点")
        self.assertTrue(window.main_tabs.isTabEnabled(window.manual_tab_index))
        self.assertTrue(window.stop_power_meter_button.isHidden())
        window.power_meter_reader = None
        window.spectrometer_reader = None
        window.automatic_test_state = AutomaticTestState.IDLE
        window.close()

    def test_saving_point_cannot_be_paused_by_operator(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.automatic_test_currents = (4.0,)
        window.automatic_test_current_index = 0

        window.set_automatic_test_state(AutomaticTestState.SAVING_POINT, "正在保存 4.0 A 测试点")

        self.assertFalse(window.pause_automatic_test_button.isEnabled())
        window.toggle_automatic_pause()
        window.pause_automatic_test("操作者暂停", operator_requested=True)
        self.assertEqual(window.automatic_test_state, AutomaticTestState.SAVING_POINT)
        self.assertFalse(window.automatic_pause_safety_timer.isActive())
        window.automatic_test_state = AutomaticTestState.IDLE
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
            window.test_station_field.setText("老化站 1")
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

    def test_automatic_test_without_spectrometer_starts_after_power_meter_is_ready(self) -> None:
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
            window.auto_use_spectrometer_check.setChecked(False)
            window.sn_field.setText("AUTO-NO-SPECTRUM")
            window.test_station_field.setText("老化站 1")
            window.output_dir_field.setText(temp_dir)
            spectrometer_starts: list[bool] = []
            window.start_spectrometer = lambda: spectrometer_starts.append(True)  # type: ignore[method-assign]

            window.start_automatic_test()
            window.on_power_meter_ready()

            self.assertEqual(spectrometer_starts, [])
            self.assertEqual(controller.writes, [[0xB4, 0xFF, 0x01, 0x00]])
            self.assertEqual(window.automatic_test_state, AutomaticTestState.WAITING_STABLE)
            self.assertIn("功率稳定", window.automatic_test_status_label.text())
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

    def test_power_reading_displays_continuous_entry_and_hysteresis_spans(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        window.on_power_meter_reading(PowerMeterReading(1.0, 150.0, False, 0.2, 1.0))

        self.assertTrue(window.stable_tolerance_spin.isReadOnly())
        self.assertEqual(window.stable_tolerance_spin.value(), 0.30)
        self.assertIn("判稳 ≤0.3000 W", window.stability_tolerance_label.text())
        self.assertIn("稳定保持 ≤0.4500 W", window.stability_tolerance_label.text())
        self.assertIn("≤ 0.3000 W", window.live_plots.stability_detail_text.get_text())

        window.on_power_meter_reading(
            PowerMeterReading(
                4.0,
                150.0,
                True,
                0.4,
                3.0,
                stable_tolerance_w=0.45,
            )
        )

        self.assertEqual(window.live_plots.stability_status_text.get_text(), "STABLE")
        self.assertIn("≤ 0.4500 W", window.live_plots.stability_detail_text.get_text())
        self.assertIsNotNone(window.live_plots._stable_region_artist)
        self.assertEqual(
            window.live_plots.power_curve_line.get_color(),
            window.live_plots._stable_line_color,
        )
        window.close()

    def test_non_finite_centroid_resets_wavelength_hysteresis(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        detector = window.wavelength_stability_detector
        for elapsed_s in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.1):
            result = detector.add_sample(elapsed_s, 976.0)
        self.assertTrue(result.stable)
        self.assertAlmostEqual(detector.active_tolerance_w, 0.3)

        window.on_spectrometer_reading(
            SpectrometerReading(
                peak_wavelength_nm=math.nan,
                centroid_nm=math.nan,
                fwhm_nm=math.nan,
            )
        )

        self.assertFalse(window.latest_wavelength_stable)
        self.assertAlmostEqual(detector.active_tolerance_w, 0.2)
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
            first_window.test_station_field.setText("老化站 1")
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
            self.assertEqual(restored_window.test_station_field.text(), "老化站 1")
            self.assertEqual(restored_window.output_dir_field.text(), str(Path(temp_dir) / "records"))
            restored_window.close()

    def test_excel_test_point_saves_liv_and_spectrum_in_one_workbook(self) -> None:
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(QSettings(str(Path(temp_dir) / "inputs.ini"), QSettings.Format.IniFormat))
            window.excel_workbook_path = Path(temp_dir) / "SN001_2026_07_10_14_30_25.xlsx"
            window.test_session_station = "老化站 1"
            window.latest_spectrum_wavelength = [974.0, 975.0, 976.0, 977.0, 978.0]
            window.latest_spectrum_intensity = [0.0, 5000.0, 10000.0, 5000.0, 0.0]

            window.queue_excel_test_point(3.0, 50.5, 33.0, 33.0 / 3.0 / 50.5)
            self.assertEqual(window.pending_excel_records[3.0].test_station, "老化站 1")
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
            window.latest_spectrum_intensity = [0.0, 5000.0, 10000.0, 5000.0, 0.0]
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

    def test_tdk_ramp_down_write_failure_falls_back_to_output_off(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FailedCurrentController:
            is_connected = True
            output_enabled = True

            def __init__(self) -> None:
                self.output_commands: list[bool] = []

            def i2c_write(self, _address: int, _command: list[int]) -> tuple[bool, str]:
                return False, "PC command rejected: E01"

            def set_output_enabled(self, enabled: bool) -> None:
                self.output_commands.append(enabled)
                self.output_enabled = enabled

        window = MainWindow()
        controller = FailedCurrentController()
        window.manual_ch341_controller = controller
        window.power_supply_controller_kind = "tdk"
        window.automatic_test_settings = window.collect_automatic_test_settings()
        window.active_output_current_a = 4.0
        window.automatic_test_state = AutomaticTestState.RAMPING_DOWN
        window.automatic_controller._set_pending_terminal_outcome(
            AutomaticTestTerminalOutcome.SUCCEEDED,
            "所有计划测试点均已保存",
        )
        window.last_power_supply_command_monotonic_s = (
            combined_test_window.time.monotonic() - POWER_SUPPLY_COMMAND_MIN_INTERVAL_S - 0.1
        )

        window.write_automatic_current(0.0, "ramp_down")

        self.assertEqual(controller.output_commands, [False])
        self.assertEqual(window.automatic_test_state, AutomaticTestState.COMPLETED)
        self.assertEqual(
            window.automatic_controller.terminal_outcome,
            AutomaticTestTerminalOutcome.ABORTED_SAFELY,
        )
        self.assertEqual(window.active_output_current_a, 0.0)
        self.assertEqual(window.set_current_spin.value(), 0.0)
        self.assertIn("直接关闭 TDK 输出", window.automatic_test_status_label.text())
        self.assertEqual(window.result_title_label.text(), "测试异常中止")
        self.assertIn("PC command rejected: E01", window.log_text.text())
        window.manual_ch341_controller = None
        window.close()

    def test_failed_tdk_output_shutdown_does_not_report_zero_current(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FailedOutputController:
            is_connected = True
            output_enabled = True

            def set_output_enabled(self, _enabled: bool) -> None:
                raise RuntimeError("OUT 0 timeout")

        window = MainWindow()
        controller = FailedOutputController()
        window.manual_ch341_controller = controller
        window.power_supply_controller_kind = "tdk"
        window.active_output_current_a = 4.0
        window.set_current_spin.setValue(4.0)
        window.automatic_test_state = AutomaticTestState.RAMPING_DOWN

        window.complete_automatic_test()

        self.assertEqual(window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertEqual(window.active_output_current_a, 4.0)
        self.assertEqual(window.set_current_spin.value(), 4.0)
        self.assertIn("TDK 输出关闭失败", window.automatic_test_status_label.text())
        window.manual_ch341_controller = None
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
        window.latest_wavelength_stable = True
        window.latest_wavelength_span_nm = 0.05
        window.last_power_supply_command_monotonic_s = (
            combined_test_window.time.monotonic() - POWER_SUPPLY_COMMAND_MIN_INTERVAL_S - 0.1
        )

        window.stop_all()

        self.assertEqual(window.automatic_test_state, AutomaticTestState.RAMPING_DOWN)
        self.assertEqual(controller.writes, [[0xB4, 0xFF, 0x05, 0x00]])
        window.automatic_ramp_down_timer.stop()
        window.automatic_test_state = AutomaticTestState.IDLE
        window.close()

    def test_manual_emergency_stop_immediately_sets_ch341_current_to_zero_and_stops_acquisition(self) -> None:
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
        window.active_output_current_a = 8.0
        window.set_current_spin.setValue(8.0)
        window.manual_power_tab_lock_active = True
        self.assertTrue(window.emergency_stop_button.isEnabled())
        stopped: list[str] = []
        window.stop_power_meter = lambda: stopped.append("power")  # type: ignore[method-assign]
        window.stop_spectrometer = lambda: stopped.append("spectrum")  # type: ignore[method-assign]
        window.pd_panel.stop_acquisition = lambda: stopped.append("pd")  # type: ignore[method-assign]
        # A recent routine command must not delay an emergency command.
        window.last_power_supply_command_monotonic_s = combined_test_window.time.monotonic()

        window.emergency_stop_button.click()

        self.assertEqual(controller.writes, [[0xB4, 0xFF, 0x00, 0x00]])
        self.assertEqual(window.active_output_current_a, 0.0)
        self.assertEqual(window.set_current_spin.value(), 0.0)
        self.assertFalse(window.manual_power_tab_lock_active)
        self.assertEqual(stopped, ["power", "spectrum", "pd"])
        self.assertIn("电源电流已归零", window.statusBar().currentMessage())
        window.manual_ch341_controller = None
        window.close()

    def test_manual_emergency_stop_closes_tdk_output_after_setting_zero_current(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakeTdkController:
            is_connected = True
            output_enabled = True

            def __init__(self) -> None:
                self.commands: list[str] = []

            def set_output_current(self, current_a: float) -> None:
                self.commands.append(f"current:{current_a:g}")

            def set_output_enabled(self, enabled: bool) -> None:
                self.commands.append(f"output:{int(enabled)}")
                self.output_enabled = enabled

        window = MainWindow()
        controller = FakeTdkController()
        window.manual_ch341_controller = controller
        window.power_supply_controller_kind = "tdk"
        window.active_output_current_a = 5.0
        window.set_current_spin.setValue(5.0)

        window.emergency_stop()

        self.assertEqual(controller.commands, ["current:0", "output:0"])
        self.assertFalse(controller.output_enabled)
        self.assertEqual(window.active_output_current_a, 0.0)
        self.assertEqual(window.tdk_output_status_label.text(), "输出关闭")
        self.assertIn("TDK 输出已关闭", window.statusBar().currentMessage())
        window.manual_ch341_controller = None
        window.close()

    def test_failed_manual_emergency_current_command_does_not_report_zero(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FailedController:
            is_connected = True

            def i2c_write(self, _address: int, _command: list[int]) -> tuple[bool, str]:
                return False, "write timeout"

        window = MainWindow()
        window.manual_ch341_controller = FailedController()
        window.active_output_current_a = 4.0
        window.set_current_spin.setValue(4.0)
        captured: list[tuple[str, str]] = []
        old_critical = QMessageBox.critical
        try:
            QMessageBox.critical = (  # type: ignore[method-assign]
                lambda _parent, title, message: captured.append((title, message))
            )
            window.emergency_stop()
        finally:
            QMessageBox.critical = old_critical  # type: ignore[method-assign]

        self.assertEqual(window.active_output_current_a, 4.0)
        self.assertEqual(window.set_current_spin.value(), 4.0)
        self.assertEqual(captured[0][0], "紧急停止")
        self.assertIn("电流置零失败", captured[0][1])
        self.assertIn("请立即检查电源面板", window.statusBar().currentMessage())
        window.manual_ch341_controller = None
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

    def test_closing_waits_asynchronously_for_running_acquisition_threads(self) -> None:
        app = QApplication.instance() or QApplication([])

        class ReaderStub:
            def __init__(self) -> None:
                self.stopped = False

            def stop(self) -> None:
                self.stopped = True

            def isRunning(self) -> bool:
                return True

            def wait(self, _timeout: int) -> None:
                raise AssertionError("close must not block the GUI while hardware I/O is still running")

        window = MainWindow()
        reader = ReaderStub()
        window.power_meter_reader = reader  # type: ignore[assignment]
        event = QCloseEvent()

        window.closeEvent(event)

        self.assertFalse(event.isAccepted())
        self.assertTrue(reader.stopped)
        self.assertTrue(window.close_after_background_tasks)
        self.assertTrue(window.background_stop_timeout_timer.isActive())
        window.background_stop_timeout_timer.stop()
        window.close_after_background_tasks = False
        window.power_meter_reader = None
        window.close()

    def test_close_timeout_allows_explicit_force_stop_of_hung_acquisition(self) -> None:
        app = QApplication.instance() or QApplication([])

        class HungReader:
            def __init__(self) -> None:
                self.running = True
                self.stopped = False
                self.terminated = False

            def stop(self) -> None:
                self.stopped = True

            def isRunning(self) -> bool:
                return self.running

            def terminate(self) -> None:
                self.terminated = True
                self.running = False

            def wait(self, _timeout: int) -> bool:
                return not self.running

        window = MainWindow()
        reader = HungReader()
        window.power_meter_reader = reader  # type: ignore[assignment]
        window._ask_background_stop_timeout_action = lambda _can_force: "force"  # type: ignore[method-assign]
        event = QCloseEvent()

        window.closeEvent(event)
        window.background_stop_timeout_timer.stop()
        window.on_background_stop_timeout()

        self.assertTrue(reader.stopped)
        self.assertTrue(reader.terminated)
        self.assertFalse(window.close_after_background_tasks)
        window.power_meter_reader = None
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

    def test_main_window_uses_mode_tabs_and_automatic_workflow_stack(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertIsInstance(window.centralWidget(), QWidget)
        self.assertIs(window.centralWidget(), window.central_shell)
        self.assertGreaterEqual(window.central_shell.layout().indexOf(window.main_tabs), 0)
        self.assertEqual(
            [window.main_tabs.tabText(index) for index in range(window.main_tabs.count())],
            ["自动测试", "手动调试", "当前记录", "PD 采集"],
        )
        self.assertEqual(window.main_tabs.currentIndex(), window.automatic_tab_index)
        self.assertEqual(window.automatic_stack.count(), 3)
        self.assertEqual(window.automatic_stack.currentIndex(), window.automatic_prepare_index)
        self.assertTrue(hasattr(window, "prepare_scroll_area"))
        self.assertTrue(hasattr(window, "left_control_panel"))
        self.assertTrue(hasattr(window, "manual_scroll_area"))
        self.assertTrue(hasattr(window, "monitor_panel"))
        window.close()

    def test_main_window_uses_left_navigation_while_retaining_internal_tabs(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertTrue(window.main_tabs.tabBar().isHidden())
        self.assertEqual(window.navigation_panel.minimumWidth(), 148)
        self.assertEqual(window.navigation_panel.maximumWidth(), 148)
        self.assertEqual(
            [button.text() for button in window.navigation_buttons.values()],
            ["自动测试", "手动调试", "当前记录", "PD 采集"],
        )

        window.navigation_buttons[window.manual_tab_index].click()
        self.assertEqual(window.main_tabs.currentIndex(), window.manual_tab_index)
        self.assertEqual(window.page_title_label.text(), "手动调试")
        window.close()

    def test_prepare_page_owns_routine_device_selection_without_tab_jump(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertIs(window.prepare_power_meter_combo.model(), window.power_meter_combo.model())
        self.assertIs(window.prepare_spectrometer_combo.model(), window.spectrometer_combo.model())
        self.assertEqual(window.main_tabs.currentIndex(), window.automatic_tab_index)

        tdk_index = window.prepare_power_supply_combo.findData("tdk")
        window.prepare_power_supply_combo.setCurrentIndex(tdk_index)

        self.assertEqual(window.power_supply_controller_combo.currentData(), "tdk")
        self.assertEqual(window.main_tabs.currentIndex(), window.automatic_tab_index)
        self.assertFalse(window.prepare_tdk_resource_combo.isHidden())
        self.assertNotEqual(window.prepare_power_meter_button.text(), "设置")
        window.close()

    def test_prepare_page_displays_power_meter_name_and_keeps_resource_as_data(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertIn("功率计（待检测）", window.prepare_power_meter_combo.currentText())
        self.assertEqual(window._selected_power_resource(), "ASRL3::INSTR")

        window.power_meter_combo.setEditText("ASRL8::INSTR")
        self.assertEqual(window._selected_power_resource(), "ASRL8::INSTR")
        window.close()

    def test_prepare_page_restores_detected_power_meter_name(self) -> None:
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = QSettings(str(Path(temp_dir) / "inputs.ini"), QSettings.Format.IniFormat)
            settings.setValue("input/power_resource", "ASRL3::INSTR")
            settings.setValue("input/power_meter_device_type", "LaserPoint")
            settings.setValue("input/power_meter_detail", "SN 123456")
            settings.setValue("input/power_meter_driver_kind", "laserpoint")

            window = MainWindow(settings)

            self.assertEqual(
                window.prepare_power_meter_combo.currentText(),
                "LaserPoint | ASRL3::INSTR | SN 123456",
            )
            self.assertEqual(window.collect_power_meter_settings().driver_kind, "laserpoint")
            window.close()

    def test_prepare_page_exposes_open_and_close_actions_for_measurement_devices(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertEqual(window.prepare_power_meter_open_button.text(), "打开")
        self.assertEqual(window.prepare_power_meter_close_button.text(), "关闭")
        self.assertEqual(window.prepare_spectrometer_open_button.text(), "打开")
        self.assertEqual(window.prepare_spectrometer_close_button.text(), "关闭")
        self.assertTrue(window.prepare_power_meter_open_button.isEnabled())
        self.assertFalse(window.prepare_power_meter_close_button.isEnabled())
        self.assertTrue(window.prepare_spectrometer_open_button.isEnabled())
        self.assertFalse(window.prepare_spectrometer_close_button.isEnabled())

        window.power_meter_reader = types.SimpleNamespace(is_ready=True)
        window.spectrometer_reader = types.SimpleNamespace(is_ready=True)
        window.update_global_status()

        self.assertFalse(window.prepare_power_meter_open_button.isEnabled())
        self.assertTrue(window.prepare_power_meter_close_button.isEnabled())
        self.assertFalse(window.prepare_spectrometer_open_button.isEnabled())
        self.assertTrue(window.prepare_spectrometer_close_button.isEnabled())
        window.power_meter_reader = None
        window.spectrometer_reader = None
        window.close()

    def test_prepare_workflow_follows_dependency_order(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        direct_widgets = [
            window.prepare_left_layout.itemAt(index).widget()
            for index in range(window.prepare_left_layout.count())
        ]
        expected = [
            window.session_group,
            window.automatic_test_section,
            window.power_prepare_group,
            window.measurement_prepare_group,
        ]
        self.assertEqual([direct_widgets.index(widget) for widget in expected], [0, 1, 2, 3])
        self.assertEqual(window.session_group.title(), "1. 测试任务")
        self.assertEqual(window.automatic_test_content.title(), "2. 测试计划")
        self.assertEqual(window.power_prepare_group.title(), "3. 电源")
        self.assertEqual(window.measurement_prepare_group.title(), "4. 测量设备")
        self.assertEqual(window.preflight_group.title(), "5. 启动前检查")
        self.assertFalse(hasattr(window, "test_plan_label"))
        self.assertFalse(
            any(label.text() == "976 nm 标准测试" for label in window.findChildren(QLabel))
        )
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
            "test_station_field",
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
        self.assertAlmostEqual(window.power_curve_axis.get_position().width, 0.67)
        self.assertAlmostEqual(window.stable_power_axis.get_position().width, 0.67)
        self.assertFalse(hasattr(window, "kpi_panel"))
        self.assertEqual(window.live_plots.power_value_text.get_text(), "-- W")
        self.assertEqual(window.live_plots.power_value_text.get_position(), (0.975, 0.95))
        self.assertEqual(window.live_plots.power_value_text.get_ha(), "right")
        self.assertEqual(window.live_plots.stability_status_text.get_position(), (0.025, 0.95))
        self.assertEqual(window.live_plots.stability_status_text.get_ha(), "left")
        self.assertIn("Center wavelength", window.live_plots.spectrum_centroid_text.get_text())
        self.assertIn("FWHM", window.live_plots.spectrum_fwhm_text.get_text())
        self.assertIn("PIB", window.live_plots.spectrum_pib_text.get_text())
        self.assertIn("SMSR", window.live_plots.spectrum_smsr_text.get_text())
        self.assertFalse(window.log_text.isHidden())
        self.assertIsInstance(window.log_text, QLabel)
        self.assertFalse(hasattr(window, "toggle_log_button"))
        self.assertFalse(hasattr(window, "clear_log_button"))
        window.close()

    def test_global_device_indicators_follow_connection_state(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        semantic = combined_test_window.semantic_colors_for_palette(window.palette())
        self.assertIn(semantic.secondary_text, window.global_psu_status_indicator.styleSheet())
        self.assertIn(semantic.secondary_text, window.global_power_meter_status_indicator.styleSheet())
        self.assertIn(semantic.secondary_text, window.global_spectrometer_status_indicator.styleSheet())

        window.automatic_test_state = AutomaticTestState.PAUSED
        window.active_output_current_a = 6.0
        window.update_global_status()
        self.assertIn(semantic.error_text, window.global_psu_status_indicator.styleSheet())
        self.assertIn("最近输出 6.0 A", window.global_psu_status_label.text())
        window.automatic_test_state = AutomaticTestState.IDLE
        window.active_output_current_a = None

        class ConnectedController:
            is_connected = True

        class ReadyReader:
            is_ready = True

        window.manual_ch341_controller = ConnectedController()
        window.power_meter_reader = ReadyReader()  # type: ignore[assignment]
        window.spectrometer_reader = ReadyReader()  # type: ignore[assignment]
        window.update_global_status()

        self.assertIn(semantic.success_text, window.global_psu_status_indicator.styleSheet())
        self.assertIn(semantic.success_text, window.global_power_meter_status_indicator.styleSheet())
        self.assertIn(semantic.success_text, window.global_spectrometer_status_indicator.styleSheet())
        object.__setattr__(window.automatic_controller, "_output_shutdown_unconfirmed", True)
        window.active_output_current_a = 0.0
        window.update_global_status()
        self.assertIn(semantic.error_text, window.global_psu_status_indicator.styleSheet())
        self.assertIn("输出状态未确认", window.global_psu_status_label.text())
        object.__setattr__(window.automatic_controller, "_output_shutdown_unconfirmed", False)
        window._power_meter_fault_message = "VISA timeout"
        window._spectrometer_fault_message = "USB disconnected"
        window.update_global_status()
        self.assertIn(semantic.error_text, window.global_power_meter_status_indicator.styleSheet())
        self.assertIn(semantic.error_text, window.global_spectrometer_status_indicator.styleSheet())
        self.assertEqual(window.global_power_meter_status_label.text(), "功率计：故障")
        self.assertEqual(window.global_spectrometer_status_label.text(), "光谱仪：故障")
        window.power_meter_reader = None
        window.spectrometer_reader = None
        window.close()

    def test_log_shows_only_the_latest_line(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.show()
        app.processEvents()

        window.add_log("first message")
        window.add_log("latest message")
        app.processEvents()

        self.assertIn("latest message", window.log_text.text())
        self.assertNotIn("first message", window.log_text.text())
        self.assertFalse(window.log_text.wordWrap())
        self.assertGreaterEqual(window.log_text.height(), window.log_text.sizeHint().height())
        self.assertGreaterEqual(window.log_text.geometry().bottom(), window.log_text.sizeHint().height())
        self.assertGreaterEqual(window.log_text.parentWidget().height(), window.log_text.geometry().bottom() + 1)
        window.close()

    def test_plot_layout_uses_tabs_below_the_dashboard_width(self) -> None:
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
            [window.chart_tabs.tabText(index) for index in range(window.chart_tabs.count())],
            ["功率实时", "功率 / 效率", "光谱"],
        )
        self.assertEqual(window.curves_layout.indexOf(window.chart_tabs), 0)
        self.assertEqual(window.chart_tabs.indexOf(window.power_curve_canvas), 0)
        self.assertEqual(window.chart_tabs.indexOf(window.stable_power_canvas), 1)
        self.assertEqual(window.chart_tabs.indexOf(window.spectrum_curve_canvas), 2)

        window.live_plots.relayout(1000)
        self.assertTrue(window.chart_tabs.isHidden())
        for canvas in (
            window.power_curve_canvas,
            window.stable_power_canvas,
            window.spectrum_curve_canvas,
        ):
            self.assertFalse(canvas.isHidden())
        window.close()

    def test_live_plots_show_all_three_charts_on_the_manual_page(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.show()
        app.processEvents()

        self.assertIs(window.live_plots.layout_context, PlotLayoutContext.AUTOMATIC)
        self.assertTrue(window.chart_tabs.isHidden())

        window.main_tabs.setCurrentIndex(window.manual_tab_index)
        window.live_plots.relayout(700)
        app.processEvents()

        self.assertIs(window.live_plots.layout_context, PlotLayoutContext.MANUAL)
        self.assertEqual(
            [
                window.curves_layout.getItemPosition(window.curves_layout.indexOf(canvas))
                for canvas in (
                    window.power_curve_canvas,
                    window.stable_power_canvas,
                    window.spectrum_curve_canvas,
                )
            ],
            [(0, 0, 1, 1), (0, 1, 1, 1), (1, 0, 1, 2)],
        )
        self.assertTrue(window.chart_tabs.isHidden())
        self.assertEqual(window.chart_tabs.count(), 0)
        for canvas in (
            window.power_curve_canvas,
            window.stable_power_canvas,
            window.spectrum_curve_canvas,
        ):
            self.assertFalse(canvas.isHidden())
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

    def test_common_1100_by_700_window_has_no_horizontal_page_scrolling(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.resize(1100, 700)
        window.show()
        app.processEvents()

        self.assertLessEqual(window.width(), 1100)
        self.assertEqual(window.prepare_scroll_area.horizontalScrollBar().maximum(), 0)

        window.main_tabs.setCurrentIndex(window.manual_tab_index)
        app.processEvents()
        self.assertEqual(window.manual_scroll_area.horizontalScrollBar().maximum(), 0)

        pages_and_key_widgets = (
            (
                window.automatic_run_page,
                (
                    window.run_state_label,
                    window.run_stage_label,
                    window.pause_automatic_test_button,
                    window.retry_automatic_test_button,
                    window.end_automatic_test_button,
                ),
            ),
            (
                window.automatic_result_page,
                (
                    window.result_outcome_panel,
                    window.open_result_button,
                    window.open_result_folder_button,
                    window.return_to_prepare_button,
                ),
            ),
            (window.records_page, (window.records_empty_state,)),
            (
                window.pd_panel,
                (
                    window.pd_panel.device_settings_group,
                    window.pd_panel.sampling_settings_group,
                    window.pd_panel.calibration_settings_group,
                    window.pd_panel.storage_settings_group,
                    window.pd_panel.start_button,
                    window.pd_panel.stop_button,
                ),
            ),
        )
        for page, widgets in pages_and_key_widgets:
            if page in (window.automatic_run_page, window.automatic_result_page):
                window.main_tabs.setCurrentIndex(window.automatic_tab_index)
                window.automatic_stack.setCurrentWidget(page)
            elif page is window.records_page:
                window.main_tabs.setCurrentIndex(window.records_tab_index)
            else:
                window.main_tabs.setCurrentIndex(window.pd_tab_index)
            app.processEvents()
            for widget in widgets:
                with self.subTest(page=page, widget=widget):
                    mapped_rect = widget.rect()
                    mapped_rect.moveTopLeft(widget.mapTo(page, widget.rect().topLeft()))
                    self.assertTrue(page.rect().contains(mapped_rect), mapped_rect)

        result_groups = (
            window.result_sn_label.parentWidget(),
            window.result_metric_labels["current"].parentWidget(),
        )
        self.assertFalse(result_groups[0].geometry().intersects(result_groups[1].geometry()))
        pd_groups = (
            window.pd_panel.device_settings_group,
            window.pd_panel.sampling_settings_group,
            window.pd_panel.calibration_settings_group,
            window.pd_panel.storage_settings_group,
        )
        for index, group in enumerate(pd_groups):
            for other_group in pd_groups[index + 1 :]:
                self.assertFalse(group.geometry().intersects(other_group.geometry()))
        window.close()

    def test_prepare_page_keyboard_focus_follows_the_operator_workflow(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        expected_order = (
            window.sn_field,
            window.test_station_field,
            window.output_dir_field,
            window.browse_button,
            window.auto_initial_current_spin,
            window.auto_target_current_spin,
            window.auto_current_step_spin,
        )

        def next_focusable(widget: QWidget) -> QWidget:
            candidate = widget.nextInFocusChain()
            while (
                candidate.focusPolicy() == Qt.FocusPolicy.NoFocus
                or widget.isAncestorOf(candidate)
            ):
                candidate = candidate.nextInFocusChain()
            return candidate

        for current, following in zip(expected_order, expected_order[1:]):
            candidate = next_focusable(current)
            self.assertTrue(candidate is following or following.isAncestorOf(candidate))
        window.close()

    def test_idle_header_does_not_repeat_a_standby_status(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.show()
        app.processEvents()

        self.assertEqual(window.global_status_label.text(), "")
        self.assertTrue(window.global_status_label.isHidden())
        visible_header_texts = {
            label.text() for label in window.page_header.findChildren(QLabel) if label.isVisible()
        }
        self.assertNotIn("测试待机", visible_header_texts)
        self.assertNotIn("准备测试", visible_header_texts)
        window.close()

    def test_records_page_exposes_a_clear_empty_state(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertEqual(window.records_empty_title.text(), "还没有测试记录")
        self.assertFalse(window.records_empty_state.isHidden())
        self.assertTrue(window.records_session_panel.isHidden())
        window.close()

    def test_task_and_record_controls_follow_their_workflow_modes(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        task_form = self._group(window, "1. 测试任务").layout()
        self.assertIsInstance(task_form, QFormLayout)

        for widget in (
            window.sn_field,
            window.test_station_field,
            window.output_dir_field,
        ):
            self._form_row_containing_widget(task_form, widget)

        self.assertTrue(window.records_page.isAncestorOf(window.save_excel_button))
        self.assertTrue(window.records_page.isAncestorOf(window.records_open_button))
        self.assertLess(window.records_tab_index, window.pd_tab_index)
        self.assertTrue(window.manual_page.isAncestorOf(self._group(window, "电源")))
        window.close()

    def test_button_roles_use_native_default_and_one_destructive_color(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertTrue(window.start_automatic_test_button.isDefault())
        self.assertFalse(hasattr(window, "start_all_button"))
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

        for title in (
            "1. 测试任务",
            "2. 测试计划",
            "3. 电源",
            "4. 测量设备",
            "电源",
            "功率计",
            "光谱仪",
        ):
            group = self._group(window, title)
            if group.minimumHeight() > 0:
                self.assertGreaterEqual(group.minimumHeight(), group.sizeHint().height(), title)

        window.close()

    def test_advanced_acquisition_summary_stays_inside_automatic_workflow(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertTrue(window.advanced_settings_content.isHidden())
        window.advanced_settings_toggle.click()
        self.assertFalse(window.advanced_settings_content.isHidden())
        self.assertIn("功率计 976 nm", window.advanced_settings_summary_label.text())
        self.assertFalse(hasattr(window, "open_advanced_settings_button"))
        self.assertEqual(window.main_tabs.currentIndex(), window.automatic_tab_index)

        power_supply_form = window.power_supply_details_form
        self.assertFalse(hasattr(window, "i2c_addr_field"))
        self.assertFalse(hasattr(window, "i2c_speed_combo"))
        self.assertEqual(combined_test_window.DEFAULT_I2C_ADDRESS, 0x41)
        self.assertEqual(combined_test_window.DEFAULT_I2C_SPEED, 0)
        self._form_row_containing_widget(power_supply_form, window.power_supply_controller_combo)

        power_meter_form = window.power_meter_details_form
        self._form_row_containing_widget(power_meter_form, window.software_gain_spin)
        self._form_row_containing_widget(power_meter_form, window.power_meter_interval_spin)

        spectrometer_form = window.spectrometer_details_form
        self._form_row_containing_widget(spectrometer_form, window.interval_spin)
        window.close()

    def test_manual_device_details_open_in_font_consistent_dialogs(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.main_tabs.setCurrentIndex(window.manual_tab_index)
        window.show()
        app.processEvents()

        self.assertFalse(hasattr(window, "power_supply_details_toggle"))
        self.assertFalse(window.power_supply_details_content.isHidden())
        self._form_row_containing_widget(
            window.power_supply_details_form,
            window.power_supply_controller_combo,
        )
        sections = (
            (
                window.power_meter_details_button,
                window.power_meter_details_dialog,
                window.power_meter_details_content,
                window.power_meter_details_form,
                window.software_gain_spin,
                self._group(window, "功率计"),
            ),
            (
                window.spectrometer_details_button,
                window.spectrometer_details_dialog,
                window.spectrometer_details_content,
                window.spectrometer_details_form,
                window.integration_spin,
                self._group(window, "光谱仪"),
            ),
        )
        for button, dialog, content, form, setting, group in sections:
            self.assertFalse(button.isCheckable())
            self.assertTrue(dialog.isHidden())
            self.assertFalse(dialog.isModal())
            self.assertFalse(window.manual_scroll_content.isAncestorOf(content))
            self.assertTrue(dialog.isAncestorOf(content))
            self.assertEqual(button.font().pointSizeF(), 9.0)
            self.assertEqual(dialog.font().pointSizeF(), 10.0)
            self.assertEqual(
                group.sizePolicy().verticalPolicy(),
                QSizePolicy.Policy.Maximum,
            )
            self._form_row_containing_widget(form, setting)
            page_height_before = window.left_control_content.sizeHint().height()

            button.click()
            app.processEvents()

            self.assertFalse(dialog.isHidden())
            self.assertEqual(window.left_control_content.sizeHint().height(), page_height_before)
            dialog.reject()
            app.processEvents()

        window.open_manual_settings("power_meter")
        app.processEvents()
        self.assertEqual(window.main_tabs.currentIndex(), window.manual_tab_index)
        self.assertFalse(window.power_meter_details_dialog.isHidden())
        self.assertTrue(window.power_meter_combo.hasFocus())
        window.close()

    def test_manual_detail_buttons_share_the_primary_device_status_rows(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        for form, detail_button, start_button in (
            (
                window.power_meter_form,
                window.power_meter_details_button,
                window.start_power_meter_button,
            ),
            (
                window.spectrometer_form,
                window.spectrometer_details_button,
                window.start_spectrometer_button,
            ),
        ):
            self.assertEqual(
                self._form_row_containing_widget(form, detail_button),
                self._form_row_containing_widget(form, start_button),
            )
        window.close()

    def test_manual_tdk_power_card_stays_compact_and_uses_body_font(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.main_tabs.setCurrentIndex(window.manual_tab_index)
        window.power_supply_controller_combo.setCurrentIndex(
            window.power_supply_controller_combo.findData("tdk")
        )
        window.resize(1280, 800)
        window.show()
        app.processEvents()

        self.assertEqual(window.manual_page.font().pointSizeF(), 10.0)
        self.assertEqual(
            window.power_supply_group.sizePolicy().verticalPolicy(),
            QSizePolicy.Policy.Maximum,
        )
        self.assertLessEqual(
            window.power_supply_group.height(),
            window.power_supply_group.sizeHint().height() + 2,
        )
        window.close()

    def test_power_supply_controls_follow_the_safe_operating_order(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        form = window.power_supply_form

        rows = {
            "controller": self._form_row_containing_widget(form, window.power_supply_controller_combo),
            "resource": self._form_row_containing_widget(form, window.tdk_resource_combo),
            "connection": self._form_row_containing_widget(form, window.connect_i2c_button),
            "voltage": self._form_row_containing_widget(form, window.tdk_voltage_spin),
            "output": self._form_row_containing_widget(form, window.tdk_output_button),
            "current": self._form_row_containing_widget(form, window.set_current_spin),
            "read": self._form_row_containing_widget(form, window.read_output_current_button),
        }
        self.assertLess(rows["controller"], rows["connection"])
        self.assertLess(rows["connection"], rows["current"])
        self.assertLess(rows["current"], rows["read"])

        window.power_supply_controller_combo.setCurrentIndex(
            window.power_supply_controller_combo.findData("tdk")
        )
        self.assertEqual(
            [
                rows["controller"],
                rows["resource"],
                rows["connection"],
                rows["voltage"],
                rows["output"],
                rows["current"],
            ],
            sorted(
                [
                    rows["controller"],
                    rows["resource"],
                    rows["connection"],
                    rows["voltage"],
                    rows["output"],
                    rows["current"],
                ]
            ),
        )
        window.close()

    def test_manual_page_uses_one_vertical_scroll_area_for_controls_curves_and_log(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.resize(1280, 800)
        window.main_tabs.setCurrentIndex(window.manual_tab_index)
        window.show()
        app.processEvents()

        self.assertIs(window.manual_scroll_area.widget(), window.manual_scroll_content)
        for widget in (window.left_control_content, window.manual_monitor_panel, window.log_text):
            self.assertTrue(window.manual_scroll_content.isAncestorOf(widget))
        self.assertEqual(window.manual_scroll_area.horizontalScrollBar().maximum(), 0)
        self.assertGreater(window.manual_scroll_area.verticalScrollBar().maximum(), 0)
        window.close()

    def test_left_control_panel_does_not_need_horizontal_scrolling(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.resize(2048, 1152)
        window.main_tabs.setCurrentIndex(window.manual_tab_index)
        window.show()
        app.processEvents()

        self.assertLessEqual(window.left_control_content.width(), window.left_control_panel.viewport().width())

        window.main_tabs.setCurrentIndex(window.automatic_tab_index)
        app.processEvents()

        self.assertEqual(window.prepare_scroll_area.horizontalScrollBar().maximum(), 0)
        self.assertLessEqual(window.prepare_content.width(), window.prepare_scroll_area.viewport().width())
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

    def test_manual_page_shows_the_shared_realtime_curves_without_losing_data(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.update_power_curve(1.5, 2.25)

        window.main_tabs.setCurrentIndex(window.manual_tab_index)
        app.processEvents()

        self.assertTrue(window.manual_page.isAncestorOf(window.live_plots.group))
        self.assertEqual(window.manual_monitor_layout.indexOf(window.live_plots.group), 0)
        self.assertEqual(list(window.power_curve_line.get_xdata()), [1.5])
        self.assertEqual(list(window.power_curve_line.get_ydata()), [2.25])

        window.main_tabs.setCurrentIndex(window.automatic_tab_index)
        app.processEvents()

        self.assertTrue(window.automatic_run_page.isAncestorOf(window.live_plots.group))
        self.assertEqual(window.automatic_monitor_layout.indexOf(window.live_plots.group), 0)
        self.assertEqual(list(window.power_curve_line.get_ydata()), [2.25])
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
        self.assertEqual(window.power_curve_axis.get_ylabel(), "Power (W)")
        self.assertEqual(window.stable_power_axis.get_ylabel(), "Stable Power (W)")
        self.assertEqual(window.efficiency_axis.get_ylabel(), "Efficiency (%)")
        self.assertTrue(window.power_curve_axis.yaxis.get_visible())
        self.assertTrue(window.stable_power_axis.yaxis.get_visible())
        self.assertTrue(window.efficiency_axis.yaxis.get_visible())
        self.assertGreaterEqual(window.power_curve_axis.get_position().x0, 0.17)
        self.assertLessEqual(window.stable_power_axis.get_position().x1, 0.84)
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

    def test_power_curve_rounds_and_smooths_display_without_changing_raw_reading(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        raw_reading = PowerMeterReading(0.2, 1.0074, False, 0.01, 0.2)
        window.update_power_curve(0.0, 1.0004)
        window.update_power_curve(0.1, 1.0044)
        window.on_power_meter_reading(raw_reading)

        self.assertEqual(list(window.power_curve_values), [1.0, 1.002, 1.004])
        self.assertEqual(list(window.power_curve_line.get_ydata()), [1.0, 1.002, 1.004])
        self.assertIs(window.latest_power_meter_reading, raw_reading)
        self.assertEqual(window.latest_power_meter_reading.power_w, 1.0074)
        window.close()

    def test_power_curve_smoothing_window_drops_old_samples(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        window.update_power_curve(0.0, 1.0)
        window.update_power_curve(0.1, 3.0)
        window.update_power_curve(0.31, 5.0)

        self.assertEqual(list(window.power_curve_values), [1.0, 2.0, 5.0])
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
        self.assertEqual(window.live_plots.spectrum_pib_text.get_text(), "PIB   -- %")
        window.close()

    def test_spectrum_curve_displays_smsr_from_main_and_highest_side_mode(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        wavelength = [956.0 + index * 0.1 for index in range(401)]
        intensity = [0.0] * len(wavelength)
        intensity[179:182] = [60.0, 100.0, 60.0]
        intensity[199:202] = [600.0, 1000.0, 600.0]
        window.on_spectrum_curve(wavelength, intensity)

        self.assertEqual(window.live_plots.spectrum_smsr_text.get_text(), "SMSR   10.00 dB")
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
        window.latest_wavelength_stable = True
        window.latest_wavelength_span_nm = 0.05
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

    def test_automatic_test_does_not_accept_power_stability_before_wavelength_stability(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.automatic_test_state = AutomaticTestState.WAITING_STABLE
        window.active_output_current_a = 3.0
        window.pending_stable_point_current_a = 3.0
        window.pending_stable_point_generation = 7

        window.on_power_meter_reading(
            PowerMeterReading(1.0, 10.0, True, 0.01, 3.0, stability_generation=7)
        )

        self.assertEqual(window.automatic_test_state, AutomaticTestState.WAITING_STABLE)
        self.assertIsNone(window.pending_auto_vout_current_a)
        window.automatic_test_state = AutomaticTestState.IDLE
        window.close()

    def test_automatic_test_without_spectrometer_uses_power_stability_and_queues_liv(self) -> None:
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(
                QSettings(str(Path(temp_dir) / "inputs.ini"), QSettings.Format.IniFormat)
            )
            window.auto_use_spectrometer_check.setChecked(False)
            window.automatic_test_settings = window.collect_automatic_test_settings()
            window.automatic_test_state = AutomaticTestState.WAITING_STABLE
            window.active_output_current_a = 3.0
            window.pending_stable_point_current_a = 3.0
            window.pending_stable_point_generation = 7

            window.on_power_meter_reading(
                PowerMeterReading(1.0, 75.0, True, 0.01, 3.0, stability_generation=7)
            )

            self.assertEqual(window.automatic_test_state, AutomaticTestState.WAITING_VOLTAGE)
            self.assertTrue(window.queue_excel_test_point(3.0, 50.0, 75.0, 0.5))
            record = window.pending_excel_records[3.0]
            self.assertEqual(list(record.wavelength), [])
            self.assertEqual(list(record.intensity), [])
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

    def test_tdk_voltage_read_uses_rs232_label_without_i2c_frame(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakeTdkController:
            is_connected = True
            output_enabled = True

            def read_output_voltage(self) -> float:
                return 29.5

            def i2c_write_read(self, *_args: object) -> tuple[bool, list[int]]:
                raise AssertionError("TDK voltage read must not use I2C")

        window = MainWindow()
        window.manual_ch341_controller = FakeTdkController()
        window.power_supply_controller_kind = "tdk"
        window.last_power_supply_command_monotonic_s = (
            combined_test_window.time.monotonic() - POWER_SUPPLY_COMMAND_MIN_INTERVAL_S - 0.1
        )

        value = window.execute_i2c_read([0xB4, 0x8B, 0x00, 0x00], "输出电压", "V")

        self.assertEqual(value, 29.5)
        self.assertIn("RS-232 MV?", window.log_text.text())
        self.assertNotIn("B4 8B", window.log_text.text())
        window.manual_ch341_controller = None
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

    def test_manual_current_write_uses_power_supply_port_without_false_failure(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakeController:
            is_connected = True

            def __init__(self) -> None:
                self.writes: list[list[int]] = []

            def i2c_write(self, _address: int, command: list[int]) -> tuple[bool, str]:
                self.writes.append(command)
                return True, "OK"

            def disconnect_device(self) -> bool:
                self.is_connected = False
                return True

        window = MainWindow()
        controller = FakeController()
        window.manual_ch341_controller = controller
        window.set_current_spin.setValue(3.2)

        window.apply_output_current()

        self.assertEqual(controller.writes, [[0xB4, 0xFF, 3, 20]])
        self.assertEqual(window.active_output_current_a, 3.2)
        self.assertIn("3.2 A", window.log_text.text())
        window.close()

    def test_manual_nonzero_current_locks_other_tabs_until_current_returns_to_zero(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakeController:
            is_connected = True

            def __init__(self) -> None:
                self.writes: list[list[int]] = []

            def i2c_write(self, _address: int, command: list[int]) -> tuple[bool, str]:
                self.writes.append(command)
                return True, "OK"

        window = MainWindow()
        window.manual_ch341_controller = FakeController()
        window.main_tabs.setCurrentIndex(window.manual_tab_index)
        window.set_current_spin.setValue(3.2)

        window.apply_output_current()

        self.assertTrue(window.manual_power_tab_lock_active)
        self.assertEqual(window.main_tabs.currentIndex(), window.manual_tab_index)
        self.assertTrue(window.main_tabs.isTabEnabled(window.manual_tab_index))
        for index in (
            window.automatic_tab_index,
            window.records_tab_index,
        ):
            self.assertFalse(window.main_tabs.isTabEnabled(index))
            self.assertIn("0 A", window.main_tabs.tabToolTip(index))
        self.assertTrue(window.main_tabs.isTabEnabled(window.pd_tab_index))
        self.assertIn("加电期间", window.main_tabs.tabToolTip(window.pd_tab_index))
        window.main_tabs.setCurrentIndex(window.pd_tab_index)
        self.assertEqual(window.main_tabs.currentIndex(), window.pd_tab_index)

        window.last_power_supply_command_monotonic_s = None
        window.set_current_spin.setValue(0.0)
        window.apply_output_current()

        self.assertFalse(window.manual_power_tab_lock_active)
        for index in range(window.main_tabs.count()):
            self.assertTrue(window.main_tabs.isTabEnabled(index))
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
        self.assertEqual(controller.writes, [[0xB4, 0xFF, 0x01, 0x00]])
        self.assertEqual(window.automatic_test_state, AutomaticTestState.SETTING_CURRENT)
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
        ):
            self.assertTrue(hasattr(window, attribute), attribute)

        window.close()

    def test_power_meter_common_action_buttons_stay_in_power_meter_group(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        form = window.power_meter_details_form

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

    def test_spectrometer_start_stop_buttons_stay_outside_detailed_configuration(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        primary_form = window.spectrometer_form
        details_form = window.spectrometer_details_form

        self._form_row_containing_widget(details_form, window.integration_spin)
        start_row = self._form_row_containing_widget(primary_form, window.start_spectrometer_button)
        stop_row = self._form_row_containing_widget(primary_form, window.stop_spectrometer_button)

        self.assertEqual(start_row, stop_row)
        self.assertTrue(window.spectrometer_details_dialog.isHidden())
        self.assertFalse(window.manual_scroll_content.isAncestorOf(window.spectrometer_details_content))
        window.close()

    def test_spectrometer_default_integration_time_is_10000_us(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertEqual(window.integration_spin.value(), 10000)
        self.assertEqual(window.collect_spectrometer_settings().integration_time_us, 10000)

    def test_auto_integration_moves_toward_target_and_respects_limits(self) -> None:
        self.assertEqual(combined_test_devices.next_auto_integration_time(10_000, 1_000, 1_000, 300_000), 20_000)
        self.assertEqual(combined_test_devices.next_auto_integration_time(10_000, 11_000, 1_000, 300_000), 10_000)
        self.assertEqual(combined_test_devices.next_auto_integration_time(10_000, 16_000, 1_000, 300_000), 6_875)

    def test_weak_spectrum_is_not_queued_for_excel(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.latest_spectrum_wavelength = [975.0, 976.0, 977.0]
        window.latest_spectrum_intensity = [0.0, 499.0, 0.0]

        self.assertFalse(window.queue_excel_test_point(3.0, 50.0, 10.0, 0.1))
        self.assertIn("500", window.last_point_record_error)
        window.close()

    def test_pause_arms_safety_ramp_down_timer(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.automatic_test_settings = window.collect_automatic_test_settings()
        window.automatic_test_state = AutomaticTestState.WAITING_STABLE

        window.pause_automatic_test("测试暂停")

        self.assertTrue(window.automatic_pause_safety_timer.isActive())
        window.active_output_current_a = 6.0
        window.update_automatic_elapsed()
        self.assertEqual(window.run_current_label.text(), "保持 6.0 A")
        self.assertIn("后自动安全下电", window.run_remaining_label.text())
        window.automatic_pause_safety_timer.stop()
        window.automatic_test_state = AutomaticTestState.IDLE
        window.close()

    def test_completed_tdk_test_turns_output_off(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakeTdkController:
            output_enabled = True

            def set_output_enabled(self, enabled: bool) -> None:
                self.output_enabled = enabled

        window = MainWindow()
        controller = FakeTdkController()
        window.manual_ch341_controller = controller
        window.power_supply_controller_kind = "tdk"
        window.automatic_test_state = AutomaticTestState.RAMPING_DOWN

        window.complete_automatic_test()

        self.assertFalse(controller.output_enabled)
        self.assertEqual(window.automatic_test_state, AutomaticTestState.COMPLETED)
        window.manual_ch341_controller = None
        window.close()

    def test_successful_automatic_test_shows_target_current_summary_after_ramp_down(self) -> None:
        app = QApplication.instance() or QApplication([])
        record = ExcelTestRecord(
            current_a=20.0,
            voltage_v=50.5,
            power_w=200.0,
            efficiency=0.19802,
            peak_wavelength_nm=976.1234,
            centroid_nm=976.12,
            fwhm_nm=0.2474,
            pib=0.99123,
            wavelength=[975.0, 976.0, 977.0],
            intensity=[1.0, 10.0, 1.0],
        )
        window = MainWindow()
        window.automatic_test_state = AutomaticTestState.RAMPING_DOWN
        window.automatic_test_currents = (20.0,)
        window.automatic_test_current_index = 0
        window.automatic_completion_record = record
        window.test_session_station = "老化站 1"
        window.record_store.recorded_currents.add(20.0)
        window.complete_automatic_test()

        self.assertEqual(window.automatic_test_state, AutomaticTestState.COMPLETED)
        self.assertEqual(window.automatic_stack.currentIndex(), window.automatic_result_index)
        self.assertEqual(window.result_title_label.text(), "测试完整完成")
        self.assertEqual(window.result_station_label.text(), "老化站 1")
        self.assertEqual(window.result_metric_labels["current"].text(), "20.0 A")
        self.assertEqual(window.result_metric_labels["power"].text(), "200.000 W")
        self.assertEqual(window.result_metric_labels["efficiency"].text(), "19.80 %")
        self.assertEqual(window.result_metric_labels["wavelength"].text(), "976.123 nm")
        self.assertEqual(window.result_metric_labels["fwhm"].text(), "0.247 nm")
        self.assertEqual(window.result_metric_labels["pib"].text(), "99.12 %")
        self.assertIsNone(window.automatic_completion_record)

        window.return_to_automatic_prepare()

        self.assertEqual(window.automatic_test_state, AutomaticTestState.IDLE)
        self.assertEqual(window.automatic_test_currents, ())
        self.assertEqual(window.automatic_stack.currentIndex(), window.automatic_prepare_index)
        window.close()

    def test_result_page_distinguishes_terminal_outcomes(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.automatic_test_currents = (1.0, 2.0)
        window.record_store.recorded_currents.add(1.0)

        window.automatic_controller._set_terminal_outcome(
            AutomaticTestTerminalOutcome.STOPPED_BY_OPERATOR,
            "操作者提前结束测试",
        )
        window.show_automatic_result(None, "已安全下电")
        self.assertEqual(window.result_title_label.text(), "测试已提前结束")
        self.assertIn("1/2", window.result_completion_label.text())

        window.automatic_controller._set_terminal_outcome(
            AutomaticTestTerminalOutcome.ABORTED_SAFELY,
            "功率计通信中断",
        )
        window.show_automatic_result(None, "已安全下电")
        self.assertEqual(window.result_title_label.text(), "测试异常中止")
        self.assertIn("功率计通信中断", window.result_completion_label.text())

        window.record_store.recorded_currents.add(2.0)
        window.automatic_controller._set_terminal_outcome(
            AutomaticTestTerminalOutcome.SUCCEEDED,
            "所有计划测试点均已保存",
        )
        window.show_automatic_result(None, "测试完成")
        self.assertEqual(window.result_title_label.text(), "测试完整完成")
        window.close()

    def test_result_file_actions_require_an_existing_file(self) -> None:
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow()
            result_path = Path(temp_dir) / "result.xlsx"
            window.excel_workbook_path = result_path

            window.show_automatic_result(None, "测试已结束")

            self.assertFalse(window.open_result_button.isEnabled())
            self.assertFalse(window.open_result_folder_button.isEnabled())
            self.assertIn("尚未生成", window.result_file_label.text())

            result_path.write_bytes(b"test")
            window.show_automatic_result(None, "测试已结束")
            self.assertTrue(window.open_result_button.isEnabled())
            self.assertTrue(window.open_result_folder_button.isEnabled())
            window.close()

    def test_automatic_completion_without_spectrometer_omits_spectrum_summary(self) -> None:
        app = QApplication.instance() or QApplication([])
        record = ExcelTestRecord(
            current_a=4.0,
            voltage_v=14.7,
            power_w=8.871,
            efficiency=0.1465,
            peak_wavelength_nm=float("nan"),
            centroid_nm=float("nan"),
            fwhm_nm=float("nan"),
            pib=float("nan"),
            wavelength=[],
            intensity=[],
        )
        window = MainWindow()
        window.auto_use_spectrometer_check.setChecked(False)
        window.automatic_test_settings = window.collect_automatic_test_settings()
        window.automatic_test_state = AutomaticTestState.RAMPING_DOWN
        window.automatic_completion_record = record
        window.complete_automatic_test()

        self.assertEqual(window.result_metric_labels["current"].text(), "4.0 A")
        self.assertEqual(window.result_metric_labels["power"].text(), "8.871 W")
        self.assertEqual(window.result_metric_labels["efficiency"].text(), "14.65 %")
        for key in ("wavelength", "fwhm", "pib"):
            self.assertEqual(window.result_metric_labels[key].text(), "--")
        window.close()

    def test_optional_spectrometer_instability_does_not_restart_power_stability(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.auto_use_spectrometer_check.setChecked(False)
        window.automatic_test_settings = window.collect_automatic_test_settings()
        window.automatic_test_state = AutomaticTestState.WAITING_VOLTAGE
        window.latest_wavelength_stable = True
        window.wavelength_stability_detector = types.SimpleNamespace(
            add_sample=lambda *_args: types.SimpleNamespace(stable=False, span_w=1.0)
        )

        window.on_spectrometer_reading(SpectrometerReading(976.0, 976.0, 1.0))

        self.assertEqual(window.automatic_test_state, AutomaticTestState.WAITING_VOLTAGE)
        window.automatic_test_state = AutomaticTestState.IDLE
        window.close()

    def test_automatic_vout_read_without_spectrometer_does_not_require_wavelength_stability(self) -> None:
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(
                QSettings(str(Path(temp_dir) / "inputs.ini"), QSettings.Format.IniFormat)
            )
            window.auto_use_spectrometer_check.setChecked(False)
            window.automatic_test_settings = window.collect_automatic_test_settings()
            window.automatic_test_state = AutomaticTestState.WAITING_VOLTAGE
            window.active_output_current_a = 3.0
            window.recorded_stable_point_current_a = 3.0
            window.recorded_stable_point_generation = 7
            window.pending_auto_vout_current_a = 3.0
            window.pending_auto_vout_generation = 7
            window.latest_wavelength_stable = False
            window.latest_power_meter_reading = PowerMeterReading(
                1.0,
                10.0,
                True,
                0.01,
                3.0,
                stability_generation=7,
            )
            reads: list[bool] = []
            window.read_output_voltage = lambda automatic=False: reads.append(automatic)  # type: ignore[method-assign]

            window.on_auto_vout_timer_timeout()

            self.assertEqual(reads, [True])
            window.automatic_test_state = AutomaticTestState.IDLE
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
        calls: list[tuple[str, str, int]] = []

        class FakeResourceManager:
            def list_resources(self) -> tuple[str, str]:
                return ("ASRL1::INSTR", "ASRL2::INSTR")

            def close(self) -> None:
                pass

        class FakeCaihuangPowerMeter:
            @staticmethod
            def probe(resource: str, timeout_ms: int = 1000) -> object | None:
                calls.append(("caihuang", resource, timeout_ms))
                if resource == "ASRL2::INSTR":
                    return types.SimpleNamespace(
                        resource=resource,
                        device_type="Caihuang CHLP-P",
                        detail="OK",
                        driver_kind="caihuang",
                    )
                return None

        class FakeLaserPointPowerMeter:
            @staticmethod
            def probe(resource: str, timeout_ms: int = 1000) -> object | None:
                calls.append(("laserpoint", resource, timeout_ms))
                return None

        old_modules = dict(sys.modules)
        try:
            sys.modules["pyvisa"] = types.SimpleNamespace(ResourceManager=lambda: FakeResourceManager())
            sys.modules["tools.power_meter_mvp"] = types.SimpleNamespace(
                CaihuangPowerMeter=FakeCaihuangPowerMeter,
                LaserPointPowerMeter=FakeLaserPointPowerMeter,
            )
            thread = combined_test_devices.PowerMeterDetectThread("ASRL2::INSTR")
            detected: list[PowerMeterOption] = []
            statuses: list[str] = []
            thread.detected.connect(lambda options: detected.extend(options))
            thread.status.connect(statuses.append)

            thread.run()

            self.assertEqual(
                calls[0],
                ("caihuang", "ASRL2::INSTR", combined_test_devices.POWER_METER_PROBE_TIMEOUT_MS),
            )
            self.assertEqual(
                calls[1],
                ("caihuang", "ASRL1::INSTR", combined_test_devices.POWER_METER_PROBE_TIMEOUT_MS),
            )
            self.assertEqual(
                calls[2],
                ("laserpoint", "ASRL1::INSTR", combined_test_devices.POWER_METER_PROBE_TIMEOUT_MS),
            )
            self.assertEqual([option.resource for option in detected], ["ASRL2::INSTR"])
            self.assertEqual(detected[0].driver_kind, "caihuang")
            self.assertIn("检测功率计", statuses[0])
        finally:
            sys.modules.clear()
            sys.modules.update(old_modules)

    def test_power_meter_detect_thread_falls_back_to_laserpoint_protocol(self) -> None:
        calls: list[tuple[str, str]] = []

        class FakeResourceManager:
            def list_resources(self) -> tuple[str]:
                return ("ASRL4::INSTR",)

            def close(self) -> None:
                pass

        class FakeCaihuangPowerMeter:
            @staticmethod
            def probe(resource: str, timeout_ms: int = 1000) -> None:
                calls.append(("caihuang", resource))
                return None

        class FakeLaserPointPowerMeter:
            @staticmethod
            def probe(resource: str, timeout_ms: int = 1000) -> object:
                calls.append(("laserpoint", resource))
                return types.SimpleNamespace(
                    resource=resource,
                    device_type="LaserPoint",
                    detail="SN 123456",
                    driver_kind="laserpoint",
                )

        old_modules = dict(sys.modules)
        try:
            sys.modules["pyvisa"] = types.SimpleNamespace(ResourceManager=lambda: FakeResourceManager())
            sys.modules["tools.power_meter_mvp"] = types.SimpleNamespace(
                CaihuangPowerMeter=FakeCaihuangPowerMeter,
                LaserPointPowerMeter=FakeLaserPointPowerMeter,
            )
            thread = combined_test_devices.PowerMeterDetectThread()
            detected: list[PowerMeterOption] = []
            thread.detected.connect(lambda options: detected.extend(options))

            thread.run()

            self.assertEqual(calls, [("caihuang", "ASRL4::INSTR"), ("laserpoint", "ASRL4::INSTR")])
            self.assertEqual(len(detected), 1)
            self.assertEqual(detected[0].driver_kind, "laserpoint")
            self.assertEqual(detected[0].label(), "LaserPoint | ASRL4::INSTR | SN 123456")
        finally:
            sys.modules.clear()
            sys.modules.update(old_modules)


class AcquisitionReadySignalTests(unittest.TestCase):
    def test_power_meter_reader_honors_stop_requested_during_startup(self) -> None:
        app = QApplication.instance() or QApplication([])
        startup_entered = threading.Event()
        allow_startup_to_finish = threading.Event()

        class SlowStartingPowerMeter:
            def __init__(self, _resource: str) -> None:
                startup_entered.set()
                allow_startup_to_finish.wait(1.0)

            def test(self) -> str:
                return "OK"

            def set_wavelength(self, _wavelength_nm: float) -> None:
                pass

            def read_power_w(self) -> float:
                return 1.0

            def close(self) -> None:
                pass

        old_meter_class = power_meter_mvp.CaihuangPowerMeter
        reader = combined_test_devices.PowerMeterReaderThread(
            PowerMeterSettings("ASRL1::INSTR", 976.0, 1.0, 20, 3.0, 0.15)
        )
        try:
            power_meter_mvp.CaihuangPowerMeter = SlowStartingPowerMeter
            reader.start()
            self.assertTrue(startup_entered.wait(1.0))

            reader.stop()
            allow_startup_to_finish.set()

            self.assertTrue(reader.wait(500), "reader restarted its loop after stop was requested")
            self.assertFalse(reader.isRunning())
        finally:
            allow_startup_to_finish.set()
            reader.stop()
            reader.wait(1000)
            power_meter_mvp.CaihuangPowerMeter = old_meter_class

    def test_spectrometer_reader_honors_stop_requested_during_startup(self) -> None:
        app = QApplication.instance() or QApplication([])
        startup_entered = threading.Event()
        allow_startup_to_finish = threading.Event()

        class SlowStartingSpectrometer:
            def __init__(self) -> None:
                startup_entered.set()
                allow_startup_to_finish.wait(1.0)

            def open_first(self) -> int:
                return 7

            def set_integration_time(self, _integration_time_us: int) -> None:
                pass

            def read_spectrum(self) -> tuple[list[float], list[float]]:
                return [975.0, 976.0, 977.0], [0.0, 1.0, 0.0]

            def close(self) -> None:
                pass

        original_loader = combined_test_devices.load_spectrometer_components
        reader = combined_test_devices.SpectrometerReaderThread(SpectrometerSettings(10000, 50))
        try:
            combined_test_devices.load_spectrometer_components = (  # type: ignore[assignment]
                lambda _root: (SlowStartingSpectrometer, lambda _x, _y: None)
            )
            reader.start()
            self.assertTrue(startup_entered.wait(1.0))

            reader.stop()
            allow_startup_to_finish.set()

            self.assertTrue(reader.wait(500), "spectrometer restarted its loop after stop was requested")
            self.assertFalse(reader.isRunning())
        finally:
            allow_startup_to_finish.set()
            reader.stop()
            reader.wait(1000)
            combined_test_devices.load_spectrometer_components = original_loader

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

    def test_power_meter_reader_configures_selected_laserpoint_driver(self) -> None:
        calls: list[object] = []

        class FakeLaserPointPowerMeter:
            driver_kind = "laserpoint"
            device_type = "LaserPoint"

            def __init__(self, resource: str) -> None:
                calls.append(("open", resource))

            def test(self) -> str:
                calls.append("test")
                return "OK"

            def set_power_mode(self) -> None:
                calls.append("power_mode")

            def set_gain_mode(self, mode: int) -> None:
                calls.append(("gain_mode", mode))

            def set_wavelength(self, wavelength_nm: float) -> None:
                calls.append(("wavelength", wavelength_nm))

            def read_power_w(self) -> float:
                raise RuntimeError("stop test loop")

            def close(self) -> None:
                calls.append("close")

        old_meter_class = power_meter_mvp.LaserPointPowerMeter
        try:
            power_meter_mvp.LaserPointPowerMeter = FakeLaserPointPowerMeter
            reader = combined_test_devices.PowerMeterReaderThread(
                PowerMeterSettings(
                    "ASRL4::INSTR",
                    976.0,
                    1.0,
                    300,
                    3.0,
                    0.15,
                    driver_kind="laserpoint",
                )
            )

            reader.run()

            self.assertEqual(
                calls[:5],
                [
                    ("open", "ASRL4::INSTR"),
                    "test",
                    "power_mode",
                    ("gain_mode", 3),
                    ("wavelength", 976.0),
                ],
            )
            self.assertEqual(calls[-1], "close")
        finally:
            power_meter_mvp.LaserPointPowerMeter = old_meter_class


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

    def test_laserpoint_wavelength_uses_five_digit_integer_field(self) -> None:
        self.assertEqual(power_meter_mvp.format_laserpoint_wavelength_nm(976.0), "00976")
        with self.assertRaisesRegex(RuntimeError, "整数"):
            power_meter_mvp.format_laserpoint_wavelength_nm(976.5)

    def test_laserpoint_response_parsers_validate_serial_and_power(self) -> None:
        self.assertEqual(power_meter_mvp.parse_laserpoint_serial("SN 123456;"), "123456")
        self.assertAlmostEqual(power_meter_mvp.parse_laserpoint_power_w("P=12.345;"), 12.345)
        with self.assertRaises(RuntimeError):
            power_meter_mvp.parse_laserpoint_serial("OK;")

    def test_laserpoint_adapter_uses_scripts_runner_serial_protocol(self) -> None:
        class FakeInstrument:
            def __init__(self) -> None:
                self.timeout = 0
                self.write_termination = ""
                self.read_termination = ""
                self.baud_rate = 0
                self.data_bits = 0
                self.parity = None
                self.stop_bits = None
                self.flow_control = None
                self.queries: list[str] = []
                self.writes: list[str] = []

            def query(self, command: str) -> str:
                self.queries.append(command)
                return {
                    "*SERNU": "123456",
                    "*OUTPM": "1.25",
                }[command]

            def write(self, command: str) -> None:
                self.writes.append(command)

            def close(self) -> None:
                pass

        class FakeResourceManager:
            def __init__(self, instrument: FakeInstrument) -> None:
                self.instrument = instrument

            def open_resource(self, resource: str) -> FakeInstrument:
                self.resource = resource
                return self.instrument

        instrument = FakeInstrument()
        resource_manager = FakeResourceManager(instrument)
        original_acquire = power_meter_mvp.acquire_visa_resource_manager
        original_release = power_meter_mvp.release_visa_resource_manager
        try:
            power_meter_mvp.acquire_visa_resource_manager = lambda: resource_manager
            power_meter_mvp.release_visa_resource_manager = lambda _rm: None
            meter = power_meter_mvp.LaserPointPowerMeter("COM4")

            self.assertEqual(meter.test(), "OK")
            meter.set_power_mode()
            meter.set_gain_mode(3)
            meter.set_wavelength(976.0)
            self.assertAlmostEqual(meter.read_power_w(), 1.25)
            meter.close()

            self.assertEqual(resource_manager.resource, "ASRL4::INSTR")
            self.assertEqual(instrument.timeout, 1000)
            self.assertEqual(instrument.baud_rate, 38400)
            self.assertEqual(instrument.write_termination, ":")
            self.assertEqual(instrument.read_termination, ";")
            self.assertEqual(
                instrument.queries,
                ["*SERNU", "*OUTPM"],
            )
            self.assertEqual(instrument.writes, ["*POWER", "*SETX1 3", "*SETLAM00976"])
        finally:
            power_meter_mvp.acquire_visa_resource_manager = original_acquire
            power_meter_mvp.release_visa_resource_manager = original_release


class DeviceOptionTests(unittest.TestCase):
    def test_power_meter_option_label_includes_model_resource_and_detail(self) -> None:
        option = PowerMeterOption(
            resource="ASRL4::INSTR",
            device_type="Caihuang CHLP-P",
            detail="OK, version 1.2",
        )

        self.assertEqual(option.label(), "Caihuang CHLP-P | ASRL4::INSTR | OK, version 1.2")

    def test_power_meter_option_label_omits_empty_detection_detail(self) -> None:
        option = PowerMeterOption(
            resource="ASRL3::INSTR",
            device_type="Caihuang CHLP-P",
            detail="",
        )

        self.assertEqual(option.label(), "Caihuang CHLP-P | ASRL3::INSTR")

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
