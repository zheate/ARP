"""Controller-facing record store backed by the persistent local archive."""

from __future__ import annotations

import math
import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from .excel_export import ExcelTestRecord
from .test_archive import (
    AttemptValidity,
    DeviceSnapshot,
    EventSeverity,
    ExportState,
    MeasurementAttempt,
    PowerTraceWriter,
    SessionFilters,
    SessionStatus,
    TestArchive,
    TestSession,
)


@runtime_checkable
class RecordStore(Protocol):
    workbook_path: Path | None
    pending_records: dict[float, ExcelTestRecord]
    recorded_currents: set[float]
    current_session: TestSession | None
    archive: TestArchive | None

    def begin_session(
        self,
        output_dir: Path,
        sn: str,
        started_at: datetime,
        *,
        test_station: str = "",
        reset: bool = True,
        mode: str = "manual",
        product_model: str = "",
        batch: str = "",
        settings: Mapping[str, Any] | None = None,
        devices: tuple[DeviceSnapshot, ...] = (),
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
        self.exported_currents: set[float] = set()
        self.archive: TestArchive | None = None
        self.current_session: TestSession | None = None
        self.planned_currents: tuple[float, ...] = ()
        self.power_trace_writer: PowerTraceWriter | None = None

    def open_archive(self, output_dir: Path) -> TestArchive:
        archive = TestArchive(Path(output_dir))
        self.archive = archive
        return archive

    def begin_session(
        self,
        output_dir: Path,
        sn: str,
        started_at: datetime,
        *,
        test_station: str = "",
        reset: bool = True,
        mode: str = "manual",
        product_model: str = "",
        batch: str = "",
        settings: Mapping[str, Any] | None = None,
        devices: tuple[DeviceSnapshot, ...] = (),
    ) -> Path:
        if not reset and self.current_session is not None:
            return self.current_session.workbook_path
        self.archive = TestArchive(Path(output_dir))
        self.current_session = self.archive.begin_session(
            sn=sn,
            station=test_station,
            mode=mode,
            started_at=started_at,
            product_model=product_model,
            batch=batch,
            settings=settings,
            devices=devices,
        )
        self.workbook_path = self.current_session.workbook_path
        self.workbook_path.parent.mkdir(parents=True, exist_ok=True)
        if reset:
            self.pending_records.clear()
            self.recorded_currents.clear()
            self.exported_currents.clear()
            self.planned_currents = ()
        return self.workbook_path

    def configure_sequence(self, currents: tuple[float, ...]) -> None:
        self.planned_currents = tuple(float(current) for current in currents)

    def _sequence_index(self, current_a: float) -> int:
        current = float(current_a)
        for index, planned in enumerate(self.planned_currents):
            if math.isclose(current, planned, rel_tol=0.0, abs_tol=1e-9):
                return index
        existing = sorted(self.pending_records)
        if current in existing:
            return existing.index(current)
        return len(existing)

    def queue(
        self,
        record: ExcelTestRecord,
        *,
        actual_current_a: float = math.nan,
        voltage_raw_v: float = math.nan,
        stable_span_w: float = math.nan,
        stable_window_s: float = math.nan,
        stable_tolerance_w: float = math.nan,
        integration_time_us: int | None = None,
    ) -> None:
        if self.archive is None or self.current_session is None:
            raise RuntimeError("请先创建测试会话")
        sequence_index = self._sequence_index(record.current_a)
        self.archive.record_attempt(
            self.current_session.session_id,
            sequence_index=sequence_index,
            target_current_a=record.current_a,
            validity=AttemptValidity.VALID,
            selected=True,
            current_a=record.current_a,
            actual_current_a=actual_current_a,
            voltage_raw_v=voltage_raw_v,
            voltage_v=record.voltage_v,
            power_w=record.power_w,
            efficiency=record.efficiency,
            peak_wavelength_nm=record.peak_wavelength_nm,
            centroid_nm=record.centroid_nm,
            fwhm_nm=record.fwhm_nm,
            pib=record.pib,
            smsr_db=record.smsr_db,
            stable_span_w=stable_span_w,
            stable_window_s=stable_window_s,
            stable_tolerance_w=stable_tolerance_w,
            integration_time_us=integration_time_us,
            wavelength=record.wavelength,
            intensity=record.intensity,
        )
        self.pending_records[float(record.current_a)] = record
        self.recorded_currents.add(float(record.current_a))
        self.exported_currents.discard(float(record.current_a))

    def record_invalid_attempt(
        self,
        current_a: float,
        validity: AttemptValidity,
        reason: str,
        *,
        wavelength: Any = (),
        intensity: Any = (),
        integration_time_us: int | None = None,
    ) -> MeasurementAttempt | None:
        if self.archive is None or self.current_session is None:
            return None
        return self.archive.record_attempt(
            self.current_session.session_id,
            sequence_index=self._sequence_index(current_a),
            target_current_a=current_a,
            validity=validity,
            invalid_reason=reason,
            selected=False,
            current_a=current_a,
            integration_time_us=integration_time_us,
            wavelength=wavelength,
            intensity=intensity,
        )

    def discard_pending(self, current_a: float) -> None:
        if float(current_a) not in self.recorded_currents:
            self.pending_records.pop(float(current_a), None)

    def unsaved_records(self) -> tuple[ExcelTestRecord, ...]:
        return tuple(
            sorted(
                (
                    record
                    for current, record in self.pending_records.items()
                    if current not in self.exported_currents
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
                self.exported_currents.add(float(saved_record.current_a))
        if self.archive is not None and self.current_session is not None:
            self.archive.mark_export_state(self.current_session.session_id, ExportState.EXPORTED)

    def mark_export_failed(self, message: str) -> None:
        if self.archive is not None and self.current_session is not None:
            self.archive.mark_export_state(
                self.current_session.session_id,
                ExportState.FAILED,
                message,
            )

    def append_event(
        self,
        code: str,
        severity: EventSeverity,
        message: str,
        *,
        current_a: float | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        if self.archive is None or self.current_session is None:
            return
        self.archive.append_event(
            self.current_session.session_id,
            code,
            severity,
            message,
            current_a=current_a,
            details=details,
        )

    def complete_session(
        self,
        status: SessionStatus,
        reason: str,
        *,
        shutdown_confirmed: bool | None,
    ) -> None:
        if self.archive is None or self.current_session is None:
            return
        self.stop_power_trace()
        self.archive.complete_session(
            self.current_session.session_id,
            status,
            reason,
            shutdown_confirmed=shutdown_confirmed,
        )
        self.current_session = self.archive.get_session(self.current_session.session_id)
        self.exported_currents.clear()
        self.archive.mark_export_state(self.current_session.session_id, ExportState.PENDING)

    def start_power_trace(self) -> Path | None:
        if self.current_session is None:
            return None
        if self.power_trace_writer is not None:
            return self.power_trace_writer.path
        path = self.current_session.session_dir / "power_trace.csv"
        self.power_trace_writer = PowerTraceWriter(path)
        self.power_trace_writer.start()
        return path

    def append_power_trace(self, **sample: Any) -> None:
        if self.power_trace_writer is None:
            return
        self.power_trace_writer.append(**sample)

    def stop_power_trace(self) -> None:
        writer = self.power_trace_writer
        self.power_trace_writer = None
        if writer is not None:
            writer.stop()

    def list_sessions(self, filters: SessionFilters | None = None) -> tuple[TestSession, ...]:
        return () if self.archive is None else self.archive.list_sessions(filters)

    def list_attempts(
        self,
        session_id: str,
        *,
        selected_only: bool = False,
    ) -> tuple[MeasurementAttempt, ...]:
        return () if self.archive is None else self.archive.list_attempts(
            session_id,
            selected_only=selected_only,
        )

    def resume_session(self, session_id: str) -> TestSession:
        if self.archive is None:
            raise RuntimeError("测试档案尚未打开")
        session = self.archive.resume_session(session_id)
        self.current_session = session
        self.workbook_path = session.workbook_path
        self.pending_records.clear()
        self.recorded_currents.clear()
        self.exported_currents.clear()
        for record in self.records_for_session(session_id):
            self.pending_records[record.current_a] = record
            self.recorded_currents.add(record.current_a)
        if session.export_state is ExportState.EXPORTED:
            self.exported_currents.update(self.recorded_currents)
        self.start_power_trace()
        return session

    def records_for_session(self, session_id: str) -> tuple[ExcelTestRecord, ...]:
        if self.archive is None:
            return ()
        session = self.archive.get_session(session_id)
        records: list[ExcelTestRecord] = []
        for attempt in self.archive.list_attempts(session_id, selected_only=True):
            wavelength: list[float] = []
            intensity: list[float] = []
            if attempt.spectrum_path:
                spectrum_path = session.session_dir / attempt.spectrum_path
                if spectrum_path.is_file():
                    with spectrum_path.open(newline="", encoding="utf-8") as file:
                        reader = csv.DictReader(file)
                        for row in reader:
                            wavelength.append(float(row["wavelength_nm"]))
                            intensity.append(float(row["intensity"]))
            records.append(ExcelTestRecord(
                current_a=attempt.target_current_a,
                voltage_v=attempt.voltage_v,
                power_w=attempt.power_w,
                efficiency=attempt.efficiency,
                peak_wavelength_nm=attempt.peak_wavelength_nm,
                centroid_nm=attempt.centroid_nm,
                fwhm_nm=attempt.fwhm_nm,
                pib=attempt.pib,
                wavelength=wavelength,
                intensity=intensity,
                smsr_db=attempt.smsr_db,
                test_station=session.station,
            ))
        return tuple(sorted(records, key=lambda record: record.current_a))
