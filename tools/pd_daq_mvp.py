"""NI-DAQmx based PD voltage acquisition, CSV recording, and diagnostics."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from combined_test.concurrency import Signal, WorkerThread

from combined_test.native_realtime import NativeRealtimeUnavailable, get_native_realtime_core


DEFAULT_SAMPLE_RATE_HZ = 1_000.0
DEFAULT_BLOCK_SIZE = 100
DEFAULT_HISTORY_S = 60.0
MAX_PLOT_POINTS_PER_SECOND = 200
PLOT_BUFFER_POINTS = int(DEFAULT_HISTORY_S * MAX_PLOT_POINTS_PER_SECOND) + 1_000


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
    native_core = get_native_realtime_core()
    if native_core is not None:
        try:
            native = native_core.summarize_samples(numeric)
            return SampleSummary(
                latest=native.latest,
                mean=native.mean,
                minimum=native.minimum,
                maximum=native.maximum,
                standard_deviation=native.standard_deviation,
                rms=native.rms,
            )
        except (NativeRealtimeUnavailable, OSError, ValueError):
            pass
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


class PdAcquisitionThread(WorkerThread):
    block_ready = Signal(object)
    acquisition_ready = Signal(str)
    acquisition_failed = Signal(str)
    recording_stopped = Signal(str)

    def __init__(self, settings: PdDaqSettings, parent: object | None = None) -> None:
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
    parser = argparse.ArgumentParser(description="无界面 NI USB DAQ PD 数据采集工具")
    parser.add_argument("--diagnose", action="store_true", help="识别采集卡并读取一次短样本")
    parser.add_argument("--sample-count", type=int, default=1_000)
    parser.add_argument("--sample-rate-hz", type=float, default=1_000.0)
    args = parser.parse_args(argv)
    if not args.diagnose:
        parser.print_help()
        return 0
    print(
        json.dumps(
            diagnose(sample_count=args.sample_count, sample_rate_hz=args.sample_rate_hz),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
