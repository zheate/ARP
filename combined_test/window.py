"""Qt main window for the combined optical test application."""

from __future__ import annotations

import math
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

from PySide6.QtCore import QEvent, QSettings, QTimer, Qt
from PySide6.QtGui import QCloseEvent, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .core import (
    CombinedMeasurement,
    build_set_current_command,
    spectrum_curve_to_rows,
    stability_tolerance_for_power,
)
from .devices import (
    POWER_METER_PROBE_TIMEOUT_MS,
    PowerMeterDetectThread,
    PowerMeterReaderThread,
    SpectrometerReaderThread,
    extract_power_resource_name,
    load_legacy_ch341_controller_class,
    load_spectrometer_components,
    normalize_power_resource_name,
    open_spectrometer_device,
    parse_i2c_address,
    read_power_status_value,
)
from .models import (
    CombinedTestSettings,
    LiveReading,
    PowerMeterOption,
    PowerMeterReading,
    PowerMeterSettings,
    SpectrometerOption,
    SpectrometerReading,
    SpectrometerSettings,
)
from .persistence import (
    ExcelSaveThread,
    append_csv_record,
    build_spectrum_csv_path,
    save_spectrum_curve,
)
from .plots import LivePlots
from .spectrum import (
    SPECTRUM_CENTER_LOCK_HALF_RANGE_NM,
    SPECTRUM_CENTER_LOCK_REQUIRED_SAMPLES,
    SPECTRUM_CENTER_LOCK_TOLERANCE_NM,
    detect_spectrum_saturation,
)
from .excel_export import ExcelTestRecord, build_test_workbook_path, sanitize_sn
from .spectrum_math import calculate_pib, calculate_stats


DEFAULT_POWER_RESOURCE = "ASRL3::INSTR"
DEFAULT_OUTPUT_DIR = "test_records"
DEFAULT_I2C_ADDRESS = 0x41
DEFAULT_I2C_SPEED = 0  # 20 KHz
AUTO_VOUT_AFTER_STABLE_S = 5.0
MIN_VOUT_READ_INTERVAL_S = 5.0
POWER_SUPPLY_COMMAND_MIN_INTERVAL_S = 1.1
DEFAULT_SPECTROMETER_INTEGRATION_US = 10000
LEFT_PANEL_MIN_WIDTH = 350
LEFT_PANEL_MAX_WIDTH = 360


class MainWindow(QMainWindow):
    def __init__(self, input_settings: QSettings | None = None) -> None:
        super().__init__()
        self.input_settings = input_settings or QSettings("Changguang Huaxin", "Pump Driver Integrated Test")
        self.setWindowTitle("Combined Power / Power Meter / Wavelength Test")
        self.resize(1450, 980)
        self.power_meter_detect_thread: PowerMeterDetectThread | None = None
        self.power_meter_reader: PowerMeterReaderThread | None = None
        self.spectrometer_reader: SpectrometerReaderThread | None = None
        self.manual_ch341_controller: Any | None = None
        self.latest_spectrum_wavelength: Any | None = None
        self.latest_spectrum_intensity: Any | None = None
        self.stable_power_points: dict[float, float] = {}
        self.efficiency_points: dict[float, float] = {}
        self.efficiency_voltage_points: dict[float, float] = {}
        self.active_output_current_a: float | None = None
        self.pending_stable_point_current_a: float | None = None
        self.pending_stable_point_generation: int | None = None
        self.recorded_stable_point_current_a: float | None = None
        self.recorded_stable_point_generation: int | None = None
        self.latest_power_meter_reading: PowerMeterReading | None = None
        self.pending_auto_vout_current_a: float | None = None
        self.pending_auto_vout_generation: int | None = None
        self.last_vout_read_monotonic_s: float | None = None
        self.last_power_supply_command_monotonic_s: float | None = None
        self.auto_vout_timer = QTimer(self)
        self.auto_vout_timer.setSingleShot(True)
        self.auto_vout_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.auto_vout_timer.timeout.connect(self.on_auto_vout_timer_timeout)
        self.spectrum_center_candidate_nm: float | None = None
        self.spectrum_center_candidate_count = 0
        self.spectrum_center_locked_nm: float | None = None
        self.centroid_display_samples: deque[float] = deque(maxlen=5)
        self.latest_spectrum_saturated = False
        self.test_session_started_at: datetime | None = None
        self.excel_workbook_path: Path | None = None
        self.excel_recorded_currents: set[float] = set()
        self.pending_excel_records: dict[float, ExcelTestRecord] = {}
        self.excel_save_thread: ExcelSaveThread | None = None

        self.content_widget = QWidget(self)
        self.setCentralWidget(self.content_widget)

        root = self.content_widget
        main = QVBoxLayout(root)
        main.setContentsMargins(16, 14, 16, 12)
        main.setSpacing(10)

        self._build_global_status_bar(main)

        body = QHBoxLayout()
        body.setSpacing(10)
        main.addLayout(body, stretch=1)

        self.left_control_panel = QScrollArea(self)
        self.left_control_panel.setMinimumWidth(LEFT_PANEL_MIN_WIDTH)
        self.left_control_panel.setMaximumWidth(LEFT_PANEL_MAX_WIDTH)
        self.left_control_panel.setWidgetResizable(True)
        self.left_control_panel.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.left_control_panel.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.left_control_panel.setFrameShape(QScrollArea.Shape.NoFrame)
        self.left_control_panel.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self.left_control_content = QWidget(self.left_control_panel)
        self.left_control_panel.setWidget(self.left_control_content)
        left = QVBoxLayout(self.left_control_content)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        body.addWidget(self.left_control_panel)

        self._build_session_group(left)
        self._build_power_supply_group(left)
        self._build_power_meter_group(left)
        self._build_spectrometer_group(left)
        self._build_record_group(left)
        left.addStretch(1)

        self.monitor_panel = QWidget(self)
        monitor = QVBoxLayout(self.monitor_panel)
        monitor.setContentsMargins(0, 0, 0, 0)
        monitor.setSpacing(10)
        body.addWidget(self.monitor_panel, stretch=1)

        self._build_kpi_panel(monitor)
        self._build_curve_panel(monitor)

        self._build_log_panel(main)
        self._configure_button_semantics()
        self._disable_wheel_input_changes()
        self._restore_input_settings()

        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage("Ready")
        self.update_global_status()

    def _restore_input_settings(self) -> None:
        """Restore only operator-entered configuration, never live acquisition state."""
        settings = self.input_settings
        prefix = "input/"
        self.set_current_spin.setValue(settings.value(prefix + "set_current_a", self.set_current_spin.value(), type=float))

        saved_resource = extract_power_resource_name(
            str(settings.value(prefix + "power_resource", self.power_meter_combo.currentText()))
        )
        resource_index = self.power_meter_combo.findText(saved_resource)
        if resource_index < 0 and saved_resource:
            self.power_meter_combo.addItem(saved_resource, None)
            resource_index = self.power_meter_combo.count() - 1
        if resource_index >= 0:
            self.power_meter_combo.setCurrentIndex(resource_index)
        self.power_wavelength_spin.setValue(
            settings.value(prefix + "power_wavelength_nm", self.power_wavelength_spin.value(), type=float)
        )
        self.software_gain_spin.setValue(settings.value(prefix + "software_gain", self.software_gain_spin.value(), type=float))
        self.power_meter_interval_spin.setValue(
            settings.value(prefix + "power_meter_interval_ms", self.power_meter_interval_spin.value(), type=int)
        )

        self.integration_spin.setValue(settings.value(prefix + "integration_time_us", self.integration_spin.value(), type=int))
        self.interval_spin.setValue(settings.value(prefix + "spectrometer_interval_ms", self.interval_spin.value(), type=int))
        self.stable_window_spin.setValue(settings.value(prefix + "stable_window_s", self.stable_window_spin.value(), type=float))
        self.stable_tolerance_spin.setValue(0.15)
        self.sn_field.setText(str(settings.value(prefix + "sn", self.sn_field.text())))
        saved_output_dir = settings.value(
            prefix + "output_dir",
            settings.value(prefix + "csv_path", self.output_dir_field.text()),
        )
        saved_output_path = Path(str(saved_output_dir)).expanduser()
        if saved_output_path.suffix.lower() == ".csv":
            saved_output_path = saved_output_path.parent
        self.output_dir_field.setText(str(saved_output_path))
        self.output_dir_field.setCursorPosition(0)
        self.stop_after_record_check.setChecked(
            settings.value(prefix + "stop_after_record", self.stop_after_record_check.isChecked(), type=bool)
        )
    def save_input_settings(self) -> None:
        settings = self.input_settings
        prefix = "input/"
        settings.setValue(prefix + "set_current_a", self.set_current_spin.value())
        settings.setValue(prefix + "power_resource", self._selected_power_resource())
        settings.setValue(prefix + "power_wavelength_nm", self.power_wavelength_spin.value())
        settings.setValue(prefix + "software_gain", self.software_gain_spin.value())
        settings.setValue(prefix + "power_meter_interval_ms", self.power_meter_interval_spin.value())
        settings.setValue(prefix + "integration_time_us", self.integration_spin.value())
        settings.setValue(prefix + "spectrometer_interval_ms", self.interval_spin.value())
        settings.setValue(prefix + "stable_window_s", self.stable_window_spin.value())
        settings.setValue(prefix + "sn", self.sn_field.text().strip())
        settings.setValue(prefix + "output_dir", self.output_dir_field.text().strip())
        settings.setValue(prefix + "stop_after_record", self.stop_after_record_check.isChecked())
        settings.sync()

    def _build_global_status_bar(self, parent: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(10)

        self.global_status_label = QLabel("Test Idle", self)
        self.global_status_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        row.addWidget(self.global_status_label)
        row.addStretch(1)

        self.global_psu_status_label = QLabel("PSU: Disconnected", self)
        self.global_power_meter_status_label = QLabel("PM: Stopped", self)
        self.global_spectrometer_status_label = QLabel("SP: Stopped", self)
        for label in (
            self.global_psu_status_label,
            self.global_power_meter_status_label,
            self.global_spectrometer_status_label,
        ):
            label.setMinimumWidth(118)
            row.addWidget(label)

        self.start_all_button = QPushButton("Start Acquisition", self)
        self.stop_all_button = QPushButton("Stop All", self)
        self.start_all_button.setMinimumSize(132, 32)
        self.stop_all_button.setMinimumSize(96, 32)
        self.start_all_button.setDefault(True)
        self.start_all_button.clicked.connect(self.start_all)
        self.stop_all_button.clicked.connect(self.stop_all)
        row.addWidget(self.start_all_button)
        row.addWidget(self.stop_all_button)

        parent.addLayout(row)

    def _reserve_group_height(self, group: QGroupBox) -> None:
        group.setMinimumHeight(0)
        group.updateGeometry()
        group.setMinimumHeight(group.sizeHint().height())

    @staticmethod
    def _configure_left_form(form: QFormLayout) -> None:
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setContentsMargins(8, 10, 8, 10)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)

    @staticmethod
    def _configure_action_button(button: QPushButton, minimum_width: int = 88) -> None:
        button.setMinimumWidth(minimum_width)
        button.setMinimumHeight(28)

    def _configure_button_semantics(self) -> None:
        """Keep native controls and reserve color for destructive actions."""
        self.start_all_button.setStyleSheet("font-weight: 600;")
        self.apply_current_button.setStyleSheet("font-weight: 600;")
        self.save_excel_button.setStyleSheet("font-weight: 600;")

        window_is_light = self.palette().color(QPalette.ColorRole.Window).lightness() >= 128
        danger_color = "#b42318" if window_is_light else "#ff7b72"
        destructive_style = (
            f"QPushButton {{ color: {danger_color}; font-weight: 600; }}"
            "QPushButton:disabled { color: palette(mid); }"
        )
        for button in (
            self.stop_all_button,
            self.stop_power_meter_button,
            self.stop_spectrometer_button,
        ):
            button.setStyleSheet(destructive_style)

    def _build_session_group(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("Test Record", self)
        form = QFormLayout(group)
        self._configure_left_form(form)

        self.sn_field = QLineEdit(self)
        self.sn_field.setPlaceholderText("Required before acquisition")
        form.addRow("SN", self.sn_field)

        self.output_dir_field = QLineEdit(str(Path(DEFAULT_OUTPUT_DIR).resolve()), self)
        self.output_dir_field.setPlaceholderText("Excel output folder")
        self.output_dir_field.setToolTip(self.output_dir_field.text())
        self.browse_button = QPushButton("Browse", self)
        self._configure_action_button(self.browse_button, 72)
        self.browse_button.clicked.connect(self.browse_output_dir)
        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        path_row.addWidget(self.output_dir_field, stretch=1)
        path_row.addWidget(self.browse_button)
        form.addRow("Folder", path_row)

        record_actions = QHBoxLayout()
        record_actions.setSpacing(8)
        self.stop_after_record_check = QCheckBox("Stop after record", self)
        self.stop_after_record_check.setChecked(True)
        self.save_excel_button = QPushButton("Save Excel", self)
        self._configure_action_button(self.save_excel_button, 104)
        self.save_excel_button.setEnabled(False)
        self.save_excel_button.clicked.connect(self.save_pending_excel_records)
        record_actions.addWidget(self.stop_after_record_check)
        record_actions.addStretch(1)
        record_actions.addWidget(self.save_excel_button)
        form.addRow("", record_actions)

        self.save_status_label = QLabel("No test point ready", self)
        self.save_status_label.setWordWrap(True)
        form.addRow("Status", self.save_status_label)

        parent.addWidget(group)
        self._reserve_group_height(group)

    def _build_power_supply_group(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("Power Supply", self)
        form = QFormLayout(group)
        self._configure_left_form(form)

        self.set_current_spin = QDoubleSpinBox(self)
        self.set_current_spin.setRange(0.0, 20.0)
        self.set_current_spin.setDecimals(1)
        self.set_current_spin.setSingleStep(1.0)
        self.set_current_spin.setValue(1.0)
        self.set_current_spin.setSuffix(" A")
        self.apply_current_button = QPushButton("Apply Current", self)
        self._configure_action_button(self.apply_current_button)
        self.apply_current_button.clicked.connect(self.apply_output_current)
        current_row = QHBoxLayout()
        current_row.setSpacing(6)
        current_row.addWidget(self.set_current_spin, stretch=1)
        current_row.addWidget(self.apply_current_button)
        form.addRow("Set current", current_row)

        self.connect_i2c_button = QPushButton("Connect CH341", self)
        self._configure_action_button(self.connect_i2c_button)
        self.connect_i2c_button.clicked.connect(self.connect_i2c_device)
        self.i2c_status_label = QLabel("Disconnected", self)
        connection_row = QHBoxLayout()
        connection_row.setSpacing(6)
        connection_row.addWidget(self.i2c_status_label, stretch=1)
        connection_row.addWidget(self.connect_i2c_button)
        form.addRow("Connection", connection_row)

        read_grid = QGridLayout()
        self.read_input_voltage_button = QPushButton("Vin", self)
        self.read_output_voltage_button = QPushButton("Vout", self)
        self.read_output_current_button = QPushButton("Iout", self)
        self.read_temperature_button = QPushButton("Temp", self)
        self.read_input_voltage_button.clicked.connect(self.read_input_voltage)
        self.read_output_voltage_button.clicked.connect(self.read_output_voltage)
        self.read_output_current_button.clicked.connect(self.read_output_current)
        self.read_temperature_button.clicked.connect(self.read_temperature)
        for button in (
            self.read_input_voltage_button,
            self.read_output_voltage_button,
            self.read_output_current_button,
            self.read_temperature_button,
        ):
            button.setMinimumHeight(28)
        read_grid.setHorizontalSpacing(4)
        read_grid.addWidget(self.read_input_voltage_button, 0, 0)
        read_grid.addWidget(self.read_output_voltage_button, 0, 1)
        read_grid.addWidget(self.read_output_current_button, 1, 0)
        read_grid.addWidget(self.read_temperature_button, 1, 1)
        form.addRow("Read", read_grid)

        parent.addWidget(group)
        self._reserve_group_height(group)

    def _build_power_meter_group(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("Power Meter", self)
        form = QFormLayout(group)
        self._configure_left_form(form)

        self.power_meter_combo = QComboBox(self)
        self.power_meter_combo.setEditable(True)
        self.power_meter_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.power_meter_combo.setMinimumContentsLength(8)
        self.power_meter_combo.addItem(DEFAULT_POWER_RESOURCE, None)
        self.detect_power_meter_button = QPushButton("Auto Detect", self)
        self._configure_action_button(self.detect_power_meter_button)
        self.detect_power_meter_button.clicked.connect(self.auto_detect_power_meters)
        device_row = QHBoxLayout()
        device_row.setSpacing(6)
        device_row.addWidget(self.power_meter_combo, stretch=1)
        device_row.addWidget(self.detect_power_meter_button)
        form.addRow("Device", device_row)

        power_actions = QHBoxLayout()
        power_actions.setSpacing(8)
        self.refresh_power_meter_button = QPushButton("Refresh Ports", self)
        self._configure_action_button(self.refresh_power_meter_button)
        self.rel_zero_check = QCheckBox("REL zero", self)
        self.refresh_power_meter_button.clicked.connect(self.refresh_power_meter_resources)
        self.rel_zero_check.toggled.connect(self.set_power_meter_relative_zero)
        power_actions.addWidget(self.refresh_power_meter_button)
        power_actions.addWidget(self.rel_zero_check)
        form.addRow(power_actions)

        self.power_wavelength_spin = QDoubleSpinBox(self)
        self.power_wavelength_spin.setRange(190.0, 25000.0)
        self.power_wavelength_spin.setDecimals(3)
        self.power_wavelength_spin.setSingleStep(0.1)
        self.power_wavelength_spin.setValue(976.0)
        self.power_wavelength_spin.setSuffix(" nm")
        form.addRow("Wavelength", self.power_wavelength_spin)

        self.software_gain_spin = QDoubleSpinBox(self)
        self.software_gain_spin.setRange(0.000001, 1000000.0)
        self.software_gain_spin.setDecimals(6)
        self.software_gain_spin.setValue(1.0)
        form.addRow("Software gain", self.software_gain_spin)

        self.power_meter_interval_spin = QSpinBox(self)
        self.power_meter_interval_spin.setRange(20, 5000)
        self.power_meter_interval_spin.setValue(300)
        self.power_meter_interval_spin.setSingleStep(50)
        self.power_meter_interval_spin.setSuffix(" ms")
        form.addRow("Interval", self.power_meter_interval_spin)

        self.power_meter_status_label = QLabel("Stopped", self)
        power_run_actions = QHBoxLayout()
        power_run_actions.setSpacing(6)
        self.start_power_meter_button = QPushButton("Start", self)
        self.stop_power_meter_button = QPushButton("Stop", self)
        self._configure_action_button(self.start_power_meter_button)
        self._configure_action_button(self.stop_power_meter_button)
        self.stop_power_meter_button.hide()
        self.start_power_meter_button.clicked.connect(self.start_power_meter)
        self.stop_power_meter_button.clicked.connect(self.stop_power_meter)
        power_run_actions.addWidget(self.power_meter_status_label, stretch=1)
        power_run_actions.addWidget(self.start_power_meter_button)
        power_run_actions.addWidget(self.stop_power_meter_button)
        form.addRow("Status", power_run_actions)

        parent.addWidget(group)
        self._reserve_group_height(group)

    def _build_spectrometer_group(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("Spectrometer", self)
        form = QFormLayout(group)
        self._configure_left_form(form)

        self.spectrometer_combo = QComboBox(self)
        self.spectrometer_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.spectrometer_combo.setMinimumContentsLength(8)
        self.spectrometer_combo.addItem("Auto select first Ocean Insight", None)
        self.detect_spectrometer_button = QPushButton("Auto Detect", self)
        self._configure_action_button(self.detect_spectrometer_button)
        self.detect_spectrometer_button.clicked.connect(self.auto_detect_spectrometers)
        device_row = QHBoxLayout()
        device_row.setSpacing(6)
        device_row.addWidget(self.spectrometer_combo, stretch=1)
        device_row.addWidget(self.detect_spectrometer_button)
        form.addRow("Device", device_row)

        self.integration_spin = QSpinBox(self)
        self.integration_spin.setRange(1, 10_000_000)
        self.integration_spin.setValue(DEFAULT_SPECTROMETER_INTEGRATION_US)
        self.integration_spin.setSingleStep(100)
        self.integration_spin.setSuffix(" us")
        form.addRow("Integration", self.integration_spin)

        self.interval_spin = QSpinBox(self)
        self.interval_spin.setRange(50, 5000)
        self.interval_spin.setValue(300)
        self.interval_spin.setSingleStep(50)
        self.interval_spin.setSuffix(" ms")
        form.addRow("Interval", self.interval_spin)

        self.spectrometer_status_label = QLabel("Stopped", self)
        spectrometer_run_actions = QHBoxLayout()
        spectrometer_run_actions.setSpacing(6)
        self.start_spectrometer_button = QPushButton("Start", self)
        self.stop_spectrometer_button = QPushButton("Stop", self)
        self._configure_action_button(self.start_spectrometer_button)
        self._configure_action_button(self.stop_spectrometer_button)
        self.stop_spectrometer_button.hide()
        self.start_spectrometer_button.clicked.connect(self.start_spectrometer)
        self.stop_spectrometer_button.clicked.connect(self.stop_spectrometer)
        spectrometer_run_actions.addWidget(self.spectrometer_status_label, stretch=1)
        spectrometer_run_actions.addWidget(self.start_spectrometer_button)
        spectrometer_run_actions.addWidget(self.stop_spectrometer_button)
        form.addRow("Status", spectrometer_run_actions)

        spectrum_actions = QHBoxLayout()
        spectrum_actions.setSpacing(6)
        self.copy_spectrum_button = QPushButton("Copy CSV", self)
        self.save_spectrum_button = QPushButton("Save CSV", self)
        self._configure_action_button(self.copy_spectrum_button)
        self._configure_action_button(self.save_spectrum_button)
        self.copy_spectrum_button.setEnabled(False)
        self.save_spectrum_button.setEnabled(False)
        self.copy_spectrum_button.clicked.connect(self.copy_spectrum_csv)
        self.save_spectrum_button.clicked.connect(self.save_spectrum_csv)
        spectrum_actions.addWidget(self.copy_spectrum_button)
        spectrum_actions.addWidget(self.save_spectrum_button)
        form.addRow("Spectrum", spectrum_actions)

        parent.addWidget(group)
        self._reserve_group_height(group)

    def _build_record_group(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("Stability", self)
        form = QFormLayout(group)
        self._configure_left_form(form)

        self.stable_window_spin = QDoubleSpinBox(self)
        self.stable_window_spin.setRange(0.5, 300.0)
        self.stable_window_spin.setDecimals(1)
        self.stable_window_spin.setValue(3.0)
        self.stable_window_spin.setSuffix(" s")
        self.stable_window_spin.valueChanged.connect(self.on_stability_settings_changed)
        form.addRow("Stable window", self.stable_window_spin)

        self.stable_tolerance_spin = QDoubleSpinBox(self)
        self.stable_tolerance_spin.setRange(0.0, 100000.0)
        self.stable_tolerance_spin.setDecimals(4)
        self.stable_tolerance_spin.setValue(0.15)
        self.stable_tolerance_spin.setSuffix(" W")
        self.stable_tolerance_spin.setReadOnly(True)
        self.stable_tolerance_spin.setToolTip(
            "Automatic: <100 W = 0.15 W; 100-<200 W = 0.25 W; >=200 W = 0.35 W"
        )
        form.addRow("Allowed span", self.stable_tolerance_spin)

        parent.addWidget(group)
        self._reserve_group_height(group)

    def _build_kpi_panel(self, parent: QVBoxLayout) -> None:
        self.kpi_panel = QWidget(self)
        layout = QGridLayout(self.kpi_panel)
        self.kpi_layout = layout
        self.kpi_cards: list[QWidget] = []
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)

        self.power_card_value, _power_detail = self._add_kpi_card(layout, 0, "Power", "-- W", "")
        self.centroid_card_value, _centroid_detail = self._add_kpi_card(
            layout, 1, "Centroid Wavelength", "-- nm", ""
        )
        self.fwhm_card_value, _fwhm_detail = self._add_kpi_card(layout, 2, "FWHM", "-- nm", "")
        self.spectrum_saturation_label = QLabel(
            "Saturated, reduce integration",
            self.centroid_card_value.parentWidget(),
        )
        window_is_light = self.palette().color(QPalette.ColorRole.Window).lightness() >= 128
        danger_color = "#b42318" if window_is_light else "#ff7b72"
        self.spectrum_saturation_label.setStyleSheet(f"color: {danger_color}; font-weight: 600;")
        self.spectrum_saturation_label.hide()
        self.centroid_card_value.parentWidget().layout().addWidget(self.spectrum_saturation_label)
        self.stability_card_value, self.stability_detail_label = self._add_kpi_card(
            layout,
            3,
            "Stability",
            "Waiting",
            "span -- W / -- s",
        )

        self.power_label = self.power_card_value
        self.centroid_wavelength_label = self.centroid_card_value
        self.fwhm_label = self.fwhm_card_value
        self.stability_label = self.stability_card_value
        parent.addWidget(self.kpi_panel)
        self._relayout_kpi_cards()

    def _add_kpi_card(self, parent: QGridLayout, column: int, title: str, value: str, detail: str) -> tuple[QLabel, QLabel]:
        card = QGroupBox(title, self)
        box = QVBoxLayout(card)
        box.setContentsMargins(10, 10, 10, 8)
        box.setSpacing(2)

        value_label = QLabel(value, card)
        value_font = value_label.font()
        value_font.setPointSize(20)
        value_font.setBold(True)
        value_label.setFont(value_font)
        value_label.setWordWrap(True)
        detail_label = QLabel(detail, card)
        detail_label.setWordWrap(True)

        box.addWidget(value_label)
        box.addWidget(detail_label)
        self.kpi_cards.append(card)
        return value_label, detail_label

    def _relayout_kpi_cards(self) -> None:
        if not hasattr(self, "kpi_layout"):
            return
        layout = self.kpi_layout
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)

        available_width = self.monitor_panel.width() if hasattr(self, "monitor_panel") else 0
        if hasattr(self, "left_control_panel"):
            available_width = max(available_width, self.width() - self.left_control_panel.width() - 64)
        if available_width and available_width < 720:
            for index, card in enumerate(self.kpi_cards):
                layout.addWidget(card, index // 2, index % 2)
            columns = 2
        else:
            for index, card in enumerate(self.kpi_cards):
                layout.addWidget(card, 0, index)
            columns = 4
        for column in range(columns):
            layout.setColumnStretch(column, 1)

    def _build_curve_panel(self, parent: QVBoxLayout) -> None:
        self.live_plots = LivePlots(self)
        self.live_plots.expose_compatibility_attributes(self)
        parent.addWidget(self.live_plots.group, stretch=2)
        self.reset_curves()

    def _build_log_panel(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("Log", self)
        group.setMaximumHeight(58)
        layout = QHBoxLayout(group)
        layout.setContentsMargins(10, 6, 10, 8)
        self.log_text = QLabel("Ready", self)
        self.log_text.setMinimumWidth(0)
        self.log_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.log_text, stretch=1)
        parent.addWidget(group)

    def _disable_wheel_input_changes(self) -> None:
        for widget in self.findChildren(QAbstractSpinBox):
            widget.installEventFilter(self)
        for widget in self.findChildren(QComboBox):
            widget.installEventFilter(self)

    def eventFilter(self, watched: Any, event: Any) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(watched, (QAbstractSpinBox, QComboBox)):
            return True
        return super().eventFilter(watched, event)

    def browse_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose Excel Output Folder", self.output_dir_field.text())
        if path:
            self.output_dir_field.setText(path)
            self.output_dir_field.setToolTip(path)

    def start_all(self) -> None:
        if self.excel_save_thread is not None:
            self.statusBar().showMessage("Wait for the current Excel save to finish")
            return
        if self.power_meter_reader is None and self.spectrometer_reader is None:
            try:
                self.begin_test_session()
            except ValueError as exc:
                QMessageBox.warning(self, "Test Record", str(exc))
                return
        self.start_power_meter()
        self.start_spectrometer()

    def begin_test_session(self, reset_records: bool = True) -> Path:
        sn = sanitize_sn(self.sn_field.text())
        output_dir_text = self.output_dir_field.text().strip()
        if not output_dir_text:
            raise ValueError("Excel output folder cannot be empty")
        self.test_session_started_at = datetime.now()
        self.excel_workbook_path = build_test_workbook_path(Path(output_dir_text), sn, self.test_session_started_at)
        if reset_records:
            self.excel_recorded_currents.clear()
            self.pending_excel_records.clear()
            self.save_status_label.setText("No test point ready")
            self.save_excel_button.setEnabled(False)
        self.add_log(f"Test record: {self.excel_workbook_path}")
        return self.excel_workbook_path

    def stop_all(self) -> None:
        self.stop_power_meter()
        self.stop_spectrometer()

    def update_global_status(self) -> None:
        if not hasattr(self, "global_status_label"):
            return
        psu_connected = self._manual_i2c_connected()
        power_running = self.power_meter_reader is not None
        spectrometer_running = self.spectrometer_reader is not None
        power_detecting = self.power_meter_detect_thread is not None

        self.global_status_label.setText("Test Running" if power_running or spectrometer_running else "Test Idle")
        self.global_psu_status_label.setText("PSU: Connected" if psu_connected else "PSU: Disconnected")
        if power_detecting:
            self.global_power_meter_status_label.setText("PM: Detecting")
        else:
            self.global_power_meter_status_label.setText("PM: Running" if power_running else "PM: Stopped")
        self.global_spectrometer_status_label.setText("SP: Running" if spectrometer_running else "SP: Stopped")
        self.stop_all_button.setEnabled(power_running or spectrometer_running)

        if hasattr(self, "power_meter_status_label"):
            self.power_meter_status_label.setText("Detecting" if power_detecting else ("Running" if power_running else "Stopped"))
        if hasattr(self, "spectrometer_status_label"):
            self.spectrometer_status_label.setText("Running" if spectrometer_running else "Stopped")

    def _manual_i2c_connected(self) -> bool:
        return self.manual_ch341_controller is not None and bool(getattr(self.manual_ch341_controller, "is_connected", False))

    def _get_manual_ch341_controller(self) -> Any:
        if self.manual_ch341_controller is None:
            controller_class = load_legacy_ch341_controller_class()
            self.manual_ch341_controller = controller_class()
        return self.manual_ch341_controller

    def connect_i2c_device(self) -> None:
        controller = self._get_manual_ch341_controller()
        if self._manual_i2c_connected():
            controller.disconnect_device()
            self.connect_i2c_button.setText("Connect CH341")
            self.i2c_status_label.setText("Disconnected")
            self.add_log("CH341 disconnected")
            self.update_global_status()
            return

        try:
            controller.set_i2c_speed(DEFAULT_I2C_SPEED)
            connected, detail = controller.connect_device(0)
            if not connected:
                raise RuntimeError(str(detail))
            self.connect_i2c_button.setText("Disconnect CH341")
            self.i2c_status_label.setText("Connected")
            self.add_log(f"CH341 connected: {detail}")
            self.update_global_status()
        except Exception as exc:
            QMessageBox.critical(self, "CH341", str(exc))

    def _require_manual_i2c_controller(self) -> Any | None:
        if not self._manual_i2c_connected():
            QMessageBox.warning(self, "CH341", "Connect CH341 first.")
            return None
        return self.manual_ch341_controller

    def begin_power_supply_command(self, command_name: str) -> bool:
        """Reserve the power-supply bus so I2C commands remain safely spaced."""
        now = time.monotonic()
        if self.last_power_supply_command_monotonic_s is not None:
            elapsed_s = now - self.last_power_supply_command_monotonic_s
            remaining_s = POWER_SUPPLY_COMMAND_MIN_INTERVAL_S - elapsed_s
            if remaining_s > 0.0:
                message = f"{command_name} blocked; wait {remaining_s:.1f} s before the next power-supply command"
                self.statusBar().showMessage(message)
                self.add_log(message)
                return False
        self.last_power_supply_command_monotonic_s = now
        return True

    def read_input_voltage(self) -> None:
        self.execute_i2c_read([0xB4, 0x88, 0x00, 0x00], "Input voltage", "V")

    def read_output_voltage(self, automatic: bool = False) -> None:
        remaining_s = self.vout_read_interval_remaining_s()
        if remaining_s > 0.0:
            message = f"Vout read is rate-limited; wait {remaining_s:.1f} s"
            self.statusBar().showMessage(message)
            self.add_log(message)
            if automatic:
                self.schedule_auto_vout_read(delay_s=remaining_s)
            return

        voltage_v = self.execute_i2c_read([0xB4, 0x8B, 0x00, 0x00], "Output voltage", "V")
        if voltage_v is not None:
            self.last_vout_read_monotonic_s = time.monotonic()
            self.record_efficiency_from_vout(voltage_v)

    def vout_read_interval_remaining_s(self) -> float:
        if self.last_vout_read_monotonic_s is None:
            return 0.0
        elapsed_s = time.monotonic() - self.last_vout_read_monotonic_s
        return max(0.0, MIN_VOUT_READ_INTERVAL_S - elapsed_s)

    def cancel_auto_vout_read(self) -> None:
        self.auto_vout_timer.stop()
        self.pending_auto_vout_current_a = None
        self.pending_auto_vout_generation = None

    def schedule_auto_vout_read(self, delay_s: float = AUTO_VOUT_AFTER_STABLE_S) -> None:
        current_a = self.recorded_stable_point_current_a
        generation = self.recorded_stable_point_generation
        if (
            current_a is None
            or current_a <= 0.0
            or generation is None
            or current_a in self.efficiency_voltage_points
        ):
            return

        delay_s = max(float(delay_s), self.vout_read_interval_remaining_s())
        self.pending_auto_vout_current_a = current_a
        self.pending_auto_vout_generation = generation
        self.auto_vout_timer.start(max(1, math.ceil(delay_s * 1000.0)))
        self.statusBar().showMessage(f"Power is stable; Vout will be read automatically in {delay_s:.1f} s")
        self.add_log(f"Stable power at {current_a:.3f} A; scheduling automatic Vout read in {delay_s:.1f} s")

    def on_auto_vout_timer_timeout(self) -> None:
        current_a = self.pending_auto_vout_current_a
        generation = self.pending_auto_vout_generation
        self.pending_auto_vout_current_a = None
        self.pending_auto_vout_generation = None
        reading = self.latest_power_meter_reading
        if (
            current_a is None
            or generation is None
            or current_a != self.active_output_current_a
            or current_a != self.recorded_stable_point_current_a
            or generation != self.recorded_stable_point_generation
            or reading is None
            or not reading.stable
            or reading.stability_generation != generation
        ):
            self.add_log("Automatic Vout read cancelled because the current point is no longer stable")
            return
        self.read_output_voltage(automatic=True)

    def read_output_current(self) -> None:
        self.execute_i2c_read([0xB4, 0x8C, 0x00, 0x00], "Output current", "A")

    def read_temperature(self) -> None:
        self.execute_i2c_read([0xB4, 0x8D, 0x00, 0x00], "Module temperature", "°C")

    def execute_i2c_read(self, command: list[int], name: str, unit: str) -> float | None:
        controller = self._require_manual_i2c_controller()
        if controller is None:
            return None
        if not self.begin_power_supply_command(name):
            return None
        try:
            value = read_power_status_value(controller, DEFAULT_I2C_ADDRESS, command)
            raw_command = " ".join(f"{item:02X}" for item in command)
            self.add_log(f"{name}: {value:.2f} {unit} ({raw_command})")
            self.statusBar().showMessage(f"{name}: {value:.2f} {unit}")
            return value
        except Exception as exc:
            QMessageBox.critical(self, name, str(exc))
            return None

    def record_efficiency_from_vout(self, voltage_v: float) -> None:
        current_a = self.active_output_current_a
        if current_a is None or current_a <= 0.0:
            self.statusBar().showMessage("Efficiency is recorded only for current points greater than zero")
            self.add_log("Vout read; efficiency not plotted for 0 A")
            return
        if self.pending_stable_point_current_a == current_a:
            self.statusBar().showMessage("Wait for the newly applied current point to become stable before reading Vout")
            self.add_log("Vout read; efficiency not updated while the new current point is settling")
            return
        if current_a not in self.stable_power_points:
            self.statusBar().showMessage("Wait for the current point to become stable before reading Vout")
            self.add_log("Vout read; efficiency not plotted because no stable power point is available")
            return
        if voltage_v <= 0.0:
            self.statusBar().showMessage("Efficiency requires Vout greater than zero")
            self.add_log("Vout read; efficiency not plotted because Vout is zero")
            return

        power_w = self.stable_power_points[current_a]
        self.efficiency_voltage_points[current_a] = voltage_v
        efficiency_percent = self.update_efficiency_point(current_a)
        if current_a == self.pending_auto_vout_current_a:
            self.cancel_auto_vout_read()
        self.update_stable_power_curve()
        self.statusBar().showMessage(f"Efficiency at {current_a:.3f} A: {efficiency_percent:.2f}%")
        self.add_log(
            f"Efficiency point: {current_a:.3f} A, {power_w:.3f} W / "
            f"({current_a:.3f} A × {voltage_v:.3f} V) = {efficiency_percent:.2f}%"
        )

        self.queue_excel_test_point(current_a, voltage_v, power_w, efficiency_percent / 100.0)

    def queue_excel_test_point(
        self,
        current_a: float,
        voltage_v: float,
        power_w: float,
        efficiency: float,
    ) -> None:
        if self.latest_spectrum_wavelength is None or self.latest_spectrum_intensity is None:
            self.add_log("Excel record skipped: no spectrum is available")
            self.statusBar().showMessage("Cannot prepare test point until spectrum data is available")
            return
        saturation = detect_spectrum_saturation(self.latest_spectrum_intensity)
        if saturation.saturated:
            if current_a not in self.excel_recorded_currents:
                self.pending_excel_records.pop(current_a, None)
            self.save_excel_button.setEnabled(
                any(current not in self.excel_recorded_currents for current in self.pending_excel_records)
            )
            self.save_status_label.setText(f"{current_a:.1f} A saturated - not queued")
            message = (
                f"Spectrum saturated at {current_a:.1f} A "
                f"({saturation.peak_intensity:.0f} counts, {saturation.consecutive_pixels} pixels); "
                "reduce integration time"
            )
            self.statusBar().showMessage(message)
            self.add_log(message)
            return
        stats = calculate_stats(self.latest_spectrum_wavelength, self.latest_spectrum_intensity)
        self.pending_excel_records[current_a] = ExcelTestRecord(
            current_a=current_a,
            voltage_v=voltage_v,
            power_w=power_w,
            efficiency=efficiency,
            peak_wavelength_nm=stats.peak_wavelength_nm,
            centroid_nm=stats.centroid_nm,
            fwhm_nm=stats.fwhm_nm,
            pib=calculate_pib(self.latest_spectrum_wavelength, self.latest_spectrum_intensity),
            wavelength=list(self.latest_spectrum_wavelength),
            intensity=list(self.latest_spectrum_intensity),
        )
        pending_count = len([current for current in self.pending_excel_records if current not in self.excel_recorded_currents])
        self.save_excel_button.setEnabled(pending_count > 0)
        self.save_status_label.setText(f"{pending_count} test point(s) ready")
        self.statusBar().showMessage(f"Test point {current_a:.1f} A is ready; click Save Excel")

    def save_pending_excel_records(self) -> None:
        if self.excel_save_thread is not None:
            return
        unsaved_records = sorted(
            (
                record
                for current, record in self.pending_excel_records.items()
                if current not in self.excel_recorded_currents
            ),
            key=lambda record: record.current_a,
        )
        if not unsaved_records:
            QMessageBox.information(self, "Excel Save", "No unsaved test point is available.")
            return
        if self.excel_workbook_path is None:
            try:
                self.begin_test_session(reset_records=False)
            except ValueError as exc:
                QMessageBox.warning(self, "Excel Save", str(exc))
                return

        records_snapshot = sorted(self.pending_excel_records.values(), key=lambda record: record.current_a)
        self.excel_save_thread = ExcelSaveThread(self.excel_workbook_path, records_snapshot, self)
        self.excel_save_thread.saved.connect(self.on_excel_save_succeeded)
        self.excel_save_thread.failed.connect(self.on_excel_save_failed)
        self.excel_save_thread.finished.connect(self.on_excel_save_finished)
        self.save_excel_button.setEnabled(False)
        self.save_excel_button.setText("Saving...")
        self.start_all_button.setEnabled(False)
        self.save_status_label.setText(f"Saving {len(records_snapshot)} point(s)...")
        self.add_log(f"Saving {len(records_snapshot)} test point(s) in background")
        self.excel_save_thread.start()

    def on_excel_save_succeeded(self, elapsed_s: float) -> None:
        thread = self.excel_save_thread
        if thread is None:
            return
        for saved_record in thread.records:
            current_record = self.pending_excel_records.get(saved_record.current_a)
            if current_record == saved_record:
                self.excel_recorded_currents.add(saved_record.current_a)
        remaining_count = len(
            [current for current in self.pending_excel_records if current not in self.excel_recorded_currents]
        )
        if remaining_count:
            self.save_status_label.setText(f"Saved in {elapsed_s:.2f}s; {remaining_count} new point(s) ready")
        else:
            self.save_status_label.setText(f"Saved in {elapsed_s:.2f}s: {thread.path.name}")
        self.statusBar().showMessage(f"Excel saved in {elapsed_s:.2f} s: {thread.path.name}")
        self.add_log(f"Excel saved in {elapsed_s:.2f} s: {thread.path}")

    def on_excel_save_failed(self, message: str) -> None:
        self.save_status_label.setText("Save failed")
        self.add_log(f"Excel save failed: {message}")
        QMessageBox.critical(self, "Excel Save", message)

    def on_excel_save_finished(self) -> None:
        thread = self.excel_save_thread
        self.excel_save_thread = None
        self.save_excel_button.setEnabled(
            any(current not in self.excel_recorded_currents for current in self.pending_excel_records)
        )
        self.save_excel_button.setText("Save Excel")
        self.start_all_button.setEnabled(True)
        if thread is not None:
            thread.deleteLater()

    def apply_output_current(self) -> None:
        controller = self._require_manual_i2c_controller()
        if controller is None:
            return
        if not self.begin_power_supply_command("Set output current"):
            return
        try:
            command = build_set_current_command(self.set_current_spin.value())
            success, result = controller.i2c_write(DEFAULT_I2C_ADDRESS, command)
            if not success:
                raise RuntimeError(str(result))
            self.cancel_auto_vout_read()
            self.active_output_current_a = float(self.set_current_spin.value())
            self.pending_stable_point_current_a = self.active_output_current_a
            self.recorded_stable_point_current_a = None
            self.recorded_stable_point_generation = None
            if self.power_meter_reader is not None:
                self.pending_stable_point_generation = self.power_meter_reader.reset_stability_window()
            else:
                self.pending_stable_point_generation = None
            self.update_stable_power_curve()
            raw_command = " ".join(f"{item:02X}" for item in command)
            self.add_log(f"Output current set to {self.set_current_spin.value():.1f} A ({raw_command})")
            self.statusBar().showMessage(f"Output current set to {self.set_current_spin.value():.1f} A")
        except Exception as exc:
            QMessageBox.critical(self, "Apply Current", str(exc))

    def refresh_power_meter_resources(self) -> None:
        current = self._selected_power_resource()
        try:
            import pyvisa

            rm = pyvisa.ResourceManager()
            try:
                resources = sorted(str(item) for item in rm.list_resources() if str(item).startswith("ASRL"))
            finally:
                rm.close()
            self.power_meter_combo.clear()
            self.power_meter_combo.addItems(resources)
            if current:
                index = self.power_meter_combo.findText(current)
                if index >= 0:
                    self.power_meter_combo.setCurrentIndex(index)
                else:
                    self.power_meter_combo.setEditText(current)
            elif resources:
                self.power_meter_combo.setCurrentIndex(0)
            self.statusBar().showMessage(f"Found {len(resources)} serial resource(s)")
            self.add_log(f"Found {len(resources)} serial resource(s)")
        except Exception as exc:
            QMessageBox.critical(self, "Refresh Ports", str(exc))

    def set_power_meter_relative_zero(self, enabled: bool) -> None:
        resource = self._selected_power_resource()
        if not resource:
            QMessageBox.warning(self, "REL Zero", "Select a power meter first.")
            return
        try:
            from tools.power_meter_mvp import CaihuangPowerMeter

            meter = CaihuangPowerMeter(resource)
            try:
                meter.set_relative_zero(enabled)
            finally:
                meter.close()
            state = "enabled" if enabled else "disabled"
            self.statusBar().showMessage(f"REL zero {state}")
            self.add_log(f"Power meter REL zero {state}")
        except Exception as exc:
            QMessageBox.critical(self, "REL Zero", str(exc))

    def auto_detect_power_meters(self) -> None:
        if self.power_meter_detect_thread is not None:
            return
        self.power_meter_detect_thread = PowerMeterDetectThread(self._selected_power_resource(), self)
        self.power_meter_detect_thread.detected.connect(self.on_power_meter_detected)
        self.power_meter_detect_thread.status.connect(self.on_status)
        self.power_meter_detect_thread.failed.connect(self.on_power_meter_detect_failed)
        self.power_meter_detect_thread.finished.connect(self.on_power_meter_detect_finished)
        self.set_power_meter_detecting_state(True)
        self.statusBar().showMessage("Detecting power meters...")
        self.power_meter_detect_thread.start()

    def auto_detect_spectrometers(self) -> None:
        try:
            OceanSpectrometer, _calculate_stats = load_spectrometer_components(None)

            device_ids = OceanSpectrometer.detect()
            self.spectrometer_combo.clear()
            self.spectrometer_combo.addItem("Auto select first Ocean Insight", None)
            if not device_ids:
                QMessageBox.warning(
                    self,
                    "Spectrometer Auto Detect",
                    "OceanDirect found 0 spectrometers. Check the Ocean Insight driver.",
                )
                self.statusBar().showMessage("No spectrometer detected")
                return

            for device_id in device_ids:
                option = SpectrometerOption(device_id=int(device_id))
                self.spectrometer_combo.addItem(option.label(), option)
            self.spectrometer_combo.setCurrentIndex(0)
            self.statusBar().showMessage(f"Detected {len(device_ids)} spectrometer(s)")
            self.add_log(f"Detected {len(device_ids)} spectrometer(s)")
        except Exception as exc:
            QMessageBox.critical(self, "Spectrometer Auto Detect", str(exc))

    def collect_settings(self) -> CombinedTestSettings:
        return CombinedTestSettings(
            i2c_address=DEFAULT_I2C_ADDRESS,
            i2c_speed=DEFAULT_I2C_SPEED,
            set_current_a=self.set_current_spin.value(),
            power_resource=self._selected_power_resource(),
            power_meter_wavelength_nm=self.power_wavelength_spin.value(),
            software_gain=self.software_gain_spin.value(),
            integration_time_us=self.integration_spin.value(),
            interval_ms=self.interval_spin.value(),
            stable_window_s=self.stable_window_spin.value(),
            stable_tolerance_w=self.stable_tolerance_spin.value(),
            output_dir=Path(self.output_dir_field.text()).expanduser(),
            stop_after_record=self.stop_after_record_check.isChecked(),
            spectrometer_device_id=self._selected_spectrometer_device_id(),
        )

    def _selected_power_resource(self) -> str:
        option = self.power_meter_combo.currentData()
        if isinstance(option, PowerMeterOption):
            return option.resource
        return extract_power_resource_name(self.power_meter_combo.currentText())

    def _selected_spectrometer_device_id(self) -> int | None:
        option = self.spectrometer_combo.currentData()
        if isinstance(option, SpectrometerOption):
            return option.device_id
        return None

    def collect_power_meter_settings(self) -> PowerMeterSettings:
        return PowerMeterSettings(
            resource=self._selected_power_resource(),
            wavelength_nm=self.power_wavelength_spin.value(),
            software_gain=self.software_gain_spin.value(),
            interval_ms=self.power_meter_interval_spin.value(),
            stable_window_s=self.stable_window_spin.value(),
            stable_tolerance_w=self.stable_tolerance_spin.value(),
        )

    def on_stability_settings_changed(self, _value: float) -> None:
        """Synchronize live stability criteria with the acquisition thread."""
        if self.power_meter_reader is None:
            return
        self.power_meter_reader.update_stability_settings(
            self.stable_window_spin.value(),
            self.stable_tolerance_spin.value(),
        )

    def collect_spectrometer_settings(self) -> SpectrometerSettings:
        return SpectrometerSettings(
            integration_time_us=self.integration_spin.value(),
            interval_ms=self.interval_spin.value(),
            device_id=self._selected_spectrometer_device_id(),
        )

    def start_power_meter(self) -> None:
        if self.power_meter_reader is not None:
            return
        try:
            settings = self.collect_power_meter_settings()
        except Exception as exc:
            QMessageBox.warning(self, "Power Meter", str(exc))
            return
        if not settings.resource:
            QMessageBox.warning(self, "Power Meter", "Power meter resource is empty.")
            return

        self.cancel_auto_vout_read()
        self.reset_power_curve()
        self.reset_stable_power_curve()
        self.active_output_current_a = float(self.set_current_spin.value())
        self.pending_stable_point_current_a = self.active_output_current_a
        self.pending_stable_point_generation = 0
        self.recorded_stable_point_current_a = None
        self.recorded_stable_point_generation = None
        self.add_log("Starting power meter acquisition")
        self.power_meter_reader = PowerMeterReaderThread(settings, self)
        self.power_meter_reader.reading.connect(self.on_power_meter_reading)
        self.power_meter_reader.status.connect(self.on_status)
        self.power_meter_reader.failed.connect(self.on_power_meter_failed)
        self.power_meter_reader.finished.connect(self.on_power_meter_finished)
        self.power_meter_reader.start()
        self.set_power_meter_running_state(True)

    def stop_power_meter(self) -> None:
        self.cancel_auto_vout_read()
        if self.power_meter_reader is not None:
            self.add_log("Stopping power meter acquisition")
            self.power_meter_reader.stop()
            self.power_meter_reader.wait(3000)

    def start_spectrometer(self) -> None:
        if self.spectrometer_reader is not None:
            return
        try:
            settings = self.collect_spectrometer_settings()
        except Exception as exc:
            QMessageBox.warning(self, "Spectrometer", str(exc))
            return

        self.reset_spectrum_curve()
        self.copy_spectrum_button.setEnabled(False)
        self.save_spectrum_button.setEnabled(False)
        self.add_log("Starting spectrometer acquisition")
        self.spectrometer_reader = SpectrometerReaderThread(settings, self)
        self.spectrometer_reader.reading.connect(self.on_spectrometer_reading)
        self.spectrometer_reader.spectrum.connect(self.on_spectrum_curve)
        self.spectrometer_reader.status.connect(self.on_status)
        self.spectrometer_reader.failed.connect(self.on_spectrometer_failed)
        self.spectrometer_reader.finished.connect(self.on_spectrometer_finished)
        self.spectrometer_reader.start()
        self.set_spectrometer_running_state(True)

    def stop_spectrometer(self) -> None:
        if self.spectrometer_reader is not None:
            self.add_log("Stopping spectrometer acquisition")
            self.spectrometer_reader.stop()
            self.spectrometer_reader.wait(3000)

    def update_stability_card(
        self,
        stable: bool,
        span_w: float,
        covered_window_s: float,
        tolerance_w: float | None = None,
    ) -> None:
        target_window_s = self.stable_window_spin.value() if hasattr(self, "stable_window_spin") else 0.0
        if tolerance_w is None:
            tolerance_w = self.stable_tolerance_spin.value() if hasattr(self, "stable_tolerance_spin") else 0.0
        displayed_window_s = min(max(covered_window_s, 0.0), target_window_s)
        self.stability_label.setText("Stable" if stable else "Waiting")
        window_is_light = self.palette().color(QPalette.ColorRole.Window).lightness() >= 128
        stable_color = "#18794e" if window_is_light else "#57d69a"
        self.stability_label.setStyleSheet(f"color: {stable_color};" if stable else "")
        self.stability_detail_label.setText(
            f"{displayed_window_s:.2f} / {target_window_s:.2f} s\n"
            f"span {span_w:.4f} W <= {tolerance_w:.4f} W"
        )

    def on_power_meter_reading(self, reading: PowerMeterReading) -> None:
        self.latest_power_meter_reading = reading
        self.power_label.setText(f"{reading.power_w:.3f} W")
        tolerance_w = (
            reading.stable_tolerance_w
            if math.isfinite(reading.stable_tolerance_w)
            else stability_tolerance_for_power(reading.power_w)
        )
        signals_were_blocked = self.stable_tolerance_spin.blockSignals(True)
        try:
            self.stable_tolerance_spin.setValue(tolerance_w)
        finally:
            self.stable_tolerance_spin.blockSignals(signals_were_blocked)
        self.update_stability_card(reading.stable, reading.stable_span_w, reading.stable_window_s, tolerance_w)
        self.update_power_curve(reading.elapsed_s, reading.power_w)
        self.capture_stable_power_point(reading)

    def on_spectrometer_reading(self, reading: SpectrometerReading) -> None:
        self.update_centroid_display(reading.centroid_nm)
        self.fwhm_label.setText(
            "-- nm"
            if self.latest_spectrum_saturated
            else f"{self._format_optional(reading.fwhm_nm)} nm"
        )
        self.update_spectrum_center_lock(reading)

    def on_live_reading(self, reading: LiveReading) -> None:
        self.power_label.setText(f"{reading.power_w:.3f} W")
        self.update_centroid_display(reading.centroid_nm)
        self.fwhm_label.setText(
            "-- nm"
            if self.latest_spectrum_saturated
            else f"{self._format_optional(reading.fwhm_nm)} nm"
        )
        self.update_spectrum_center_lock(
            SpectrometerReading(
                peak_wavelength_nm=reading.peak_wavelength_nm,
                centroid_nm=reading.centroid_nm,
                fwhm_nm=reading.fwhm_nm,
            )
        )
        self.update_stability_card(
            reading.stable,
            reading.stable_span_w,
            reading.stable_window_s,
            stability_tolerance_for_power(reading.power_w),
        )
        self.update_power_curve(reading.elapsed_s, reading.power_w)

    def on_recorded(self, timestamp: str, measurement: CombinedMeasurement) -> None:
        self.save_status_label.setText(f"Saved {measurement.set_current_a:.1f} A at {timestamp[11:]}")
        self.add_log(
            "Recorded stable point: "
            f"set {measurement.set_current_a} A, "
            f"Iout {measurement.output_current_a:.3f} A, "
            f"Vout {measurement.output_voltage_v:.3f} V, "
            f"power {measurement.power_w:.3f} W, "
            f"peak {measurement.peak_wavelength_nm:.3f} nm, "
            f"spectrum {measurement.spectrum_csv_path}"
        )

    def on_spectrum_curve(self, wavelength: Any, intensity: Any) -> None:
        self.latest_spectrum_wavelength = wavelength
        self.latest_spectrum_intensity = intensity
        saturation = detect_spectrum_saturation(intensity)
        was_saturated = self.latest_spectrum_saturated
        self.latest_spectrum_saturated = saturation.saturated
        self.spectrum_saturation_label.setVisible(saturation.saturated)
        window_is_light = self.palette().color(QPalette.ColorRole.Window).lightness() >= 128
        danger_color = "#b42318" if window_is_light else "#ff7b72"
        self.centroid_card_value.setStyleSheet(f"color: {danger_color};" if saturation.saturated else "")
        if saturation.saturated and not was_saturated:
            message = (
                f"Spectrum saturated: {saturation.peak_intensity:.0f} counts across "
                f"{saturation.consecutive_pixels} pixels; reduce integration time"
            )
            self.statusBar().showMessage(message)
            self.add_log(message)
        elif was_saturated and not saturation.saturated:
            self.statusBar().showMessage("Spectrum saturation cleared")
            self.add_log("Spectrum saturation cleared")
        self.copy_spectrum_button.setEnabled(True)
        self.save_spectrum_button.setEnabled(True)
        self.update_spectrum_curve(wavelength, intensity)

    def reset_curves(self) -> None:
        self.reset_power_curve()
        self.reset_stable_power_curve()
        self.reset_spectrum_curve()

    def reset_power_curve(self) -> None:
        self.live_plots.reset_power()

    def reset_stable_power_curve(self) -> None:
        self.cancel_auto_vout_read()
        self.stable_power_points.clear()
        self.efficiency_points.clear()
        self.efficiency_voltage_points.clear()
        self.recorded_stable_point_current_a = None
        self.recorded_stable_point_generation = None
        self.update_stable_power_curve()

    def capture_stable_power_point(self, reading: PowerMeterReading) -> None:
        current_a = self.pending_stable_point_current_a
        if current_a is None:
            if (
                not reading.stable
                and self.pending_auto_vout_current_a == self.active_output_current_a
                and self.pending_auto_vout_generation == reading.stability_generation
            ):
                self.cancel_auto_vout_read()
                self.add_log("Automatic Vout read cancelled because power is no longer stable")
            self.update_latest_stable_power_point(reading)
            if (
                reading.stable
                and self.recorded_stable_point_current_a == self.active_output_current_a
                and self.recorded_stable_point_generation == reading.stability_generation
                and self.active_output_current_a not in self.efficiency_voltage_points
                and self.pending_auto_vout_current_a is None
            ):
                self.schedule_auto_vout_read()
            return
        if not reading.stable:
            return
        if (
            self.pending_stable_point_generation is not None
            and reading.stability_generation != self.pending_stable_point_generation
        ):
            return

        if current_a <= 0.0:
            self.pending_stable_point_current_a = None
            self.pending_stable_point_generation = None
            self.statusBar().showMessage("0 A is stable; no power or efficiency point recorded")
            self.add_log("0 A stable; skipped power and efficiency point")
            return

        self.stable_power_points[current_a] = float(reading.power_w)
        self.efficiency_points.pop(current_a, None)
        self.efficiency_voltage_points.pop(current_a, None)
        self.pending_stable_point_current_a = None
        self.pending_stable_point_generation = None
        self.recorded_stable_point_current_a = current_a
        self.recorded_stable_point_generation = reading.stability_generation
        self.update_stable_power_curve()
        self.statusBar().showMessage(f"Stable power recorded at {current_a:.3f} A: {reading.power_w:.3f} W")
        self.add_log(f"Stable power point: {current_a:.3f} A, {reading.power_w:.3f} W")
        self.schedule_auto_vout_read()

    def update_latest_stable_power_point(self, reading: PowerMeterReading) -> None:
        current_a = self.recorded_stable_point_current_a
        if (
            current_a is None
            or current_a != self.active_output_current_a
            or self.recorded_stable_point_generation != reading.stability_generation
            or not reading.stable
        ):
            return

        self.stable_power_points[current_a] = float(reading.power_w)
        self.update_efficiency_point(current_a)
        self.update_stable_power_curve()

    def update_efficiency_point(self, current_a: float) -> float:
        power_w = self.stable_power_points[current_a]
        voltage_v = self.efficiency_voltage_points.get(current_a)
        if voltage_v is None or voltage_v <= 0.0:
            return math.nan
        efficiency_percent = power_w / current_a / voltage_v * 100.0
        self.efficiency_points[current_a] = efficiency_percent
        return efficiency_percent

    def update_stable_power_curve(self) -> None:
        self.live_plots.update_stable(self.stable_power_points, self.efficiency_points)

    def reset_spectrum_curve(self) -> None:
        self.centroid_display_samples.clear()
        self.latest_spectrum_saturated = False
        if hasattr(self, "centroid_wavelength_label"):
            self.centroid_wavelength_label.setText("-- nm")
            self.centroid_card_value.setStyleSheet("")
        if hasattr(self, "fwhm_label"):
            self.fwhm_label.setText("-- nm")
        if hasattr(self, "spectrum_saturation_label"):
            self.spectrum_saturation_label.hide()
        self.spectrum_center_candidate_nm = None
        self.spectrum_center_candidate_count = 0
        self.spectrum_center_locked_nm = None
        self.live_plots.reset_spectrum()

    def update_centroid_display(self, centroid_nm: float) -> None:
        if self.latest_spectrum_saturated:
            self.centroid_wavelength_label.setText("SATURATED")
            return
        value = float(centroid_nm)
        if not math.isfinite(value):
            self.centroid_wavelength_label.setText("-- nm")
            return
        self.centroid_display_samples.append(value)
        self.centroid_wavelength_label.setText(f"{median(self.centroid_display_samples):.3f} nm")

    def update_power_curve(self, elapsed_s: float, power_w: float) -> None:
        self.live_plots.update_power(elapsed_s, power_w)

    def update_spectrum_curve(self, wavelength: Any, intensity: Any) -> None:
        self.live_plots.update_spectrum(wavelength, intensity, self.spectrum_center_locked_nm)

    def update_spectrum_center_lock(self, reading: SpectrometerReading) -> None:
        if self.spectrum_center_locked_nm is not None:
            return

        # The whole-spectrum centroid moves with baseline and broadband noise.
        # The highest peak is the stable reference that users expect the ±20 nm
        # view to follow; centroid remains a fallback for incomplete readings.
        center_nm = reading.peak_wavelength_nm
        if not math.isfinite(float(center_nm)):
            center_nm = reading.centroid_nm
        center = float(center_nm)
        if not math.isfinite(center):
            self.spectrum_center_candidate_nm = None
            self.spectrum_center_candidate_count = 0
            return

        if (
            self.spectrum_center_candidate_nm is None
            or abs(center - self.spectrum_center_candidate_nm) > SPECTRUM_CENTER_LOCK_TOLERANCE_NM
        ):
            self.spectrum_center_candidate_nm = center
            self.spectrum_center_candidate_count = 1
        else:
            self.spectrum_center_candidate_count += 1
            count = self.spectrum_center_candidate_count
            previous = self.spectrum_center_candidate_nm
            self.spectrum_center_candidate_nm = previous + (center - previous) / count

        if self.spectrum_center_candidate_count >= SPECTRUM_CENTER_LOCK_REQUIRED_SAMPLES:
            self.spectrum_center_locked_nm = self.spectrum_center_candidate_nm
            self.on_status(
                "Spectrum x-axis locked: "
                f"{self.spectrum_center_locked_nm:.3f} nm +/- {SPECTRUM_CENTER_LOCK_HALF_RANGE_NM:g} nm"
            )
            if self.latest_spectrum_wavelength is not None and self.latest_spectrum_intensity is not None:
                self.update_spectrum_curve(self.latest_spectrum_wavelength, self.latest_spectrum_intensity)

    def copy_spectrum_csv(self) -> None:
        if self.latest_spectrum_wavelength is None or self.latest_spectrum_intensity is None:
            return
        output = spectrum_curve_to_rows(self.latest_spectrum_wavelength, self.latest_spectrum_intensity)
        text = "\n".join(",".join(row) for row in output) + "\n"
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage("Spectrum copied as CSV")
        self.add_log("Spectrum copied as CSV")

    def save_spectrum_csv(self) -> None:
        if self.latest_spectrum_wavelength is None or self.latest_spectrum_intensity is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Spectrum CSV", "spectrum.csv", "CSV Files (*.csv)")
        if not path:
            return
        save_spectrum_curve(Path(path), self.latest_spectrum_wavelength, self.latest_spectrum_intensity)
        self.statusBar().showMessage(f"Saved {path}")
        self.add_log(f"Saved spectrum CSV: {path}")

    def on_power_meter_detected(self, options: list[PowerMeterOption]) -> None:
        self.power_meter_combo.clear()
        if not options:
            self.power_meter_combo.addItem(DEFAULT_POWER_RESOURCE, None)
            QMessageBox.warning(self, "Power Meter Auto Detect", "No supported power meter was detected.")
            self.statusBar().showMessage("No supported power meter detected")
            return

        for option in options:
            self.power_meter_combo.addItem(option.label(), option)
        self.power_meter_combo.setCurrentIndex(0)
        self.statusBar().showMessage(f"Detected {len(options)} power meter(s)")
        self.add_log(f"Detected {len(options)} power meter(s)")

    def on_power_meter_detect_failed(self, message: str) -> None:
        self.add_log(f"Power meter auto detect error: {message}")
        QMessageBox.critical(self, "Power Meter Auto Detect", message)

    def on_power_meter_detect_finished(self) -> None:
        self.power_meter_detect_thread = None
        self.set_power_meter_detecting_state(False)

    def on_status(self, message: str) -> None:
        self.statusBar().showMessage(message)
        self.add_log(message)

    def on_power_meter_failed(self, message: str) -> None:
        self.add_log(f"Power meter error: {message}")
        QMessageBox.critical(self, "Power Meter Error", message)

    def on_spectrometer_failed(self, message: str) -> None:
        self.add_log(f"Spectrometer error: {message}")
        QMessageBox.critical(self, "Spectrometer Error", message)

    def on_power_meter_finished(self) -> None:
        self.power_meter_reader = None
        self.set_power_meter_running_state(False)
        self.statusBar().showMessage("Power meter stopped")
        self.add_log("Power meter stopped")

    def on_spectrometer_finished(self) -> None:
        self.spectrometer_reader = None
        self.set_spectrometer_running_state(False)
        self.statusBar().showMessage("Spectrometer stopped")
        self.add_log("Spectrometer stopped")

    def set_power_meter_running_state(self, running: bool) -> None:
        detecting = self.power_meter_detect_thread is not None
        self.start_power_meter_button.setHidden(running)
        self.stop_power_meter_button.setHidden(not running)
        self.start_power_meter_button.setEnabled(not running and not detecting)
        self.stop_power_meter_button.setEnabled(running)
        self.detect_power_meter_button.setEnabled(not running and not detecting)
        self.refresh_power_meter_button.setEnabled(not running and not detecting)
        self.rel_zero_check.setEnabled(not running and not detecting)
        self.power_meter_combo.setEnabled(not running and not detecting)
        self.power_wavelength_spin.setEnabled(not running and not detecting)
        self.software_gain_spin.setEnabled(not running and not detecting)
        self.power_meter_interval_spin.setEnabled(not running and not detecting)
        self.update_global_status()

    def set_power_meter_detecting_state(self, detecting: bool) -> None:
        running = self.power_meter_reader is not None
        self.start_power_meter_button.setHidden(running)
        self.stop_power_meter_button.setHidden(not running)
        self.start_power_meter_button.setEnabled(not running and not detecting)
        self.stop_power_meter_button.setEnabled(running)
        self.detect_power_meter_button.setEnabled(not running and not detecting)
        self.refresh_power_meter_button.setEnabled(not running and not detecting)
        self.rel_zero_check.setEnabled(not running and not detecting)
        self.power_meter_combo.setEnabled(not running and not detecting)
        self.power_wavelength_spin.setEnabled(not running and not detecting)
        self.software_gain_spin.setEnabled(not running and not detecting)
        self.power_meter_interval_spin.setEnabled(not running and not detecting)
        self.update_global_status()

    def set_spectrometer_running_state(self, running: bool) -> None:
        self.start_spectrometer_button.setHidden(running)
        self.stop_spectrometer_button.setHidden(not running)
        self.start_spectrometer_button.setEnabled(not running)
        self.stop_spectrometer_button.setEnabled(running)
        self.detect_spectrometer_button.setEnabled(not running)
        self.spectrometer_combo.setEnabled(not running)
        self.integration_spin.setEnabled(not running)
        self.interval_spin.setEnabled(not running)
        self.update_global_status()

    def add_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.setText(f"[{timestamp}] {message}")

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._relayout_kpi_cards()

    @staticmethod
    def _format_optional(value: float) -> str:
        if not math.isfinite(float(value)):
            return "--"
        return f"{value:.3f}"

    def closeEvent(self, event: QCloseEvent) -> None:
        self.save_input_settings()
        if self.excel_save_thread is not None:
            self.excel_save_thread.wait()
        if self.power_meter_detect_thread is not None:
            self.power_meter_detect_thread.wait(3000)
        self.stop_power_meter()
        self.stop_spectrometer()
        if self.manual_ch341_controller is not None:
            try:
                self.manual_ch341_controller.disconnect_device()
            except Exception:
                pass
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
