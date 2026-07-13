"""Spectrum statistics and power-in-band calculations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Iterable

from scipy.signal import find_peaks


PIB_CENTER_NM = 976.0
PIB_HALF_RANGE_NM = 1.5
PIB_ANALYSIS_LOWER_NM = 956.0
PIB_ANALYSIS_UPPER_NM = 996.0
SMSR_MINIMUM_PEAK_DISTANCE_NM = 0.2
SMSR_MINIMUM_PEAK_WIDTH_SAMPLES = 1.5
SMSR_NOISE_SIGMA_MULTIPLIER = 6.0


@dataclass(frozen=True)
class SpectrumStats:
    peak_wavelength_nm: float
    peak_intensity: float
    centroid_nm: float
    fwhm_nm: float


@dataclass(frozen=True)
class SmsrResult:
    smsr_db: float
    main_wavelength_nm: float
    main_intensity: float
    side_wavelength_nm: float
    side_intensity: float


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
    return _calculate_centroid_values(x_values, y_values)


def _calculate_centroid_values(x_values: list[float], y_values: list[float]) -> float:
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
    return _calculate_fwhm_values(x_values, y_values)


def _calculate_fwhm_values(x_values: list[float], y_values: list[float]) -> float:
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
        centroid_nm=_calculate_centroid_values(wavelength_values, intensity_values),
        fwhm_nm=_calculate_fwhm_values(wavelength_values, intensity_values),
    )


def calculate_pib(
    wavelength: Iterable[float],
    intensity: Iterable[float],
    center_nm: float = PIB_CENTER_NM,
    half_range_nm: float = PIB_HALF_RANGE_NM,
    analysis_lower_nm: float = PIB_ANALYSIS_LOWER_NM,
    analysis_upper_nm: float = PIB_ANALYSIS_UPPER_NM,
) -> float:
    """Return integrated power in band over the fixed laser analysis band."""
    wavelength_values, intensity_values = _as_float_lists(wavelength, intensity)
    if not wavelength_values:
        return math.nan
    if (
        not math.isfinite(center_nm)
        or not math.isfinite(half_range_nm)
        or half_range_nm < 0
        or not math.isfinite(analysis_lower_nm)
        or not math.isfinite(analysis_upper_nm)
        or analysis_lower_nm >= analysis_upper_nm
    ):
        raise ValueError("PIB 中心、半范围和分析波段必须为有效有限数值")

    points = sorted(zip(wavelength_values, intensity_values))
    sorted_wavelength = [point[0] for point in points]
    sorted_intensity = [max(0.0, point[1]) for point in points]
    lower_nm = center_nm - half_range_nm
    upper_nm = center_nm + half_range_nm
    if lower_nm < analysis_lower_nm or upper_nm > analysis_upper_nm:
        raise ValueError("PIB 目标波段必须位于分析波段内")
    if sorted_wavelength[0] > analysis_lower_nm or sorted_wavelength[-1] < analysis_upper_nm:
        return math.nan

    total_intensity = _integrate_band(
        sorted_wavelength,
        sorted_intensity,
        analysis_lower_nm,
        analysis_upper_nm,
    )
    if not math.isfinite(total_intensity) or total_intensity <= 0.0:
        return math.nan

    band_intensity = _integrate_band(
        sorted_wavelength,
        sorted_intensity,
        lower_nm,
        upper_nm,
    )
    return band_intensity / total_intensity


def _integrate_band(
    wavelength: list[float],
    intensity: list[float],
    lower_nm: float,
    upper_nm: float,
) -> float:
    """Integrate a sampled spectrum with linearly interpolated band edges."""
    if len(wavelength) < 2 or lower_nm >= upper_nm:
        return 0.0
    lower = max(float(lower_nm), wavelength[0])
    upper = min(float(upper_nm), wavelength[-1])
    if lower >= upper:
        return 0.0

    band_wavelength = [lower]
    band_intensity = [_interpolate(wavelength, intensity, lower)]
    for wavelength_nm, value in zip(wavelength, intensity):
        if lower < wavelength_nm < upper:
            band_wavelength.append(wavelength_nm)
            band_intensity.append(value)
    band_wavelength.append(upper)
    band_intensity.append(_interpolate(wavelength, intensity, upper))

    return sum(
        (band_intensity[index] + band_intensity[index + 1])
        * (band_wavelength[index + 1] - band_wavelength[index])
        / 2.0
        for index in range(len(band_wavelength) - 1)
    )


def _interpolate(wavelength: list[float], intensity: list[float], target_nm: float) -> float:
    if target_nm <= wavelength[0]:
        return intensity[0]
    if target_nm >= wavelength[-1]:
        return intensity[-1]
    for index in range(1, len(wavelength)):
        if wavelength[index] < target_nm:
            continue
        left_wavelength = wavelength[index - 1]
        right_wavelength = wavelength[index]
        if right_wavelength == left_wavelength:
            return intensity[index]
        fraction = (target_nm - left_wavelength) / (right_wavelength - left_wavelength)
        return intensity[index - 1] + fraction * (intensity[index] - intensity[index - 1])
    return intensity[-1]


def calculate_smsr(
    wavelength: Iterable[float],
    intensity: Iterable[float],
    analysis_lower_nm: float = PIB_ANALYSIS_LOWER_NM,
    analysis_upper_nm: float = PIB_ANALYSIS_UPPER_NM,
    minimum_peak_distance_nm: float = SMSR_MINIMUM_PEAK_DISTANCE_NM,
    minimum_peak_width_samples: float = SMSR_MINIMUM_PEAK_WIDTH_SAMPLES,
    noise_sigma_multiplier: float = SMSR_NOISE_SIGMA_MULTIPLIER,
) -> SmsrResult:
    """Return 10*log10(main mode / highest significant resolved side mode)."""
    wavelength_values, intensity_values = _as_float_lists(wavelength, intensity)
    empty = SmsrResult(math.nan, math.nan, math.nan, math.nan, math.nan)
    if not wavelength_values:
        return empty
    if (
        not math.isfinite(analysis_lower_nm)
        or not math.isfinite(analysis_upper_nm)
        or analysis_lower_nm >= analysis_upper_nm
        or not math.isfinite(minimum_peak_distance_nm)
        or minimum_peak_distance_nm < 0.0
        or not math.isfinite(minimum_peak_width_samples)
        or minimum_peak_width_samples <= 0.0
        or not math.isfinite(noise_sigma_multiplier)
        or noise_sigma_multiplier < 0.0
    ):
        raise ValueError("SMSR 分析波段、峰间距和噪声阈值必须为有效数值")

    measured_lower_nm = min(wavelength_values)
    measured_upper_nm = max(wavelength_values)
    if measured_lower_nm > analysis_lower_nm or measured_upper_nm < analysis_upper_nm:
        return empty

    points = sorted(
        (wavelength_nm, max(0.0, value))
        for wavelength_nm, value in zip(wavelength_values, intensity_values)
        if analysis_lower_nm <= wavelength_nm <= analysis_upper_nm
    )
    if len(points) < 3:
        return empty
    analysis_wavelength = [point[0] for point in points]
    analysis_intensity = [point[1] for point in points]
    baseline = min(analysis_intensity)
    corrected_intensity = [max(0.0, value - baseline) for value in analysis_intensity]
    maximum_intensity = max(corrected_intensity)
    if maximum_intensity <= 0.0:
        return empty

    # Use a robust estimate of the background spread. A fixed percentage of
    # the main peak would incorrectly discard otherwise valid high-SMSR side
    # modes (for example a side mode 40 dB below the main mode).
    noise_sigma = 1.4826 * median(abs(value - baseline) for value in analysis_intensity)
    numerical_floor = max(math.ulp(maximum_intensity) * 10.0, 1e-12)
    significance_threshold = max(noise_sigma * noise_sigma_multiplier, numerical_floor)

    spacings = [
        right - left
        for left, right in zip(analysis_wavelength, analysis_wavelength[1:])
        if right > left
    ]
    sample_spacing_nm = median(spacings) if spacings else minimum_peak_distance_nm
    minimum_distance_samples = max(
        1,
        round(minimum_peak_distance_nm / sample_spacing_nm) if sample_spacing_nm > 0 else 1,
    )
    peak_indices, _ = find_peaks(
        corrected_intensity,
        distance=minimum_distance_samples,
        height=significance_threshold,
        prominence=significance_threshold,
        width=minimum_peak_width_samples,
    )
    if len(peak_indices) < 2:
        return empty

    ordered_peaks = sorted(peak_indices, key=lambda index: corrected_intensity[index], reverse=True)
    main_index = int(ordered_peaks[0])
    main_fwhm_nm = _calculate_fwhm_values(analysis_wavelength, corrected_intensity)
    main_lobe_exclusion_nm = minimum_peak_distance_nm
    if math.isfinite(main_fwhm_nm):
        main_lobe_exclusion_nm = max(main_lobe_exclusion_nm, main_fwhm_nm)
    side_candidates = [
        int(index)
        for index in ordered_peaks[1:]
        if abs(analysis_wavelength[int(index)] - analysis_wavelength[main_index])
        >= main_lobe_exclusion_nm
    ]
    if not side_candidates:
        return empty

    side_index = side_candidates[0]
    main_intensity = corrected_intensity[main_index]
    side_intensity = corrected_intensity[side_index]
    if main_intensity <= 0.0 or side_intensity <= 0.0:
        return empty
    return SmsrResult(
        smsr_db=10.0 * math.log10(main_intensity / side_intensity),
        main_wavelength_nm=analysis_wavelength[main_index],
        main_intensity=main_intensity,
        side_wavelength_nm=analysis_wavelength[side_index],
        side_intensity=side_intensity,
    )
