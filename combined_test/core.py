"""Pure measurement, stability, and record formatting logic."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from statistics import median
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
    """Conservative time-window detector with noise and drift protection.

    A stable result requires enough continuously sampled data, an acceptable
    robust peak-to-peak span, and no meaningful shift between the beginning
    and end of the window.  One isolated interior spike may be ignored, but a
    new endpoint or a sustained level change is never treated as a spike.
    """

    MIN_SAMPLE_COUNT = 5
    MAX_SAMPLE_GAP_FRACTION = 0.5
    MAX_CENTER_SHIFT_FRACTION = 0.5
    REQUIRED_CONFIRMATIONS = 2

    def __init__(self, window_s: float, tolerance_w: float) -> None:
        if window_s <= 0:
            raise ValueError("稳定窗口必须大于 0 秒")
        if tolerance_w < 0:
            raise ValueError("允许波动必须大于或等于 0 W")
        self.window_s = float(window_s)
        self.tolerance_w = float(tolerance_w)
        self._samples: Deque[tuple[float, float]] = deque()
        self._stable_confirmations = 0

    def reset(self) -> None:
        self._samples.clear()
        self._stable_confirmations = 0

    def _remove_isolated_spike(self, samples: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """Remove at most one clear, single-sample interior impulse."""
        if len(samples) < self.MIN_SAMPLE_COUNT or self.tolerance_w <= 0.0:
            return samples

        candidates: list[tuple[float, int]] = []
        neighbor_limit = self.tolerance_w * self.MAX_CENTER_SHIFT_FRACTION
        for index in range(1, len(samples) - 1):
            previous_power = samples[index - 1][1]
            power = samples[index][1]
            next_power = samples[index + 1][1]
            neighbor_center = (previous_power + next_power) / 2.0
            deviation = abs(power - neighbor_center)
            if abs(previous_power - next_power) <= neighbor_limit and deviation > self.tolerance_w:
                candidates.append((deviation, index))

        if not candidates:
            return samples
        _deviation, spike_index = max(candidates)
        return samples[:spike_index] + samples[spike_index + 1 :]

    def _unstable_result(self, span: float, covered_s: float) -> StabilityResult:
        self._stable_confirmations = 0
        return StabilityResult(False, span, covered_s, len(self._samples))

    def add_sample(self, elapsed_s: float, power_w: float) -> StabilityResult:
        elapsed = float(elapsed_s)
        power = float(power_w)
        if not math.isfinite(elapsed) or not math.isfinite(power):
            return self._unstable_result(math.inf, 0.0)

        if self._samples and elapsed < self._samples[-1][0]:
            # A restarted clock must not be combined with the previous run.
            self.reset()
        elif self._samples and elapsed == self._samples[-1][0]:
            # Replacing a duplicate timestamp avoids manufacturing sample
            # density when a source retries the same acquisition instant.
            self._samples.pop()

        self._samples.append((elapsed, power))
        cutoff = elapsed - self.window_s
        # Keep the sample immediately before the window cutoff.  A fixed polling
        # interval rarely lands exactly on the cutoff (for example, 2.99 s for a
        # 3.00 s window).  Removing that boundary sample first can make the
        # apparent coverage remain just below the requested window forever.
        # Retaining it makes the span check slightly more conservative while
        # allowing a fully stable window to be recognised despite poll jitter.
        while len(self._samples) > 1 and self._samples[1][0] <= cutoff:
            self._samples.popleft()

        samples = list(self._samples)
        filtered_samples = self._remove_isolated_spike(samples)
        powers = [sample[1] for sample in filtered_samples]
        span = max(powers) - min(powers) if powers else math.inf
        covered_s = self._samples[-1][0] - self._samples[0][0] if len(self._samples) >= 2 else 0.0
        if covered_s < self.window_s or len(filtered_samples) < self.MIN_SAMPLE_COUNT:
            return self._unstable_result(span, covered_s)

        gaps = [
            samples[index][0] - samples[index - 1][0]
            for index in range(1, len(samples))
        ]
        if gaps and max(gaps) > self.window_s * self.MAX_SAMPLE_GAP_FRACTION:
            return self._unstable_result(span, covered_s)

        section_size = max(2, len(filtered_samples) // 3)
        beginning_center = median(sample[1] for sample in filtered_samples[:section_size])
        ending_center = median(sample[1] for sample in filtered_samples[-section_size:])
        center_shift = abs(ending_center - beginning_center)
        comparison_margin = max(1e-12, self.tolerance_w * 1e-12)
        candidate_stable = (
            span <= self.tolerance_w + comparison_margin
            and center_shift
            <= self.tolerance_w * self.MAX_CENTER_SHIFT_FRACTION + comparison_margin
        )
        if not candidate_stable:
            return self._unstable_result(span, covered_s)

        self._stable_confirmations += 1
        stable = self._stable_confirmations >= self.REQUIRED_CONFIRMATIONS
        return StabilityResult(stable, span, covered_s, len(self._samples))


class WavelengthStabilityDetector(PowerStabilityDetector):
    """Time-window stability detector for spectral centroid wavelength."""


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
        raise ValueError("电流必须在 0 至 20 A 范围内")
    centiampere = round(value * 100)
    if centiampere > 2000:
        raise ValueError("电流必须在 0 至 20 A 范围内")
    integer_part, decimal_part = divmod(centiampere, 100)
    return [0xB4, 0xFF, integer_part, decimal_part]


def decode_i2c_value(data: Iterable[int]) -> float:
    values = list(data)
    if len(values) < 4:
        raise ValueError("I2C 响应至少需要包含 4 个字节")
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
        raise ValueError("波长和强度数据的长度必须一致")

    rows = [["wavelength_nm", "intensity"]]
    for x, y in zip(wavelength_values, intensity_values):
        rows.append([f"{float(x):.6f}", f"{float(y):.6f}"])
    return rows
