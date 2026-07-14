from __future__ import annotations

import os
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTabWidget

from combined_test.window import MainWindow
import tools.pd_daq_mvp as pd_daq_module
from tools.pd_daq_mvp import (
    DEFAULT_HISTORY_S,
    DaqDeviceInfo,
    MAX_PLOT_POINTS_PER_SECOND,
    PLOT_BUFFER_POINTS,
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


class MainWindowPdIntegrationTests(unittest.TestCase):
    def test_main_window_contains_lazy_pd_acquisition_tab(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()

        self.assertIsInstance(window.centralWidget(), QTabWidget)
        self.assertEqual(
            [window.main_tabs.tabText(index) for index in range(window.main_tabs.count())],
            ["自动测试", "手动调试", "PD 采集", "测试记录"],
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
