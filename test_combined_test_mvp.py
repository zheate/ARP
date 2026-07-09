import tempfile
import sys
import os
import unittest
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QApplication, QScrollArea

from combined_test_mvp import (
    LiveReading,
    MainWindow,
    PowerMeterOption,
    SpectrometerOption,
    add_scripts_runner_root,
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
    def test_main_window_can_be_constructed(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertIsNotNone(window.log_text)
        window.close()

    def test_main_window_uses_scroll_area_for_tall_layout(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertIsInstance(window.centralWidget(), QScrollArea)
        self.assertGreaterEqual(window.content_widget.minimumHeight(), 1120)
        self.assertGreaterEqual(window.content_widget.minimumWidth(), 1280)
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
            "rel_zero_on_button",
            "rel_zero_off_button",
            "copy_spectrum_button",
            "save_spectrum_button",
        ):
            self.assertTrue(hasattr(window, attribute), attribute)

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

        self.assertFalse(window.start_power_meter_button.isEnabled())
        self.assertTrue(window.stop_power_meter_button.isEnabled())
        self.assertFalse(window.start_spectrometer_button.isEnabled())
        self.assertTrue(window.stop_spectrometer_button.isEnabled())
        window.close()


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


class ScriptsRunnerPathTests(unittest.TestCase):
    def test_sth_eb314_launcher_uses_named_conda_environment(self) -> None:
        launcher = Path(__file__).resolve().parent / "run_combined_test_sth_eb314.bat"

        self.assertTrue(launcher.exists())
        content = launcher.read_text(encoding="utf-8")
        self.assertIn("sth_eb314", content)
        self.assertIn("combined_test_mvp.py", content)

    def test_add_scripts_runner_root_makes_application_importable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package_dir = root / "application" / "models" / "device_models"
            package_dir.mkdir(parents=True)
            (root / "application" / "__init__.py").write_text("", encoding="utf-8")
            (root / "application" / "models" / "__init__.py").write_text("", encoding="utf-8")
            (package_dir / "__init__.py").write_text("", encoding="utf-8")
            (package_dir / "ocean_direct_control.py").write_text("class OceanDirectControl: pass\n", encoding="utf-8")

            old_path = list(sys.path)
            old_modules = dict(sys.modules)
            old_cwd = Path.cwd()
            try:
                added = add_scripts_runner_root(root)

                self.assertEqual(added, root.resolve())
                self.assertEqual(Path(sys.path[0]), Path(__file__).resolve().parent)
                self.assertIn(str(root.resolve()), sys.path)
                self.assertEqual(Path.cwd(), Path(__file__).resolve().parent)
                import application.models.device_models.ocean_direct_control as ocean_module

                self.assertTrue(hasattr(ocean_module, "OceanDirectControl"))
            finally:
                os.chdir(old_cwd)
                sys.path[:] = old_path
                for name in list(sys.modules):
                    if name.startswith("application"):
                        sys.modules.pop(name, None)
                sys.modules.update({key: value for key, value in old_modules.items() if key.startswith("application")})


if __name__ == "__main__":
    unittest.main()
