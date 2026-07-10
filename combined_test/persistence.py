"""Background and CSV persistence for combined test measurements."""

from __future__ import annotations

import csv
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal

from .core import CSV_HEADER, CombinedMeasurement, record_to_row, spectrum_curve_to_rows
from .excel_export import ExcelTestRecord, save_test_records


class ExcelSaveThread(QThread):
    saved = Signal(float)
    failed = Signal(str)

    def __init__(self, path: Path, records: list[ExcelTestRecord], parent: Any | None = None) -> None:
        super().__init__(parent)
        self.path = Path(path)
        self.records = list(records)

    def run(self) -> None:
        started = time.monotonic()
        try:
            save_test_records(self.path, self.records)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.saved.emit(time.monotonic() - started)


def append_csv_record(path: Path, timestamp: str, measurement: CombinedMeasurement) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        if write_header:
            writer.writerow(CSV_HEADER)
        writer.writerow(record_to_row(timestamp, measurement))


def build_spectrum_csv_path(main_csv_path: Path, timestamp: datetime) -> Path:
    base = main_csv_path.expanduser()
    spectrum_dir = base.with_name(f"{base.stem}_spectra")
    filename = f"spectrum_{timestamp.strftime('%Y%m%d_%H%M%S_%f')}.csv"
    return spectrum_dir / filename


def save_spectrum_curve(path: Path, wavelength: Any, intensity: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerows(spectrum_curve_to_rows(wavelength, intensity))
