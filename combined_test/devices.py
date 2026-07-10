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

    def __init__(self, preferred_resource: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.preferred_resource = preferred_resource

    def run(self) -> None:
        try:
            try:
                import pyvisa
                from tools.power_meter_mvp import CaihuangPowerMeter
            except ModuleNotFoundError as exc:
                raise RuntimeError(f"缺少功率计依赖：{exc.name}。请在 sth_eb314 环境中运行。") from exc

            rm = pyvisa.ResourceManager()
            try:
                resources: list[str] = []
                for item in rm.list_resources():
                    resource = normalize_power_resource_name(str(item))
                    if resource.startswith("ASRL"):
                        resources.append(resource)
                resources.sort()
            finally:
                rm.close()

            candidates: list[str] = []
            preferred = normalize_power_resource_name(self.preferred_resource)
            if preferred:
                candidates.append(preferred)
            for resource in resources:
                if resource not in candidates:
                    candidates.append(resource)

            self.status.emit(f"正在 {len(candidates)} 个端口上检测功率计…")
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
        self._stable_window_s = float(settings.stable_window_s)
        self._stable_tolerance_w = float(settings.stable_tolerance_w)

    def stop(self) -> None:
        self._running = False

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
                from tools.power_meter_mvp import CaihuangPowerMeter, normalize_resource
            except ModuleNotFoundError as exc:
                raise RuntimeError(f"缺少功率计依赖：{exc.name}。请在 sth_eb314 环境中运行。") from exc

            meter = CaihuangPowerMeter(self.settings.resource)
            if meter.test() != "OK":
                raise RuntimeError("功率计自检未返回 OK")
            meter.set_wavelength(self.settings.wavelength_nm)
            self.status.emit(f"功率计已连接：{normalize_resource(self.settings.resource)}")

            with self._stability_state_lock:
                stable_window_s = self._stable_window_s
                stable_tolerance_w = self._stable_tolerance_w
            detector = PowerStabilityDetector(stable_window_s, stable_tolerance_w)
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
                    stable_window_s = self._stable_window_s
                    stable_tolerance_w = self._stable_tolerance_w
                if reset_requested:
                    detector = PowerStabilityDetector(stable_window_s, stable_tolerance_w)
                else:
                    detector.window_s = stable_window_s
                    detector.tolerance_w = stable_tolerance_w
                elapsed = time.monotonic() - start
                power_w = meter.read_power_w() * self.settings.software_gain
                stable_tolerance_w = stability_tolerance_for_power(power_w)
                detector.tolerance_w = stable_tolerance_w
                stability = detector.add_sample(elapsed, power_w)
                self.reading.emit(
                    PowerMeterReading(
                        elapsed_s=elapsed,
                        power_w=power_w,
                        stable=stability.stable,
                        stable_span_w=stability.span_w,
                        stable_window_s=stability.window_s,
                        stability_generation=generation,
                        stable_tolerance_w=stable_tolerance_w,
                    )
                )
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
                    f"缺少光谱仪依赖：{exc.name}。请检查项目环境和本地 OceanDirect 文件。"
                ) from exc

            spectrometer = OceanSpectrometer()
            device_id = open_spectrometer_device(spectrometer, self.settings.device_id)
            spectrometer.set_integration_time(self.settings.integration_time_us)
            self.status.emit(f"光谱仪已连接，设备 ID：{device_id}")

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
