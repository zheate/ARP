"""Spectrum statistics and power-in-band calculations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Iterable

import numpy as np
from scipy.signal import find_peaks, medfilt

from .native_realtime import NativeRealtimeUnavailable, get_native_realtime_core


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
class SpectrumModePeak:
    wavelength_nm: float
    intensity: float


@dataclass(frozen=True)
class SmsrResult:
    smsr_db: float
    main_wavelength_nm: float
    main_intensity: float
    side_wavelength_nm: float
    side_intensity: float
    peaks: tuple[SpectrumModePeak, ...] = ()


def _as_float_lists(x: Iterable[float], y: Iterable[float]) -> tuple[list[float], list[float]]:
    try:
        x_values = [float(value) for value in x]
        y_values = [float(value) for value in y]
    except (TypeError, ValueError, OverflowError):
        return [], []
    if not x_values or len(x_values) != len(y_values):
        return [], []
    if not all(
        math.isfinite(x_value) and math.isfinite(y_value)
        for x_value, y_value in zip(x_values, y_values)
    ):
        return [], []
    return x_values, y_values


def calculate_central(x: Iterable[float], y: Iterable[float]) -> float:
    x_values, y_values = _as_float_lists(x, y)
    if len(x_values) < 3:
        return math.nan
    return _calculate_central_values(x_values, y_values)


def _calculate_central_values(x_values: list[float], y_values: list[float]) -> float:
    crossings = _absolute_half_max_crossings(x_values, y_values)
    if crossings is None:
        return math.nan
    left, right = crossings
    central = (left + right) / 2.0
    return central if math.isfinite(central) else math.nan


def calculate_centroid(x: Iterable[float], y: Iterable[float]) -> float:
    x_values, y_values = _as_float_lists(x, y)
    if not x_values:
        return math.nan
    return _calculate_centroid_values(x_values, y_values)


def _calculate_centroid_values(x_values: list[float], y_values: list[float]) -> float:
    if not x_values or len(x_values) != len(y_values):
        return math.nan
    x_array = np.asarray(x_values, dtype=float)
    y_array = np.asarray(y_values, dtype=float)
    peak_index = int(y_array.argmax())
    move_length = 7

    left_index = 0
    right_index = len(y_values) - 1
    if len(y_values) >= move_length:
        window_deviations = np.lib.stride_tricks.sliding_window_view(
            y_array,
            move_length,
        ).std(axis=1)

        left_stop = peak_index - move_length + 1
        if left_stop > 0:
            left_noisy = np.flatnonzero(window_deviations[:left_stop] > 200.0)
            left_index = (
                max(0, int(left_noisy[0]) - 1)
                if left_noisy.size
                else left_stop - 1
            )

        right_start = peak_index + 1
        if right_start < len(window_deviations):
            right_noisy = np.flatnonzero(window_deviations[right_start:] > 200.0)
            # The compiled scripts_runner implementation keeps the final
            # quiet-window sample, one position before the scan cursor.
            right_index = (
                right_start + int(right_noisy[-1]) + move_length
                if right_noisy.size
                else peak_index + move_length
            )

    if not (0 <= left_index <= peak_index <= right_index < len(y_values)):
        return math.nan
    noise = (y_array[left_index] + y_array[right_index]) / 2.0
    centroid_x = x_array[left_index : right_index + 1]
    centroid_y = y_array[left_index : right_index + 1] - noise
    weight_total = float(centroid_y.sum())
    if not math.isfinite(weight_total) or abs(weight_total) < 0.01:
        return math.nan
    centroid = float((centroid_x * centroid_y).sum() / weight_total)
    return centroid if math.isfinite(centroid) else math.nan


def calculate_fwhm(x: Iterable[float], y: Iterable[float]) -> float:
    x_values, y_values = _as_float_lists(x, y)
    if len(x_values) < 3:
        return math.nan
    return _calculate_fwhm_values(x_values, y_values)


def _calculate_fwhm_values(x_values: list[float], y_values: list[float]) -> float:
    if len(x_values) < 3:
        return math.nan
    crossings = _absolute_half_max_crossings(x_values, y_values)
    if crossings is None:
        return math.nan
    left, right = crossings
    fwhm = right - left
    return fwhm if math.isfinite(fwhm) else math.nan


def _absolute_half_max_crossings(
    x_values: list[float],
    y_values: list[float],
) -> tuple[float, float] | None:
    if len(x_values) < 3 or len(x_values) != len(y_values):
        return None
    half_maximum = max(y_values) / 2.0
    crossing_indices = [
        index
        for index in range(len(y_values) - 1)
        if (y_values[index] - half_maximum)
        * (y_values[index + 1] - half_maximum)
        <= 0.0
    ]
    if len(crossing_indices) < 2:
        return None
    left = _legacy_linear_crossing(
        x_values,
        y_values,
        crossing_indices[0],
        half_maximum,
    )
    right = _legacy_linear_crossing(
        x_values,
        y_values,
        crossing_indices[-1],
        half_maximum,
    )
    if left is None or right is None:
        return None
    return left, right


def _legacy_linear_crossing(
    x_values: list[float],
    y_values: list[float],
    left_index: int,
    target: float,
) -> float | None:
    x1 = x_values[left_index]
    x2 = x_values[left_index + 1]
    y1 = y_values[left_index]
    y2 = y_values[left_index + 1]
    if x2 == x1 or y2 == y1:
        return None
    slope = (y2 - y1) / (x2 - x1)
    if not math.isfinite(slope) or slope == 0.0:
        return None
    intercept = y1 - slope * x1
    crossing = (target - intercept) / slope
    return crossing if math.isfinite(crossing) else None


def calculate_stats(wavelength: Iterable[float], intensity: Iterable[float]) -> SpectrumStats:
    wavelength_values, intensity_values = _as_float_lists(wavelength, intensity)
    if not wavelength_values:
        return SpectrumStats(math.nan, math.nan, math.nan, math.nan)

    native_core = get_native_realtime_core()
    if native_core is not None:
        try:
            native = native_core.calculate_spectrum_stats(
                wavelength_values,
                intensity_values,
            )
            return SpectrumStats(
                peak_wavelength_nm=native.peak_wavelength_nm,
                peak_intensity=native.peak_intensity,
                # NumPy uses a pairwise reduction for these sums. Keep the
                # established bitwise result while the native path handles
                # the remaining per-spectrum statistics.
                centroid_nm=_calculate_centroid_values(
                    wavelength_values,
                    intensity_values,
                ),
                fwhm_nm=native.fwhm_nm,
            )
        except (NativeRealtimeUnavailable, OSError, ValueError):
            pass

    peak_index = max(range(len(intensity_values)), key=intensity_values.__getitem__)
    return SpectrumStats(
        peak_wavelength_nm=_calculate_central_values(wavelength_values, intensity_values),
        peak_intensity=intensity_values[peak_index],
        centroid_nm=_calculate_centroid_values(wavelength_values, intensity_values),
        fwhm_nm=_calculate_fwhm_values(wavelength_values, intensity_values),
    )


def calculate_pib(
    wavelength: Iterable[float],
    intensity: Iterable[float],
    center_nm: float = PIB_CENTER_NM,
    half_range_nm: float = PIB_HALF_RANGE_NM,
) -> float:
    """Return the Scripts Runner compatible power-in-band ratio.

    The spectrum is median-filtered, cropped to the signal region detected
    around its strongest peak, and corrected by the mean of the two boundary
    samples. The numerator is the trapezoidal integral inside
    ``center_nm +/- half_range_nm`` and the denominator is the trapezoidal
    integral over that background-corrected signal region.
    """
    wavelength_values, intensity_values = _as_float_lists(wavelength, intensity)
    if not wavelength_values:
        return math.nan
    if not math.isfinite(center_nm) or not math.isfinite(half_range_nm) or half_range_nm < 0:
        raise ValueError("PIB 中心和半范围必须为有效有限数值")

    native_core = get_native_realtime_core()
    if native_core is not None:
        try:
            return native_core.calculate_pib(
                wavelength_values,
                intensity_values,
                center_nm,
                half_range_nm,
            )
        except (NativeRealtimeUnavailable, OSError, ValueError):
            pass

    x_array = np.asarray(wavelength_values, dtype=float)
    y_array = np.asarray(medfilt(np.asarray(intensity_values, dtype=float)), dtype=float)
    peak_index = int(y_array.argmax())
    move_length = 7

    left_index = 0
    for index in range(peak_index - move_length + 1):
        if float(np.std(y_array[index : index + move_length])) > 200.0:
            break
        left_index = index

    right_index = len(y_array) - 1
    for index in range(len(y_array), peak_index + move_length, -1):
        if float(np.std(y_array[index - move_length : index])) > 200.0:
            break
        right_index = index - 1

    signal_wavelength = x_array[left_index : right_index + 1]
    if signal_wavelength.size < 2:
        return 0.0
    noise = (y_array[left_index] + y_array[right_index]) / 2.0
    signal_intensity = y_array[left_index : right_index + 1] - noise
    spacing = signal_wavelength[1:] - signal_wavelength[:-1]

    if center_nm < 1e-6:
        center_nm = _calculate_central_values(
            signal_wavelength.tolist(),
            signal_intensity.tolist(),
        )
        if not math.isfinite(center_nm):
            return math.nan

    lower_index = int(
        np.argmin(np.abs(signal_wavelength - (center_nm - half_range_nm)))
    )
    upper_index = int(
        np.argmin(np.abs(signal_wavelength - (center_nm + half_range_nm)))
    )
    band_start = min(lower_index, upper_index)
    band_stop = max(lower_index, upper_index) + 1
    band_wavelength = signal_wavelength[band_start:band_stop]
    band_intensity_values = signal_intensity[band_start:band_stop]

    total_intensity = float(
        np.sum(spacing * (signal_intensity[:-1] + signal_intensity[1:]) / 2.0)
    )
    if abs(total_intensity) < 1e-6:
        return 0.0

    band_spacing = band_wavelength[1:] - band_wavelength[:-1]
    band_intensity = float(
        np.sum(
            band_spacing
            * (band_intensity_values[:-1] + band_intensity_values[1:])
            / 2.0
        )
    )
    pib = band_intensity / total_intensity
    return 1.0 if pib > 1.0 else pib


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
        (wavelength_nm, value)
        for wavelength_nm, value in zip(wavelength_values, intensity_values)
        if analysis_lower_nm <= wavelength_nm <= analysis_upper_nm
    )
    if len(points) < 3:
        return empty
    analysis_wavelength = [point[0] for point in points]
    analysis_intensity = [point[1] for point in points]
    baseline = median(analysis_intensity)
    corrected_intensity = [max(0.0, value - baseline) for value in analysis_intensity]
    maximum_intensity = max(corrected_intensity)
    if maximum_intensity <= 0.0:
        return empty

    # Use a robust estimate of the background spread. A fixed percentage of
    # the main peak would incorrectly discard otherwise valid high-SMSR side
    # modes (for example a side mode 40 dB below the main mode).
    noise_sigma = 1.4826 * median(
        abs(value - baseline) for value in analysis_intensity
    )
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
    if len(peak_indices) < 1:
        return empty

    ordered_peaks = sorted(peak_indices, key=lambda index: corrected_intensity[index], reverse=True)
    main_index = int(ordered_peaks[0])
    side_candidates = [
        int(index)
        for index in ordered_peaks[1:]
        if abs(analysis_wavelength[int(index)] - analysis_wavelength[main_index])
        >= minimum_peak_distance_nm
    ]
    trusted_indices = [main_index, *side_candidates]
    trusted_peaks = tuple(
        SpectrumModePeak(
            wavelength_nm=analysis_wavelength[index],
            intensity=corrected_intensity[index],
        )
        for index in trusted_indices
    )
    main_intensity = corrected_intensity[main_index]
    if not side_candidates:
        return SmsrResult(
            smsr_db=math.nan,
            main_wavelength_nm=analysis_wavelength[main_index],
            main_intensity=main_intensity,
            side_wavelength_nm=math.nan,
            side_intensity=math.nan,
            peaks=trusted_peaks,
        )

    side_index = side_candidates[0]
    side_intensity = corrected_intensity[side_index]
    if main_intensity <= 0.0 or side_intensity <= 0.0:
        return empty
    return SmsrResult(
        smsr_db=10.0 * math.log10(main_intensity / side_intensity),
        main_wavelength_nm=analysis_wavelength[main_index],
        main_intensity=main_intensity,
        side_wavelength_nm=analysis_wavelength[side_index],
        side_intensity=side_intensity,
        peaks=trusted_peaks,
    )
