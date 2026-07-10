from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from scipy.signal import medfilt


@dataclass(frozen=True)
class SpectrumStats:
    peak_wavelength_nm: float
    peak_intensity: float
    centroid_nm: float
    fwhm_nm: float


def _as_float_lists(x: Iterable[float], y: Iterable[float]) -> tuple[list[float], list[float]]:
    x_values = [float(value) for value in x]
    y_values = [float(value) for value in y]
    if not x_values or len(x_values) != len(y_values):
        return [], []
    pairs = [(x_value, y_value) for x_value, y_value in zip(x_values, y_values) if math.isfinite(x_value) and math.isfinite(y_value)]
    if not pairs:
        return [], []
    return [item[0] for item in pairs], [item[1] for item in pairs]


def calculate_central(x: Iterable[float], y: Iterable[float]) -> float:
    x_values, y_values = _as_float_lists(x, y)
    if not x_values:
        return math.nan
    peak_index = max(range(len(y_values)), key=y_values.__getitem__)
    return x_values[peak_index]


def calculate_centroid(x: Iterable[float], y: Iterable[float]) -> float:
    x_values, y_values = _as_float_lists(x, y)
    if not x_values:
        return math.nan
    peak_index = max(range(len(y_values)), key=y_values.__getitem__)
    baseline = min(y_values)
    peak = y_values[peak_index]
    if peak <= baseline:
        return math.nan

    # A whole-spectrum centroid is dominated by broadband offset and detector
    # noise, so its displayed value can jump even while the laser peak is
    # visually stationary. Restrict the centroid to the contiguous dominant
    # peak lobe and subtract its baseline before weighting.
    threshold = baseline + (peak - baseline) * 0.1
    left = peak_index
    while left > 0 and y_values[left - 1] >= threshold:
        left -= 1
    right = peak_index
    while right < len(y_values) - 1 and y_values[right + 1] >= threshold:
        right += 1

    weighted_sum = 0.0
    weight_total = 0.0
    for index in range(left, right + 1):
        weight = max(0.0, y_values[index] - baseline)
        weighted_sum += x_values[index] * weight
        weight_total += weight
    if weight_total <= 0.0:
        return math.nan
    return weighted_sum / weight_total


def calculate_fwhm(x: Iterable[float], y: Iterable[float]) -> float:
    x_values, y_values = _as_float_lists(x, y)
    if len(x_values) < 3:
        return math.nan

    peak_index = max(range(len(y_values)), key=y_values.__getitem__)
    peak = y_values[peak_index]
    baseline = min(y_values)
    if peak <= baseline:
        return math.nan
    half = baseline + (peak - baseline) / 2.0

    left = _interpolate_crossing(x_values, y_values, peak_index, -1, half)
    right = _interpolate_crossing(x_values, y_values, peak_index, 1, half)
    if left is None or right is None:
        return math.nan
    return abs(right - left)


def _interpolate_crossing(x_values: list[float], y_values: list[float], start: int, step: int, target: float) -> float | None:
    previous_index = start
    index = start + step
    while 0 <= index < len(y_values):
        previous_y = y_values[previous_index]
        y = y_values[index]
        if (previous_y >= target and y <= target) or (previous_y <= target and y >= target):
            previous_x = x_values[previous_index]
            x = x_values[index]
            if y == previous_y:
                return x
            fraction = (target - previous_y) / (y - previous_y)
            return previous_x + fraction * (x - previous_x)
        previous_index = index
        index += step
    return None


def calculate_stats(wavelength: Iterable[float], intensity: Iterable[float]) -> SpectrumStats:
    wavelength_values, intensity_values = _as_float_lists(wavelength, intensity)
    if not wavelength_values:
        return SpectrumStats(math.nan, math.nan, math.nan, math.nan)

    peak_index = max(range(len(intensity_values)), key=intensity_values.__getitem__)
    return SpectrumStats(
        peak_wavelength_nm=wavelength_values[peak_index],
        peak_intensity=intensity_values[peak_index],
        centroid_nm=calculate_centroid(wavelength_values, intensity_values),
        fwhm_nm=calculate_fwhm(wavelength_values, intensity_values),
    )


def calculate_pib(
    wavelength: Iterable[float],
    intensity: Iterable[float],
    center_nm: float = 976.0,
    half_range_nm: float = 1.5,
) -> float:
    """Return the median-filtered power-in-band ratio as a value from 0 to 1."""
    wavelength_values, intensity_values = _as_float_lists(wavelength, intensity)
    if not wavelength_values:
        return math.nan
    if not math.isfinite(center_nm) or not math.isfinite(half_range_nm) or half_range_nm < 0:
        raise ValueError("PIB center and half range must be finite; half range cannot be negative")

    filtered_intensity = [float(value) for value in medfilt(intensity_values)]
    total_intensity = sum(filtered_intensity)
    if not math.isfinite(total_intensity) or total_intensity == 0.0:
        return math.nan

    lower_nm = center_nm - half_range_nm
    upper_nm = center_nm + half_range_nm
    band_intensity = sum(
        value
        for wavelength_nm, value in zip(wavelength_values, filtered_intensity)
        if lower_nm <= wavelength_nm <= upper_nm
    )
    return band_intensity / total_intensity
