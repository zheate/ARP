"""Shared immutable data contracts for the combined test application."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .spectrum_math import SmsrResult, SpectrumStats


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
    stability_limit_percent: float
    output_dir: Path
    spectrometer_device_id: int | None = None


@dataclass(frozen=True)
class PowerMeterOption:
    resource: str
    device_type: str
    detail: str
    driver_kind: str = "caihuang"
    serial_number: str = ""

    def label(self) -> str:
        parts = (self.device_type, self.resource, self.detail)
        return " | ".join(part for part in parts if part)


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
    stability_limit_percent: float
    driver_kind: str = "caihuang"
    serial_number: str = ""
    absolute_floor_w: float = 0.0


@dataclass(frozen=True)
class SpectrometerSettings:
    integration_time_us: int
    interval_ms: int
    device_id: int | None = None
    auto_integration_enabled: bool = False
    average_frames: int = 1
    pib_center_wavelength_nm: float = 976.0
    pib_half_range_nm: float = 1.5
    serial_number: str = ""


@dataclass(frozen=True)
class PowerMeterReading:
    elapsed_s: float
    power_w: float
    stable: bool
    stable_span_w: float
    stable_window_s: float
    stable_median_w: float
    stability_generation: int = 0
    stability_percent: float = math.nan
    stability_limit_percent: float = math.nan
    stable_duration_s: float = 0.0
    stability_limit_w: float = math.nan
    stability_drift_w: float = math.nan
    stability_drift_limit_w: float = math.nan
    stability_drift_exceeded: bool = False
    stability_two_sigma_w: float = math.nan
    connection_generation: int = 0
    transaction_started_monotonic_s: float = 0.0
    instrument_configuration_token: str = ""


@dataclass(frozen=True)
class SpectrometerReading:
    peak_wavelength_nm: float
    centroid_nm: float
    fwhm_nm: float


@dataclass(frozen=True, eq=False)
class SpectrumFrame:
    wavelength: Any
    intensity: Any
    stats: SpectrumStats | None
    # Analysis produced on the reader thread; ``saturation is None`` marks a
    # frame that has not been analyzed (the GUI falls back to computing it).
    pib: float = math.nan
    smsr: SmsrResult | None = None
    saturation: SpectrumSaturationResult | None = None
    peak_annotations: tuple[SpectrumPeakAnnotation, ...] = ()
    peak_intensity: float = 0.0

    @staticmethod
    def _values_equal(left: Any, right: Any) -> bool:
        try:
            left_values = list(left)
            right_values = list(right)
        except TypeError:
            return left == right
        if len(left_values) != len(right_values):
            return False
        for left_value, right_value in zip(left_values, right_values):
            try:
                left_number = float(left_value)
                right_number = float(right_value)
            except (TypeError, ValueError):
                if left_value != right_value:
                    return False
            else:
                if math.isnan(left_number) and math.isnan(right_number):
                    continue
                if left_number != right_number:
                    return False
        return True

    @staticmethod
    def _numbers_equal(left: float, right: float) -> bool:
        return (math.isnan(float(left)) and math.isnan(float(right))) or float(left) == float(right)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SpectrumFrame):
            return NotImplemented
        return (
            self._values_equal(self.wavelength, other.wavelength)
            and self._values_equal(self.intensity, other.intensity)
            and self.stats == other.stats
            and self._numbers_equal(self.pib, other.pib)
            and self.smsr == other.smsr
            and self.saturation == other.saturation
            and self.peak_annotations == other.peak_annotations
            and self._numbers_equal(self.peak_intensity, other.peak_intensity)
        )


@dataclass(frozen=True)
class SpectrumPeakAnnotation:
    label: str
    peak_wavelength_nm: float
    peak_intensity: float


@dataclass(frozen=True)
class SpectrumSaturationResult:
    saturated: bool
    peak_intensity: float
    consecutive_pixels: int
