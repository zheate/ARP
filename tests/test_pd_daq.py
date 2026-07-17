from __future__ import annotations

import os
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from matplotlib.colors import to_hex
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QFormLayout, QWidget

from combined_test.window import MainWindow
import tools.pd_daq_mvp as pd_daq_module
from tools.pd_daq_mvp import (
    DEFAULT_HISTORY_S,
    DaqDeviceInfo,
    MAX_PLOT_POINTS_PER_SECOND,
    PLOT_BUFFER_POINTS,
    PdDaqPanel,
    PdDaqSettings,
    calibrate_voltage,
    channels_for_terminal_mode,
    default_csv_path,
    plot_sample_indices,
    positive_axis_upper,
    positive_voltage_ranges,
    summarize_samples,
)


class PdDaqTests(unittest.TestCase):
    def test_calibration_uses_linear_scale_and_offset(self) -> None:
        self.assertAlmostEqual(calibrate_voltage(0.25, 4.0, -0.1), 0.9)

    def test_calibration_returns_absolute_pd_value_but_raw_voltage_can_be_negative(self) -> None:
        self.assertAlmostEqual(calibrate_voltage(-0.25, 1.0, 0.0), 0.25)
        self.assertAlmostEqual(calibrate_voltage(0.10, 1.0, -0.25), 0.15)

    def test_plot_sampling_keeps_two_hundred_points_per_second_across_blocks(self) -> None:
        first = list(plot_sample_indices(0, 100, 1_000.0))
        second = list(plot_sample_indices(100, 100, 1_000.0))
        self.assertEqual(first, list(range(0, 100, 5)))
        self.assertEqual(second, list(range(0, 100, 5)))
        self.assertEqual(len(first) + len(second), 40)

    def test_plot_sampling_stays_globally_aligned_for_uneven_blocks(self) -> None:
        local_indices = list(plot_sample_indices(103, 11, 1_000.0))
        self.assertEqual(local_indices, [2, 7])
        self.assertEqual([103 + index for index in local_indices], [105, 110])

    def test_plot_buffer_can_hold_the_complete_visible_history(self) -> None:
        required_points = DEFAULT_HISTORY_S * MAX_PLOT_POINTS_PER_SECOND
        self.assertGreaterEqual(PLOT_BUFFER_POINTS, required_points)

    def test_positive_axis_upper_tracks_visible_pd_peak_with_padding(self) -> None:
        self.assertAlmostEqual(positive_axis_upper([0.1, 0.4, 0.2]), 0.44)
        self.assertAlmostEqual(positive_axis_upper([0.001], 0.20), 0.0012)

    def test_positive_axis_upper_has_safe_empty_and_zero_defaults(self) -> None:
        self.assertEqual(positive_axis_upper([]), 1.0)
        self.assertEqual(positive_axis_upper([0.0, 0.0]), 1.0)

    def test_sample_summary_reports_population_statistics(self) -> None:
        summary = summarize_samples([1.0, 2.0, 3.0])
        self.assertEqual(summary.latest, 3.0)
        self.assertEqual(summary.mean, 2.0)
        self.assertEqual(summary.minimum, 1.0)
        self.assertEqual(summary.maximum, 3.0)
        self.assertAlmostEqual(summary.standard_deviation, (2.0 / 3.0) ** 0.5)
        self.assertAlmostEqual(summary.rms, (14.0 / 3.0) ** 0.5)

    def test_positive_voltage_ranges_are_sorted_and_unique(self) -> None:
        self.assertEqual(positive_voltage_ranges([-10, 10, -1, 1, 10]), (1.0, 10.0))

    def test_usb_6009_diff_mode_only_exposes_ai0_through_ai3(self) -> None:
        device = DaqDeviceInfo(
            name="Dev4",
            product_type="USB-6009",
            serial_number=1,
            ai_channels=tuple(f"Dev4/ai{i}" for i in range(8)),
            voltage_ranges=(1.0, 10.0),
            max_single_channel_rate_hz=48_000.0,
            simulated=False,
        )
        self.assertEqual(
            channels_for_terminal_mode(device, "DIFF"),
            ("Dev4/ai0", "Dev4/ai1", "Dev4/ai2", "Dev4/ai3"),
        )
        self.assertEqual(channels_for_terminal_mode(device, "RSE"), device.ai_channels)

    def test_settings_reject_sample_rate_above_device_limit(self) -> None:
        settings = PdDaqSettings(
            channel="Dev4/ai0",
            terminal_mode="DIFF",
            voltage_range_v=10.0,
            sample_rate_hz=48_001.0,
            block_size=100,
            scale=1.0,
            offset=0.0,
            unit="V",
        )
        with self.assertRaisesRegex(ValueError, "不能超过"):
            settings.validate(48_000.0)

    def test_default_csv_path_contains_channel_and_timestamp(self) -> None:
        path = default_csv_path(
            Path("records"),
            "Dev4/ai0",
            datetime(2026, 7, 14, 18, 30, 45),
        )
        self.assertEqual(path, Path("records/PD_Dev4_ai0_2026_07_14_18_30_45.csv"))


class PdDaqPanelUiTests(unittest.TestCase):
    def test_settings_are_grouped_into_four_operator_sections(self) -> None:
        app = QApplication.instance() or QApplication([])
        panel = PdDaqPanel(auto_refresh=False)

        self.assertEqual(
            [
                panel.device_settings_group.title(),
                panel.sampling_settings_group.title(),
                panel.calibration_settings_group.title(),
                panel.storage_settings_group.title(),
            ],
            ["设备与接线", "采样参数", "线性标定", "数据保存"],
        )
        self.assertEqual(panel.settings_grid.getItemPosition(0)[:2], (0, 0))
        self.assertEqual(panel.settings_grid.getItemPosition(1)[:2], (0, 1))
        self.assertEqual(panel.settings_grid.getItemPosition(2)[:2], (1, 0))
        self.assertEqual(panel.settings_grid.getItemPosition(3)[:2], (1, 1))
        panel.close()

    def test_editable_inputs_have_accessible_names_and_label_buddies(self) -> None:
        app = QApplication.instance() or QApplication([])
        panel = PdDaqPanel(auto_refresh=False)

        expected_names = {
            panel.device_combo: "采集卡",
            panel.channel_combo: "模拟输入通道",
            panel.terminal_combo: "接线方式",
            panel.range_combo: "输入量程",
            panel.sample_rate_spin: "采样率",
            panel.block_size_spin: "每批点数",
            panel.scale_spin: "线性标定比例系数",
            panel.offset_spin: "线性标定偏置",
            panel.unit_edit: "显示单位",
            panel.save_checkbox: "保存完整原始数据",
            panel.output_dir_edit: "数据保存文件夹",
        }
        for widget, accessible_name in expected_names.items():
            with self.subTest(accessible_name=accessible_name):
                self.assertEqual(widget.accessibleName(), accessible_name)

        expected_buddies = {
            panel.device_field_label: panel.device_combo,
            panel.channel_field_label: panel.channel_combo,
            panel.terminal_field_label: panel.terminal_combo,
            panel.range_field_label: panel.range_combo,
            panel.sample_rate_field_label: panel.sample_rate_spin,
            panel.block_size_field_label: panel.block_size_spin,
            panel.scale_field_label: panel.scale_spin,
            panel.offset_field_label: panel.offset_spin,
            panel.unit_field_label: panel.unit_edit,
            panel.data_save_field_label: panel.save_checkbox,
            panel.output_dir_field_label: panel.output_dir_edit,
        }
        for label, buddy in expected_buddies.items():
            with self.subTest(label=label.text()):
                self.assertIs(label.buddy(), buddy)

        self.assertEqual(
            panel.settings_layout.rowWrapPolicy(),
            QFormLayout.RowWrapPolicy.WrapLongRows,
        )
        panel.close()

    def test_plot_colors_follow_dark_qt_palette(self) -> None:
        app = QApplication.instance() or QApplication([])
        panel = PdDaqPanel(auto_refresh=False)
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#202020"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#282828"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#f0f0f0"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#308cc6"))
        palette.setColor(QPalette.ColorRole.Mid, QColor("#606060"))
        panel.setPalette(palette)
        app.processEvents()
        palette = panel.palette()

        self.assertEqual(
            to_hex(panel.figure.get_facecolor()),
            palette.color(QPalette.ColorRole.Window).name(),
        )
        self.assertEqual(
            to_hex(panel.axis.get_facecolor()),
            palette.color(QPalette.ColorRole.Base).name(),
        )
        self.assertEqual(
            panel.axis.xaxis.label.get_color(),
            palette.color(QPalette.ColorRole.Text).name(),
        )
        self.assertEqual(
            panel.axis.yaxis.label.get_color(),
            palette.color(QPalette.ColorRole.Text).name(),
        )
        self.assertEqual(
            panel.line.get_color(),
            palette.color(QPalette.ColorRole.Highlight).name(),
        )
        self.assertTrue(panel.axis.get_xgridlines())
        self.assertEqual(
            panel.axis.get_xgridlines()[0].get_color(),
            palette.color(QPalette.ColorRole.Mid).name(),
        )
        self.assertEqual(panel.canvas.accessibleName(), "PD 实时趋势图")
        panel.close()


class MainWindowPdIntegrationTests(unittest.TestCase):
    def test_main_window_contains_lazy_pd_acquisition_tab(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertIsInstance(window.centralWidget(), QWidget)
        self.assertIs(window.centralWidget(), window.central_shell)
        self.assertGreaterEqual(window.central_shell.layout().indexOf(window.main_tabs), 0)
        self.assertEqual(
            [window.main_tabs.tabText(index) for index in range(window.main_tabs.count())],
            ["自动测试", "手动调试", "当前记录", "PD 采集"],
        )
        self.assertEqual(window.pd_panel.device_combo.count(), 0)
        window.close()

    def test_opening_pd_tab_detects_devices(self) -> None:
        app = QApplication.instance() or QApplication([])
        original_discover = pd_daq_module.discover_ni_daq_devices
        fake_device = DaqDeviceInfo(
            name="Dev9",
            product_type="USB-6009",
            serial_number=9,
            ai_channels=tuple(f"Dev9/ai{i}" for i in range(8)),
            voltage_ranges=(1.0, 10.0),
            max_single_channel_rate_hz=48_000.0,
            simulated=False,
        )
        pd_daq_module.discover_ni_daq_devices = lambda: ("NI-DAQmx test", [fake_device])
        try:
            window = MainWindow()
            window.main_tabs.setCurrentIndex(window.pd_tab_index)
            app.processEvents()

            self.assertEqual(window.pd_panel.device_combo.count(), 1)
            self.assertEqual(window.pd_panel.channel_combo.currentText(), "Dev9/ai0")
            self.assertTrue(window.pd_panel.start_button.isEnabled())
            window.close()
        finally:
            pd_daq_module.discover_ni_daq_devices = original_discover

    def test_pd_can_be_opened_and_started_after_manual_power_is_energized(self) -> None:
        app = QApplication.instance() or QApplication([])
        original_discover = pd_daq_module.discover_ni_daq_devices
        fake_device = DaqDeviceInfo(
            name="Dev9",
            product_type="USB-6009",
            serial_number=9,
            ai_channels=("Dev9/ai0",),
            voltage_ranges=(10.0,),
            max_single_channel_rate_hz=48_000.0,
            simulated=False,
        )
        pd_daq_module.discover_ni_daq_devices = lambda: ("NI-DAQmx test", [fake_device])
        try:
            window = MainWindow()
            window.main_tabs.setCurrentIndex(window.manual_tab_index)
            window.manual_power_tab_lock_active = True
            window._update_main_tab_access()

            window.main_tabs.setCurrentIndex(window.pd_tab_index)
            app.processEvents()

            self.assertEqual(window.main_tabs.currentIndex(), window.pd_tab_index)
            self.assertEqual(window.pd_panel.channel_combo.currentText(), "Dev9/ai0")
            self.assertTrue(window.pd_panel.start_button.isEnabled())
            window.close()
        finally:
            pd_daq_module.discover_ni_daq_devices = original_discover

    def test_stop_all_and_background_tracking_include_pd_reader(self) -> None:
        app = QApplication.instance() or QApplication([])

        class FakeReader:
            def __init__(self) -> None:
                self.stopped = False

            def stop(self) -> None:
                self.stopped = True

            def isRunning(self) -> bool:
                return True

        window = MainWindow()
        reader = FakeReader()
        window.pd_panel.reader = reader  # type: ignore[assignment]

        self.assertTrue(window._background_tasks_are_running())
        window.stop_all()
        self.assertTrue(reader.stopped)

        window.pd_panel.reader = None
        window.close()


if __name__ == "__main__":
    unittest.main()
