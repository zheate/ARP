from __future__ import annotations

import csv
import io
import sys

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QDoubleSpinBox,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure

from combined_test.ocean_direct_adapter import OceanDirectControl
from combined_test.spectrum_math import SpectrumStats, calculate_stats


DEFAULT_INTEGRATION_TIME_US = 10000
DEFAULT_INTERVAL_MS = 100


def spectrum_to_csv(wavelength: NDArray[np.float64], intensity: NDArray[np.float64]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["wavelength_nm", "intensity"])
    for x, y in zip(wavelength, intensity):
        writer.writerow([f"{float(x):.6f}", f"{float(y):.6f}"])
    return output.getvalue()


def slice_spectrum(
    wavelength: NDArray[np.float64],
    intensity: NDArray[np.float64],
    x_range: tuple[float, float] | None,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    if x_range is None:
        return wavelength, intensity
    mask = (wavelength >= x_range[0]) & (wavelength <= x_range[1])
    visible_wavelength = wavelength[mask]
    visible_intensity = intensity[mask]
    if visible_wavelength.size == 0:
        return wavelength, intensity
    return visible_wavelength, visible_intensity


def integrate_area(y: NDArray[np.float64], x: NDArray[np.float64]) -> float:
    if hasattr(np, "trapezoid"):
        return float(np.trapezoid(y, x))
    return float(np.sum((y[1:] + y[:-1]) * np.diff(x) / 2.0))


class OceanSpectrometer:
    def __init__(self) -> None:
        self.control = OceanDirectControl()
        self.device_id: int | None = None

    @staticmethod
    def detect() -> list[int]:
        control = OceanDirectControl()
        try:
            state = control.find_usb_devices()
            if state == -1:
                return []
            return list(control.get_device_ids())
        finally:
            try:
                control.close_device()
            except Exception:
                pass

    def open_first(self) -> int:
        state = self.control.find_usb_devices()
        if state == -1:
            raise RuntimeError("OceanDirect failed to search USB spectrometers")
        device_ids = self.control.get_device_ids()
        if not device_ids:
            raise RuntimeError("OceanDirect found 0 spectrometers. Check the Ocean Insight driver.")
        self.device_id = int(device_ids[0])
        state = self.control.open_device(self.device_id)
        if state == -1:
            raise RuntimeError(f"Failed to open spectrometer device id {self.device_id}")
        return self.device_id

    def set_integration_time(self, integration_time_us: int) -> None:
        state = self.control.set_integration_time(integration_time_us)
        if state == -1:
            raise RuntimeError(f"Failed to set integration time: {integration_time_us} us")

    def get_minimum_integration_time(self) -> int:
        return self.control.get_minimum_integration_time()

    def get_maximum_integration_time(self) -> int:
        return self.control.get_maximum_integration_time()

    def read_spectrum(self) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        wavelength = self.control.get_wavelength()
        intensity = self.control.get_intensity()
        return wavelength, intensity

    def close(self) -> None:
        self.control.close_device()


class SpectrumReaderThread(QThread):
    spectrum = Signal(object, object, object)
    status = Signal(str)
    failed = Signal(str)

    def __init__(self, integration_time_us: int, interval_ms: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.integration_time_us = integration_time_us
        self.interval_ms = interval_ms
        self._running = False

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        spectrometer: OceanSpectrometer | None = None
        try:
            spectrometer = OceanSpectrometer()
            device_id = spectrometer.open_first()
            spectrometer.set_integration_time(self.integration_time_us)
            self.status.emit(f"Connected Ocean Insight spectrometer, device id {device_id}")

            self._running = True
            while self._running:
                wavelength, intensity = spectrometer.read_spectrum()
                stats = calculate_stats(wavelength, intensity)
                self.spectrum.emit(wavelength, intensity, stats)
                self.msleep(self.interval_ms)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if spectrometer is not None:
                try:
                    spectrometer.close()
                except Exception:
                    pass


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Spectrometer MVP")
        self.resize(1100, 720)

        self.reader: SpectrumReaderThread | None = None
        self.latest_wavelength: NDArray[np.float64] | None = None
        self.latest_intensity: NDArray[np.float64] | None = None
        self.x_range: tuple[float, float] | None = None

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        layout.addLayout(top)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        top.addLayout(form, stretch=1)

        self.device_field = QLineEdit(self)
        self.device_field.setReadOnly(True)
        form.addRow("Device", self.device_field)

        self.integration_spin = QSpinBox(self)
        self.integration_spin.setRange(1, 10_000_000)
        self.integration_spin.setValue(DEFAULT_INTEGRATION_TIME_US)
        self.integration_spin.setSingleStep(100)
        self.integration_spin.setSuffix(" us")
        form.addRow("Integration", self.integration_spin)

        self.interval_spin = QSpinBox(self)
        self.interval_spin.setRange(20, 5000)
        self.interval_spin.setValue(DEFAULT_INTERVAL_MS)
        self.interval_spin.setSingleStep(20)
        self.interval_spin.setSuffix(" ms")
        form.addRow("Interval", self.interval_spin)

        self.x_min_field = QLineEdit(self)
        self.x_min_field.setPlaceholderText("auto")
        form.addRow("Wavelength min", self.x_min_field)

        self.x_max_field = QLineEdit(self)
        self.x_max_field.setPlaceholderText("auto")
        form.addRow("Wavelength max", self.x_max_field)

        self.lock_wavelength_spin = QDoubleSpinBox(self)
        self.lock_wavelength_spin.setRange(0.0, 100000.0)
        self.lock_wavelength_spin.setDecimals(3)
        self.lock_wavelength_spin.setValue(976.0)
        self.lock_wavelength_spin.setSuffix(" nm")
        form.addRow("Lock wavelength", self.lock_wavelength_spin)

        self.lock_half_width_spin = QDoubleSpinBox(self)
        self.lock_half_width_spin.setRange(0.01, 1000.0)
        self.lock_half_width_spin.setDecimals(3)
        self.lock_half_width_spin.setValue(1.5)
        self.lock_half_width_spin.setSuffix(" nm")
        form.addRow("Lock half width", self.lock_half_width_spin)

        actions = QVBoxLayout()
        actions.setSpacing(8)
        top.addLayout(actions)

        self.detect_button = QPushButton("Auto Detect", self)
        self.start_button = QPushButton("Start", self)
        self.stop_button = QPushButton("Stop", self)
        self.copy_button = QPushButton("Copy CSV", self)
        self.save_button = QPushButton("Save CSV", self)
        self.apply_range_button = QPushButton("Apply Range", self)
        self.auto_range_button = QPushButton("Auto Range", self)
        self.stop_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.save_button.setEnabled(False)
        actions.addWidget(self.detect_button)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        actions.addWidget(self.apply_range_button)
        actions.addWidget(self.auto_range_button)
        actions.addWidget(self.copy_button)
        actions.addWidget(self.save_button)
        actions.addStretch(1)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(24)
        layout.addLayout(stats_row)

        self.peak_label = QLabel("Peak: -- nm / --", self)
        self.centroid_label = QLabel("Centroid: -- nm", self)
        self.fwhm_label = QLabel("FWHM: -- nm", self)
        self.lock_db_label = QLabel("Locked dB: -- nm / main -- / side -- / -- dB", self)
        for label in (self.peak_label, self.centroid_label, self.fwhm_label, self.lock_db_label):
            label.setStyleSheet("font-size: 20px; font-weight: 600;")
            stats_row.addWidget(label)
        stats_row.addStretch(1)

        self.figure = Figure(figsize=(8, 4), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.line, = self.ax.plot([], [], color="#1476d4", linewidth=1.2)
        self.ax.set_xlabel("Wavelength (nm)")
        self.ax.set_ylabel("Intensity")
        self.ax.grid(True, alpha=0.25)
        layout.addWidget(NavigationToolbar(self.canvas, self))
        layout.addWidget(self.canvas, stretch=1)

        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage("Ready")

        self.detect_button.clicked.connect(self.auto_detect)
        self.start_button.clicked.connect(self.start_reading)
        self.stop_button.clicked.connect(self.stop_reading)
        self.copy_button.clicked.connect(self.copy_csv)
        self.save_button.clicked.connect(self.save_csv)
        self.apply_range_button.clicked.connect(self.apply_wavelength_range)
        self.auto_range_button.clicked.connect(self.clear_wavelength_range)

    def auto_detect(self) -> None:
        try:
            device_ids = OceanSpectrometer.detect()
        except Exception as exc:
            QMessageBox.critical(self, "Auto Detect", str(exc))
            return
        if not device_ids:
            self.device_field.setText("Not detected")
            QMessageBox.warning(
                self,
                "Auto Detect",
                "OceanDirect found 0 spectrometers. Check the Ocean Insight device driver.",
            )
            return
        self.device_field.setText(", ".join(str(device_id) for device_id in device_ids))
        self.statusBar().showMessage(f"Detected {len(device_ids)} spectrometer(s)")

    def start_reading(self) -> None:
        if self.reader is not None:
            return
        self.reader = SpectrumReaderThread(
            integration_time_us=self.integration_spin.value(),
            interval_ms=self.interval_spin.value(),
            parent=self,
        )
        self.reader.spectrum.connect(self.on_spectrum)
        self.reader.status.connect(self.statusBar().showMessage)
        self.reader.failed.connect(self.on_reader_failed)
        self.reader.finished.connect(self.on_reader_finished)
        self.reader.start()
        self.set_running_state(True)

    def stop_reading(self) -> None:
        if self.reader is not None:
            self.reader.stop()
            self.reader.wait(3000)

    def on_spectrum(
        self,
        wavelength: NDArray[np.float64],
        intensity: NDArray[np.float64],
        stats: SpectrumStats,
    ) -> None:
        self.latest_wavelength = wavelength
        self.latest_intensity = intensity
        self.copy_button.setEnabled(True)
        self.save_button.setEnabled(True)

        if wavelength.size and intensity.size:
            visible_wavelength, visible_intensity = slice_spectrum(wavelength, intensity, self.x_range)
            self.line.set_data(visible_wavelength, visible_intensity)
            x_min = float(np.min(wavelength))
            x_max = float(np.max(wavelength))
            if self.x_range is not None:
                x_min, x_max = self.x_range
            y_min = float(np.min(visible_intensity))
            y_max = float(np.max(visible_intensity))
            y_pad = max((y_max - y_min) * 0.1, 1.0)
            self.ax.set_xlim(x_min, x_max)
            self.ax.set_ylim(y_min - y_pad, y_max + y_pad)
        self.canvas.draw_idle()

        self.peak_label.setText(f"Peak: {stats.peak_wavelength_nm:.2f} nm / {stats.peak_intensity:.0f}")
        self.centroid_label.setText(f"Centroid: {stats.centroid_nm:.2f} nm")
        self.fwhm_label.setText(f"FWHM: {stats.fwhm_nm:.2f} nm")
        self.update_locked_db_label()

    def on_reader_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Spectrometer Error", message)

    def on_reader_finished(self) -> None:
        self.reader = None
        self.set_running_state(False)
        self.statusBar().showMessage("Stopped")

    def copy_csv(self) -> None:
        if self.latest_wavelength is None or self.latest_intensity is None:
            return
        QApplication.clipboard().setText(spectrum_to_csv(self.latest_wavelength, self.latest_intensity))
        self.statusBar().showMessage("Spectrum copied as CSV")

    def save_csv(self) -> None:
        if self.latest_wavelength is None or self.latest_intensity is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Spectrum CSV", "spectrum.csv", "CSV Files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as file:
            file.write(spectrum_to_csv(self.latest_wavelength, self.latest_intensity))
        self.statusBar().showMessage(f"Saved {path}")

    def apply_wavelength_range(self) -> None:
        try:
            x_min = float(self.x_min_field.text().strip())
            x_max = float(self.x_max_field.text().strip())
        except ValueError:
            QMessageBox.warning(self, "Wavelength Range", "Enter numeric min and max wavelength values.")
            return
        if x_max <= x_min:
            QMessageBox.warning(self, "Wavelength Range", "Max wavelength must be greater than min wavelength.")
            return
        self.x_range = (x_min, x_max)
        self.statusBar().showMessage(f"Showing wavelength range {x_min:g} - {x_max:g} nm")
        if self.latest_wavelength is not None and self.latest_intensity is not None:
            self.on_spectrum(self.latest_wavelength, self.latest_intensity, calculate_stats(self.latest_wavelength, self.latest_intensity))

    def clear_wavelength_range(self) -> None:
        self.x_range = None
        self.x_min_field.clear()
        self.x_max_field.clear()
        self.statusBar().showMessage("Using automatic wavelength range")
        if self.latest_wavelength is not None and self.latest_intensity is not None:
            self.on_spectrum(self.latest_wavelength, self.latest_intensity, calculate_stats(self.latest_wavelength, self.latest_intensity))

    def get_locked_signal(self) -> tuple[float, float, float] | None:
        if self.latest_wavelength is None or self.latest_intensity is None:
            return None
        target = self.lock_wavelength_spin.value()
        half_width = self.lock_half_width_spin.value()
        wavelength_scope, intensity_scope = slice_spectrum(self.latest_wavelength, self.latest_intensity, self.x_range)
        distance = np.abs(wavelength_scope - target)
        peak_mask = distance <= half_width
        if not np.any(peak_mask):
            return None
        peak_wavelength = wavelength_scope[peak_mask]
        peak_intensity = intensity_scope[peak_mask].astype(float)
        peak_index = int(np.argmax(peak_intensity))
        peak = float(peak_intensity[peak_index])

        side_mask = ~peak_mask
        side_values = intensity_scope[side_mask].astype(float)
        if side_values.size == 0:
            return None
        side_peak = float(np.max(side_values))
        return float(peak_wavelength[peak_index]), peak, side_peak

    def update_locked_db_label(self) -> None:
        locked = self.get_locked_signal()
        if locked is None:
            self.lock_db_label.setText("Locked dB: -- nm / main -- / side -- / -- dB")
            return
        wavelength, peak, side_peak = locked
        if peak <= 0 or side_peak <= 0:
            db_text = "invalid"
        else:
            db = 10.0 * math.log10(peak / side_peak)
            db_text = f"{db:.2f} dB"
        self.lock_db_label.setText(
            f"Locked dB: {wavelength:.2f} nm / main {peak:.0f} / side {side_peak:.0f} / {db_text}"
        )

    def set_running_state(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.detect_button.setEnabled(not running)
        self.integration_spin.setEnabled(not running)
        self.interval_spin.setEnabled(not running)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.stop_reading()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
