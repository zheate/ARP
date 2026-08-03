"""Standalone power-meter diagnostic UI and serial power-meter adapters."""

from __future__ import annotations

import math
import re
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque

import pyvisa
from pyvisa import constants
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from tools.visa_session import acquire_visa_resource_manager, release_visa_resource_manager

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure


MAX_SAMPLES = 10000
PLOT_HISTORY_S = 30.0
PLOT_REFRESH_S = 0.2
DEFAULT_RESOURCE = "ASRL3::INSTR"


@dataclass(frozen=True)
class ProbeResult:
    resource: str
    device_type: str
    detail: str
    driver_kind: str = "caihuang"


def normalize_resource(name: str) -> str:
    value = name.strip().upper()
    if value.startswith("COM") and value[3:].isdigit():
        return f"ASRL{value[3:]}::INSTR"
    return value


def configure_caihuang(inst: pyvisa.resources.Resource, timeout_ms: int = 1000) -> None:
    inst.timeout = int(timeout_ms)
    inst.write_termination = "\r\n"
    inst.read_termination = "\r\n"
    if hasattr(inst, "baud_rate"):
        inst.baud_rate = 9600
        inst.data_bits = 8
        inst.parity = constants.Parity.none
        inst.stop_bits = constants.StopBits.one


def configure_laserpoint(inst: pyvisa.resources.Resource, timeout_ms: int = 1000) -> None:
    """Configure the VISA serial session used by scripts_runner's LaserPoint driver."""
    inst.timeout = int(timeout_ms)
    inst.write_termination = ":"
    inst.read_termination = ";"
    if hasattr(inst, "baud_rate"):
        inst.baud_rate = 38400
        inst.data_bits = 8
        inst.parity = constants.Parity.none
        inst.stop_bits = constants.StopBits.one
        if hasattr(inst, "flow_control"):
            inst.flow_control = constants.ControlFlow.none


def parse_power_w(raw: str) -> float:
    text = raw.strip()
    if text.upper() == "OVERFLOW":
        raise RuntimeError("power meter overflow")
    value = float(text)
    if not math.isfinite(value):
        raise RuntimeError(f"invalid power value: {text}")
    return value


def format_wavelength_nm(wavelength_nm: float) -> str:
    value = float(wavelength_nm)
    if not math.isfinite(value):
        raise RuntimeError(f"invalid wavelength value: {wavelength_nm}")
    return f"{value:.6f}".rstrip("0").rstrip(".")


def format_laserpoint_wavelength_nm(wavelength_nm: float) -> str:
    """Return LaserPoint's five-digit integer wavelength field."""
    value = float(wavelength_nm)
    if not math.isfinite(value) or not value.is_integer():
        raise RuntimeError("LaserPoint 波长必须是整数 nm")
    integer_value = int(value)
    if integer_value < 0 or integer_value > 99999:
        raise RuntimeError("LaserPoint 波长必须在 0 至 99999 nm 范围内")
    return f"{integer_value:05d}"


def parse_laserpoint_serial(raw: str) -> str:
    """Extract the six-digit serial returned by the LaserPoint SERNU command."""
    text = raw.strip().rstrip(";").strip()
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    if match is None:
        raise RuntimeError(f"LaserPoint 序列号响应无效：{text or '<empty>'}")
    return match.group(1)


def parse_laserpoint_power_w(raw: str) -> float:
    """Parse the numeric OUTPM response, tolerating a short textual prefix."""
    text = raw.strip().rstrip(";").strip()
    if not text or text.startswith("?"):
        raise RuntimeError(f"LaserPoint 功率响应无效：{text or '<empty>'}")
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", text)
    if match is None:
        raise RuntimeError(f"LaserPoint 功率响应无效：{text}")
    value = float(match.group(0))
    if not math.isfinite(value):
        raise RuntimeError(f"LaserPoint 功率响应无效：{text}")
    return value


class CaihuangPowerMeter:
    device_type = "Caihuang CHLP-P"
    driver_kind = "caihuang"

    def __init__(self, resource: str) -> None:
        self.resource = normalize_resource(resource)
        self.rm = acquire_visa_resource_manager()
        self.inst = None
        try:
            self.inst = self.rm.open_resource(self.resource)
            configure_caihuang(self.inst)
        except Exception:
            if self.inst is not None:
                try:
                    self.inst.close()
                except Exception:
                    pass
                self.inst = None
            release_visa_resource_manager(self.rm)
            self.rm = None
            raise

    @staticmethod
    def probe(resource: str, timeout_ms: int = 1000) -> ProbeResult | None:
        rm = acquire_visa_resource_manager()
        inst = None
        try:
            inst = rm.open_resource(normalize_resource(resource))
            configure_caihuang(inst, timeout_ms=timeout_ms)
            reply = inst.query("$TES").strip()
            if reply == "OK":
                detail = "OK"
                try:
                    version = inst.query("$VER").strip()
                    if version:
                        detail = f"OK, version {version}"
                except Exception:
                    pass
                return ProbeResult(
                    normalize_resource(resource),
                    CaihuangPowerMeter.device_type,
                    detail,
                    CaihuangPowerMeter.driver_kind,
                )
        except Exception:
            return None
        finally:
            if inst is not None:
                try:
                    inst.close()
                except Exception:
                    pass
            release_visa_resource_manager(rm)
        return None

    def test(self) -> str:
        return self.inst.query("$TES").strip()

    def set_wavelength(self, wavelength_nm: float) -> None:
        reply = self.inst.query(f"$WAV={format_wavelength_nm(wavelength_nm)}").strip()
        if reply != "SUCCEED":
            raise RuntimeError(f"set wavelength failed: {reply}")

    def read_power_w(self) -> float:
        return parse_power_w(self.inst.query("$POW"))

    def set_relative_zero(self, enabled: bool) -> None:
        reply = self.inst.query(f"$REL={1 if enabled else 0}").strip()
        if reply != "SUCCEED":
            raise RuntimeError(f"set relative zero failed: {reply}")

    def close(self) -> None:
        inst, rm = self.inst, self.rm
        self.inst = None
        self.rm = None
        try:
            if inst is not None:
                inst.close()
        finally:
            if rm is not None:
                release_visa_resource_manager(rm)


class LaserPointPowerMeter:
    """PyVISA adapter matching the LaserPoint protocol used by scripts_runner."""

    device_type = "LaserPoint"
    driver_kind = "laserpoint"

    def __init__(self, resource: str) -> None:
        self.resource = normalize_resource(resource)
        self.rm = acquire_visa_resource_manager()
        self.inst = None
        self.serial_number = ""
        try:
            self.inst = self.rm.open_resource(self.resource)
            configure_laserpoint(self.inst)
        except Exception:
            if self.inst is not None:
                try:
                    self.inst.close()
                except Exception:
                    pass
                self.inst = None
            release_visa_resource_manager(self.rm)
            self.rm = None
            raise

    @staticmethod
    def probe(resource: str, timeout_ms: int = 1000) -> ProbeResult | None:
        rm = acquire_visa_resource_manager()
        inst = None
        try:
            normalized_resource = normalize_resource(resource)
            inst = rm.open_resource(normalized_resource)
            configure_laserpoint(inst, timeout_ms=timeout_ms)
            serial_number = parse_laserpoint_serial(inst.query("*SERNU"))
            return ProbeResult(
                normalized_resource,
                LaserPointPowerMeter.device_type,
                f"SN {serial_number}",
                LaserPointPowerMeter.driver_kind,
            )
        except Exception:
            return None
        finally:
            if inst is not None:
                try:
                    inst.close()
                except Exception:
                    pass
            release_visa_resource_manager(rm)

    def test(self) -> str:
        self.serial_number = parse_laserpoint_serial(self.inst.query("*SERNU"))
        return "OK"

    def _setting_command(self, command: str) -> None:
        # scripts_runner's compiled driver sends these setters without waiting
        # for a reply. Waiting here would turn a successful write-only command
        # into a VISA timeout on the supported LaserPoint controller.
        self.inst.write(command)

    def set_power_mode(self) -> None:
        self._setting_command("*POWER")

    def set_gain_mode(self, mode: int) -> None:
        value = int(mode)
        if value < 0 or value > 3:
            raise ValueError("LaserPoint 增益模式必须在 0 至 3 之间")
        self._setting_command(f"*SETX1 {value}")

    def set_wavelength(self, wavelength_nm: float) -> None:
        self._setting_command(f"*SETLAM{format_laserpoint_wavelength_nm(wavelength_nm)}")

    def read_power_w(self) -> float:
        return parse_laserpoint_power_w(self.inst.query("*OUTPM"))

    def close(self) -> None:
        inst, rm = self.inst, self.rm
        self.inst = None
        self.rm = None
        try:
            if inst is not None:
                inst.close()
        finally:
            if rm is not None:
                release_visa_resource_manager(rm)


class PowerReaderThread(QThread):
    sample = Signal(float, float)
    status = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        resource: str,
        wavelength_nm: float,
        software_gain: float,
        interval_ms: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.resource = resource
        self.wavelength_nm = wavelength_nm
        self.software_gain = software_gain
        self.interval_ms = interval_ms
        self._running = False

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        meter: CaihuangPowerMeter | None = None
        try:
            meter = CaihuangPowerMeter(self.resource)
            if meter.test() != "OK":
                raise RuntimeError("device test did not return OK")
            meter.set_wavelength(self.wavelength_nm)
            self.status.emit(f"Connected: {meter.device_type} on {normalize_resource(self.resource)}")
            start = time.monotonic()
            self._running = True
            while self._running:
                power = round(meter.read_power_w() * self.software_gain, 2)
                self.sample.emit(time.monotonic() - start, power)
                self.msleep(self.interval_ms)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if meter is not None:
                try:
                    meter.close()
                except Exception:
                    pass


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Power Meter MVP")
        self.resize(1100, 720)

        self.rm = acquire_visa_resource_manager()
        self.reader: PowerReaderThread | None = None
        self.times: Deque[float] = deque(maxlen=MAX_SAMPLES)
        self.powers: Deque[float] = deque(maxlen=MAX_SAMPLES)
        self.sample_count = 0
        self.last_plot_update = 0.0

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

        self.resource_combo = QComboBox(self)
        self.resource_combo.setEditable(True)
        form.addRow("Resource", self.resource_combo)

        self.device_field = QLineEdit(self)
        self.device_field.setReadOnly(True)
        form.addRow("Detected", self.device_field)

        self.wavelength_spin = QDoubleSpinBox(self)
        self.wavelength_spin.setRange(190.0, 25000.0)
        self.wavelength_spin.setDecimals(3)
        self.wavelength_spin.setSingleStep(0.1)
        self.wavelength_spin.setValue(976.0)
        self.wavelength_spin.setSuffix(" nm")
        form.addRow("Wavelength", self.wavelength_spin)

        self.gain_spin = QDoubleSpinBox(self)
        self.gain_spin.setRange(0.000001, 1000000.0)
        self.gain_spin.setDecimals(6)
        self.gain_spin.setValue(1.0)
        form.addRow("Software gain", self.gain_spin)

        self.interval_spin = QSpinBox(self)
        self.interval_spin.setRange(20, 5000)
        self.interval_spin.setValue(300)
        self.interval_spin.setSingleStep(50)
        self.interval_spin.setSuffix(" ms")
        form.addRow("Interval", self.interval_spin)

        actions = QVBoxLayout()
        actions.setSpacing(8)
        top.addLayout(actions)

        self.refresh_button = QPushButton("Refresh Ports", self)
        self.detect_button = QPushButton("Auto Detect", self)
        self.start_button = QPushButton("Start", self)
        self.stop_button = QPushButton("Stop", self)
        self.zero_on_button = QPushButton("REL Zero On", self)
        self.zero_off_button = QPushButton("REL Zero Off", self)
        self.stop_button.setEnabled(False)
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.detect_button)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        actions.addWidget(self.zero_on_button)
        actions.addWidget(self.zero_off_button)
        actions.addStretch(1)

        power_row = QHBoxLayout()
        layout.addLayout(power_row)

        self.power_label = QLabel("-- W", self)
        self.power_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.power_label.setStyleSheet("font-size: 42px; font-weight: 700;")
        power_row.addWidget(self.power_label, stretch=1)

        self.stats_label = QLabel("Samples: 0", self)
        self.stats_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        power_row.addWidget(self.stats_label)

        self.figure = Figure(figsize=(8, 4), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.line, = self.ax.plot([], [], color="#1476d4", linewidth=1.8)
        self.ax.set_xlabel("Elapsed time (s), showing last 30 s")
        self.ax.set_ylabel("Power (W)")
        self.ax.grid(True, alpha=0.25)
        layout.addWidget(NavigationToolbar(self.canvas, self))
        layout.addWidget(self.canvas, stretch=1)

        self.setStatusBar(QStatusBar(self))

        self.refresh_button.clicked.connect(self.refresh_resources)
        self.detect_button.clicked.connect(self.auto_detect)
        self.start_button.clicked.connect(self.start_reading)
        self.stop_button.clicked.connect(self.stop_reading)
        self.zero_on_button.clicked.connect(lambda: self.set_relative_zero(True))
        self.zero_off_button.clicked.connect(lambda: self.set_relative_zero(False))

        self.resource_combo.addItem(DEFAULT_RESOURCE)
        self.resource_combo.setCurrentText(DEFAULT_RESOURCE)
        self.statusBar().showMessage("Ready. Use Auto Detect or Refresh Ports if the port changed.")

    def refresh_resources(self) -> None:
        current = self.resource_combo.currentText().strip()
        self.resource_combo.clear()
        try:
            resources = sorted(str(item) for item in self.rm.list_resources() if str(item).startswith("ASRL"))
        except Exception as exc:
            self.statusBar().showMessage(f"List resources failed: {exc}")
            resources = []
        self.resource_combo.addItems(resources)
        if current:
            index = self.resource_combo.findText(current)
            if index >= 0:
                self.resource_combo.setCurrentIndex(index)
            else:
                self.resource_combo.setEditText(current)
        elif resources:
            self.resource_combo.setCurrentIndex(0)
        self.statusBar().showMessage(f"Found {len(resources)} serial resource(s)")

    def auto_detect(self) -> None:
        typed = self.resource_combo.currentText().strip()
        candidates = []
        if typed and typed not in candidates:
            candidates.insert(0, typed)
        candidates.extend(
            self.resource_combo.itemText(i)
            for i in range(self.resource_combo.count())
            if self.resource_combo.itemText(i) not in candidates
        )
        if not candidates:
            self.refresh_resources()
            candidates = [self.resource_combo.itemText(i) for i in range(self.resource_combo.count())]
        for resource in candidates:
            result = CaihuangPowerMeter.probe(resource)
            if result:
                self.resource_combo.setEditText(result.resource)
                self.device_field.setText(f"{result.device_type} ({result.detail})")
                self.statusBar().showMessage(f"Detected {result.device_type} on {result.resource}")
                return
        self.device_field.clear()
        QMessageBox.warning(self, "Auto Detect", "No supported power meter was detected.")

    def start_reading(self) -> None:
        if self.reader is not None:
            return
        resource = self.resource_combo.currentText().strip()
        if not resource:
            QMessageBox.warning(self, "Start", "Select a serial resource first.")
            return
        self.times.clear()
        self.powers.clear()
        self.sample_count = 0
        self.last_plot_update = 0.0
        self.update_plot()
        self.reader = PowerReaderThread(
            resource=resource,
            wavelength_nm=self.wavelength_spin.value(),
            software_gain=self.gain_spin.value(),
            interval_ms=self.interval_spin.value(),
            parent=self,
        )
        self.reader.sample.connect(self.on_sample)
        self.reader.status.connect(self.statusBar().showMessage)
        self.reader.failed.connect(self.on_reader_failed)
        self.reader.finished.connect(self.on_reader_finished)
        self.reader.start()
        self.set_running_state(True)

    def stop_reading(self) -> None:
        if self.reader is not None:
            self.reader.stop()
            self.reader.wait(2000)

    def on_sample(self, elapsed_s: float, power_w: float) -> None:
        self.times.append(elapsed_s)
        self.powers.append(power_w)
        self.sample_count += 1
        self.power_label.setText(f"{power_w:.2f} W")
        self.stats_label.setText(f"Samples: {self.sample_count}")
        now = time.monotonic()
        if now - self.last_plot_update >= PLOT_REFRESH_S:
            self.last_plot_update = now
            self.update_plot()

    def on_reader_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Power Meter Error", message)

    def on_reader_finished(self) -> None:
        self.reader = None
        self.set_running_state(False)
        self.statusBar().showMessage("Stopped")

    def update_plot(self) -> None:
        if self.times and self.powers:
            x_min = max(0.0, self.times[-1] - PLOT_HISTORY_S)
            x_max = max(10.0, self.times[-1])
            visible = [(t, p) for t, p in zip(self.times, self.powers) if t >= x_min]
            visible_times = [item[0] for item in visible]
            visible_powers = [item[1] for item in visible]
            self.line.set_data(visible_times, visible_powers)
            y_min = min(visible_powers)
            y_max = max(visible_powers)
            if math.isclose(y_min, y_max):
                pad = max(abs(y_min) * 0.1, 0.001)
            else:
                pad = (y_max - y_min) * 0.12
            self.ax.set_xlim(x_min, x_max)
            self.ax.set_ylim(y_min - pad, y_max + pad)
        else:
            self.line.set_data([], [])
            self.ax.set_xlim(0, 10)
            self.ax.set_ylim(-0.01, 0.01)
        self.canvas.draw_idle()

    def set_relative_zero(self, enabled: bool) -> None:
        resource = self.resource_combo.currentText().strip()
        if not resource:
            QMessageBox.warning(self, "REL Zero", "Select a serial resource first.")
            return
        try:
            meter = CaihuangPowerMeter(resource)
            meter.set_relative_zero(enabled)
            meter.close()
            self.statusBar().showMessage(f"REL zero {'enabled' if enabled else 'disabled'}")
        except Exception as exc:
            QMessageBox.critical(self, "REL Zero", str(exc))

    def set_running_state(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.refresh_button.setEnabled(not running)
        self.detect_button.setEnabled(not running)
        self.resource_combo.setEnabled(not running)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.stop_reading()
        rm, self.rm = self.rm, None
        if rm is not None:
            release_visa_resource_manager(rm)
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
