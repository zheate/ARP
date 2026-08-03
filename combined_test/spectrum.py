"""Realtime spectrum validation and peak annotation analysis."""

from __future__ import annotations

import math
from collections import deque
from typing import Any, Iterable

from .models import SpectrumPeakAnnotation, SpectrumSaturationResult
from .spectrum_math import SmsrResult, SpectrumStats, calculate_smsr, calculate_stats


SPECTRUM_CENTER_LOCK_REQUIRED_SAMPLES = 5
SPECTRUM_CENTER_LOCK_TOLERANCE_NM = 1.0
SPECTRUM_CENTER_LOCK_HALF_RANGE_NM = 20.0
SPECTRUM_PEAK_ORDINAL_LABELS = ("P1", "P2", "P3")
SPECTRUM_DEFAULT_FULL_SCALE_COUNTS = 64_000.0
SPECTRUM_SATURATION_FULL_SCALE_FRACTION = 0.995
SPECTRUM_SATURATION_MIN_CONSECUTIVE_PIXELS = 3
SPECTRUM_Y_AXIS_MINIMUM_UPPER_COUNTS = 1_000.0
SPECTRUM_Y_AXIS_HEADROOM_FRACTION = 0.1
SPECTRUM_Y_AXIS_SHRINK_TRIGGER_FRACTION = 0.55
SPECTRUM_Y_AXIS_SHRINK_REQUIRED_FRAMES = 6


def _spectrum_floats(values: Iterable[Any]) -> list[float]:
    """Convert samples without removing gaps between physical pixels."""
    result: list[float] = []
    for raw_value in values:
        value = float(raw_value)
        result.append(value)
    return result


def normalize_spectrum_full_scale(full_scale_counts: Any) -> float:
    try:
        value = float(full_scale_counts)
    except (TypeError, ValueError):
        return SPECTRUM_DEFAULT_FULL_SCALE_COUNTS
    if not math.isfinite(value) or value <= 0.0:
        return SPECTRUM_DEFAULT_FULL_SCALE_COUNTS
    return value


def detect_spectrum_saturation(
    intensity: Iterable[Any],
    full_scale_counts: Any = SPECTRUM_DEFAULT_FULL_SCALE_COUNTS,
) -> SpectrumSaturationResult:
    values = _spectrum_floats(intensity)
    finite_values = [value for value in values if math.isfinite(value)]
    if not finite_values:
        return SpectrumSaturationResult(False, math.nan, 0)
    peak_intensity = max(finite_values)
    saturation_floor = (
        normalize_spectrum_full_scale(full_scale_counts)
        * SPECTRUM_SATURATION_FULL_SCALE_FRACTION
    )
    if peak_intensity < saturation_floor:
        return SpectrumSaturationResult(False, peak_intensity, 0)

    longest_run = 0
    current_run = 0
    for value in values:
        if math.isfinite(value) and value >= saturation_floor:
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 0
    return SpectrumSaturationResult(
        saturated=longest_run >= SPECTRUM_SATURATION_MIN_CONSECUTIVE_PIXELS,
        peak_intensity=peak_intensity,
        consecutive_pixels=longest_run,
    )


def _nice_axis_step(span: float, target_tick_count: int = 5) -> float:
    if not math.isfinite(span) or span <= 0.0:
        return 1.0
    raw_step = span / max(1, target_tick_count)
    magnitude = 10.0 ** math.floor(math.log10(raw_step))
    normalized = raw_step / magnitude
    if normalized <= 1.0:
        multiplier = 1.0
    elif normalized <= 2.0:
        multiplier = 2.0
    elif normalized <= 5.0:
        multiplier = 5.0
    else:
        multiplier = 10.0
    return multiplier * magnitude


def spectrum_y_axis_upper_limit(peak_intensity: Any) -> float:
    try:
        peak = max(0.0, float(peak_intensity))
    except (TypeError, ValueError):
        peak = 0.0
    if not math.isfinite(peak):
        peak = 0.0
    target = max(
        SPECTRUM_Y_AXIS_MINIMUM_UPPER_COUNTS,
        peak * (1.0 + SPECTRUM_Y_AXIS_HEADROOM_FRACTION),
    )
    step = _nice_axis_step(target)
    return max(
        SPECTRUM_Y_AXIS_MINIMUM_UPPER_COUNTS,
        math.ceil(target / step) * step,
    )


def stabilize_spectrum_y_axis_upper_limit(
    peak_intensity: Any,
    current_upper_limit: Any,
    low_frame_count: int,
) -> tuple[float, int]:
    """Expand immediately, but shrink only after a sustained large drop."""
    desired_upper_limit = spectrum_y_axis_upper_limit(peak_intensity)
    try:
        current = float(current_upper_limit)
    except (TypeError, ValueError):
        current = math.nan
    if not math.isfinite(current) or current <= 0.0:
        return desired_upper_limit, 0
    if desired_upper_limit > current:
        return desired_upper_limit, 0

    try:
        peak = max(0.0, float(peak_intensity))
    except (TypeError, ValueError):
        peak = 0.0
    if not math.isfinite(peak):
        peak = 0.0
    if (
        desired_upper_limit < current
        and peak <= current * SPECTRUM_Y_AXIS_SHRINK_TRIGGER_FRACTION
    ):
        next_low_frame_count = max(0, int(low_frame_count)) + 1
        if next_low_frame_count >= SPECTRUM_Y_AXIS_SHRINK_REQUIRED_FRAMES:
            return desired_upper_limit, 0
        return current, next_low_frame_count
    return current, 0


def _centered_window_minimums(values: list[float], radius: int) -> list[float]:
    """Return each centered-window minimum in O(n) time."""
    if not values:
        return []

    candidates: deque[int] = deque()
    minimums: list[float] = []
    right = -1
    last_index = len(values) - 1
    for center in range(len(values)):
        target_right = min(last_index, center + radius)
        while right < target_right:
            right += 1
            while candidates and values[candidates[-1]] >= values[right]:
                candidates.pop()
            candidates.append(right)

        left = max(0, center - radius)
        while candidates and candidates[0] < left:
            candidates.popleft()
        minimums.append(values[candidates[0]])
    return minimums


def find_spectrum_peak_annotations(
    points: Iterable[tuple[Any, Any]],
    limit: int = 3,
    *,
    stats: SpectrumStats | None = None,
    smsr_result: SmsrResult | None = None,
) -> list[SpectrumPeakAnnotation]:
    clean_points: list[tuple[float, float]] = []
    for x_raw, y_raw in points:
        x = float(x_raw)
        y = float(y_raw)
        if math.isfinite(x) and math.isfinite(y):
            clean_points.append((x, y))
    clean_points.sort(key=lambda item: item[0])
    if len(clean_points) < 3 or limit <= 0:
        return []
    wavelength = [item[0] for item in clean_points]
    intensity = [item[1] for item in clean_points]
    frame_stats = stats or calculate_stats(wavelength, intensity)
    mode_result = smsr_result or calculate_smsr(wavelength, intensity)
    annotation_limit = min(
        limit,
        len(SPECTRUM_PEAK_ORDINAL_LABELS),
        len(mode_result.peaks),
    )
    annotations: list[SpectrumPeakAnnotation] = []
    for rank, peak in enumerate(mode_result.peaks[:annotation_limit]):
        wavelength_nm = peak.wavelength_nm
        if rank == 0 and math.isfinite(frame_stats.peak_wavelength_nm):
            wavelength_nm = frame_stats.peak_wavelength_nm
        annotations.append(
            SpectrumPeakAnnotation(
                label=SPECTRUM_PEAK_ORDINAL_LABELS[rank],
                centroid_nm=wavelength_nm,
                peak_wavelength_nm=wavelength_nm,
                peak_intensity=peak.intensity,
            )
        )
    return annotations
