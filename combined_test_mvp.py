from __future__ import annotations

import csv
import importlib.util
import math
import os
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from combined_test_core import (
    CSV_HEADER,
    CombinedMeasurement,
    PowerStabilityDetector,
    build_set_current_command,
    decode_i2c_value,
    record_to_row,
    spectrum_curve_to_rows,
)


DEFAULT_POWER_RESOURCE = "ASRL3::INSTR"
DEFAULT_CSV_PATH = "combined_test_records.csv"
DEFAULT_SCRIPTS_RUNNER_ROOT = os.environ.get("SCRIPTS_RUNNER_ROOT", r"E:\scripts_runner - 副本")
PROJECT_ROOT = Path(__file__).resolve().parent
MAX_CURVE_POINTS = 10000
POWER_PLOT_HISTORY_S = 60.0
CONTENT_MIN_WIDTH = 1280
CONTENT_MIN_HEIGHT = 1120


@dataclass(frozen=True)
class CombinedTestSettings:
    i2c_address: int
    i2c_speed: int
    set_current_a: int
    power_resource: str
    power_meter_wavelength_nm: int
    software_gain: float
    integration_time_us: int
    interval_ms: int
    stable_window_s: float
    stable_tolerance_w: float
    csv_path: Path
    stop_after_record: bool
    scripts_runner_root: Path | None = None
    spectrometer_device_id: int | None = None


@dataclass(frozen=True)
class PowerMeterOption:
    resource: str
    device_type: str
    detail: str

    def label(self) -> str:
        return f"{self.device_type} | {self.resource} | {self.detail}"


@dataclass(frozen=True)
class SpectrometerOption:
    device_id: int
    device_type: str = "Ocean Insight"

    def label(self) -> str:
        return f"{self.device_type} | device id {self.device_id}"


@dataclass(frozen=True)
class LiveReading:
    elapsed_s: float
    power_w: float
    peak_wavelength_nm: float
    centroid_nm: float
    fwhm_nm: float
    stable: bool
    stable_span_w: float
    stable_window_s: float


def load_legacy_ch341_controller_class() -> type:
    root = Path(__file__).resolve().parent
    candidates = sorted(root.glob("*TEST.py"))
    for path in candidates:
        spec = importlib.util.spec_from_file_location("legacy_ch341_control", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        controller_class = getattr(module, "CH341I2CController", None)
        if controller_class is not None:
            return controller_class
    raise RuntimeError("Cannot find CH341I2CController in *TEST.py")


def parse_i2c_address(text: str) -> int:
    value = text.strip()
    if value.lower().startswith("0x"):
        value = value[2:]
    if not value:
        raise ValueError("I2C address is empty")
    address = int(value, 16)
    if address < 0 or address > 0x7F:
        raise ValueError("I2C address must be in range 0x00..0x7F")
    return address


def add_scripts_runner_root(root: Path | str | None) -> Path | None:
    if root is None:
        return None
    value = Path(root).expanduser()
    if str(value).strip() == "." and not Path(str(root)).is_absolute():
        value = Path.cwd()
    resolved = value.resolve()
    if not resolved.exists():
        raise RuntimeError(f"Scripts runner root does not exist: {resolved}")
    if not (resolved / "application").exists():
        raise RuntimeError(f"Scripts runner root must contain an application folder: {resolved}")

    path_text = str(resolved)
    while path_text in sys.path:
        sys.path.remove(path_text)
    sys.path.insert(0, path_text)
    os.chdir(PROJECT_ROOT)
    return resolved


def append_csv_record(path: Path, timestamp: str, measurement: CombinedMeasurement) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        if write_header:
            writer.writerow(CSV_HEADER)
        writer.writerow(record_to_row(timestamp, measurement))


def build_spectrum_csv_path(main_csv_path: Path, timestamp: datetime) -> Path:
    base = main_csv_path.expanduser()
    spectrum_dir = base.with_name(f"{base.stem}_spectra")
    filename = f"spectrum_{timestamp.strftime('%Y%m%d_%H%M%S_%f')}.csv"
    return spectrum_dir / filename


def save_spectrum_curve(path: Path, wavelength: Any, intensity: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerows(spectrum_curve_to_rows(wavelength, intensity))


def read_power_status_value(ch341_controller: Any, i2c_address: int, command: list[int]) -> float:
    success, result = ch341_controller.i2c_write_read(i2c_address, command, 4)
    if not success:
        raise RuntimeError(f"I2C read failed for command {' '.join(f'{item:02X}' for item in command)}: {result}")
    return decode_i2c_value(result)


class CombinedTestThread(QThread):
    live = Signal(object)
    recorded = Signal(str, object)
    spectrum = Signal(object, object)
    status = Signal(str)
    failed = Signal(str)

    def __init__(self, settings: CombinedTestSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._running = False

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        ch341_controller = None
        meter = None
        spectrometer = None
        try:
            if self.settings.scripts_runner_root is not None:
                added_root = add_scripts_runner_root(self.settings.scripts_runner_root)
                if added_root is not None:
                    self.status.emit(f"Scripts runner root added: {added_root}")

            try:
                from power_meter_mvp import CaihuangPowerMeter, normalize_resource
            except ModuleNotFoundError as exc:
                raise RuntimeError(f"Power meter dependency missing: {exc.name}. Install pyvisa and NI/VISA runtime.") from exc

            try:
                from spectrometer_mvp import OceanSpectrometer, calculate_stats
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    f"Spectrometer dependency missing: {exc.name}. Run from the environment that contains the OceanDirect application package."
                ) from exc

            controller_class = load_legacy_ch341_controller_class()
            ch341_controller = controller_class()
            ch341_controller.set_i2c_speed(self.settings.i2c_speed)
            connected, detail = ch341_controller.connect_device(0)
            if not connected:
                raise RuntimeError(f"CH341 connect failed: {detail}")
            self.status.emit("CH341 connected")

            set_command = build_set_current_command(self.settings.set_current_a)
            success, result = ch341_controller.i2c_write(self.settings.i2c_address, set_command)
            if not success:
                raise RuntimeError(f"Set output current failed: {result}")
            self.status.emit(f"Output current set to {self.settings.set_current_a} A")
            time.sleep(0.5)

            meter = CaihuangPowerMeter(self.settings.power_resource)
            if meter.test() != "OK":
                raise RuntimeError("Power meter test did not return OK")
            meter.set_wavelength(self.settings.power_meter_wavelength_nm)
            self.status.emit(f"Power meter connected: {normalize_resource(self.settings.power_resource)}")

            spectrometer = OceanSpectrometer()
            if self.settings.spectrometer_device_id is None:
                device_id = spectrometer.open_first()
            else:
                state = spectrometer.control.find_usb_devices()
                if state == -1:
                    raise RuntimeError("OceanDirect failed to search USB spectrometers")
                device_ids = [int(item) for item in spectrometer.control.get_device_ids()]
                if self.settings.spectrometer_device_id not in device_ids:
                    raise RuntimeError(
                        f"Selected spectrometer device id {self.settings.spectrometer_device_id} was not found. "
                        f"Detected ids: {device_ids}"
                    )
                state = spectrometer.control.open_device(self.settings.spectrometer_device_id)
                if state == -1:
                    raise RuntimeError(f"Failed to open spectrometer device id {self.settings.spectrometer_device_id}")
                spectrometer.device_id = self.settings.spectrometer_device_id
                device_id = self.settings.spectrometer_device_id
            spectrometer.set_integration_time(self.settings.integration_time_us)
            self.status.emit(f"Spectrometer connected, device id {device_id}")

            detector = PowerStabilityDetector(self.settings.stable_window_s, self.settings.stable_tolerance_w)
            start = time.monotonic()
            recorded_once = False
            self._running = True

            while self._running:
                elapsed = time.monotonic() - start
                power_w = meter.read_power_w() * self.settings.software_gain
                wavelength, intensity = spectrometer.read_spectrum()
                self.spectrum.emit(wavelength, intensity)
                stats = calculate_stats(wavelength, intensity)
                stability = detector.add_sample(elapsed, power_w)

                reading = LiveReading(
                    elapsed_s=elapsed,
                    power_w=power_w,
                    peak_wavelength_nm=stats.peak_wavelength_nm,
                    centroid_nm=stats.centroid_nm,
                    fwhm_nm=stats.fwhm_nm,
                    stable=stability.stable,
                    stable_span_w=stability.span_w,
                    stable_window_s=stability.window_s,
                )
                self.live.emit(reading)

                if stability.stable and not recorded_once:
                    output_voltage_v = read_power_status_value(ch341_controller, self.settings.i2c_address, [0xB4, 0x8B, 0x00, 0x00])
                    output_current_a = read_power_status_value(ch341_controller, self.settings.i2c_address, [0xB4, 0x8C, 0x00, 0x00])
                    recorded_at = datetime.now()
                    spectrum_csv_path = build_spectrum_csv_path(self.settings.csv_path, recorded_at)
                    save_spectrum_curve(spectrum_csv_path, wavelength, intensity)
                    measurement = CombinedMeasurement(
                        elapsed_s=elapsed,
                        set_current_a=self.settings.set_current_a,
                        output_current_a=output_current_a,
                        output_voltage_v=output_voltage_v,
                        power_w=power_w,
                        peak_wavelength_nm=stats.peak_wavelength_nm,
                        centroid_nm=stats.centroid_nm,
                        fwhm_nm=stats.fwhm_nm,
                        stable_span_w=stability.span_w,
                        stable_window_s=stability.window_s,
                        spectrum_csv_path=str(spectrum_csv_path),
                    )
                    timestamp = recorded_at.strftime("%Y-%m-%d %H:%M:%S")
                    append_csv_record(self.settings.csv_path, timestamp, measurement)
                    self.recorded.emit(timestamp, measurement)
                    recorded_once = True
                    if self.settings.stop_after_record:
                        self._running = False
                        break

                self.msleep(self.settings.interval_ms)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if spectrometer is not None:
                try:
                    spectrometer.close()
                except Exception:
                    pass
            if meter is not None:
                try:
                    meter.close()
                except Exception:
                    pass
            if ch341_controller is not None:
                try:
                    ch341_controller.disconnect_device()
                except Exception:
                    pass


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Combined Power / Power Meter / Wavelength Test")
        self.resize(1450, 980)
        self.worker: CombinedTestThread | None = None
        self.manual_ch341_controller: Any | None = None
        self.latest_spectrum_wavelength: Any | None = None
        self.latest_spectrum_intensity: Any | None = None
        self.power_curve_times: deque[float] = deque(maxlen=MAX_CURVE_POINTS)
        self.power_curve_values: deque[float] = deque(maxlen=MAX_CURVE_POINTS)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setCentralWidget(self.scroll_area)

        self.content_widget = QWidget(self.scroll_area)
        self.content_widget.setMinimumSize(CONTENT_MIN_WIDTH, CONTENT_MIN_HEIGHT)
        self.scroll_area.setWidget(self.content_widget)

        root = self.content_widget
        main = QVBoxLayout(root)
        main.setContentsMargins(12, 12, 12, 12)
        main.setSpacing(10)

        settings_grid = QGridLayout()
        settings_grid.setColumnStretch(0, 1)
        settings_grid.setColumnStretch(1, 1)
        settings_grid.setColumnStretch(2, 1)
        main.addLayout(settings_grid)

        self._build_power_supply_group(settings_grid, 0)
        self._build_power_meter_group(settings_grid, 1)
        self._build_spectrometer_group(settings_grid, 2)

        self._build_test_options(main)
        self._build_live_panel(main)
        self._build_curve_panel(main)

        self.log_text = QTextEdit(self)
        self.log_text.setReadOnly(True)

        self._build_actions(main)
        main.addWidget(self.log_text, stretch=1)

        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage("Ready")

    def _build_power_supply_group(self, parent: QGridLayout, column: int) -> None:
        group = QGroupBox("Power Supply", self)
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.i2c_addr_field = QLineEdit("0x41", self)
        self.i2c_addr_field.textChanged.connect(self.update_i2c_address_info)
        form.addRow("I2C address", self.i2c_addr_field)

        self.i2c_address_info_label = QLabel("", self)
        form.addRow("", self.i2c_address_info_label)

        self.i2c_speed_combo = QComboBox(self)
        self.i2c_speed_combo.addItem("20 KHz", 0)
        self.i2c_speed_combo.addItem("100 KHz", 1)
        self.i2c_speed_combo.addItem("400 KHz", 2)
        self.i2c_speed_combo.addItem("750 KHz", 3)
        self.i2c_speed_combo.currentIndexChanged.connect(self.on_i2c_speed_changed)
        form.addRow("I2C speed", self.i2c_speed_combo)

        self.set_current_spin = QSpinBox(self)
        self.set_current_spin.setRange(0, 20)
        self.set_current_spin.setValue(1)
        self.set_current_spin.setSuffix(" A")
        form.addRow("Set current", self.set_current_spin)

        self.connect_i2c_button = QPushButton("Connect CH341", self)
        self.connect_i2c_button.clicked.connect(self.connect_i2c_device)
        form.addRow("", self.connect_i2c_button)

        self.i2c_status_label = QLabel("Disconnected", self)
        form.addRow("Status", self.i2c_status_label)

        read_row = QHBoxLayout()
        self.read_input_voltage_button = QPushButton("Read Vin", self)
        self.read_output_voltage_button = QPushButton("Read Vout", self)
        self.read_output_current_button = QPushButton("Read Iout", self)
        self.read_input_voltage_button.clicked.connect(self.read_input_voltage)
        self.read_output_voltage_button.clicked.connect(self.read_output_voltage)
        self.read_output_current_button.clicked.connect(self.read_output_current)
        read_row.addWidget(self.read_input_voltage_button)
        read_row.addWidget(self.read_output_voltage_button)
        read_row.addWidget(self.read_output_current_button)
        form.addRow("", read_row)

        self.apply_current_button = QPushButton("Apply Current", self)
        self.apply_current_button.clicked.connect(self.apply_output_current)
        form.addRow("", self.apply_current_button)

        parent.addWidget(group, 0, column)
        self.update_i2c_address_info()

    def _build_power_meter_group(self, parent: QGridLayout, column: int) -> None:
        group = QGroupBox("Power Meter", self)
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.power_meter_combo = QComboBox(self)
        self.power_meter_combo.setEditable(True)
        self.power_meter_combo.addItem(DEFAULT_POWER_RESOURCE, None)
        form.addRow("Device", self.power_meter_combo)

        self.detect_power_meter_button = QPushButton("Auto Detect Power Meters", self)
        self.detect_power_meter_button.clicked.connect(self.auto_detect_power_meters)
        form.addRow("", self.detect_power_meter_button)

        power_actions = QHBoxLayout()
        self.refresh_power_meter_button = QPushButton("Refresh Ports", self)
        self.rel_zero_on_button = QPushButton("REL Zero On", self)
        self.rel_zero_off_button = QPushButton("REL Zero Off", self)
        self.refresh_power_meter_button.clicked.connect(self.refresh_power_meter_resources)
        self.rel_zero_on_button.clicked.connect(lambda: self.set_power_meter_relative_zero(True))
        self.rel_zero_off_button.clicked.connect(lambda: self.set_power_meter_relative_zero(False))
        power_actions.addWidget(self.refresh_power_meter_button)
        power_actions.addWidget(self.rel_zero_on_button)
        power_actions.addWidget(self.rel_zero_off_button)
        form.addRow("", power_actions)

        self.power_wavelength_spin = QSpinBox(self)
        self.power_wavelength_spin.setRange(190, 25000)
        self.power_wavelength_spin.setValue(976)
        self.power_wavelength_spin.setSuffix(" nm")
        form.addRow("Wavelength", self.power_wavelength_spin)

        self.software_gain_spin = QDoubleSpinBox(self)
        self.software_gain_spin.setRange(0.000001, 1000000.0)
        self.software_gain_spin.setDecimals(6)
        self.software_gain_spin.setValue(1.0)
        form.addRow("Software gain", self.software_gain_spin)

        parent.addWidget(group, 0, column)

    def _build_spectrometer_group(self, parent: QGridLayout, column: int) -> None:
        group = QGroupBox("Spectrometer", self)
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.spectrometer_combo = QComboBox(self)
        self.spectrometer_combo.addItem("Auto select first Ocean Insight", None)
        form.addRow("Device", self.spectrometer_combo)

        self.detect_spectrometer_button = QPushButton("Auto Detect Spectrometers", self)
        self.detect_spectrometer_button.clicked.connect(self.auto_detect_spectrometers)
        form.addRow("", self.detect_spectrometer_button)

        self.integration_spin = QSpinBox(self)
        self.integration_spin.setRange(1, 10_000_000)
        self.integration_spin.setValue(3800)
        self.integration_spin.setSingleStep(100)
        self.integration_spin.setSuffix(" us")
        form.addRow("Integration", self.integration_spin)

        self.interval_spin = QSpinBox(self)
        self.interval_spin.setRange(50, 5000)
        self.interval_spin.setValue(300)
        self.interval_spin.setSingleStep(50)
        self.interval_spin.setSuffix(" ms")
        form.addRow("Interval", self.interval_spin)

        spectrum_actions = QHBoxLayout()
        self.copy_spectrum_button = QPushButton("Copy Spectrum CSV", self)
        self.save_spectrum_button = QPushButton("Save Spectrum CSV", self)
        self.copy_spectrum_button.setEnabled(False)
        self.save_spectrum_button.setEnabled(False)
        self.copy_spectrum_button.clicked.connect(self.copy_spectrum_csv)
        self.save_spectrum_button.clicked.connect(self.save_spectrum_csv)
        spectrum_actions.addWidget(self.copy_spectrum_button)
        spectrum_actions.addWidget(self.save_spectrum_button)
        form.addRow("", spectrum_actions)

        parent.addWidget(group, 0, column)

    def _build_test_options(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("Stability And Record", self)
        layout = QHBoxLayout(group)

        layout.addWidget(QLabel("Stable window", self))
        self.stable_window_spin = QDoubleSpinBox(self)
        self.stable_window_spin.setRange(0.5, 300.0)
        self.stable_window_spin.setDecimals(1)
        self.stable_window_spin.setValue(3.0)
        self.stable_window_spin.setSuffix(" s")
        layout.addWidget(self.stable_window_spin)

        layout.addWidget(QLabel("Power tolerance", self))
        self.stable_tolerance_spin = QDoubleSpinBox(self)
        self.stable_tolerance_spin.setRange(0.0, 100000.0)
        self.stable_tolerance_spin.setDecimals(4)
        self.stable_tolerance_spin.setValue(0.05)
        self.stable_tolerance_spin.setSuffix(" W")
        layout.addWidget(self.stable_tolerance_spin)

        layout.addWidget(QLabel("CSV", self))
        self.csv_path_field = QLineEdit(str(Path(DEFAULT_CSV_PATH).resolve()), self)
        layout.addWidget(self.csv_path_field, stretch=1)

        self.browse_button = QPushButton("Browse", self)
        self.browse_button.clicked.connect(self.browse_csv)
        layout.addWidget(self.browse_button)

        layout.addWidget(QLabel("Scripts runner", self))
        self.scripts_runner_root_field = QLineEdit(DEFAULT_SCRIPTS_RUNNER_ROOT, self)
        layout.addWidget(self.scripts_runner_root_field, stretch=1)

        self.stop_after_record_check = QCheckBox("Stop after record", self)
        self.stop_after_record_check.setChecked(True)
        layout.addWidget(self.stop_after_record_check)

        parent.addWidget(group)

    def _build_live_panel(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("Live Reading", self)
        layout = QGridLayout(group)

        self.power_label = QLabel("-- W", self)
        self.power_label.setStyleSheet("font-size: 32px; font-weight: 700;")
        self.peak_label = QLabel("-- nm", self)
        self.peak_label.setStyleSheet("font-size: 32px; font-weight: 700;")
        self.centroid_label = QLabel("Centroid: -- nm", self)
        self.fwhm_label = QLabel("FWHM: -- nm", self)
        self.stability_label = QLabel("Stability: waiting", self)
        self.record_label = QLabel("Record: --", self)

        layout.addWidget(QLabel("Power", self), 0, 0)
        layout.addWidget(self.power_label, 1, 0)
        layout.addWidget(QLabel("Peak wavelength", self), 0, 1)
        layout.addWidget(self.peak_label, 1, 1)
        layout.addWidget(self.centroid_label, 2, 0)
        layout.addWidget(self.fwhm_label, 2, 1)
        layout.addWidget(self.stability_label, 3, 0)
        layout.addWidget(self.record_label, 3, 1)

        parent.addWidget(group)

    def _build_curve_panel(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("Realtime Curves", self)
        layout = QGridLayout(group)

        self.power_curve_figure = Figure(figsize=(5, 2.4), dpi=100)
        self.power_curve_canvas = FigureCanvas(self.power_curve_figure)
        self.power_curve_canvas.setMinimumHeight(220)
        self.power_curve_axis = self.power_curve_figure.add_subplot(111)
        self.power_curve_line, = self.power_curve_axis.plot([], [], color="#2f9cf4", linewidth=1.6)
        self._style_axis(
            self.power_curve_figure,
            self.power_curve_axis,
            title="Power",
            x_label="Elapsed time (s)",
            y_label="Power (W)",
        )

        self.spectrum_curve_figure = Figure(figsize=(5, 2.4), dpi=100)
        self.spectrum_curve_canvas = FigureCanvas(self.spectrum_curve_figure)
        self.spectrum_curve_canvas.setMinimumHeight(220)
        self.spectrum_curve_axis = self.spectrum_curve_figure.add_subplot(111)
        self.spectrum_curve_line, = self.spectrum_curve_axis.plot([], [], color="#f0b429", linewidth=1.2)
        self._style_axis(
            self.spectrum_curve_figure,
            self.spectrum_curve_axis,
            title="Spectrum",
            x_label="Wavelength (nm)",
            y_label="Intensity",
        )

        layout.addWidget(self.power_curve_canvas, 0, 0)
        layout.addWidget(self.spectrum_curve_canvas, 0, 1)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        parent.addWidget(group, stretch=2)
        self.reset_curves()

    @staticmethod
    def _style_axis(figure: Figure, axis: Any, title: str, x_label: str, y_label: str) -> None:
        figure.patch.set_facecolor("#1f1f1f")
        axis.set_facecolor("#242424")
        axis.set_title(title, color="#f2f2f2")
        axis.set_xlabel(x_label, color="#f2f2f2")
        axis.set_ylabel(y_label, color="#f2f2f2")
        axis.tick_params(colors="#f2f2f2")
        for spine in axis.spines.values():
            spine.set_color("#777777")
        axis.grid(True, alpha=0.25, color="#aaaaaa")
        figure.tight_layout()

    def _build_actions(self, parent: QVBoxLayout) -> None:
        row = QHBoxLayout()
        self.start_button = QPushButton("Start Combined Test", self)
        self.stop_button = QPushButton("Stop", self)
        self.clear_log_button = QPushButton("Clear Log", self)
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_test)
        self.stop_button.clicked.connect(self.stop_test)
        self.clear_log_button.clicked.connect(self.log_text.clear)
        row.addWidget(self.start_button)
        row.addWidget(self.stop_button)
        row.addWidget(self.clear_log_button)
        row.addStretch(1)
        parent.addLayout(row)

    def browse_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save Combined Test CSV", self.csv_path_field.text(), "CSV Files (*.csv)")
        if path:
            self.csv_path_field.setText(path)

    def update_i2c_address_info(self) -> None:
        try:
            address = parse_i2c_address(self.i2c_addr_field.text())
        except Exception as exc:
            self.i2c_address_info_label.setText(str(exc))
            return
        write_address = (address << 1) & 0xFE
        read_address = (address << 1) | 0x01
        self.i2c_address_info_label.setText(f"Write 0x{write_address:02X}, read 0x{read_address:02X}")

    def on_i2c_speed_changed(self) -> None:
        if self.manual_ch341_controller is not None and getattr(self.manual_ch341_controller, "is_connected", False):
            if not self.manual_ch341_controller.set_i2c_speed(int(self.i2c_speed_combo.currentData())):
                self.add_log("Failed to update CH341 I2C speed")

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
            return

        try:
            controller.set_i2c_speed(int(self.i2c_speed_combo.currentData()))
            connected, detail = controller.connect_device(0)
            if not connected:
                raise RuntimeError(str(detail))
            self.connect_i2c_button.setText("Disconnect CH341")
            self.i2c_status_label.setText("Connected")
            self.add_log(f"CH341 connected: {detail}")
        except Exception as exc:
            QMessageBox.critical(self, "CH341", str(exc))

    def _require_manual_i2c_controller(self) -> Any | None:
        if not self._manual_i2c_connected():
            QMessageBox.warning(self, "CH341", "Connect CH341 first.")
            return None
        return self.manual_ch341_controller

    def read_input_voltage(self) -> None:
        self.execute_i2c_read([0xB4, 0x88, 0x00, 0x00], "Input voltage", "V")

    def read_output_voltage(self) -> None:
        self.execute_i2c_read([0xB4, 0x8B, 0x00, 0x00], "Output voltage", "V")

    def read_output_current(self) -> None:
        self.execute_i2c_read([0xB4, 0x8C, 0x00, 0x00], "Output current", "A")

    def execute_i2c_read(self, command: list[int], name: str, unit: str) -> None:
        controller = self._require_manual_i2c_controller()
        if controller is None:
            return
        try:
            value = read_power_status_value(controller, parse_i2c_address(self.i2c_addr_field.text()), command)
            raw_command = " ".join(f"{item:02X}" for item in command)
            self.add_log(f"{name}: {value:.2f} {unit} ({raw_command})")
            self.statusBar().showMessage(f"{name}: {value:.2f} {unit}")
        except Exception as exc:
            QMessageBox.critical(self, name, str(exc))

    def apply_output_current(self) -> None:
        controller = self._require_manual_i2c_controller()
        if controller is None:
            return
        try:
            command = build_set_current_command(self.set_current_spin.value())
            success, result = controller.i2c_write(parse_i2c_address(self.i2c_addr_field.text()), command)
            if not success:
                raise RuntimeError(str(result))
            raw_command = " ".join(f"{item:02X}" for item in command)
            self.add_log(f"Output current set to {self.set_current_spin.value()} A ({raw_command})")
            self.statusBar().showMessage(f"Output current set to {self.set_current_spin.value()} A")
        except Exception as exc:
            QMessageBox.critical(self, "Apply Current", str(exc))

    def refresh_power_meter_resources(self) -> None:
        current = self.power_meter_combo.currentText().strip()
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
            from power_meter_mvp import CaihuangPowerMeter

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
        try:
            import pyvisa
            from power_meter_mvp import CaihuangPowerMeter

            rm = pyvisa.ResourceManager()
            try:
                resources = sorted(str(item) for item in rm.list_resources() if str(item).startswith("ASRL"))
            finally:
                rm.close()

            options: list[PowerMeterOption] = []
            for resource in resources:
                result = CaihuangPowerMeter.probe(resource)
                if result is not None:
                    options.append(
                        PowerMeterOption(
                            resource=result.resource,
                            device_type=result.device_type,
                            detail=result.detail,
                        )
                    )

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
        except Exception as exc:
            QMessageBox.critical(self, "Power Meter Auto Detect", str(exc))

    def auto_detect_spectrometers(self) -> None:
        try:
            root = self._scripts_runner_root_from_field()
            if root is not None:
                add_scripts_runner_root(root)
            from spectrometer_mvp import OceanSpectrometer

            device_ids = OceanSpectrometer.detect()
            self.spectrometer_combo.clear()
            if not device_ids:
                self.spectrometer_combo.addItem("Auto select first Ocean Insight", None)
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
            i2c_address=parse_i2c_address(self.i2c_addr_field.text()),
            i2c_speed=int(self.i2c_speed_combo.currentData()),
            set_current_a=self.set_current_spin.value(),
            power_resource=self._selected_power_resource(),
            power_meter_wavelength_nm=self.power_wavelength_spin.value(),
            software_gain=self.software_gain_spin.value(),
            integration_time_us=self.integration_spin.value(),
            interval_ms=self.interval_spin.value(),
            stable_window_s=self.stable_window_spin.value(),
            stable_tolerance_w=self.stable_tolerance_spin.value(),
            csv_path=Path(self.csv_path_field.text()).expanduser(),
            stop_after_record=self.stop_after_record_check.isChecked(),
            scripts_runner_root=self._scripts_runner_root_from_field(),
            spectrometer_device_id=self._selected_spectrometer_device_id(),
        )

    def _selected_power_resource(self) -> str:
        option = self.power_meter_combo.currentData()
        if isinstance(option, PowerMeterOption):
            return option.resource
        return self.power_meter_combo.currentText().strip()

    def _selected_spectrometer_device_id(self) -> int | None:
        option = self.spectrometer_combo.currentData()
        if isinstance(option, SpectrometerOption):
            return option.device_id
        return None

    def _scripts_runner_root_from_field(self) -> Path | None:
        text = self.scripts_runner_root_field.text().strip()
        if not text:
            return None
        return Path(text).expanduser()

    def start_test(self) -> None:
        if self.worker is not None:
            return
        try:
            settings = self.collect_settings()
        except Exception as exc:
            QMessageBox.warning(self, "Settings", str(exc))
            return
        if not settings.power_resource:
            QMessageBox.warning(self, "Settings", "Power meter resource is empty.")
            return

        if self._manual_i2c_connected():
            self.manual_ch341_controller.disconnect_device()
            self.connect_i2c_button.setText("Connect CH341")
            self.i2c_status_label.setText("Disconnected")

        self.log_text.clear()
        self.record_label.setText("Record: --")
        self.reset_curves()
        self.add_log("Starting combined test")
        self.worker = CombinedTestThread(settings, self)
        self.worker.live.connect(self.on_live_reading)
        self.worker.recorded.connect(self.on_recorded)
        self.worker.spectrum.connect(self.on_spectrum_curve)
        self.worker.status.connect(self.on_status)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()
        self.set_running_state(True)

    def stop_test(self) -> None:
        if self.worker is not None:
            self.add_log("Stopping")
            self.worker.stop()
            self.worker.wait(3000)

    def on_live_reading(self, reading: LiveReading) -> None:
        self.power_label.setText(f"{reading.power_w:.3f} W")
        self.peak_label.setText(f"{reading.peak_wavelength_nm:.3f} nm")
        self.centroid_label.setText(f"Centroid: {self._format_optional(reading.centroid_nm)} nm")
        self.fwhm_label.setText(f"FWHM: {self._format_optional(reading.fwhm_nm)} nm")
        state = "stable" if reading.stable else "waiting"
        self.stability_label.setText(
            f"Stability: {state}, span {reading.stable_span_w:.4f} W / {reading.stable_window_s:.2f} s"
        )
        self.update_power_curve(reading.elapsed_s, reading.power_w)

    def on_recorded(self, timestamp: str, measurement: CombinedMeasurement) -> None:
        self.record_label.setText(
            f"Record: {timestamp}, Iout {measurement.output_current_a:.3f} A, "
            f"Vout {measurement.output_voltage_v:.3f} V"
        )
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
        self.copy_spectrum_button.setEnabled(True)
        self.save_spectrum_button.setEnabled(True)
        self.update_spectrum_curve(wavelength, intensity)

    def reset_curves(self) -> None:
        self.power_curve_times.clear()
        self.power_curve_values.clear()
        self.power_curve_line.set_data([], [])
        self.power_curve_axis.set_xlim(0, 10)
        self.power_curve_axis.set_ylim(-0.01, 0.01)
        self.power_curve_canvas.draw_idle()

        self.spectrum_curve_line.set_data([], [])
        self.spectrum_curve_axis.set_xlim(0, 1)
        self.spectrum_curve_axis.set_ylim(0, 1)
        self.spectrum_curve_canvas.draw_idle()

    def update_power_curve(self, elapsed_s: float, power_w: float) -> None:
        elapsed = float(elapsed_s)
        power = float(power_w)
        if not math.isfinite(elapsed) or not math.isfinite(power):
            return

        self.power_curve_times.append(elapsed)
        self.power_curve_values.append(power)
        times = list(self.power_curve_times)
        powers = list(self.power_curve_values)
        x_min = max(0.0, times[-1] - POWER_PLOT_HISTORY_S)
        visible = [(x, y) for x, y in zip(times, powers) if x >= x_min]
        visible_times = [item[0] for item in visible]
        visible_powers = [item[1] for item in visible]
        self.power_curve_line.set_data(visible_times, visible_powers)

        x_max = max(10.0, times[-1])
        y_min = min(visible_powers)
        y_max = max(visible_powers)
        y_pad = self._axis_padding(y_min, y_max, fallback=0.001)
        self.power_curve_axis.set_xlim(x_min, x_max)
        self.power_curve_axis.set_ylim(y_min - y_pad, y_max + y_pad)
        self.power_curve_canvas.draw_idle()

    def update_spectrum_curve(self, wavelength: Any, intensity: Any) -> None:
        points: list[tuple[float, float]] = []
        for x_raw, y_raw in zip(wavelength, intensity):
            x = float(x_raw)
            y = float(y_raw)
            if math.isfinite(x) and math.isfinite(y):
                points.append((x, y))
        if not points:
            return

        x_values = [item[0] for item in points]
        y_values = [item[1] for item in points]
        self.spectrum_curve_line.set_data(x_values, y_values)

        x_min = min(x_values)
        x_max = max(x_values)
        y_min = min(y_values)
        y_max = max(y_values)
        x_pad = self._axis_padding(x_min, x_max, fallback=1.0)
        y_pad = self._axis_padding(y_min, y_max, fallback=1.0)
        self.spectrum_curve_axis.set_xlim(x_min - x_pad, x_max + x_pad)
        self.spectrum_curve_axis.set_ylim(y_min - y_pad, y_max + y_pad)
        self.spectrum_curve_canvas.draw_idle()

    @staticmethod
    def _axis_padding(min_value: float, max_value: float, fallback: float) -> float:
        if math.isclose(min_value, max_value):
            return max(abs(min_value) * 0.1, fallback)
        return (max_value - min_value) * 0.12

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

    def on_status(self, message: str) -> None:
        self.statusBar().showMessage(message)
        self.add_log(message)

    def on_failed(self, message: str) -> None:
        self.add_log(f"Error: {message}")
        QMessageBox.critical(self, "Combined Test Error", message)

    def on_finished(self) -> None:
        self.worker = None
        self.set_running_state(False)
        self.statusBar().showMessage("Stopped")
        self.add_log("Stopped")

    def set_running_state(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.browse_button.setEnabled(not running)
        self.detect_power_meter_button.setEnabled(not running)
        self.detect_spectrometer_button.setEnabled(not running)
        self.connect_i2c_button.setEnabled(not running)
        self.read_input_voltage_button.setEnabled(not running)
        self.read_output_voltage_button.setEnabled(not running)
        self.read_output_current_button.setEnabled(not running)
        self.apply_current_button.setEnabled(not running)
        self.refresh_power_meter_button.setEnabled(not running)
        self.rel_zero_on_button.setEnabled(not running)
        self.rel_zero_off_button.setEnabled(not running)
        self.power_meter_combo.setEnabled(not running)
        self.spectrometer_combo.setEnabled(not running)

    def add_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")

    @staticmethod
    def _format_optional(value: float) -> str:
        if not math.isfinite(float(value)):
            return "--"
        return f"{value:.3f}"

    def closeEvent(self, event: QCloseEvent) -> None:
        self.stop_test()
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
