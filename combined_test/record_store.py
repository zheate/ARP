"""Session record storage independent from the Qt window and save worker."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from .excel_export import ExcelTestRecord, build_test_workbook_path


@runtime_checkable
class RecordStore(Protocol):
    workbook_path: Path | None
    pending_records: dict[float, ExcelTestRecord]
    recorded_currents: set[float]

    def begin_session(
        self,
        output_dir: Path,
        sn: str,
        started_at: datetime,
        *,
        reset: bool = True,
    ) -> Path: ...

    def queue(self, record: ExcelTestRecord) -> None: ...

    def discard_pending(self, current_a: float) -> None: ...

    def unsaved_records(self) -> tuple[ExcelTestRecord, ...]: ...

    def snapshot(self) -> tuple[ExcelTestRecord, ...]: ...

    def mark_saved(self, records: tuple[ExcelTestRecord, ...]) -> None: ...


class SessionRecordStore:
    def __init__(self) -> None:
        self.workbook_path: Path | None = None
        self.pending_records: dict[float, ExcelTestRecord] = {}
        self.recorded_currents: set[float] = set()

    def begin_session(
        self,
        output_dir: Path,
        sn: str,
        started_at: datetime,
        *,
        reset: bool = True,
    ) -> Path:
        self.workbook_path = build_test_workbook_path(output_dir, sn, started_at)
        if reset:
            self.pending_records.clear()
            self.recorded_currents.clear()
        return self.workbook_path

    def queue(self, record: ExcelTestRecord) -> None:
        self.pending_records[float(record.current_a)] = record

    def discard_pending(self, current_a: float) -> None:
        if float(current_a) not in self.recorded_currents:
            self.pending_records.pop(float(current_a), None)

    def unsaved_records(self) -> tuple[ExcelTestRecord, ...]:
        return tuple(
            sorted(
                (
                    record
                    for current, record in self.pending_records.items()
                    if current not in self.recorded_currents
                ),
                key=lambda record: record.current_a,
            )
        )

    def snapshot(self) -> tuple[ExcelTestRecord, ...]:
        return tuple(sorted(self.pending_records.values(), key=lambda record: record.current_a))

    def mark_saved(self, records: tuple[ExcelTestRecord, ...]) -> None:
        for saved_record in records:
            current_record = self.pending_records.get(float(saved_record.current_a))
            if current_record == saved_record:
                self.recorded_currents.add(float(saved_record.current_a))
