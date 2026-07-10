from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Iterable


CSV_HEADER = [
    "timestamp",
    "elapsed_s",
    "set_current_a",
    "output_current_a",
    "output_voltage_v",
    "power_w",
    "peak_wavelength_nm",
    "centroid_nm",
    "fwhm_nm",
    "stable_span_w",
    "stable_window_s",
    "spectrum_csv_path",
]


@dataclass(frozen=True)
class StabilityResult:
    stable: bool
    span_w: float
    window_s: float
    sample_count: int


@dataclass(frozen=True)
class CombinedMeasurement:
    elapsed_s: float
    set_current_a: float
    output_current_a: float
    output_voltage_v: float
    power_w: float
    peak_wavelength_nm: float
    centroid_nm: float
    fwhm_nm: float
    stable_span_w: float
    stable_window_s: float
    spectrum_csv_path: str = ""


class PowerStabilityDetector:
    def __init__(self, window_s: float, tolerance_w: float) -> None:
        if window_s <= 0:
            raise ValueError("window_s must be greater than 0")
        if tolerance_w < 0:
            raise ValueError("tolerance_w must be greater than or equal to 0")
        self.window_s = float(window_s)
        self.tolerance_w = float(tolerance_w)
        self._samples: Deque[tuple[float, float]] = deque()

    def reset(self) -> None:
        self._samples.clear()

    def add_sample(self, elapsed_s: float, power_w: float) -> StabilityResult:
        self._samples.append((float(elapsed_s), float(power_w)))
        cutoff = float(elapsed_s) - self.window_s
        # Keep the sample immediately before the window cutoff.  A fixed polling
        # interval rarely lands exactly on the cutoff (for example, 2.99 s for a
        # 3.00 s window).  Removing that boundary sample first can make the
        # apparent coverage remain just below the requested window forever.
        # Retaining it makes the span check slightly more conservative while
        # allowing a fully stable window to be recognised despite poll jitter.
        while len(self._samples) > 1 and self._samples[1][0] <= cutoff:
            self._samples.popleft()

        powers = [sample[1] for sample in self._samples]
        span = max(powers) - min(powers) if powers else math.inf
        covered_s = self._samples[-1][0] - self._samples[0][0] if len(self._samples) >= 2 else 0.0
        stable = covered_s >= self.window_s and span <= self.tolerance_w
        return StabilityResult(stable, span, covered_s, len(self._samples))


def stability_tolerance_for_power(power_w: float) -> float:
    """Return the allowed stability span for the current power range."""
    power = float(power_w)
    if power < 100.0:
        return 0.15
    if power < 200.0:
        return 0.25
    return 0.35


def build_set_current_command(current_a: float) -> list[int]:
    value = float(current_a)
    if not math.isfinite(value) or value < 0.0 or value > 20.0:
        raise ValueError("current_a must be in range 0..20")
    centiampere = round(value * 100)
    if centiampere > 2000:
        raise ValueError("current_a must be in range 0..20")
    integer_part, decimal_part = divmod(centiampere, 100)
    return [0xB4, 0xFF, integer_part, decimal_part]


def decode_i2c_value(data: Iterable[int]) -> float:
    values = list(data)
    if len(values) < 4:
        raise ValueError("I2C response must contain at least 4 bytes")
    return float(values[2]) + float(values[3]) / 100.0


def format_float(value: float, decimals: int = 3) -> str:
    number = float(value)
    if not math.isfinite(number):
        return ""
    return f"{number:.{decimals}f}"


def format_current(value: float) -> str:
    number = float(value)
    if not math.isfinite(number):
        return ""
    return f"{number:.1f}".rstrip("0").rstrip(".")


def record_to_row(timestamp: str, measurement: CombinedMeasurement) -> list[str]:
    return [
        timestamp,
        format_float(measurement.elapsed_s),
        format_current(measurement.set_current_a),
        format_float(measurement.output_current_a),
        format_float(measurement.output_voltage_v),
        format_float(measurement.power_w),
        format_float(measurement.peak_wavelength_nm),
        format_float(measurement.centroid_nm),
        format_float(measurement.fwhm_nm),
        format_float(measurement.stable_span_w),
        format_float(measurement.stable_window_s),
        measurement.spectrum_csv_path,
    ]


def spectrum_curve_to_rows(wavelength: Iterable[float], intensity: Iterable[float]) -> list[list[str]]:
    wavelength_values = list(wavelength)
    intensity_values = list(intensity)
    if len(wavelength_values) != len(intensity_values):
        raise ValueError("wavelength and intensity must have the same length")

    rows = [["wavelength_nm", "intensity"]]
    for x, y in zip(wavelength_values, intensity_values):
        rows.append([f"{float(x):.6f}", f"{float(y):.6f}"])
    return rows
