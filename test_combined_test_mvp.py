import tempfile
import sys
import os
import types
import unittest
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QFormLayout, QGroupBox, QWidget

import combined_test_mvp
import power_meter_mvp
from combined_test_mvp import (
    LiveReading,
    MainWindow,
    PowerMeterOption,
    SpectrometerOption,
    build_spectrum_csv_path,
    save_spectrum_curve,
)


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
            if group.title() == "Spectrometer":
                form = group.layout()
                self.assertIsInstance(form, QFormLayout)
                return form
        raise AssertionError("Spectrometer group not found")

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
            first_window.stop_after_record_check.setChecked(True)
            first_window.save_input_settings()
            first_window.close()

            restored_window = MainWindow(settings)
            self.assertEqual(restored_window.set_current_spin.value(), 12)
            self.assertAlmostEqual(restored_window.power_wavelength_spin.value(), 973.125)
            self.assertEqual(restored_window.integration_spin.value(), 25000)
            self.assertAlmostEqual(restored_window.stable_window_spin.value(), 5.0)
            self.assertAlmostEqual(restored_window.stable_tolerance_spin.value(), 0.0123)
            self.assertTrue(restored_window.stop_after_record_check.isChecked())
            restored_window.close()

    def test_main_window_uses_workflow_layout_without_scroll_area(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertIsInstance(window.centralWidget(), QWidget)
        self.assertFalse(hasattr(window, "scroll_area"))
        self.assertTrue(hasattr(window, "left_control_panel"))
        self.assertTrue(hasattr(window, "monitor_panel"))
        self.assertGreaterEqual(window.left_control_panel.minimumWidth(), 380)
        self.assertLessEqual(window.left_control_panel.maximumWidth(), 430)
        window.close()

    def test_main_window_exposes_status_bar_kpi_cards_and_vertical_curves(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        for attribute in (
            "global_status_label",
            "power_card_value",
            "peak_card_value",
            "fwhm_card_value",
            "stability_card_value",
            "record_card_value",
            "curves_layout",
        ):
            self.assertTrue(hasattr(window, attribute), attribute)

        self.assertEqual(window.curves_layout.getItemPosition(window.curves_layout.indexOf(window.power_curve_canvas))[:2], (0, 0))
        self.assertEqual(window.curves_layout.getItemPosition(window.curves_layout.indexOf(window.spectrum_curve_canvas))[:2], (1, 0))
        window.close()

    def test_left_control_groups_reserve_enough_height_for_their_contents(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        for title in ("Power Supply", "Power Meter", "Spectrometer", "Stability & Record"):
            group = self._group(window, title)
            self.assertGreaterEqual(group.minimumHeight(), group.sizeHint().height(), title)

        window.close()

    def test_no_advanced_group_and_device_settings_stay_with_their_device(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertNotIn("Advanced", [group.title() for group in window.findChildren(QGroupBox)])

        power_supply_form = self._group(window, "Power Supply").layout()
        self.assertIsInstance(power_supply_form, QFormLayout)
        self.assertFalse(hasattr(window, "i2c_addr_field"))
        self.assertFalse(hasattr(window, "i2c_speed_combo"))
        self.assertEqual(combined_test_mvp.DEFAULT_I2C_ADDRESS, 0x41)
        self.assertEqual(combined_test_mvp.DEFAULT_I2C_SPEED, 0)

        power_meter_form = self._group(window, "Power Meter").layout()
        self.assertIsInstance(power_meter_form, QFormLayout)
        self._form_row_containing_widget(power_meter_form, window.software_gain_spin)
        self._form_row_containing_widget(power_meter_form, window.power_meter_interval_spin)

        spectrometer_form = self._group(window, "Spectrometer").layout()
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
        self.assertGreaterEqual(window.power_curve_canvas.minimumHeight(), 220)
        self.assertGreaterEqual(window.spectrum_curve_canvas.minimumHeight(), 220)
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

    def test_spectrum_x_axis_locks_to_dominant_peak_plus_minus_20_after_stable_readings(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        for _ in range(combined_test_mvp.SPECTRUM_CENTER_LOCK_REQUIRED_SAMPLES):
            window.on_spectrometer_reading(
                combined_test_mvp.SpectrometerReading(
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
                combined_test_mvp.SpectrometerReading(
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
            [("1st", 856.0), ("2nd", 860.0), ("3rd", 852.0)],
        )
        annotation_text = "\n".join(
            artist.get_text() for artist in window.spectrum_peak_annotation_artists if hasattr(artist, "get_text")
        )
        self.assertIn("1st 856.000 nm", annotation_text)
        self.assertIn("2nd 860.000 nm", annotation_text)
        self.assertIn("3rd 852.000 nm", annotation_text)
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
        self.assertIn("2nd", text_positions)
        self.assertIn("3rd", text_positions)
        self.assertGreaterEqual(abs(text_positions["2nd"][1] - text_positions["3rd"][1]), y_span * 0.07)
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
        self.assertIn("2nd", text_positions)
        self.assertIn("3rd", text_positions)
        self.assertGreaterEqual(abs(text_positions["2nd"][0] - text_positions["3rd"][0]), x_span * 0.035)
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
        self.assertLess(centroids["3rd"], centroids["2nd"])
        self.assertLess(text_positions["3rd"][0], centroids["3rd"])
        self.assertGreater(text_positions["2nd"][0], centroids["2nd"])
        self.assertEqual(text_alignments["3rd"], "right")
        self.assertEqual(text_alignments["2nd"], "left")
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

    def test_auto_detect_spectrometers_keeps_auto_select_as_current_choice(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakeOceanSpectrometer:
            @staticmethod
            def detect() -> list[int]:
                return [41]

        old_loader = combined_test_mvp.load_spectrometer_components
        try:
            combined_test_mvp.load_spectrometer_components = lambda root: (FakeOceanSpectrometer, None)
            window = MainWindow()

            window.auto_detect_spectrometers()

            self.assertIsNone(window.spectrometer_combo.itemData(0))
            self.assertEqual(window.spectrometer_combo.itemText(0), "Auto select first Ocean Insight")
            self.assertIsInstance(window.spectrometer_combo.itemData(1), SpectrometerOption)
            self.assertIsNone(window.spectrometer_combo.currentData())
            self.assertIsNone(window.collect_spectrometer_settings().device_id)
            window.close()
        finally:
            combined_test_mvp.load_spectrometer_components = old_loader

    def test_main_window_exposes_manual_device_action_buttons(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        for attribute in (
            "connect_i2c_button",
            "read_input_voltage_button",
            "read_output_voltage_button",
            "read_output_current_button",
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
        form = self._group(window, "Power Meter").layout()
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
            window.apply_current_button,
        ):
            self.assertTrue(widget.isEnabled())

        self.assertTrue(window.start_power_meter_button.isHidden())
        self.assertFalse(window.stop_power_meter_button.isHidden())
        self.assertTrue(window.start_spectrometer_button.isHidden())
        self.assertFalse(window.stop_spectrometer_button.isHidden())
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

        annotations = combined_test_mvp.find_spectrum_peak_annotations(list(zip(wavelength, intensity)))

        self.assertEqual(
            [(item.label, round(item.centroid_nm, 3), round(item.peak_intensity, 1)) for item in annotations],
            [("1st", 856.0, 300.0), ("2nd", 860.0, 200.0), ("3rd", 852.0, 80.0)],
        )


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
            sys.modules["power_meter_mvp"] = types.SimpleNamespace(CaihuangPowerMeter=FakeCaihuangPowerMeter)
            thread = combined_test_mvp.PowerMeterDetectThread("ASRL2::INSTR")
            detected: list[PowerMeterOption] = []
            statuses: list[str] = []
            thread.detected.connect(lambda options: detected.extend(options))
            thread.status.connect(statuses.append)

            thread.run()

            self.assertEqual(calls[0], ("ASRL2::INSTR", combined_test_mvp.POWER_METER_PROBE_TIMEOUT_MS))
            self.assertEqual(calls[1], ("ASRL1::INSTR", combined_test_mvp.POWER_METER_PROBE_TIMEOUT_MS))
            self.assertEqual([option.resource for option in detected], ["ASRL2::INSTR"])
            self.assertIn("Detecting power meters", statuses[0])
        finally:
            sys.modules.clear()
            sys.modules.update(old_modules)


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

        device_id = combined_test_mvp.open_spectrometer_device(spectrometer, selected_device_id=7)

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

        self.assertEqual(option.label(), "Ocean Insight | device id 123")


class LocalSpectrometerLoadingTests(unittest.TestCase):
    def test_sth_eb314_launcher_uses_named_conda_environment(self) -> None:
        launcher = Path(__file__).resolve().parent / "run_combined_test_sth_eb314.bat"

        self.assertTrue(launcher.exists())
        content = launcher.read_text(encoding="utf-8")
        self.assertIn("sth_eb314", content)
        self.assertIn("combined_test_mvp.py", content)

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
                ocean_spectrometer, calculate_stats = combined_test_mvp.load_spectrometer_components(root)

                self.assertIn("combined_local_spectrometer_mvp", ocean_spectrometer.__module__)
                self.assertEqual(calculate_stats.__module__, "spectrum_math")
                self.assertEqual(sys.path, old_path)
                self.assertEqual(Path.cwd(), Path(__file__).resolve().parent)
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
