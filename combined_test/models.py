"""Shared immutable data contracts for the combined test application."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path


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
    output_dir: Path
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
        return f"{self.device_type} | 设备 ID {self.device_id}"


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
    stable_tolerance_w: float = math.nan


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


@dataclass(frozen=True)
class SpectrumSaturationResult:
    saturated: bool
    peak_intensity: float
    consecutive_pixels: int
