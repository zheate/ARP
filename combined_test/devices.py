"""Hardware adapters and acquisition threads for the combined test app."""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget

from tools.visa_session import visa_resource_manager

from .core import PowerStabilityDetector, decode_i2c_value, stability_tolerance_for_power
from .models import (
    PowerMeterOption,
    PowerMeterReading,
    PowerMeterSettings,
    SpectrometerReading,
    SpectrometerSettings,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_ROOT = REPO_ROOT / "tools"
POWER_METER_PROBE_TIMEOUT_MS = 250
AUTO_INTEGRATION_LOW_COUNTS = 8_000.0
AUTO_INTEGRATION_HIGH_COUNTS = 14_000.0
AUTO_INTEGRATION_TARGET_COUNTS = 11_000.0


def next_auto_integration_time(
    current_us: int,
    peak_counts: float,
    minimum_us: int,
    maximum_us: int,
) -> int:
    """Return a bounded integration time that moves the peak into the target band."""
    current = max(int(minimum_us), min(int(maximum_us), int(current_us)))
    peak = float(peak_counts)
    if AUTO_INTEGRATION_LOW_COUNTS <= peak <= AUTO_INTEGRATION_HIGH_COUNTS:
        return current
    if peak <= 0.0:
        ratio = 2.0
    else:
        ratio = max(0.5, min(2.0, AUTO_INTEGRATION_TARGET_COUNTS / peak))
    return max(int(minimum_us), min(int(maximum_us), round(current * ratio)))


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

    state = spectrometer.control.find_usb_devices()
    if state == -1:
        raise RuntimeError("OceanDirect 搜索 USB 光谱仪失败")
    device_ids = [int(item) for item in spectrometer.control.get_device_ids()]
    if not device_ids:
        raise RuntimeError("OceanDirect 未找到光谱仪，请检查 Ocean Insight 驱动。")

    device_id = selected_device_id if selected_device_id in device_ids else device_ids[0]
    state = spectrometer.control.open_device(device_id)
    if state == -1:
        raise RuntimeError(f"无法打开设备 ID 为 {device_id} 的光谱仪")
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
                from tools.power_meter_mvp import CaihuangPowerMeter, LaserPointPowerMeter
            except ModuleNotFoundError as exc:
                raise RuntimeError(f"缺少功率计依赖：{exc.name}。请在 sth_eb314 环境中运行。") from exc

            with visa_resource_manager() as rm:
                resources: list[str] = []
                for item in rm.list_resources():
                    resource = normalize_power_resource_name(str(item))
                    if resource.startswith("ASRL"):
                        resources.append(resource)
                resources.sort()

            candidates: list[str] = []
            preferred = normalize_power_resource_name(self.preferred_resource)
            if preferred:
                candidates.append(preferred)
            if self.scan_all_resources:
                for resource in resources:
                    if resource not in candidates:
                        candidates.append(resource)

            self.status.emit(f"正在 {len(candidates)} 个端口上检测功率计…")
            options: list[PowerMeterOption] = []
            for resource in candidates:
                if self._stop_requested.is_set():
                    return
                for meter_class in (CaihuangPowerMeter, LaserPointPowerMeter):
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
            if not self._stop_requested.is_set():
                self.detected.emit(options)
        except Exception as exc:
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
                from tools.power_meter_mvp import (
                    CaihuangPowerMeter,
                    LaserPointPowerMeter,
                    normalize_resource,
                )
            except ModuleNotFoundError as exc:
                raise RuntimeError(f"缺少功率计依赖：{exc.name}。请在 sth_eb314 环境中运行。") from exc

            meter_classes = {
                "caihuang": CaihuangPowerMeter,
                "laserpoint": LaserPointPowerMeter,
            }
            meter_class = meter_classes.get(self.settings.driver_kind)
            if meter_class is None:
                raise RuntimeError(f"不支持的功率计驱动类型：{self.settings.driver_kind}")

            meter = meter_class(self.settings.resource)
            if meter.test() != "OK":
                raise RuntimeError("功率计自检未返回 OK")
            if self.settings.driver_kind == "laserpoint":
                meter.set_power_mode()
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
            self.failed.emit(str(exc))
        finally:
            self.is_ready = False
            if meter is not None:
                try:
                    meter.close()
                except Exception:
                    pass


class SpectrometerReaderThread(QThread):
    reading = Signal(object)
    status = Signal(str)
    ready = Signal()
    failed = Signal(str)
    integration_time_changed = Signal(int)

    def __init__(self, settings: SpectrometerSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._stop_requested = threading.Event()
        self._latest_spectrum_lock = threading.Lock()
        self._latest_spectrum: tuple[Any, Any] | None = None
        self.is_ready = False

    def stop(self) -> None:
        self._stop_requested.set()

    def take_latest_spectrum(self) -> tuple[Any, Any] | None:
        """Return the newest frame and discard any older, superseded frame."""
        with self._latest_spectrum_lock:
            spectrum = self._latest_spectrum
            self._latest_spectrum = None
        return spectrum

    def _publish_latest_spectrum(self, wavelength: Any, intensity: Any) -> None:
        # The GUI deliberately polls this one-slot mailbox. A queued Qt signal
        # per frame would retain every pair of arrays when plotting is slower
        # than acquisition and can grow memory without a bound.
        with self._latest_spectrum_lock:
            self._latest_spectrum = (wavelength, intensity)

    def run(self) -> None:
        spectrometer = None
        try:
            try:
                OceanSpectrometer, calculate_stats = load_spectrometer_components(None)
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    f"缺少光谱仪依赖：{exc.name}。请检查项目环境和本地 OceanDirect 文件。"
                ) from exc

            spectrometer = OceanSpectrometer()
            device_id = open_spectrometer_device(spectrometer, self.settings.device_id)
            current_integration_us = self.settings.integration_time_us
            spectrometer.set_integration_time(current_integration_us)
            minimum_integration_us = int(
                getattr(spectrometer, "get_minimum_integration_time", lambda: 1)()
            )
            maximum_integration_us = max(
                minimum_integration_us,
                min(
                    300_000,
                    int(getattr(spectrometer, "get_maximum_integration_time", lambda: 300_000)()),
                ),
            )
            if self._stop_requested.is_set():
                return
            self.status.emit(f"光谱仪已连接，设备 ID：{device_id}")
            self.is_ready = True
            self.ready.emit()

            while not self._stop_requested.is_set():
                wavelength, intensity = spectrometer.read_spectrum()
                if self._stop_requested.is_set():
                    break
                self._publish_latest_spectrum(wavelength, intensity)
                if self.settings.auto_integration_enabled:
                    try:
                        peak_counts = max(float(value) for value in intensity)
                    except (TypeError, ValueError):
                        peak_counts = 0.0
                    adjusted_integration_us = next_auto_integration_time(
                        current_integration_us,
                        peak_counts,
                        minimum_integration_us,
                        maximum_integration_us,
                    )
                    if adjusted_integration_us != current_integration_us:
                        current_integration_us = adjusted_integration_us
                        spectrometer.set_integration_time(current_integration_us)
                        self.integration_time_changed.emit(current_integration_us)
                        self.status.emit(f"光谱仪自动积分时间：{current_integration_us} us")
                        self._stop_requested.wait(self.settings.interval_ms / 1000.0)
                        continue
                stats = calculate_stats(wavelength, intensity)
                self.reading.emit(
                    SpectrometerReading(
                        peak_wavelength_nm=stats.peak_wavelength_nm,
                        centroid_nm=stats.centroid_nm,
                        fwhm_nm=stats.fwhm_nm,
                    )
                )
                self._stop_requested.wait(self.settings.interval_ms / 1000.0)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.is_ready = False
            if spectrometer is not None:
                try:
                    spectrometer.close()
                except Exception:
                    pass
