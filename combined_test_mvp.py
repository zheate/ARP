from __future__ import annotations

import csv
import importlib.util
import math
import os
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, QThread, Qt, Signal
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
    QToolButton,
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
DEFAULT_I2C_ADDRESS = 0x41
DEFAULT_I2C_SPEED = 0  # 20 KHz
PROJECT_ROOT = Path(__file__).resolve().parent
MAX_CURVE_POINTS = 10000
POWER_PLOT_HISTORY_S = 60.0
POWER_METER_PROBE_TIMEOUT_MS = 250
SPECTRUM_CENTER_LOCK_REQUIRED_SAMPLES = 5
SPECTRUM_CENTER_LOCK_TOLERANCE_NM = 1.0
SPECTRUM_CENTER_LOCK_HALF_RANGE_NM = 20.0
DEFAULT_SPECTROMETER_INTEGRATION_US = 10000
SPECTRUM_PEAK_ORDINAL_LABELS = ("1st", "2nd", "3rd")
SPECTRUM_PEAK_MIN_SEPARATION_NM = 0.3
SPECTRUM_PEAK_MIN_PROMINENCE_FRACTION = 0.01
LEFT_PANEL_MIN_WIDTH = 380
LEFT_PANEL_MAX_WIDTH = 430


@dataclass(frozen=True)
class CombinedTestSettings:
    i2c_address: int
    i2c_speed: int
    set_current_a: float
    power_resource: str
    power_meter_wavelength_nm: float
    software_gain: float
    integration_time_us: int
    interval_ms: int
    stable_window_s: float
    stable_tolerance_w: float
    csv_path: Path
    stop_after_record: bool
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


@dataclass(frozen=True)
class PowerMeterSettings:
    resource: str
    wavelength_nm: float
    software_gain: float
    interval_ms: int
    stable_window_s: float
    stable_tolerance_w: float


@dataclass(frozen=True)
class SpectrometerSettings:
    integration_time_us: int
    interval_ms: int
    device_id: int | None = None


@dataclass(frozen=True)
class PowerMeterReading:
    elapsed_s: float
    power_w: float
    stable: bool
    stable_span_w: float
    stable_window_s: float
    stability_generation: int = 0


@dataclass(frozen=True)
class SpectrometerReading:
    peak_wavelength_nm: float
    centroid_nm: float
    fwhm_nm: float


@dataclass(frozen=True)
class SpectrumPeakAnnotation:
    label: str
    centroid_nm: float
    peak_wavelength_nm: float
    peak_intensity: float


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


def _remove_module_tree(prefix: str) -> None:
    for name in list(sys.modules):
        if name == prefix or name.startswith(f"{prefix}."):
            sys.modules.pop(name, None)


def load_spectrometer_components(root: Path | str | None) -> tuple[type, Any]:
    module_name = "_combined_local_spectrometer_mvp"
    try:
        _remove_module_tree("application")
        sys.modules.pop(module_name, None)

        module_path = PROJECT_ROOT / "spectrometer_mvp.py"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load spectrometer module: {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module.OceanSpectrometer, module.calculate_stats
    finally:
        os.chdir(PROJECT_ROOT)


def normalize_power_resource_name(name: str) -> str:
    value = name.strip().upper()
    if value.startswith("COM") and value[3:].isdigit():
        return f"ASRL{value[3:]}::INSTR"
    return value


def extract_power_resource_name(value: str) -> str:
    """Return the VISA resource from either a raw resource or a UI display label."""
    normalized = normalize_power_resource_name(value)
    match = re.search(r"\bASRL\d+::INSTR\b", normalized)
    return match.group(0) if match is not None else normalized


def open_spectrometer_device(spectrometer: Any, selected_device_id: int | None) -> int:
    if selected_device_id is None:
        return int(spectrometer.open_first())

    state = spectrometer.control.find_usb_devices()
    if state == -1:
        raise RuntimeError("OceanDirect failed to search USB spectrometers")
    device_ids = [int(item) for item in spectrometer.control.get_device_ids()]
    if not device_ids:
        raise RuntimeError("OceanDirect found 0 spectrometers. Check the Ocean Insight driver.")

    device_id = selected_device_id if selected_device_id in device_ids else device_ids[0]
    state = spectrometer.control.open_device(device_id)
    if state == -1:
        raise RuntimeError(f"Failed to open spectrometer device id {device_id}")
    spectrometer.device_id = device_id
    return int(device_id)


def find_spectrum_peak_annotations(points: list[tuple[float, float]], limit: int = 3) -> list[SpectrumPeakAnnotation]:
    clean_points = [(float(x), float(y)) for x, y in points if math.isfinite(float(x)) and math.isfinite(float(y))]
    clean_points.sort(key=lambda item: item[0])
    if len(clean_points) < 3:
        return []

    y_values = [item[1] for item in clean_points]
    y_range = max(y_values) - min(y_values)
    if y_range <= 0:
        return []

    neighborhood = max(2, len(clean_points) // 200)
    min_prominence = y_range * SPECTRUM_PEAK_MIN_PROMINENCE_FRACTION
    candidates: list[tuple[int, float, float]] = []
    for index in range(1, len(clean_points) - 1):
        y = clean_points[index][1]
        if y <= clean_points[index - 1][1] or y < clean_points[index + 1][1]:
            continue
        start = max(0, index - neighborhood)
        end = min(len(clean_points), index + neighborhood + 1)
        local_floor = min(item[1] for item in clean_points[start:end])
        prominence = y - local_floor
        if prominence >= min_prominence:
            candidates.append((index, clean_points[index][0], y))

    selected: list[tuple[int, float, float]] = []
    for candidate in sorted(candidates, key=lambda item: item[2], reverse=True):
        if all(abs(candidate[1] - item[1]) >= SPECTRUM_PEAK_MIN_SEPARATION_NM for item in selected):
            selected.append(candidate)
        if len(selected) >= limit:
            break

    annotations: list[SpectrumPeakAnnotation] = []
    for rank, (index, peak_wavelength_nm, peak_intensity) in enumerate(selected):
        annotations.append(
            SpectrumPeakAnnotation(
                label=SPECTRUM_PEAK_ORDINAL_LABELS[rank],
                centroid_nm=_calculate_local_peak_centroid(clean_points, index),
                peak_wavelength_nm=peak_wavelength_nm,
                peak_intensity=peak_intensity,
            )
        )
    return annotations


def _calculate_local_peak_centroid(points: list[tuple[float, float]], peak_index: int) -> float:
    peak_intensity = points[peak_index][1]
    baseline = min(item[1] for item in points)
    threshold = baseline + (peak_intensity - baseline) * 0.5

    left = peak_index
    while left > 0 and points[left - 1][1] >= threshold:
        left -= 1
    right = peak_index
    while right < len(points) - 1 and points[right + 1][1] >= threshold:
        right += 1

    peak_points = points[left : right + 1]
    local_baseline = min(item[1] for item in peak_points)
    weighted_sum = 0.0
    weight_total = 0.0
    for wavelength_nm, intensity in peak_points:
        weight = max(0.0, intensity - local_baseline)
        weighted_sum += wavelength_nm * weight
        weight_total += weight
    if weight_total <= 0:
        return points[peak_index][0]
    return weighted_sum / weight_total


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


class PowerMeterDetectThread(QThread):
    detected = Signal(object)
    status = Signal(str)
    failed = Signal(str)

    def __init__(self, preferred_resource: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.preferred_resource = preferred_resource

    def run(self) -> None:
        try:
            try:
                import pyvisa
                from power_meter_mvp import CaihuangPowerMeter
            except ModuleNotFoundError as exc:
                raise RuntimeError(f"Power meter dependency missing: {exc.name}. Run from sth_eb314.") from exc

            rm = pyvisa.ResourceManager()
            try:
                resources = sorted(
                    normalize_power_resource_name(str(item))
                    for item in rm.list_resources()
                    if normalize_power_resource_name(str(item)).startswith("ASRL")
                )
            finally:
                rm.close()

            candidates: list[str] = []
            preferred = normalize_power_resource_name(self.preferred_resource)
            if preferred:
                candidates.append(preferred)
            for resource in resources:
                if resource not in candidates:
                    candidates.append(resource)

            self.status.emit(f"Detecting power meters on {len(candidates)} port(s)...")
            options: list[PowerMeterOption] = []
            for resource in candidates:
                result = CaihuangPowerMeter.probe(resource, timeout_ms=POWER_METER_PROBE_TIMEOUT_MS)
                if result is not None:
                    options.append(
                        PowerMeterOption(
                            resource=result.resource,
                            device_type=result.device_type,
                            detail=result.detail,
                        )
                    )
            self.detected.emit(options)
        except Exception as exc:
            self.failed.emit(str(exc))


class PowerMeterReaderThread(QThread):
    reading = Signal(object)
    status = Signal(str)
    failed = Signal(str)

    def __init__(self, settings: PowerMeterSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._running = False
        self._stability_reset_requested = threading.Event()
        self._stability_state_lock = threading.Lock()
        self._stability_generation = 0

    def stop(self) -> None:
        self._running = False

    def reset_stability_window(self) -> int:
        """Start a fresh stability window and return its generation number."""
        with self._stability_state_lock:
            self._stability_generation += 1
            generation = self._stability_generation
            self._stability_reset_requested.set()
        return generation

    def run(self) -> None:
        meter = None
        try:
            try:
                from power_meter_mvp import CaihuangPowerMeter, normalize_resource
            except ModuleNotFoundError as exc:
                raise RuntimeError(f"Power meter dependency missing: {exc.name}. Run from sth_eb314.") from exc

            meter = CaihuangPowerMeter(self.settings.resource)
            if meter.test() != "OK":
                raise RuntimeError("Power meter test did not return OK")
            meter.set_wavelength(self.settings.wavelength_nm)
            self.status.emit(f"Power meter connected: {normalize_resource(self.settings.resource)}")

            detector = PowerStabilityDetector(self.settings.stable_window_s, self.settings.stable_tolerance_w)
            start = time.monotonic()
            poll_interval_s = self.settings.interval_ms / 1000.0
            next_poll_at = start
            self._running = True
            while self._running:
                with self._stability_state_lock:
                    reset_requested = self._stability_reset_requested.is_set()
                    if reset_requested:
                        self._stability_reset_requested.clear()
                    generation = self._stability_generation
                if reset_requested:
                    detector = PowerStabilityDetector(self.settings.stable_window_s, self.settings.stable_tolerance_w)
                elapsed = time.monotonic() - start
                power_w = meter.read_power_w() * self.settings.software_gain
                stability = detector.add_sample(elapsed, power_w)
                self.reading.emit(
                    PowerMeterReading(
                        elapsed_s=elapsed,
                        power_w=power_w,
                        stable=stability.stable,
                        stable_span_w=stability.span_w,
                        stable_window_s=stability.window_s,
                        stability_generation=generation,
                    )
                )
                # Keep the readings aligned to the configured interval.  Sleeping a
                # full interval after every device read accumulates read-time drift,
                # so a 3 s stability window can otherwise wait until the next 300 ms
                # poll after its deadline.
                next_poll_at += poll_interval_s
                delay_s = next_poll_at - time.monotonic()
                if delay_s > 0.0:
                    self.msleep(max(1, round(delay_s * 1000)))
                else:
                    next_poll_at = time.monotonic()
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if meter is not None:
                try:
                    meter.close()
                except Exception:
                    pass


class SpectrometerReaderThread(QThread):
    reading = Signal(object)
    spectrum = Signal(object, object)
    status = Signal(str)
    failed = Signal(str)

    def __init__(self, settings: SpectrometerSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._running = False

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        spectrometer = None
        try:
            try:
                OceanSpectrometer, calculate_stats = load_spectrometer_components(None)
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    f"Spectrometer dependency missing: {exc.name}. Check this project environment and local OceanDirect files."
                ) from exc

            spectrometer = OceanSpectrometer()
            device_id = open_spectrometer_device(spectrometer, self.settings.device_id)
            spectrometer.set_integration_time(self.settings.integration_time_us)
            self.status.emit(f"Spectrometer connected, device id {device_id}")

            self._running = True

            while self._running:
                wavelength, intensity = spectrometer.read_spectrum()
                self.spectrum.emit(wavelength, intensity)
                stats = calculate_stats(wavelength, intensity)
                self.reading.emit(
                    SpectrometerReading(
                        peak_wavelength_nm=stats.peak_wavelength_nm,
                        centroid_nm=stats.centroid_nm,
                        fwhm_nm=stats.fwhm_nm,
                    )
                )
                self.msleep(self.settings.interval_ms)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if spectrometer is not None:
                try:
                    spectrometer.close()
                except Exception:
                    pass


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
        self.power_curve_times: deque[float] = deque(maxlen=MAX_CURVE_POINTS)
        self.power_curve_values: deque[float] = deque(maxlen=MAX_CURVE_POINTS)
        self.stable_power_points: dict[float, float] = {}
        self.efficiency_points: dict[float, float] = {}
        self.efficiency_voltage_points: dict[float, float] = {}
        self.active_output_current_a: float | None = None
        self.pending_stable_point_current_a: float | None = None
        self.pending_stable_point_generation: int | None = None
        self.recorded_stable_point_current_a: float | None = None
        self.recorded_stable_point_generation: int | None = None
        self.spectrum_center_candidate_nm: float | None = None
        self.spectrum_center_candidate_count = 0
        self.spectrum_center_locked_nm: float | None = None
        self.spectrum_peak_annotations: list[SpectrumPeakAnnotation] = []
        self.spectrum_peak_annotation_artists: list[Any] = []

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
        self.stable_tolerance_spin.setValue(
            settings.value(prefix + "stable_tolerance_w", self.stable_tolerance_spin.value(), type=float)
        )
        self.csv_path_field.setText(str(settings.value(prefix + "csv_path", self.csv_path_field.text())))
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
        settings.setValue(prefix + "stable_tolerance_w", self.stable_tolerance_spin.value())
        settings.setValue(prefix + "csv_path", self.csv_path_field.text().strip())
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
        form.addRow("Set current", self.set_current_spin)

        self.connect_i2c_button = QPushButton("Connect", self)
        self.connect_i2c_button.clicked.connect(self.connect_i2c_device)
        form.addRow("", self.connect_i2c_button)

        self.i2c_status_label = QLabel("Disconnected", self)
        form.addRow("Status", self.i2c_status_label)

        read_row = QHBoxLayout()
        self.read_input_voltage_button = QPushButton("Vin", self)
        self.read_output_voltage_button = QPushButton("Vout", self)
        self.read_output_current_button = QPushButton("Iout", self)
        self.read_input_voltage_button.clicked.connect(self.read_input_voltage)
        self.read_output_voltage_button.clicked.connect(self.read_output_voltage)
        self.read_output_current_button.clicked.connect(self.read_output_current)
        read_row.addWidget(self.read_input_voltage_button)
        read_row.addWidget(self.read_output_voltage_button)
        read_row.addWidget(self.read_output_current_button)
        form.addRow("", read_row)

        self.apply_current_button = QPushButton("Apply", self)
        self.apply_current_button.clicked.connect(self.apply_output_current)
        form.addRow("", self.apply_current_button)

        parent.addWidget(group)
        self._reserve_group_height(group)

    def _build_power_meter_group(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("Power Meter", self)
        form = QFormLayout(group)
        self._configure_left_form(form)

        self.power_meter_combo = QComboBox(self)
        self.power_meter_combo.setEditable(True)
        self.power_meter_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.power_meter_combo.setMinimumContentsLength(14)
        self.power_meter_combo.addItem(DEFAULT_POWER_RESOURCE, None)
        form.addRow("Device", self.power_meter_combo)

        self.detect_power_meter_button = QPushButton("Auto Detect", self)
        self.detect_power_meter_button.clicked.connect(self.auto_detect_power_meters)
        form.addRow("", self.detect_power_meter_button)

        power_actions = QHBoxLayout()
        self.refresh_power_meter_button = QPushButton("Refresh Ports", self)
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
        form.addRow("Status", self.power_meter_status_label)

        power_run_actions = QHBoxLayout()
        self.start_power_meter_button = QPushButton("Start", self)
        self.stop_power_meter_button = QPushButton("Stop", self)
        self.stop_power_meter_button.hide()
        self.start_power_meter_button.clicked.connect(self.start_power_meter)
        self.stop_power_meter_button.clicked.connect(self.stop_power_meter)
        power_run_actions.addWidget(self.start_power_meter_button)
        power_run_actions.addWidget(self.stop_power_meter_button)
        form.addRow("", power_run_actions)

        parent.addWidget(group)
        self._reserve_group_height(group)

    def _build_spectrometer_group(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("Spectrometer", self)
        form = QFormLayout(group)
        self._configure_left_form(form)

        self.spectrometer_combo = QComboBox(self)
        self.spectrometer_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.spectrometer_combo.setMinimumContentsLength(14)
        self.spectrometer_combo.addItem("Auto select first Ocean Insight", None)
        form.addRow("Device", self.spectrometer_combo)

        self.detect_spectrometer_button = QPushButton("Auto Detect", self)
        self.detect_spectrometer_button.clicked.connect(self.auto_detect_spectrometers)
        form.addRow("", self.detect_spectrometer_button)

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
        form.addRow("Status", self.spectrometer_status_label)

        spectrometer_run_actions = QHBoxLayout()
        self.start_spectrometer_button = QPushButton("Start", self)
        self.stop_spectrometer_button = QPushButton("Stop", self)
        self.stop_spectrometer_button.hide()
        self.start_spectrometer_button.clicked.connect(self.start_spectrometer)
        self.stop_spectrometer_button.clicked.connect(self.stop_spectrometer)
        spectrometer_run_actions.addWidget(self.start_spectrometer_button)
        spectrometer_run_actions.addWidget(self.stop_spectrometer_button)
        form.addRow("", spectrometer_run_actions)

        spectrum_actions = QHBoxLayout()
        self.copy_spectrum_button = QPushButton("Copy CSV", self)
        self.save_spectrum_button = QPushButton("Save CSV", self)
        self.copy_spectrum_button.setEnabled(False)
        self.save_spectrum_button.setEnabled(False)
        self.copy_spectrum_button.clicked.connect(self.copy_spectrum_csv)
        self.save_spectrum_button.clicked.connect(self.save_spectrum_csv)
        spectrum_actions.addWidget(self.copy_spectrum_button)
        spectrum_actions.addWidget(self.save_spectrum_button)
        form.addRow("", spectrum_actions)

        parent.addWidget(group)
        self._reserve_group_height(group)

    def _build_record_group(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("Stability & Record", self)
        form = QFormLayout(group)
        self._configure_left_form(form)

        self.stable_window_spin = QDoubleSpinBox(self)
        self.stable_window_spin.setRange(0.5, 300.0)
        self.stable_window_spin.setDecimals(1)
        self.stable_window_spin.setValue(3.0)
        self.stable_window_spin.setSuffix(" s")
        form.addRow("Stable window", self.stable_window_spin)

        self.stable_tolerance_spin = QDoubleSpinBox(self)
        self.stable_tolerance_spin.setRange(0.0, 100000.0)
        self.stable_tolerance_spin.setDecimals(4)
        self.stable_tolerance_spin.setValue(0.05)
        self.stable_tolerance_spin.setSuffix(" W")
        form.addRow("Allowed span", self.stable_tolerance_spin)

        self.csv_path_field = QLineEdit(str(Path(DEFAULT_CSV_PATH).resolve()), self)
        self.csv_path_field.setToolTip(self.csv_path_field.text())
        csv_row = QHBoxLayout()
        csv_row.addWidget(self.csv_path_field, stretch=1)

        self.browse_button = QPushButton("Choose...", self)
        self.browse_button.clicked.connect(self.browse_csv)
        csv_row.addWidget(self.browse_button)
        form.addRow("CSV file", csv_row)

        self.stop_after_record_check = QCheckBox("Stop after record", self)
        self.stop_after_record_check.setChecked(True)
        form.addRow("", self.stop_after_record_check)

        parent.addWidget(group)
        self._reserve_group_height(group)

    def _build_kpi_panel(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("Monitor", self)
        layout = QGridLayout(group)
        self.kpi_layout = layout
        self.kpi_cards: list[QWidget] = []
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)

        self.power_card_value, _power_detail = self._add_kpi_card(layout, 0, "Power", "-- W", "")
        self.peak_card_value, _peak_detail = self._add_kpi_card(layout, 1, "Peak wavelength", "-- nm", "")
        self.fwhm_card_value, self.centroid_label = self._add_kpi_card(
            layout,
            2,
            "FWHM / Centroid",
            "-- nm",
            "Centroid: -- nm",
        )
        self.stability_card_value, self.stability_detail_label = self._add_kpi_card(
            layout,
            3,
            "Stability",
            "Waiting",
            "span -- W / -- s",
        )
        self.record_card_value, self.record_detail_label = self._add_kpi_card(layout, 4, "Record", "--", "")

        self.power_label = self.power_card_value
        self.peak_label = self.peak_card_value
        self.fwhm_label = self.fwhm_card_value
        self.stability_label = self.stability_card_value
        self.record_label = self.record_card_value
        parent.addWidget(group)
        self._relayout_kpi_cards()

    def _add_kpi_card(self, parent: QGridLayout, column: int, title: str, value: str, detail: str) -> tuple[QLabel, QLabel]:
        card = QWidget(self)
        card.setStyleSheet(
            "QWidget { background-color: #242424; border: 1px solid #555555; border-radius: 4px; }"
            "QLabel { border: 0; background: transparent; }"
        )
        box = QVBoxLayout(card)
        box.setContentsMargins(12, 10, 12, 10)
        box.setSpacing(3)

        title_label = QLabel(title, self)
        title_label.setStyleSheet("color: #bdbdbd; font-size: 13px;")
        value_label = QLabel(value, self)
        value_label.setStyleSheet("color: #f2f2f2; font-size: 26px; font-weight: 700;")
        value_label.setWordWrap(True)
        detail_label = QLabel(detail, self)
        detail_label.setStyleSheet("color: #d0d0d0; font-size: 12px;")
        detail_label.setWordWrap(True)

        box.addWidget(title_label)
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
        columns = 3 if available_width and available_width < 900 else 5
        for index, card in enumerate(self.kpi_cards):
            layout.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            layout.setColumnStretch(column, 1)

    def _build_curve_panel(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("Realtime Curves", self)
        layout = QGridLayout(group)
        self.curves_layout = layout

        self.power_curve_figure = Figure(figsize=(3.8, 2.4), dpi=100)
        self.power_curve_canvas = FigureCanvas(self.power_curve_figure)
        self.power_curve_canvas.setMinimumHeight(230)
        self.power_curve_axis = self.power_curve_figure.add_subplot(111)
        self.power_curve_line, = self.power_curve_axis.plot([], [], color="#2f9cf4", linewidth=1.6)
        self._style_axis(
            self.power_curve_figure,
            self.power_curve_axis,
            title="Power",
            x_label="Elapsed time (s)",
            y_label="Power (W)",
        )

        self.stable_power_figure = Figure(figsize=(5.2, 2.4), dpi=100)
        self.stable_power_canvas = FigureCanvas(self.stable_power_figure)
        self.stable_power_canvas.setMinimumHeight(230)
        self.stable_power_axis = self.stable_power_figure.add_subplot(111)
        self.efficiency_axis = self.stable_power_axis.twinx()
        self.stable_power_line, = self.stable_power_axis.plot(
            [], [], color="#2f9cf4", marker="o", markersize=5, linewidth=1.6
        )
        self.efficiency_line, = self.efficiency_axis.plot(
            [], [], color="#f0b429", marker="s", markersize=5, linewidth=1.6
        )
        self._style_axis(
            self.stable_power_figure,
            self.stable_power_axis,
            title="Stable Power & Efficiency",
            x_label="Current (A)",
            y_label="Stable Power (W)",
        )
        self.efficiency_axis.set_ylabel("Efficiency (%)", color="#f0b429")
        self.efficiency_axis.tick_params(axis="y", colors="#f0b429")
        self.efficiency_axis.spines["right"].set_color("#f0b429")
        self.stable_power_figure.subplots_adjust(left=0.12, right=0.86, top=0.88, bottom=0.20)

        self.spectrum_curve_figure = Figure(figsize=(5, 2.4), dpi=100)
        self.spectrum_curve_canvas = FigureCanvas(self.spectrum_curve_figure)
        self.spectrum_curve_canvas.setMinimumHeight(240)
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
        layout.addWidget(self.stable_power_canvas, 0, 1)
        layout.addWidget(self.spectrum_curve_canvas, 1, 0, 1, 2)
        layout.setRowStretch(0, 1)
        layout.setRowStretch(1, 1)
        layout.setColumnStretch(0, 4)
        layout.setColumnStretch(1, 6)
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

    def _build_log_panel(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("Log", self)
        layout = QVBoxLayout(group)
        row = QHBoxLayout()
        self.log_text = QTextEdit(self)
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(110)
        self.log_text.setMaximumHeight(170)

        self.toggle_log_button = QToolButton(self)
        self.toggle_log_button.setText("Show Log")
        self.toggle_log_button.setCheckable(True)
        self.toggle_log_button.toggled.connect(self._toggle_log_visibility)
        row.addWidget(self.toggle_log_button)

        self.clear_log_button = QPushButton("Clear", self)
        self.clear_log_button.clicked.connect(self.log_text.clear)
        row.addWidget(self.clear_log_button)
        row.addStretch(1)
        layout.addLayout(row)

        layout.addWidget(self.log_text)
        self.log_text.hide()
        parent.addWidget(group)

    def _toggle_log_visibility(self, visible: bool) -> None:
        self.log_text.setVisible(visible)
        self.toggle_log_button.setText("Hide Log" if visible else "Show Log")

    def browse_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save Combined Test CSV", self.csv_path_field.text(), "CSV Files (*.csv)")
        if path:
            self.csv_path_field.setText(path)
            self.csv_path_field.setToolTip(path)

    def start_all(self) -> None:
        self.start_power_meter()
        self.start_spectrometer()

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

    def read_input_voltage(self) -> None:
        self.execute_i2c_read([0xB4, 0x88, 0x00, 0x00], "Input voltage", "V")

    def read_output_voltage(self) -> None:
        voltage_v = self.execute_i2c_read([0xB4, 0x8B, 0x00, 0x00], "Output voltage", "V")
        if voltage_v is not None:
            self.record_efficiency_from_vout(voltage_v)

    def read_output_current(self) -> None:
        self.execute_i2c_read([0xB4, 0x8C, 0x00, 0x00], "Output current", "A")

    def execute_i2c_read(self, command: list[int], name: str, unit: str) -> float | None:
        controller = self._require_manual_i2c_controller()
        if controller is None:
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
        self.update_stable_power_curve()
        self.statusBar().showMessage(f"Efficiency at {current_a:.3f} A: {efficiency_percent:.2f}%")
        self.add_log(
            f"Efficiency point: {current_a:.3f} A, {power_w:.3f} W / "
            f"({current_a:.3f} A × {voltage_v:.3f} V) = {efficiency_percent:.2f}%"
        )

    def apply_output_current(self) -> None:
        controller = self._require_manual_i2c_controller()
        if controller is None:
            return
        try:
            command = build_set_current_command(self.set_current_spin.value())
            success, result = controller.i2c_write(DEFAULT_I2C_ADDRESS, command)
            if not success:
                raise RuntimeError(str(result))
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
            csv_path=Path(self.csv_path_field.text()).expanduser(),
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

    def update_stability_card(self, stable: bool, span_w: float, covered_window_s: float) -> None:
        target_window_s = self.stable_window_spin.value() if hasattr(self, "stable_window_spin") else 0.0
        tolerance_w = self.stable_tolerance_spin.value() if hasattr(self, "stable_tolerance_spin") else 0.0
        displayed_window_s = min(max(covered_window_s, 0.0), target_window_s)
        self.stability_label.setText("Stable" if stable else "Waiting")
        self.stability_label.setStyleSheet(
            "color: #4ade80; font-size: 26px; font-weight: 700;"
            if stable
            else "color: #f2f2f2; font-size: 26px; font-weight: 700;"
        )
        self.stability_detail_label.setText(
            f"{displayed_window_s:.2f} / {target_window_s:.2f} s\n"
            f"span {span_w:.4f} W <= {tolerance_w:.4f} W"
        )

    def on_power_meter_reading(self, reading: PowerMeterReading) -> None:
        self.power_label.setText(f"{reading.power_w:.3f} W")
        self.update_stability_card(reading.stable, reading.stable_span_w, reading.stable_window_s)
        self.update_power_curve(reading.elapsed_s, reading.power_w)
        self.capture_stable_power_point(reading)

    def on_spectrometer_reading(self, reading: SpectrometerReading) -> None:
        self.peak_label.setText(f"{reading.peak_wavelength_nm:.3f} nm")
        self.centroid_label.setText(f"Centroid: {self._format_optional(reading.centroid_nm)} nm")
        self.fwhm_label.setText(f"{self._format_optional(reading.fwhm_nm)} nm")
        self.update_spectrum_center_lock(reading)

    def on_live_reading(self, reading: LiveReading) -> None:
        self.power_label.setText(f"{reading.power_w:.3f} W")
        self.peak_label.setText(f"{reading.peak_wavelength_nm:.3f} nm")
        self.centroid_label.setText(f"Centroid: {self._format_optional(reading.centroid_nm)} nm")
        self.fwhm_label.setText(f"{self._format_optional(reading.fwhm_nm)} nm")
        self.update_spectrum_center_lock(
            SpectrometerReading(
                peak_wavelength_nm=reading.peak_wavelength_nm,
                centroid_nm=reading.centroid_nm,
                fwhm_nm=reading.fwhm_nm,
            )
        )
        self.update_stability_card(reading.stable, reading.stable_span_w, reading.stable_window_s)
        self.update_power_curve(reading.elapsed_s, reading.power_w)

    def on_recorded(self, timestamp: str, measurement: CombinedMeasurement) -> None:
        self.record_label.setText(timestamp)
        self.record_detail_label.setText(f"Iout {measurement.output_current_a:.3f} A, Vout {measurement.output_voltage_v:.3f} V")
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
        self.reset_power_curve()
        self.reset_stable_power_curve()
        self.reset_spectrum_curve()

    def reset_power_curve(self) -> None:
        self.power_curve_times.clear()
        self.power_curve_values.clear()
        self.power_curve_line.set_data([], [])
        self.power_curve_axis.set_xlim(0, 10)
        self.power_curve_axis.set_ylim(-0.01, 0.01)
        self.power_curve_canvas.draw_idle()

    def reset_stable_power_curve(self) -> None:
        self.stable_power_points.clear()
        self.efficiency_points.clear()
        self.efficiency_voltage_points.clear()
        self.recorded_stable_point_current_a = None
        self.recorded_stable_point_generation = None
        self.update_stable_power_curve()

    def capture_stable_power_point(self, reading: PowerMeterReading) -> None:
        current_a = self.pending_stable_point_current_a
        if current_a is None:
            self.update_latest_stable_power_point(reading)
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
        power_points = sorted(self.stable_power_points.items())
        efficiency_points = sorted(self.efficiency_points.items())

        self.stable_power_line.set_data(
            [current_a for current_a, _power_w in power_points],
            [power_w for _current_a, power_w in power_points],
        )
        self.efficiency_line.set_data(
            [current_a for current_a, _efficiency_percent in efficiency_points],
            [efficiency_percent for _current_a, efficiency_percent in efficiency_points],
        )

        all_currents = [current_a for current_a, _value in power_points]
        if all_currents:
            x_min = min(all_currents)
            x_max = max(all_currents)
            x_pad = self._axis_padding(x_min, x_max, fallback=1.0)
            self.stable_power_axis.set_xlim(x_min - x_pad, x_max + x_pad)
        else:
            self.stable_power_axis.set_xlim(0.0, 1.0)

        if power_points:
            powers = [power_w for _current_a, power_w in power_points]
            y_min = min(powers)
            y_max = max(powers)
            y_pad = self._axis_padding(y_min, y_max, fallback=0.001)
            self.stable_power_axis.set_ylim(y_min - y_pad, y_max + y_pad)
        else:
            self.stable_power_axis.set_ylim(-0.01, 0.01)

        if efficiency_points:
            self.efficiency_axis.set_ylim(20.0, 60.0)
        else:
            self.efficiency_axis.set_ylim(20.0, 60.0)

        self.stable_power_canvas.draw_idle()

    def reset_spectrum_curve(self) -> None:
        self.spectrum_center_candidate_nm = None
        self.spectrum_center_candidate_count = 0
        self.spectrum_center_locked_nm = None
        self.clear_spectrum_peak_annotation_artists()
        self.spectrum_peak_annotations = []
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
            self.clear_spectrum_peak_annotation_artists()
            self.spectrum_peak_annotations = []
            return

        x_values = [item[0] for item in points]
        y_values = [item[1] for item in points]
        self.spectrum_curve_line.set_data(x_values, y_values)

        locked_center = self.spectrum_center_locked_nm
        if locked_center is not None and math.isfinite(locked_center):
            x_min = locked_center - SPECTRUM_CENTER_LOCK_HALF_RANGE_NM
            x_max = locked_center + SPECTRUM_CENTER_LOCK_HALF_RANGE_NM
            visible_y_values = [y for x, y in points if x_min <= x <= x_max] or y_values
            y_min = min(visible_y_values)
            y_max = max(visible_y_values)
            x_pad = 0.0
            visible_points = [(x, y) for x, y in points if x_min <= x <= x_max] or points
        else:
            x_min = min(x_values)
            x_max = max(x_values)
            y_min = min(y_values)
            y_max = max(y_values)
            x_pad = self._axis_padding(x_min, x_max, fallback=1.0)
            visible_points = points
        spectrum_y_min, spectrum_y_max = self._spectrum_y_limits(y_min, y_max)
        self.spectrum_curve_axis.set_xlim(x_min - x_pad, x_max + x_pad)
        self.spectrum_curve_axis.set_ylim(spectrum_y_min, spectrum_y_max)
        self.spectrum_peak_annotations = find_spectrum_peak_annotations(visible_points)
        self.draw_spectrum_peak_annotations(self.spectrum_peak_annotations)
        self.spectrum_curve_canvas.draw_idle()

    def _spectrum_y_limits(self, y_min: float, y_max: float) -> tuple[float, float]:
        """Return an immediately responsive, zero-based scale for spectrum intensity."""
        y_pad = self._axis_padding(y_min, y_max, fallback=1.0)
        lower_limit = 0.0 if y_min >= 0.0 else float(y_min) - y_pad
        upper_limit = max(float(y_max) + y_pad, 1.0)
        return lower_limit, upper_limit

    def clear_spectrum_peak_annotation_artists(self) -> None:
        for artist in self.spectrum_peak_annotation_artists:
            try:
                artist.remove()
            except Exception:
                pass
        self.spectrum_peak_annotation_artists.clear()

    def draw_spectrum_peak_annotations(self, annotations: list[SpectrumPeakAnnotation]) -> None:
        self.clear_spectrum_peak_annotation_artists()
        x_min, x_max = self.spectrum_curve_axis.get_xlim()
        y_min, y_max = self.spectrum_curve_axis.get_ylim()
        x_span = max(x_max - x_min, 1.0)
        y_span = max(y_max - y_min, 1.0)
        label_y_limit = y_min + y_span * 0.92
        label_y_offset = y_span * 0.05
        label_y_min = y_min + y_span * 0.08
        min_label_gap = y_span * 0.08
        min_label_x_gap = x_span * 0.04
        split_side_threshold = x_span * 0.05
        occupied_labels: list[tuple[float, float, float]] = []
        for index, annotation in enumerate(annotations):
            line = self.spectrum_curve_axis.axvline(
                annotation.centroid_nm,
                color="#7dd3fc",
                linestyle=":",
                linewidth=0.7,
                alpha=0.45,
            )
            marker = self.spectrum_curve_axis.plot(
                [annotation.centroid_nm],
                [annotation.peak_intensity],
                marker="o",
                color="#7dd3fc",
                markersize=3,
                linewidth=0,
                alpha=0.85,
            )[0]
            nearby_centroids = [
                item.centroid_nm
                for item in annotations
                if item is not annotation and abs(item.centroid_nm - annotation.centroid_nm) <= split_side_threshold
            ]
            if nearby_centroids:
                nearest_centroid = min(nearby_centroids, key=lambda centroid: abs(centroid - annotation.centroid_nm))
                right_side = annotation.centroid_nm > nearest_centroid
            else:
                right_side = annotation.centroid_nm <= x_min + x_span * 0.72
            x_offset = x_span * (0.012 + index * 0.004)
            label_x = annotation.centroid_nm + x_offset if right_side else annotation.centroid_nm - x_offset
            label_x = min(max(label_x, x_min + x_span * 0.02), x_max - x_span * 0.02)
            label_y = min(max(annotation.peak_intensity + label_y_offset, label_y_min), label_y_limit)
            close_x_threshold = x_span * 0.15
            for occupied_centroid_nm, occupied_x, occupied_y in occupied_labels:
                if (
                    abs(annotation.centroid_nm - occupied_centroid_nm) <= close_x_threshold
                    and abs(label_y - occupied_y) < min_label_gap
                ):
                    if occupied_y + min_label_gap <= label_y_limit:
                        label_y = occupied_y + min_label_gap
                    else:
                        label_y = max(label_y_min, occupied_y - min_label_gap)
                if (
                    abs(annotation.centroid_nm - occupied_centroid_nm) <= close_x_threshold
                    and abs(label_x - occupied_x) < min_label_x_gap
                ):
                    if right_side:
                        label_x = occupied_x + min_label_x_gap
                    else:
                        label_x = occupied_x - min_label_x_gap
                    label_x = min(max(label_x, x_min + x_span * 0.02), x_max - x_span * 0.02)
            occupied_labels.append((annotation.centroid_nm, label_x, label_y))
            text = self.spectrum_curve_axis.text(
                label_x,
                label_y,
                f"{annotation.label} {annotation.centroid_nm:.3f} nm",
                ha="left" if right_side else "right",
                va="bottom",
                fontsize=7,
                color="#f7f7f7",
                alpha=0.9,
            )
            self.spectrum_peak_annotation_artists.extend([line, marker, text])

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
        self.log_text.append(f"[{timestamp}] {message}")

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
