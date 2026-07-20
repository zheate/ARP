"""NI-DAQmx based PD voltage acquisition, live display, and CSV recording."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from PySide6.QtCore import QEvent, QThread, Qt, Signal
from PySide6.QtGui import QCloseEvent, QPalette
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.ticker import EngFormatter, MaxNLocator

from combined_test.theme import FontRole, font_for_role


DEFAULT_SAMPLE_RATE_HZ = 1_000.0
DEFAULT_BLOCK_SIZE = 100
DEFAULT_HISTORY_S = 60.0
MAX_PLOT_POINTS_PER_SECOND = 200
PLOT_BUFFER_POINTS = int(DEFAULT_HISTORY_S * MAX_PLOT_POINTS_PER_SECOND) + 1_000

PD_AXIS_FONT = FontProperties(
    family="Microsoft YaHei" if sys.platform == "win32" else "PingFang SC",
)


@dataclass(frozen=True)
class DaqDeviceInfo:
    name: str
    product_type: str
    serial_number: int
    ai_channels: tuple[str, ...]
    voltage_ranges: tuple[float, ...]
    max_single_channel_rate_hz: float
    simulated: bool

    def label(self) -> str:
        simulated = " | 模拟设备" if self.simulated else ""
        return f"{self.product_type} | {self.name} | SN {self.serial_number}{simulated}"


@dataclass(frozen=True)
class PdDaqSettings:
    channel: str
    terminal_mode: str
    voltage_range_v: float
    sample_rate_hz: float
    block_size: int
    scale: float
    offset: float
    unit: str
    save_path: Path | None = None

    def validate(self, maximum_rate_hz: float | None = None) -> None:
        if not self.channel.strip():
            raise ValueError("请选择模拟输入通道。")
        if self.terminal_mode not in {"DIFF", "RSE", "NRSE"}:
            raise ValueError("不支持的接线方式。")
        if not math.isfinite(self.voltage_range_v) or self.voltage_range_v <= 0:
            raise ValueError("输入量程必须大于 0 V。")
        if not math.isfinite(self.sample_rate_hz) or self.sample_rate_hz <= 0:
            raise ValueError("采样率必须大于 0 S/s。")
        if maximum_rate_hz is not None and self.sample_rate_hz > maximum_rate_hz:
            raise ValueError(f"单通道采样率不能超过 {maximum_rate_hz:g} S/s。")
        if self.block_size <= 0:
            raise ValueError("每批点数必须大于 0。")
        if not math.isfinite(self.scale) or not math.isfinite(self.offset):
            raise ValueError("标定系数必须是有限数字。")
        if not self.unit.strip():
            raise ValueError("显示单位不能为空。")


@dataclass(frozen=True)
class SampleSummary:
    latest: float
    mean: float
    minimum: float
    maximum: float
    standard_deviation: float
    rms: float


@dataclass(frozen=True)
class PdSampleBlock:
    elapsed_s: float
    sample_count: int
    voltage: SampleSummary
    calibrated: SampleSummary
    plot_times_s: tuple[float, ...]
    plot_values: tuple[float, ...]


def calibrate_voltage(voltage_v: float, scale: float, offset: float) -> float:
    """Convert voltage to a non-negative PD value using abs(V * scale + offset)."""
    return abs(float(voltage_v) * float(scale) + float(offset))


def plot_sample_indices(
    block_start_index: int,
    block_length: int,
    sample_rate_hz: float,
    maximum_plot_rate_hz: float = MAX_PLOT_POINTS_PER_SECOND,
) -> range:
    """Select globally aligned plot samples while keeping the full history lightweight."""
    if block_length <= 0:
        return range(0)
    stride = max(1, math.ceil(float(sample_rate_hz) / float(maximum_plot_rate_hz)))
    first_index = (-int(block_start_index)) % stride
    return range(first_index, int(block_length), stride)


def summarize_samples(values: Sequence[float]) -> SampleSummary:
    if not values:
        raise ValueError("没有可统计的采样点。")
    numeric = [float(value) for value in values]
    return SampleSummary(
        latest=numeric[-1],
        mean=statistics.fmean(numeric),
        minimum=min(numeric),
        maximum=max(numeric),
        standard_deviation=statistics.pstdev(numeric),
        rms=math.sqrt(statistics.fmean(value * value for value in numeric)),
    )


def positive_axis_upper(values: Iterable[float], padding_fraction: float = 0.10) -> float:
    """Return a padded positive y-axis maximum for non-negative PD values."""
    finite_values = [float(value) for value in values if math.isfinite(float(value))]
    if not finite_values:
        return 1.0
    maximum = max(finite_values)
    if maximum <= 0.0:
        return 1.0
    return maximum * (1.0 + max(0.0, float(padding_fraction)))


def positive_voltage_ranges(raw_ranges: Iterable[float]) -> tuple[float, ...]:
    ranges = sorted({float(value) for value in raw_ranges if float(value) > 0})
    return tuple(ranges)


def default_csv_path(output_dir: Path, channel: str, now: datetime | None = None) -> Path:
    timestamp = (now or datetime.now()).strftime("%Y_%m_%d_%H_%M_%S")
    safe_channel = channel.replace("/", "_").replace("\\", "_").replace(":", "_")
    return Path(output_dir) / f"PD_{safe_channel}_{timestamp}.csv"


def discover_ni_daq_devices() -> tuple[str, list[DaqDeviceInfo]]:
    try:
        from nidaqmx.system import System
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少 nidaqmx；请使用 sth_eb314 环境运行。") from exc

    system = System.local()
    version = system.driver_version
    driver_label = f"NI-DAQmx {version.major_version}.{version.minor_version}.{version.update_version}"
    devices: list[DaqDeviceInfo] = []
    for device in system.devices:
        channels = tuple(str(item) for item in device.ai_physical_chans.channel_names)
        if not channels:
            continue
        devices.append(
            DaqDeviceInfo(
                name=str(device.name),
                product_type=str(device.product_type),
                serial_number=int(device.serial_num),
                ai_channels=channels,
                voltage_ranges=positive_voltage_ranges(device.ai_voltage_rngs),
                max_single_channel_rate_hz=float(device.ai_max_single_chan_rate),
                simulated=bool(device.is_simulated),
            )
        )
    return driver_label, devices


def channels_for_terminal_mode(device: DaqDeviceInfo, terminal_mode: str) -> tuple[str, ...]:
    """USB-6009 differential channels are ai0..ai3; ai4..ai7 are their negative inputs."""
    if terminal_mode != "DIFF" or "USB-6009" not in device.product_type.upper():
        return device.ai_channels
    return tuple(
        channel
        for channel in device.ai_channels
        if channel.rsplit("ai", 1)[-1].isdigit() and int(channel.rsplit("ai", 1)[-1]) < 4
    )


def _write_csv_metadata(writer: csv.writer, settings: PdDaqSettings, started_at: datetime) -> None:
    writer.writerow(["# started_at", started_at.isoformat(timespec="milliseconds")])
    writer.writerow(["# channel", settings.channel])
    writer.writerow(["# terminal_mode", settings.terminal_mode])
    writer.writerow(["# voltage_range_v", f"{settings.voltage_range_v:.12g}"])
    writer.writerow(["# sample_rate_hz", f"{settings.sample_rate_hz:.12g}"])
    writer.writerow(
        [
            "# calibration",
            f"y = abs(voltage_v * {settings.scale:.12g} + {settings.offset:.12g})",
        ]
    )
    writer.writerow(["# unit", settings.unit])
    writer.writerow(["sample_index", "elapsed_s", "voltage_v", "pd_value", "unit"])


class PdAcquisitionThread(QThread):
    block_ready = Signal(object)
    acquisition_ready = Signal(str)
    acquisition_failed = Signal(str)
    recording_stopped = Signal(str)

    def __init__(self, settings: PdDaqSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._stop_requested = threading.Event()

    def stop(self) -> None:
        self._stop_requested.set()

    def run(self) -> None:
        output_file = None
        output_path = self.settings.save_path
        try:
            import nidaqmx
            from nidaqmx.constants import AcquisitionType, TerminalConfiguration

            terminal_modes = {
                "DIFF": TerminalConfiguration.DIFF,
                "RSE": TerminalConfiguration.RSE,
                "NRSE": TerminalConfiguration.NRSE,
            }
            started_at = datetime.now()
            writer = None

            with nidaqmx.Task() as task:
                task.ai_channels.add_ai_voltage_chan(
                    self.settings.channel,
                    terminal_config=terminal_modes[self.settings.terminal_mode],
                    min_val=-self.settings.voltage_range_v,
                    max_val=self.settings.voltage_range_v,
                )
                buffer_samples = max(
                    self.settings.block_size * 10,
                    round(self.settings.sample_rate_hz * 2),
                )
                task.timing.cfg_samp_clk_timing(
                    rate=self.settings.sample_rate_hz,
                    sample_mode=AcquisitionType.CONTINUOUS,
                    samps_per_chan=buffer_samples,
                )
                task.start()
                if output_path is not None:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_file = output_path.open(
                        "w", newline="", encoding="utf-8-sig", buffering=1 << 20
                    )
                    writer = csv.writer(output_file)
                    _write_csv_metadata(writer, self.settings, started_at)
                self.acquisition_ready.emit(self.settings.channel)

                sample_index = 0
                last_flush_at = time.monotonic()
                read_timeout_s = max(2.0, self.settings.block_size / self.settings.sample_rate_hz * 5.0)
                while not self._stop_requested.is_set():
                    raw_values = task.read(
                        number_of_samples_per_channel=self.settings.block_size,
                        timeout=read_timeout_s,
                    )
                    if isinstance(raw_values, (float, int)):
                        voltages = [float(raw_values)]
                    else:
                        voltages = [float(value) for value in raw_values]
                    if not voltages:
                        continue

                    calibrated = [
                        calibrate_voltage(value, self.settings.scale, self.settings.offset)
                        for value in voltages
                    ]
                    block_start_index = sample_index
                    if writer is not None:
                        writer.writerows(
                            (
                                index,
                                f"{index / self.settings.sample_rate_hz:.9f}",
                                f"{voltage:.12g}",
                                f"{pd_value:.12g}",
                                self.settings.unit,
                            )
                            for index, voltage, pd_value in zip(
                                range(block_start_index, block_start_index + len(voltages)),
                                voltages,
                                calibrated,
                            )
                        )
                        if time.monotonic() - last_flush_at >= 1.0:
                            output_file.flush()
                            last_flush_at = time.monotonic()

                    sample_index += len(voltages)
                    plot_indices = plot_sample_indices(
                        block_start_index,
                        len(voltages),
                        self.settings.sample_rate_hz,
                    )
                    self.block_ready.emit(
                        PdSampleBlock(
                            elapsed_s=sample_index / self.settings.sample_rate_hz,
                            sample_count=sample_index,
                            voltage=summarize_samples(voltages),
                            calibrated=summarize_samples(calibrated),
                            plot_times_s=tuple(
                                (block_start_index + index) / self.settings.sample_rate_hz
                                for index in plot_indices
                            ),
                            plot_values=tuple(calibrated[index] for index in plot_indices),
                        )
                    )
        except Exception as exc:
            self.acquisition_failed.emit(str(exc))
        finally:
            if output_file is not None:
                try:
                    output_file.flush()
                    output_file.close()
                except Exception:
                    pass
            self.recording_stopped.emit(str(output_path) if output_path is not None else "")


class PdDaqPanel(QWidget):
    acquisition_finished = Signal()
    running_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None, auto_refresh: bool = True) -> None:
        super().__init__(parent)
        self.reader: PdAcquisitionThread | None = None
        self._reader_failed = False
        self.driver_label = ""
        self.plot_times: deque[float] = deque(maxlen=PLOT_BUFFER_POINTS)
        self.plot_values: deque[float] = deque(maxlen=PLOT_BUFFER_POINTS)
        self.last_plot_draw_s = -math.inf
        self._build_ui()
        if auto_refresh:
            self.refresh_devices()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        settings_panel = QWidget(self)
        self.settings_grid = QGridLayout(settings_panel)
        self.settings_grid.setContentsMargins(0, 0, 0, 0)
        self.settings_grid.setHorizontalSpacing(10)
        self.settings_grid.setVerticalSpacing(10)

        def configure_form(group: QGroupBox) -> QFormLayout:
            form = QFormLayout(group)
            form.setContentsMargins(10, 10, 10, 10)
            form.setHorizontalSpacing(8)
            form.setVerticalSpacing(6)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            return form

        self.device_settings_group = QGroupBox("设备与接线", settings_panel)
        self.settings_layout = configure_form(self.device_settings_group)

        device_row = QHBoxLayout()
        self.device_combo = QComboBox(self)
        self.device_combo.setAccessibleName("采集卡")
        self.refresh_button = QPushButton("重新识别", self)
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)
        device_row.addWidget(self.device_combo, 1)
        device_row.addWidget(self.refresh_button)
        self.device_field_label = QLabel("采集卡", self)
        self.device_field_label.setBuddy(self.device_combo)
        self.settings_layout.addRow(self.device_field_label, device_row)

        channel_row = QGridLayout()
        self.channel_combo = QComboBox(self)
        self.channel_combo.setAccessibleName("模拟输入通道")
        self.channel_combo.currentIndexChanged.connect(self._update_wiring_hint)
        self.terminal_combo = QComboBox(self)
        self.terminal_combo.setAccessibleName("接线方式")
        self.terminal_combo.addItem("差分 DIFF（推荐，抗干扰更好）", "DIFF")
        self.terminal_combo.addItem("参考单端 RSE", "RSE")
        self.terminal_combo.currentIndexChanged.connect(self._refresh_channels)
        self.channel_field_label = QLabel("模拟输入通道", self)
        self.channel_field_label.setBuddy(self.channel_combo)
        self.terminal_field_label = QLabel("接线方式", self)
        self.terminal_field_label.setBuddy(self.terminal_combo)
        channel_row.addWidget(self.channel_field_label, 0, 0)
        channel_row.addWidget(self.terminal_field_label, 0, 1)
        channel_row.addWidget(self.channel_combo, 1, 0)
        channel_row.addWidget(self.terminal_combo, 1, 1)
        channel_row.setColumnStretch(0, 1)
        channel_row.setColumnStretch(1, 2)
        self.input_config_field_label = QLabel("通道 / 接线", self)
        self.input_config_field_label.setBuddy(self.channel_combo)
        self.settings_layout.addRow(self.input_config_field_label, channel_row)

        self.wiring_hint_label = QLabel(self)
        self.wiring_hint_label.setWordWrap(True)
        self.settings_layout.addRow("接线提示", self.wiring_hint_label)

        self.sampling_settings_group = QGroupBox("采样参数", settings_panel)
        sampling_form = configure_form(self.sampling_settings_group)

        self.range_combo = QComboBox(self)
        self.range_combo.setAccessibleName("输入量程")
        self.sample_rate_spin = QDoubleSpinBox(self)
        self.sample_rate_spin.setAccessibleName("采样率")
        self.sample_rate_spin.setRange(0.1, 1_000_000.0)
        self.sample_rate_spin.setDecimals(1)
        self.sample_rate_spin.setValue(DEFAULT_SAMPLE_RATE_HZ)
        self.sample_rate_spin.setSuffix(" S/s")
        self.block_size_spin = QSpinBox(self)
        self.block_size_spin.setAccessibleName("每批点数")
        self.block_size_spin.setRange(1, 1_000_000)
        self.block_size_spin.setValue(DEFAULT_BLOCK_SIZE)
        self.block_size_spin.setSuffix(" 点/批")
        self.range_field_label = QLabel("输入量程", self)
        self.range_field_label.setBuddy(self.range_combo)
        self.sample_rate_field_label = QLabel("采样率", self)
        self.sample_rate_field_label.setBuddy(self.sample_rate_spin)
        self.block_size_field_label = QLabel("每批点数", self)
        self.block_size_field_label.setBuddy(self.block_size_spin)
        sampling_form.addRow(self.range_field_label, self.range_combo)
        sampling_form.addRow(self.sample_rate_field_label, self.sample_rate_spin)
        sampling_form.addRow(self.block_size_field_label, self.block_size_spin)
        self.sampling_field_label = QLabel("采样参数", self)
        self.sampling_field_label.setBuddy(self.range_combo)
        self.sampling_field_label.hide()

        self.calibration_settings_group = QGroupBox("线性标定", settings_panel)
        calibration_form = configure_form(self.calibration_settings_group)

        self.scale_spin = QDoubleSpinBox(self)
        self.scale_spin.setAccessibleName("线性标定比例系数")
        self.scale_spin.setRange(-1e9, 1e9)
        self.scale_spin.setDecimals(9)
        self.scale_spin.setValue(1.0)
        self.offset_spin = QDoubleSpinBox(self)
        self.offset_spin.setAccessibleName("线性标定偏置")
        self.offset_spin.setRange(-1e9, 1e9)
        self.offset_spin.setDecimals(9)
        self.unit_edit = QLineEdit("V", self)
        self.unit_edit.setAccessibleName("显示单位")
        self.unit_edit.setMaximumWidth(100)
        self.scale_field_label = QLabel("比例系数（电压 ×）", self)
        self.scale_field_label.setBuddy(self.scale_spin)
        self.offset_field_label = QLabel("偏置（+）", self)
        self.offset_field_label.setBuddy(self.offset_spin)
        self.unit_field_label = QLabel("显示单位", self)
        self.unit_field_label.setBuddy(self.unit_edit)
        calibration_form.addRow(self.scale_field_label, self.scale_spin)
        calibration_form.addRow(self.offset_field_label, self.offset_spin)
        calibration_form.addRow(self.unit_field_label, self.unit_edit)
        self.calibration_field_label = QLabel("线性标定", self)
        self.calibration_field_label.setBuddy(self.scale_spin)
        self.calibration_field_label.hide()

        self.storage_settings_group = QGroupBox("数据保存", settings_panel)
        storage_form = configure_form(self.storage_settings_group)

        self.save_checkbox = QCheckBox("采集时保存完整原始数据", self)
        self.save_checkbox.setAccessibleName("保存完整原始数据")
        self.save_checkbox.setChecked(True)
        self.output_dir_edit = QLineEdit(str(Path.cwd() / "pd_data"), self)
        self.output_dir_edit.setAccessibleName("数据保存文件夹")
        self.browse_button = QPushButton("选择文件夹", self)
        self.browse_button.clicked.connect(self._choose_output_dir)
        self.save_checkbox.toggled.connect(self.output_dir_edit.setEnabled)
        self.save_checkbox.toggled.connect(self.browse_button.setEnabled)
        self.output_dir_field_label = QLabel("保存文件夹", self)
        self.output_dir_field_label.setBuddy(self.output_dir_edit)
        self.data_save_field_label = QLabel("数据保存", self)
        self.data_save_field_label.setBuddy(self.save_checkbox)
        storage_form.addRow(self.data_save_field_label, self.save_checkbox)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_dir_edit, 1)
        output_row.addWidget(self.browse_button)
        storage_form.addRow(self.output_dir_field_label, output_row)

        self.settings_grid.addWidget(self.device_settings_group, 0, 0)
        self.settings_grid.addWidget(self.sampling_settings_group, 0, 1)
        self.settings_grid.addWidget(self.calibration_settings_group, 1, 0)
        self.settings_grid.addWidget(self.storage_settings_group, 1, 1)
        self.settings_grid.setColumnStretch(0, 1)
        self.settings_grid.setColumnStretch(1, 1)
        root.addWidget(settings_panel)

        live_group = QGroupBox("实时数据", self)
        live_layout = QVBoxLayout(live_group)
        values_layout = QGridLayout()
        self.current_value_label = QLabel("--", self)
        self.current_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.current_value_label.setFont(font_for_role(FontRole.METRIC))
        self.voltage_label = QLabel("电压：-- V", self)
        self.mean_label = QLabel("批次均值：--", self)
        self.std_label = QLabel("标准差：--", self)
        self.range_label = QLabel("最小/最大：--", self)
        self.count_label = QLabel("已采样：0 点", self)
        values_layout.addWidget(self.current_value_label, 0, 0, 2, 1)
        values_layout.addWidget(self.voltage_label, 0, 1)
        values_layout.addWidget(self.mean_label, 0, 2)
        values_layout.addWidget(self.std_label, 1, 1)
        values_layout.addWidget(self.range_label, 1, 2)
        values_layout.addWidget(self.count_label, 0, 3, 2, 1)
        live_layout.addLayout(values_layout)

        self.figure = Figure(figsize=(8, 4), dpi=100, layout="constrained")
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setAccessibleName("PD 实时趋势图")
        self.axis = self.figure.add_subplot(111)
        (self.line,) = self.axis.plot([], [], color="#2f79bd", linewidth=1.25)
        self.axis.set_xlabel("时间 (s)", fontproperties=PD_AXIS_FONT)
        self.axis.set_ylabel("PD 值", fontproperties=PD_AXIS_FONT)
        self.axis.yaxis.set_major_locator(MaxNLocator(nbins=6, min_n_ticks=4))
        self.axis.yaxis.set_major_formatter(EngFormatter(unit="V", places=3, sep=" "))
        self.axis.grid(True, alpha=0.25)
        self._sync_plot_theme()
        live_layout.addWidget(self.canvas, 1)
        root.addWidget(live_group, 1)

        controls = QHBoxLayout()
        self.status_label = QLabel("等待识别采集卡", self)
        self.start_button = QPushButton("开始采集", self)
        self.stop_button = QPushButton("停止", self)
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_acquisition)
        self.stop_button.clicked.connect(self.stop_acquisition)
        controls.addWidget(self.status_label, 1)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        root.addLayout(controls)

    def _sync_plot_theme(self) -> None:
        """Keep the embedded Matplotlib surface readable in the active Qt palette."""
        palette = self.palette()
        window_color = palette.color(QPalette.ColorRole.Window).name()
        base_color = palette.color(QPalette.ColorRole.Base).name()
        text_color = palette.color(QPalette.ColorRole.Text).name()
        grid_color = palette.color(QPalette.ColorRole.Mid).name()
        accent_color = palette.color(QPalette.ColorRole.Highlight).name()

        self.figure.set_facecolor(window_color)
        self.axis.set_facecolor(base_color)
        self.line.set_color(accent_color)
        self.axis.title.set_color(text_color)
        self.axis.xaxis.label.set_color(text_color)
        self.axis.yaxis.label.set_color(text_color)
        self.axis.xaxis.get_offset_text().set_color(text_color)
        self.axis.yaxis.get_offset_text().set_color(text_color)
        self.axis.tick_params(axis="both", colors=text_color)
        for spine in self.axis.spines.values():
            spine.set_color(grid_color)
        self.axis.grid(True, color=grid_color, alpha=0.65)
        self.canvas.draw_idle()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "figure"):
            self._sync_plot_theme()

    def refresh_devices(self) -> None:
        if self.reader is not None:
            return
        self.device_combo.clear()
        try:
            self.driver_label, devices = discover_ni_daq_devices()
        except Exception as exc:
            self.status_label.setText(f"采集卡识别失败：{exc}")
            self.start_button.setEnabled(False)
            return
        for device in devices:
            self.device_combo.addItem(device.label(), device)
        if not devices:
            self.status_label.setText(f"{self.driver_label} 已安装，但未发现模拟输入采集卡")
            self.start_button.setEnabled(False)
            return
        self.start_button.setEnabled(True)
        self.status_label.setText(f"已识别 {len(devices)} 台采集卡；{self.driver_label}")
        self._on_device_changed()

    def _selected_device(self) -> DaqDeviceInfo | None:
        value = self.device_combo.currentData()
        return value if isinstance(value, DaqDeviceInfo) else None

    def _on_device_changed(self) -> None:
        device = self._selected_device()
        self.range_combo.clear()
        if device is None:
            self.channel_combo.clear()
            return
        for value in device.voltage_ranges:
            self.range_combo.addItem(f"±{value:g} V", value)
        preferred = self.range_combo.findData(10.0)
        self.range_combo.setCurrentIndex(preferred if preferred >= 0 else self.range_combo.count() - 1)
        self.sample_rate_spin.setMaximum(max(0.1, device.max_single_channel_rate_hz))
        self._refresh_channels()

    def _refresh_channels(self) -> None:
        device = self._selected_device()
        selected = self.channel_combo.currentText()
        self.channel_combo.clear()
        if device is None:
            return
        terminal_mode = str(self.terminal_combo.currentData())
        self.channel_combo.addItems(channels_for_terminal_mode(device, terminal_mode))
        old_index = self.channel_combo.findText(selected)
        if old_index >= 0:
            self.channel_combo.setCurrentIndex(old_index)
        self._update_wiring_hint()

    def _update_wiring_hint(self) -> None:
        channel = self.channel_combo.currentText()
        terminal_mode = str(self.terminal_combo.currentData())
        device = self._selected_device()
        if not channel or device is None:
            self.wiring_hint_label.setText("--")
            return
        suffix = channel.rsplit("ai", 1)[-1]
        if terminal_mode == "DIFF" and "USB-6009" in device.product_type.upper() and suffix.isdigit():
            negative_channel = f"{channel.rsplit('ai', 1)[0]}ai{int(suffix) + 4}"
            self.wiring_hint_label.setText(
                f"差分接线：PD 输出正端 → {channel}，负端 → {negative_channel}。"
            )
            return
        self.wiring_hint_label.setText(
            f"参考单端接线：PD 输出 → {channel}，参考端按 USB-6009 端子标识连接 GND。"
        )

    def _choose_output_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择 PD 数据保存文件夹",
            self.output_dir_edit.text().strip() or str(Path.cwd()),
        )
        if selected:
            self.output_dir_edit.setText(selected)

    def _collect_settings(self) -> PdDaqSettings:
        device = self._selected_device()
        if device is None:
            raise ValueError("未识别到可用采集卡。")
        output_path = None
        if self.save_checkbox.isChecked():
            output_dir_text = self.output_dir_edit.text().strip()
            if not output_dir_text:
                raise ValueError("请选择数据保存文件夹。")
            output_path = default_csv_path(Path(output_dir_text), self.channel_combo.currentText())
        settings = PdDaqSettings(
            channel=self.channel_combo.currentText(),
            terminal_mode=str(self.terminal_combo.currentData()),
            voltage_range_v=float(self.range_combo.currentData()),
            sample_rate_hz=self.sample_rate_spin.value(),
            block_size=self.block_size_spin.value(),
            scale=self.scale_spin.value(),
            offset=self.offset_spin.value(),
            unit=self.unit_edit.text().strip(),
            save_path=output_path,
        )
        settings.validate(device.max_single_channel_rate_hz)
        return settings

    def start_acquisition(self) -> None:
        if self.reader is not None:
            return
        try:
            settings = self._collect_settings()
        except Exception as exc:
            QMessageBox.warning(self, "PD 采集设置", str(exc))
            return
        self.plot_times.clear()
        self.plot_values.clear()
        self.line.set_data([], [])
        self.canvas.draw_idle()
        self.current_value_label.setText("--")
        self.count_label.setText("已采样：0 点")
        self.axis.set_ylabel("PD 值", fontproperties=PD_AXIS_FONT)
        self.axis.yaxis.set_major_formatter(
            EngFormatter(unit=settings.unit, places=3, sep=" ")
        )
        self._reader_failed = False
        self.reader = PdAcquisitionThread(settings, self)
        self.reader.block_ready.connect(self._on_block_ready)
        self.reader.acquisition_ready.connect(self._on_acquisition_ready)
        self.reader.acquisition_failed.connect(self._on_acquisition_failed)
        self.reader.recording_stopped.connect(self._on_recording_stopped)
        self.reader.finished.connect(self._on_reader_finished)
        self._set_running(True)
        self.running_changed.emit(True)
        self.status_label.setText("正在启动 NI-DAQmx 采集…")
        self.reader.start()

    def stop_acquisition(self) -> None:
        if self.reader is None:
            return
        self.status_label.setText("正在停止并刷新保存文件…")
        self.stop_button.setEnabled(False)
        self.reader.stop()

    def _on_acquisition_ready(self, channel: str) -> None:
        save_path = self.reader.settings.save_path if self.reader is not None else None
        if save_path is None:
            self.status_label.setText(f"正在采集 {channel}；未保存到文件")
        else:
            self.status_label.setText(f"正在采集 {channel}；保存到 {save_path.name}")

    def _on_block_ready(self, block: PdSampleBlock) -> None:
        if self.reader is None:
            return
        unit = self.reader.settings.unit
        self.current_value_label.setText(f"{block.calibrated.latest:.6g} {unit}")
        self.voltage_label.setText(f"电压：{block.voltage.latest:.6g} V")
        self.mean_label.setText(f"批次均值：{block.calibrated.mean:.6g} {unit}")
        self.std_label.setText(f"标准差：{block.calibrated.standard_deviation:.6g} {unit}")
        self.range_label.setText(
            f"最小/最大：{block.calibrated.minimum:.6g} / {block.calibrated.maximum:.6g} {unit}"
        )
        self.count_label.setText(f"已采样：{block.sample_count:,} 点")
        self.plot_times.extend(block.plot_times_s)
        self.plot_values.extend(block.plot_values)
        cutoff = block.elapsed_s - DEFAULT_HISTORY_S
        while self.plot_times and self.plot_times[0] < cutoff:
            self.plot_times.popleft()
            self.plot_values.popleft()
        now = time.monotonic()
        if now - self.last_plot_draw_s >= 0.1:
            self.line.set_data(self.plot_times, self.plot_values)
            self.axis.relim()
            self.axis.set_ylim(0.0, positive_axis_upper(self.plot_values))
            if self.plot_times:
                self.axis.set_xlim(max(0.0, self.plot_times[-1] - DEFAULT_HISTORY_S), max(1.0, self.plot_times[-1]))
            self.canvas.draw_idle()
            self.last_plot_draw_s = now

    def _on_acquisition_failed(self, message: str) -> None:
        self._reader_failed = True
        self.status_label.setText(f"采集失败：{message}")
        QMessageBox.critical(self, "PD 采集失败", message)

    def _on_recording_stopped(self, path: str) -> None:
        if self._reader_failed:
            return
        if path:
            self.status_label.setText(f"采集已停止；数据已保存：{path}")
        elif not self.status_label.text().startswith("采集失败"):
            self.status_label.setText("采集已停止；本次未保存文件")

    def _on_reader_finished(self) -> None:
        thread = self.reader
        self.reader = None
        self._set_running(False)
        if thread is not None:
            thread.deleteLater()
        self.running_changed.emit(False)
        self.acquisition_finished.emit()

    def _set_running(self, running: bool) -> None:
        self.device_combo.setEnabled(not running)
        self.refresh_button.setEnabled(not running)
        self.channel_combo.setEnabled(not running)
        self.terminal_combo.setEnabled(not running)
        self.range_combo.setEnabled(not running)
        self.sample_rate_spin.setEnabled(not running)
        self.block_size_spin.setEnabled(not running)
        self.scale_spin.setEnabled(not running)
        self.offset_spin.setEnabled(not running)
        self.unit_edit.setEnabled(not running)
        self.save_checkbox.setEnabled(not running)
        self.output_dir_edit.setEnabled(not running and self.save_checkbox.isChecked())
        self.browse_button.setEnabled(not running and self.save_checkbox.isChecked())
        self.start_button.setEnabled(not running and self.device_combo.count() > 0)
        self.stop_button.setEnabled(running)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.reader is not None:
            self.reader.stop()
            if not self.reader.wait(5_000):
                QMessageBox.warning(self, "正在停止", "采集任务仍在安全停止，请稍后再关闭。")
                event.ignore()
                return
        # Matplotlib implements draw_idle() with a zero-delay Qt callback. A
        # panel can be closed before that callback runs, so clear the pending
        # flag before Qt deletes the underlying canvas object.
        self.canvas._draw_pending = False
        super().closeEvent(event)


class PdDaqWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PD 数据采集 - NI USB DAQ")
        self.resize(1_080, 760)
        self.panel = PdDaqPanel(self)
        self.setCentralWidget(self.panel)

    def closeEvent(self, event: QCloseEvent) -> None:
        reader = self.panel.reader
        if reader is not None:
            reader.stop()
            if not reader.wait(5_000):
                QMessageBox.warning(self, "正在停止", "采集任务仍在安全停止，请稍后再关闭。")
                event.ignore()
                return
        super().closeEvent(event)


def diagnose(sample_count: int = 1_000, sample_rate_hz: float = 1_000.0) -> dict[str, object]:
    driver, devices = discover_ni_daq_devices()
    report: dict[str, object] = {
        "driver": driver,
        "devices": [asdict(device) for device in devices],
    }
    if not devices:
        return report

    import nidaqmx
    from nidaqmx.constants import AcquisitionType, TerminalConfiguration

    device = devices[0]
    channel = device.ai_channels[0]
    with nidaqmx.Task() as task:
        task.ai_channels.add_ai_voltage_chan(
            channel,
            terminal_config=TerminalConfiguration.DEFAULT,
            min_val=-10.0,
            max_val=10.0,
        )
        task.timing.cfg_samp_clk_timing(
            rate=sample_rate_hz,
            sample_mode=AcquisitionType.FINITE,
            samps_per_chan=sample_count,
        )
        raw = task.read(number_of_samples_per_channel=sample_count, timeout=5.0)
    values = [float(raw)] if isinstance(raw, (float, int)) else [float(value) for value in raw]
    report["sample"] = {
        "channel": channel,
        "sample_rate_hz": sample_rate_hz,
        "sample_count": len(values),
        **asdict(summarize_samples(values)),
    }
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NI USB DAQ PD 数据采集工具")
    parser.add_argument("--diagnose", action="store_true", help="识别采集卡并读取一次短样本")
    args = parser.parse_args(argv)
    if args.diagnose:
        print(json.dumps(diagnose(), ensure_ascii=False, indent=2))
        return 0
    app = QApplication.instance() or QApplication(sys.argv)
    window = PdDaqWindow()
    window.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
