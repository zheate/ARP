"""Durable one-second samples produced by the automatic test workflow."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence, TextIO


AUTOMATIC_SAMPLE_INTERVAL_S = 1.0

CSV_HEADER = (
    "sample_index",
    "timestamp",
    "point_index",
    "point_elapsed_s",
    "target_current_a",
    "output_current_a",
    "output_voltage_raw_v",
    "output_voltage_v",
    "optical_power_w",
    "electrical_power_w",
    "efficiency_percent",
    "peak_wavelength_nm",
    "centroid_nm",
    "fwhm_nm",
    "spectrum_point_count",
    "spectrum_wavelengths_nm",
    "spectrum_intensities",
    "error",
)


def _format_number(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return ""
    return f"{float(value):.12g}"


def _format_spectrum(values: Sequence[float]) -> str:
    return ";".join(_format_number(value) for value in values)


@dataclass(frozen=True)
class AutomaticSecondSample:
    point_index: int
    point_elapsed_s: float
    target_current_a: float
    output_current_a: float | None
    output_voltage_raw_v: float | None
    output_voltage_v: float | None
    optical_power_w: float | None
    peak_wavelength_nm: float | None = None
    centroid_nm: float | None = None
    fwhm_nm: float | None = None
    spectrum_wavelengths_nm: tuple[float, ...] = ()
    spectrum_intensities: tuple[float, ...] = ()
    error: str = ""


def automatic_sample_path(workbook_path: Path) -> Path:
    target = Path(workbook_path)
    return target.with_name(f"{target.stem}_1s_samples.csv")


class AutomaticSampleRecorder:
    """Line-buffered recorder; every row is flushed before returning."""

    def __init__(self, path: Path, file: TextIO | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = file
        self._sample_index = 0
        if self._file is None:
            with self.path.open("w", newline="", encoding="utf-8-sig") as output:
                csv.writer(output).writerow(CSV_HEADER)
        else:
            csv.writer(self._file).writerow(CSV_HEADER)
            self._file.flush()

    def write(self, sample: AutomaticSecondSample, *, timestamp: str | None = None) -> None:
        electrical_power_w = None
        efficiency_percent = None
        if sample.output_current_a is not None and sample.output_voltage_v is not None:
            electrical_power_w = sample.output_current_a * sample.output_voltage_v
            if electrical_power_w > 0.0 and sample.optical_power_w is not None:
                efficiency_percent = sample.optical_power_w / electrical_power_w * 100.0
        row = (
                self._sample_index,
                timestamp or datetime.now().astimezone().isoformat(timespec="milliseconds"),
                sample.point_index,
                f"{sample.point_elapsed_s:.6f}",
                _format_number(sample.target_current_a),
                _format_number(sample.output_current_a),
                _format_number(sample.output_voltage_raw_v),
                _format_number(sample.output_voltage_v),
                _format_number(sample.optical_power_w),
                _format_number(electrical_power_w),
                _format_number(efficiency_percent),
                _format_number(sample.peak_wavelength_nm),
                _format_number(sample.centroid_nm),
                _format_number(sample.fwhm_nm),
                len(sample.spectrum_wavelengths_nm),
                _format_spectrum(sample.spectrum_wavelengths_nm),
                _format_spectrum(sample.spectrum_intensities),
                sample.error,
        )
        if self._file is None:
            with self.path.open("a", newline="", encoding="utf-8") as output:
                csv.writer(output).writerow(row)
                output.flush()
        else:
            csv.writer(self._file).writerow(row)
            self._file.flush()
        self._sample_index += 1

    def close(self) -> None:
        if self._file is not None:
            self._file.flush()
