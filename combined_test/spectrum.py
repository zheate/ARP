"""Realtime spectrum validation and peak annotation analysis."""

from __future__ import annotations

import math
from collections import deque
from typing import Any, Iterable

from .models import SpectrumPeakAnnotation, SpectrumSaturationResult


SPECTRUM_CENTER_LOCK_REQUIRED_SAMPLES = 5
SPECTRUM_CENTER_LOCK_TOLERANCE_NM = 1.0
SPECTRUM_CENTER_LOCK_HALF_RANGE_NM = 20.0
SPECTRUM_PEAK_ORDINAL_LABELS = ("P1", "P2", "P3")
SPECTRUM_PEAK_MIN_SEPARATION_NM = 0.3
SPECTRUM_PEAK_MIN_PROMINENCE_FRACTION = 0.01
SPECTRUM_SATURATION_MIN_INTENSITY = 16000.0
SPECTRUM_SATURATION_PLATEAU_FRACTION = 0.995
SPECTRUM_SATURATION_MIN_CONSECUTIVE_PIXELS = 3


def _spectrum_floats(values: Iterable[Any]) -> list[float]:
    """Convert samples without removing gaps between physical pixels."""
    result: list[float] = []
    for raw_value in values:
        value = float(raw_value)
        result.append(value)
    return result


def detect_spectrum_saturation(intensity: Iterable[Any]) -> SpectrumSaturationResult:
    values = _spectrum_floats(intensity)
    finite_values = [value for value in values if math.isfinite(value)]
    if not finite_values:
        return SpectrumSaturationResult(False, math.nan, 0)
    peak_intensity = max(finite_values)
    if peak_intensity < SPECTRUM_SATURATION_MIN_INTENSITY:
        return SpectrumSaturationResult(False, peak_intensity, 0)

    plateau_floor = max(
        SPECTRUM_SATURATION_MIN_INTENSITY,
        peak_intensity * SPECTRUM_SATURATION_PLATEAU_FRACTION,
    )
    longest_run = 0
    current_run = 0
    for value in values:
        if math.isfinite(value) and value >= plateau_floor:
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 0
    return SpectrumSaturationResult(
        saturated=longest_run >= SPECTRUM_SATURATION_MIN_CONSECUTIVE_PIXELS,
        peak_intensity=peak_intensity,
        consecutive_pixels=longest_run,
    )


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

    y_values = [item[1] for item in clean_points]
    baseline = min(y_values)
    y_range = max(y_values) - baseline
    if y_range <= 0:
        return []

    neighborhood = max(2, len(clean_points) // 200)
    local_floors = _centered_window_minimums(y_values, neighborhood)
    min_prominence = y_range * SPECTRUM_PEAK_MIN_PROMINENCE_FRACTION
    candidates: list[tuple[int, float, float]] = []
    for index in range(1, len(clean_points) - 1):
        y = y_values[index]
        if y <= y_values[index - 1] or y < y_values[index + 1]:
            continue
        if y - local_floors[index] >= min_prominence:
            candidates.append((index, clean_points[index][0], y))

    selected: list[tuple[int, float, float]] = []
    annotation_limit = min(limit, len(SPECTRUM_PEAK_ORDINAL_LABELS))
    for candidate in sorted(candidates, key=lambda item: item[2], reverse=True):
        if all(abs(candidate[1] - item[1]) >= SPECTRUM_PEAK_MIN_SEPARATION_NM for item in selected):
            selected.append(candidate)
        if len(selected) >= annotation_limit:
            break

    return [
        SpectrumPeakAnnotation(
            label=SPECTRUM_PEAK_ORDINAL_LABELS[rank],
            centroid_nm=_calculate_local_peak_centroid(clean_points, index, baseline),
            peak_wavelength_nm=peak_wavelength_nm,
            peak_intensity=peak_intensity,
        )
        for rank, (index, peak_wavelength_nm, peak_intensity) in enumerate(selected)
    ]


def _calculate_local_peak_centroid(
    points: list[tuple[float, float]],
    peak_index: int,
    baseline: float,
) -> float:
    peak_intensity = points[peak_index][1]
    threshold = baseline + (peak_intensity - baseline) * 0.5

    left = peak_index
    while left > 0 and points[left - 1][1] >= threshold:
        left -= 1
    right = peak_index
    while right < len(points) - 1 and points[right + 1][1] >= threshold:
        right += 1

    peak_points = points[left : right + 1]
    local_baseline = min(item[1] for item in peak_points)
    weighted_sum = 0.0
    weight_total = 0.0
    for wavelength_nm, intensity in peak_points:
        weight = max(0.0, intensity - local_baseline)
        weighted_sum += wavelength_nm * weight
        weight_total += weight
    if weight_total <= 0:
        return points[peak_index][0]
    return weighted_sum / weight_total
