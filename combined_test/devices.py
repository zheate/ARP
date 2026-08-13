"""Hardware adapters and acquisition threads for the combined test app."""

from __future__ import annotations

import importlib.util
import math
import os
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .runtime_logging import get_runtime_logger


_LOGGER = get_runtime_logger("devices")

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget

from tools.visa_session import visa_resource_manager

from .core import PowerStabilityDetector, decode_i2c_value, stability_tolerance_for_power
from .models import (
    PowerMeterOption,
    PowerMeterReading,
    PowerMeterSettings,
    SpectrumFrame,
    SpectrometerReading,
    SpectrometerSettings,
)
from .spectrum import detect_spectrum_saturation, find_spectrum_peak_annotations
from .spectrum_math import calculate_pib, calculate_smsr


REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_ROOT = REPO_ROOT / "tools"
POWER_METER_PROBE_TIMEOUT_MS = 250
AUTO_INTEGRATION_LOW_FRACTION = 0.5
AUTO_INTEGRATION_HIGH_FRACTION = 0.899
AUTO_INTEGRATION_MAX_COUNTS = 64_000.0
AUTO_INTEGRATION_MIN_US = 3_800
AUTO_INTEGRATION_MAX_US = 300_000
AUTO_INTEGRATION_MAX_ADJUSTMENTS = 10


class SpectrumFrameAverager:
    """Return a rolling mean only after a complete group of compatible frames."""

    def __init__(self, frame_count: int) -> None:
        self.frame_count = max(1, int(frame_count))
        self.reset()

    def reset(self) -> None:
        self._wavelength: list[float] | None = None
        self._frames: deque[list[float]] = deque()
        self._sums: list[float] = []

    def add(self, wavelength: Any, intensity: Any) -> tuple[list[float], list[float]] | None:
        wavelength_values = [float(value) for value in wavelength]
        intensity_values = [float(value) for value in intensity]
        if not wavelength_values or len(wavelength_values) != len(intensity_values):
            self.reset()
            return None

        if self._wavelength != wavelength_values:
            self.reset()
            self._wavelength = wavelength_values
            self._sums = [0.0] * len(intensity_values)

        if len(self._frames) == self.frame_count:
            oldest = self._frames.popleft()
            for index, value in enumerate(oldest):
                self._sums[index] -= value

        self._frames.append(intensity_values)
        for index, value in enumerate(intensity_values):
            self._sums[index] += value

        if len(self._frames) < self.frame_count:
            return None
        divisor = float(len(self._frames))
        return list(self._wavelength), [value / divisor for value in self._sums]


class AutoIntegrationSearch:
    """Binary-search integration time using the original Qt test thresholds."""

    def __init__(self, minimum_us: int, maximum_us: int, full_scale_counts: float) -> None:
        self.minimum_us = max(AUTO_INTEGRATION_MIN_US, int(minimum_us))
        self.maximum_us = max(
            self.minimum_us,
            min(AUTO_INTEGRATION_MAX_US, int(maximum_us)),
        )
        self.full_scale_counts = max(1.0, min(AUTO_INTEGRATION_MAX_COUNTS, float(full_scale_counts)))
        self.reset()

    def reset(self) -> None:
        self.lower_integration_us = self.minimum_us
        self.upper_integration_us = self.maximum_us
        self.adjustment_count = 0

    @property
    def lower_peak_counts(self) -> float:
        return self.full_scale_counts * AUTO_INTEGRATION_LOW_FRACTION

    @property
    def upper_peak_counts(self) -> float:
        return self.full_scale_counts * AUTO_INTEGRATION_HIGH_FRACTION

    def evaluate(self, current_us: int, peak_counts: float) -> tuple[int, bool, bool]:
        """Return ``(next_us, in_range, adjustment_failed)`` for one frame."""
        current = max(self.minimum_us, min(self.maximum_us, int(current_us)))
        peak = float(peak_counts)
        if self.lower_peak_counts <= peak <= self.upper_peak_counts:
            self.reset()
            return current, True, False
        if self.adjustment_count >= AUTO_INTEGRATION_MAX_ADJUSTMENTS:
            return current, False, True

        if peak > self.upper_peak_counts:
            self.upper_integration_us = min(self.upper_integration_us, current)
            next_us = int((current + self.lower_integration_us) / 2.0)
        else:
            self.lower_integration_us = max(self.lower_integration_us, current)
            next_us = int((current + self.upper_integration_us) / 2.0)
        self.adjustment_count += 1
        next_us = max(self.minimum_us, min(self.maximum_us, next_us))
        return next_us, False, False


@lru_cache(maxsize=1)
def load_legacy_ch341_controller_class() -> type:
    path = TOOLS_ROOT / "legacy_ch341_control.py"
    spec = importlib.util.spec_from_file_location("legacy_ch341_control", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载旧版 CH341 控制器：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    controller_class = getattr(module, "CH341I2CController", None)
    if controller_class is None:
        raise RuntimeError(f"在 {path} 中未找到 CH341I2CController")
    return controller_class


def parse_i2c_address(text: str) -> int:
    value = text.strip()
    if value.lower().startswith("0x"):
        value = value[2:]
    if not value:
        raise ValueError("I2C 地址不能为空")
    address = int(value, 16)
    if address < 0 or address > 0x7F:
        raise ValueError("I2C 地址必须在 0x00 至 0x7F 范围内")
    return address


def _remove_module_tree(prefix: str) -> None:
    for name in list(sys.modules):
        if name == prefix or name.startswith(f"{prefix}."):
            sys.modules.pop(name, None)


@lru_cache(maxsize=1)
def _load_spectrometer_components_once() -> tuple[type, Any]:
    """Load the local OceanDirect wrapper once instead of on every detect/start."""
    module_name = "_combined_local_spectrometer_mvp"
    _remove_module_tree("application")
    sys.modules.pop(module_name, None)

    module_path = TOOLS_ROOT / "spectrometer_mvp.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载光谱仪模块：{module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.OceanSpectrometer, module.calculate_stats


def load_spectrometer_components(_root: Path | str | None) -> tuple[type, Any]:
    try:
        return _load_spectrometer_components_once()
    finally:
        # OceanDirect resolves its DLL relative to the process working directory.
        os.chdir(REPO_ROOT)


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

    spectrometer.control.find_usb_devices()
    device_ids = [int(item) for item in spectrometer.control.get_device_ids()]
    if not device_ids:
        raise RuntimeError(
            "OceanDirect 未检测到光谱仪。请确认设备已连接、未被其他程序占用，"
            "然后重新插拔 USB 后重试。"
        )

    device_id = selected_device_id if selected_device_id in device_ids else device_ids[0]
    spectrometer.control.open_device(device_id)
    spectrometer.device_id = device_id
    return int(device_id)


def read_power_status_value(ch341_controller: Any, i2c_address: int, command: list[int]) -> float:
    success, result = ch341_controller.i2c_write_read(i2c_address, command, 4)
    if not success:
        raw_command = " ".join(f"{item:02X}" for item in command)
        raise RuntimeError(f"I2C 命令 {raw_command} 读取失败：{result}")
    return decode_i2c_value(result)


class PowerMeterDetectThread(QThread):
    detected = Signal(object)
    status = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        preferred_resource: str = "",
        parent: QWidget | None = None,
        *,
        scan_all_resources: bool = True,
    ) -> None:
        super().__init__(parent)
        self.preferred_resource = preferred_resource
        self.scan_all_resources = scan_all_resources
        self._stop_requested = threading.Event()

    def stop(self) -> None:
        self._stop_requested.set()

    def run(self) -> None:
        try:
            try:
                import pyvisa
                power_meter_mvp = importlib.import_module("tools.power_meter_mvp")

                CaihuangPowerMeter = power_meter_mvp.CaihuangPowerMeter
                CoherentPowerMeter = getattr(power_meter_mvp, "CoherentPowerMeter", None)
                LaserPointPowerMeter = power_meter_mvp.LaserPointPowerMeter
                OphirPowerMeter = getattr(power_meter_mvp, "OphirPowerMeter", None)
            except ModuleNotFoundError as exc:
                raise RuntimeError(f"缺少功率计依赖：{exc.name}。请在 sth_eb314 环境中运行。") from exc

            resources: list[str] = []
            try:
                with visa_resource_manager() as rm:
                    for item in rm.list_resources():
                        resource = normalize_power_resource_name(str(item))
                        if resource.startswith("ASRL"):
                            resources.append(resource)
                    resources.sort()
            except Exception:
                # Ophir uses its independent USB/COM API. A missing or busy
                # VISA backend must not hide an otherwise usable Ophir meter.
                _LOGGER.warning("VISA 资源扫描失败，继续扫描 Ophir USB 设备", exc_info=True)

            candidates: list[str] = []
            preferred = normalize_power_resource_name(self.preferred_resource)
            if preferred and not preferred.startswith("OPHIR::USB::"):
                candidates.append(preferred)
            if self.scan_all_resources:
                for resource in resources:
                    if resource not in candidates:
                        candidates.append(resource)

            self.status.emit(f"正在 {len(candidates)} 个端口上检测功率计，并扫描 Ophir USB")
            options: list[PowerMeterOption] = []
            for resource in candidates:
                if self._stop_requested.is_set():
                    return
                serial_meter_classes = [CaihuangPowerMeter]
                if CoherentPowerMeter is not None:
                    serial_meter_classes.append(CoherentPowerMeter)
                serial_meter_classes.append(LaserPointPowerMeter)
                for meter_class in serial_meter_classes:
                    result = meter_class.probe(resource, timeout_ms=POWER_METER_PROBE_TIMEOUT_MS)
                    if result is None:
                        continue
                    options.append(
                        PowerMeterOption(
                            resource=result.resource,
                            device_type=result.device_type,
                            detail=result.detail,
                            driver_kind=result.driver_kind,
                        )
                    )
                    break
            should_scan_ophir = self.scan_all_resources or preferred.startswith("OPHIR::USB::")
            if should_scan_ophir and OphirPowerMeter is not None and not self._stop_requested.is_set():
                probe_all = getattr(OphirPowerMeter, "probe_all", None)
                if callable(probe_all):
                    for result in probe_all(
                        preferred if preferred.startswith("OPHIR::USB::") else "",
                        timeout_ms=POWER_METER_PROBE_TIMEOUT_MS,
                    ):
                        options.append(
                            PowerMeterOption(
                                resource=result.resource,
                                device_type=result.device_type,
                                detail=result.detail,
                                driver_kind=result.driver_kind,
                            )
                        )
            if not self._stop_requested.is_set():
                self.detected.emit(options)
        except Exception as exc:
            _LOGGER.exception("功率计检测线程失败")
            self.failed.emit(str(exc))


class PowerMeterReaderThread(QThread):
    reading = Signal(object)
    status = Signal(str)
    ready = Signal()
    failed = Signal(str)

    def __init__(self, settings: PowerMeterSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._stop_requested = threading.Event()
        self._stability_reset_requested = threading.Event()
        self._stability_state_lock = threading.Lock()
        self._stability_generation = 0
        self._stable_window_s = float(settings.stable_window_s)
        self._stable_tolerance_w = float(settings.stable_tolerance_w)
        self.is_ready = False

    def stop(self) -> None:
        self._stop_requested.set()

    def reset_stability_window(self) -> int:
        """Start a fresh stability window and return its generation number."""
        with self._stability_state_lock:
            self._stability_generation += 1
            generation = self._stability_generation
            self._stability_reset_requested.set()
        return generation

    def update_stability_settings(self, window_s: float, tolerance_w: float) -> None:
        """Apply stability settings to the active acquisition loop."""
        if window_s <= 0:
            raise ValueError("稳定窗口必须大于 0 秒")
        if tolerance_w < 0:
            raise ValueError("允许波动必须大于或等于 0 W")
        with self._stability_state_lock:
            self._stable_window_s = float(window_s)
            self._stable_tolerance_w = float(tolerance_w)

    def run(self) -> None:
        meter = None
        try:
            try:
                power_meter_mvp = importlib.import_module("tools.power_meter_mvp")

                CaihuangPowerMeter = power_meter_mvp.CaihuangPowerMeter
                CoherentPowerMeter = getattr(power_meter_mvp, "CoherentPowerMeter", None)
                LaserPointPowerMeter = power_meter_mvp.LaserPointPowerMeter
                OphirPowerMeter = getattr(power_meter_mvp, "OphirPowerMeter", None)
                normalize_resource = power_meter_mvp.normalize_resource
            except ModuleNotFoundError as exc:
                raise RuntimeError(f"缺少功率计依赖：{exc.name}。请在 sth_eb314 环境中运行。") from exc

            meter_classes = {
                "caihuang": CaihuangPowerMeter,
                "laserpoint": LaserPointPowerMeter,
            }
            if CoherentPowerMeter is not None:
                meter_classes["coherent"] = CoherentPowerMeter
            if OphirPowerMeter is not None:
                meter_classes["ophir"] = OphirPowerMeter
            meter_class = meter_classes.get(self.settings.driver_kind)
            if meter_class is None:
                raise RuntimeError(f"不支持的功率计驱动类型：{self.settings.driver_kind}")

            meter = meter_class(self.settings.resource)
            if meter.test() != "OK":
                raise RuntimeError("功率计自检未返回 OK")
            if self.settings.driver_kind in {"laserpoint", "coherent"}:
                meter.set_power_mode()
            if self.settings.driver_kind == "laserpoint":
                meter.set_gain_mode(3)
            meter.set_wavelength(self.settings.wavelength_nm)
            if self._stop_requested.is_set():
                return
            meter_name = getattr(meter, "device_type", self.settings.driver_kind)
            self.status.emit(
                f"功率计已连接：{meter_name} | {normalize_resource(self.settings.resource)}"
            )
            self.is_ready = True
            self.ready.emit()

            with self._stability_state_lock:
                stable_window_s = self._stable_window_s
                stable_tolerance_w = self._stable_tolerance_w
            detector = PowerStabilityDetector(stable_window_s, stable_tolerance_w)
            start = time.monotonic()
            poll_interval_s = self.settings.interval_ms / 1000.0
            next_poll_at = start
            while not self._stop_requested.is_set():
                with self._stability_state_lock:
                    reset_requested = self._stability_reset_requested.is_set()
                    if reset_requested:
                        self._stability_reset_requested.clear()
                    generation = self._stability_generation
                    stable_window_s = self._stable_window_s
                    stable_tolerance_w = self._stable_tolerance_w
                if reset_requested:
                    detector = PowerStabilityDetector(stable_window_s, stable_tolerance_w)
                else:
                    detector.window_s = stable_window_s
                    detector.tolerance_w = stable_tolerance_w
                elapsed = time.monotonic() - start
                power_w = meter.read_power_w() * self.settings.software_gain
                if self._stop_requested.is_set():
                    break
                stable_tolerance_w = stability_tolerance_for_power(power_w)
                detector.tolerance_w = stable_tolerance_w
                stability = detector.add_sample(elapsed, power_w)
                active_tolerance_w = detector.active_tolerance_w
                self.reading.emit(
                    PowerMeterReading(
                        elapsed_s=elapsed,
                        power_w=power_w,
                        stable=stability.stable,
                        stable_span_w=stability.span_w,
                        stable_window_s=stability.window_s,
                        stable_median_w=stability.median_w,
                        stability_generation=generation,
                        stable_tolerance_w=active_tolerance_w,
                    )
                )
                next_poll_at += poll_interval_s
                delay_s = next_poll_at - time.monotonic()
                if delay_s > 0.0:
                    self._stop_requested.wait(delay_s)
                else:
                    next_poll_at = time.monotonic()
        except Exception as exc:
            _LOGGER.exception("功率计采集线程失败")
            self.failed.emit(str(exc))
        finally:
            self.is_ready = False
            if meter is not None:
                try:
                    meter.close()
                except Exception:
                    pass


@dataclass(frozen=True)
class TdkReading:
    """Latest TDK MC?/MV? pair captured by the background reader."""

    output_current_a: float
    output_voltage_v: float


class TdkReaderThread(QThread):
    """Poll TDK MC?/MV? in the background so the GUI never blocks on RS-232.

    The serial transaction lock inside :class:`TdkLambdaPowerSupply` keeps
    this loop from interleaving with GUI write commands.
    """

    reading = Signal(object)
    status = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        supply: Any,
        interval_s: float = 1.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.supply = supply
        self.interval_s = max(0.2, float(interval_s))
        self._stop_requested = threading.Event()
        self._latest_lock = threading.Lock()
        self._latest: TdkReading | None = None
        self.is_ready = False

    def stop(self) -> None:
        self._stop_requested.set()

    def take_latest_reading(self) -> TdkReading | None:
        """Return the newest reading and clear the slot, mailbox style."""
        with self._latest_lock:
            reading = self._latest
            self._latest = None
        return reading

    def run(self) -> None:
        self.is_ready = True
        failure_reported = False
        try:
            while not self._stop_requested.is_set():
                try:
                    current_a = self.supply.read_output_current()
                    voltage_v = self.supply.read_output_voltage()
                except Exception as exc:
                    # Keep polling: the GUI falls back to direct reads while
                    # the cache stays stale. Report the failure only once so
                    # a dead link does not spam the log every second.
                    if not failure_reported:
                        failure_reported = True
                        _LOGGER.exception("TDK 后台读取失败")
                        self.failed.emit(str(exc))
                else:
                    if failure_reported:
                        self.status.emit("TDK 读数已恢复")
                        failure_reported = False
                    reading = TdkReading(
                        output_current_a=float(current_a),
                        output_voltage_v=float(voltage_v),
                    )
                    with self._latest_lock:
                        self._latest = reading
                    self.reading.emit(reading)
                self._stop_requested.wait(self.interval_s)
        finally:
            self.is_ready = False


class SpectrometerReaderThread(QThread):
    reading = Signal(object)
    status = Signal(str)
    ready = Signal()
    failed = Signal(str)
    integration_time_changed = Signal(int)
    full_scale_changed = Signal(float)
    auto_integration_failed = Signal(object)

    def __init__(
        self,
        settings: SpectrometerSettings,
        parent: QWidget | None = None,
        *,
        headless: bool = False,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.headless = bool(headless)
        self._stop_requested = threading.Event()
        self._latest_spectrum_lock = threading.Lock()
        self._latest_spectrum: SpectrumFrame | None = None
        self._auto_integration_enabled = threading.Event()
        if settings.auto_integration_enabled:
            self._auto_integration_enabled.set()
        self._resume_auto_integration_requested = threading.Event()
        self._fixed_integration_lock = threading.Lock()
        self._fixed_integration_time_us: int | None = None
        self._fixed_integration_pending = threading.Event()
        self._spectrum_averager = SpectrumFrameAverager(settings.average_frames)
        self._calculate_stats: Any = None
        self.full_scale_counts = AUTO_INTEGRATION_MAX_COUNTS
        self.is_ready = False

    def stop(self) -> None:
        self._stop_requested.set()

    def enable_auto_integration(self) -> None:
        self._auto_integration_enabled.set()
        self._resume_auto_integration_requested.set()

    def use_fixed_integration_time(self, integration_time_us: int) -> None:
        """Disable auto integration and apply a fixed time on the reader thread."""
        with self._fixed_integration_lock:
            self._fixed_integration_time_us = int(integration_time_us)
            self._fixed_integration_pending.set()
        self._resume_auto_integration_requested.clear()

    def _take_fixed_integration_time(self) -> int | None:
        if not self._fixed_integration_pending.is_set():
            return None
        with self._fixed_integration_lock:
            integration_time_us = self._fixed_integration_time_us
            self._fixed_integration_time_us = None
            self._fixed_integration_pending.clear()
        return integration_time_us

    def resume_auto_integration(self) -> None:
        self._resume_auto_integration_requested.set()

    def take_latest_spectrum(self) -> SpectrumFrame | None:
        """Return the newest frame and discard any older, superseded frame."""
        with self._latest_spectrum_lock:
            spectrum = self._latest_spectrum
            self._latest_spectrum = None
        return spectrum

    def _publish_latest_spectrum(self, frame: SpectrumFrame) -> None:
        # The GUI deliberately polls this one-slot mailbox. A queued Qt signal
        # per frame would retain every pair of arrays when plotting is slower
        # than acquisition and can grow memory without a bound.
        # OceanDirect/native wrappers are allowed to reuse their output buffers.
        # Keep an immutable snapshot so the following acquisition cannot mutate
        # a frame while the GUI or Tauri bridge is still serializing it.
        wavelength_copy = self._copy_spectrum_values(frame.wavelength)
        intensity_copy = self._copy_spectrum_values(frame.intensity)
        with self._latest_spectrum_lock:
            self._latest_spectrum = SpectrumFrame(
                wavelength=wavelength_copy,
                intensity=intensity_copy,
                stats=frame.stats,
                pib=frame.pib,
                smsr=frame.smsr,
                saturation=frame.saturation,
                peak_annotations=frame.peak_annotations,
                peak_intensity=frame.peak_intensity,
            )

    def _publish_averaged_spectrum(
        self,
        wavelength: Any,
        intensity: Any,
    ) -> tuple[list[float], list[float]] | None:
        averaged = self._spectrum_averager.add(wavelength, intensity)
        if averaged is not None:
            calculate_stats = self._calculate_stats
            if calculate_stats is None:
                from .spectrum_math import calculate_stats

            self._publish_analyzed_spectrum(
                averaged[0],
                averaged[1],
                calculate_stats(averaged[0], averaged[1]),
            )
        return averaged

    def _publish_analyzed_spectrum(
        self,
        wavelength: Any,
        intensity: Any,
        stats: Any,
    ) -> None:
        """Analyze an averaged frame on the reader thread and publish it.

        Mirrors the GUI-side analysis previously done per frame in
        ``MainWindow.on_spectrum_curve``: saturation detection, PIB, SMSR and
        peak annotations all run here so the GUI thread only consumes results.
        """
        saturation = detect_spectrum_saturation(intensity, self.full_scale_counts)
        try:
            peak_intensity = max(float(value) for value in intensity)
        except (TypeError, ValueError):
            peak_intensity = 0.0
        try:
            has_enough_pib_samples = len(intensity) >= 3
        except TypeError:
            has_enough_pib_samples = True
        pib = (
            calculate_pib(wavelength, intensity)
            if not self.headless and not saturation.saturated and has_enough_pib_samples
            else math.nan
        )
        smsr = calculate_smsr(wavelength, intensity) if not saturation.saturated else None
        annotations = (
            find_spectrum_peak_annotations(
                zip(wavelength, intensity),
                stats=stats,
                smsr_result=smsr,
            )
            if smsr is not None
            else []
        )
        self._publish_latest_spectrum(
            SpectrumFrame(
                wavelength=wavelength,
                intensity=intensity,
                stats=stats,
                pib=pib,
                smsr=smsr,
                saturation=saturation,
                peak_annotations=tuple(annotations),
                peak_intensity=float(peak_intensity),
            )
        )

    @staticmethod
    def _copy_spectrum_values(values: Any) -> Any:
        copy_values = getattr(values, "copy", None)
        if callable(copy_values):
            return copy_values()
        try:
            return tuple(values)
        except TypeError:
            return values

    def run(self) -> None:
        spectrometer = None
        try:
            try:
                OceanSpectrometer, calculate_stats = load_spectrometer_components(None)
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    f"缺少光谱仪依赖：{exc.name}。请检查项目环境和本地 OceanDirect 文件。"
                ) from exc
            self._calculate_stats = calculate_stats

            startup_error: Exception | None = None
            for attempt in range(2):
                try:
                    spectrometer = OceanSpectrometer()
                    device_id = open_spectrometer_device(spectrometer, self.settings.device_id)
                    minimum_integration_us = int(
                        getattr(spectrometer, "get_minimum_integration_time", lambda: 1)()
                    )
                    device_maximum_integration_us = max(
                        minimum_integration_us,
                        int(
                            getattr(
                                spectrometer,
                                "get_maximum_integration_time",
                                lambda: 10_000_000,
                            )()
                        ),
                    )
                    maximum_intensity = float(
                        getattr(
                            spectrometer,
                            "get_maximum_intensity",
                            lambda: AUTO_INTEGRATION_MAX_COUNTS,
                        )()
                    )
                    self.full_scale_counts = (
                        maximum_intensity
                        if math.isfinite(maximum_intensity) and maximum_intensity > 0.0
                        else AUTO_INTEGRATION_MAX_COUNTS
                    )
                    integration_search = AutoIntegrationSearch(
                        minimum_integration_us,
                        device_maximum_integration_us,
                        self.full_scale_counts,
                    )
                    requested_fixed_integration_us = self._take_fixed_integration_time()
                    if requested_fixed_integration_us is not None:
                        self._auto_integration_enabled.clear()
                    configured_integration_us = (
                        int(self.settings.integration_time_us)
                        if requested_fixed_integration_us is None
                        else requested_fixed_integration_us
                    )
                    current_integration_us = (
                        integration_search.minimum_us
                        if self._auto_integration_enabled.is_set()
                        else max(
                            minimum_integration_us,
                            min(configured_integration_us, device_maximum_integration_us),
                        )
                    )
                    spectrometer.set_integration_time(current_integration_us)
                    self.full_scale_changed.emit(self.full_scale_counts)
                    break
                except Exception as exc:
                    startup_error = exc
                    if spectrometer is not None:
                        try:
                            spectrometer.close()
                        except Exception:
                            pass
                    spectrometer = None
                    if attempt == 0 and not self._stop_requested.is_set():
                        self.status.emit(
                            "光谱仪首次启动失败，正在清理 OceanDirect 会话并自动重试…"
                        )
                        self._stop_requested.wait(0.5)
                        if self._stop_requested.is_set():
                            return
                        continue
                    raise RuntimeError(
                        "OceanDirect 光谱仪启动失败（自动重试后仍失败）："
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
            if startup_error is not None and spectrometer is None:
                raise RuntimeError(
                    "OceanDirect 光谱仪启动失败："
                    f"{type(startup_error).__name__}: {startup_error}"
                ) from startup_error
            if self._stop_requested.is_set():
                return
            if current_integration_us != self.settings.integration_time_us:
                self.integration_time_changed.emit(current_integration_us)
                self.status.emit(
                    f"光谱仪积分时间已调整到设备允许范围：{current_integration_us} us"
                )
            self.status.emit(f"光谱仪已连接，设备 ID：{device_id}")
            self.status.emit(f"光谱平均：{self._spectrum_averager.frame_count} 帧滚动平均")
            self.is_ready = True
            self.ready.emit()

            auto_integration_waiting_for_operator = False
            while not self._stop_requested.is_set():
                requested_fixed_integration_us = self._take_fixed_integration_time()
                if requested_fixed_integration_us is not None:
                    self._spectrum_averager.reset()
                    self._auto_integration_enabled.clear()
                    fixed_integration_us = max(
                        minimum_integration_us,
                        min(requested_fixed_integration_us, device_maximum_integration_us),
                    )
                    if fixed_integration_us != current_integration_us:
                        spectrometer.set_integration_time(fixed_integration_us)
                        current_integration_us = fixed_integration_us
                        self.integration_time_changed.emit(current_integration_us)
                        self.status.emit(
                            f"光谱仪固定积分时间：{current_integration_us} us"
                        )
                    auto_integration_waiting_for_operator = False
                wavelength, intensity = spectrometer.read_spectrum()
                if self._stop_requested.is_set():
                    break
                # A fixed-time request may arrive while the native read is in
                # progress. Do not publish that now-stale frame.
                if self._fixed_integration_pending.is_set():
                    continue
                if self._auto_integration_enabled.is_set():
                    if self._resume_auto_integration_requested.is_set():
                        self._resume_auto_integration_requested.clear()
                        self._spectrum_averager.reset()
                        integration_search.reset()
                        auto_integration_waiting_for_operator = False
                    if auto_integration_waiting_for_operator:
                        self._publish_averaged_spectrum(wavelength, intensity)
                        self._stop_requested.wait(self.settings.interval_ms / 1000.0)
                        continue
                    try:
                        peak_counts = max(float(value) for value in intensity)
                    except (TypeError, ValueError):
                        peak_counts = 0.0
                    (
                        adjusted_integration_us,
                        intensity_in_range,
                        adjustment_failed,
                    ) = integration_search.evaluate(current_integration_us, peak_counts)
                    if adjustment_failed:
                        auto_integration_waiting_for_operator = True
                        self._publish_averaged_spectrum(wavelength, intensity)
                        self.auto_integration_failed.emit(
                            {
                                "peak_counts": peak_counts,
                                "full_scale_counts": integration_search.full_scale_counts,
                                "integration_time_us": current_integration_us,
                                "adjustment_count": integration_search.adjustment_count,
                            }
                        )
                        self._stop_requested.wait(self.settings.interval_ms / 1000.0)
                        continue
                    if not intensity_in_range:
                        if adjusted_integration_us != current_integration_us:
                            self._spectrum_averager.reset()
                            current_integration_us = adjusted_integration_us
                            spectrometer.set_integration_time(current_integration_us)
                            self.integration_time_changed.emit(current_integration_us)
                            self.status.emit(f"光谱仪自动积分时间：{current_integration_us} us")
                            # This spectrum belongs to the previous integration
                            # time. Publishing it after the setting changes makes
                            # the UI show a stale saturated plateau under the new
                            # integration value, so deliberately discard it.
                        else:
                            # At a device limit there is no newer setting to wait
                            # for; keep the live spectrum visible so the operator
                            # can adjust the receiver position.
                            self._publish_averaged_spectrum(wavelength, intensity)
                        self._stop_requested.wait(self.settings.interval_ms / 1000.0)
                        continue
                averaged = self._spectrum_averager.add(wavelength, intensity)
                if averaged is None:
                    self._stop_requested.wait(self.settings.interval_ms / 1000.0)
                    continue
                averaged_wavelength, averaged_intensity = averaged
                stats = calculate_stats(averaged_wavelength, averaged_intensity)
                self._publish_analyzed_spectrum(
                    averaged_wavelength,
                    averaged_intensity,
                    stats,
                )
                self.reading.emit(
                    SpectrometerReading(
                        peak_wavelength_nm=stats.peak_wavelength_nm,
                        centroid_nm=stats.centroid_nm,
                        fwhm_nm=stats.fwhm_nm,
                    )
                )
                self._stop_requested.wait(self.settings.interval_ms / 1000.0)
        except Exception as exc:
            _LOGGER.exception("光谱仪采集线程失败")
            self.failed.emit(
                f"OceanDirect 光谱仪采集失败：{type(exc).__name__}: {exc}"
            )
        finally:
            self.is_ready = False
            if spectrometer is not None:
                try:
                    spectrometer.close()
                except Exception:
                    pass
