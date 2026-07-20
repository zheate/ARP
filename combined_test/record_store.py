"""Controller-facing record store backed by the persistent local archive."""

from __future__ import annotations

import math
import csv
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from .excel_export import ExcelTestRecord, build_test_workbook_path
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
    utc_now_iso,
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

    def pending_database_count(self) -> int: ...

    def commit_pending_records(self) -> int: ...


@dataclass(frozen=True)
class _PendingAttempt:
    """A measurement kept in memory until the operator accepts it into SQLite."""

    attempt_id: str
    session_id: str
    point_id: str
    sequence_index: int
    target_current_a: float
    attempt_no: int
    created_at_utc: str
    validity: AttemptValidity
    invalid_reason: str
    selected: bool
    current_a: float
    actual_current_a: float
    voltage_raw_v: float
    voltage_v: float
    power_w: float
    efficiency: float
    peak_wavelength_nm: float
    centroid_nm: float
    fwhm_nm: float
    pib: float
    smsr_db: float
    stable_span_w: float
    stable_window_s: float
    stable_tolerance_w: float
    integration_time_us: int | None
    spectrum_path: str
    wavelength: tuple[float, ...] = ()
    intensity: tuple[float, ...] = ()


class SessionRecordStore:
    def __init__(self) -> None:
        self.workbook_path: Path | None = None
        self.pending_records: dict[float, ExcelTestRecord] = {}
        self.recorded_currents: set[float] = set()
        self.exported_currents: set[float] = set()
        self.database_saved_currents: set[float] = set()
        self._pending_attempts: list[_PendingAttempt] = []
        self.database_commit_allowed = True
        self.excel_export_allowed = True
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
        workbook_path = build_test_workbook_path(
            output_dir,
            sn,
            started_at,
            test_station,
        )
        self.current_session = self.archive.begin_session(
            sn=sn,
            station=test_station,
            mode=mode,
            started_at=started_at,
            product_model=product_model,
            batch=batch,
            settings=settings,
            devices=devices,
            workbook_path=workbook_path,
        )
        self.workbook_path = self.current_session.workbook_path
        self.workbook_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_commit_allowed = mode != "automatic"
        # Excel persistence follows the main workflow: every valid automatic
        # point is exported before the controller advances to the next point.
        self.excel_export_allowed = True
        if reset:
            self.pending_records.clear()
            self.recorded_currents.clear()
            self.exported_currents.clear()
            self.database_saved_currents.clear()
            self._pending_attempts.clear()
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
        current = float(record.current_a)
        point_id = f"{self.current_session.session_id}:{sequence_index}"
        attempt_no = 1 + sum(
            attempt.point_id == point_id for attempt in self._pending_attempts
        )
        attempt = _PendingAttempt(
            attempt_id=f"pending:{uuid.uuid4()}",
            session_id=self.current_session.session_id,
            sequence_index=sequence_index,
            target_current_a=record.current_a,
            point_id=point_id,
            attempt_no=attempt_no,
            created_at_utc=utc_now_iso(),
            validity=AttemptValidity.VALID,
            invalid_reason="",
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
            spectrum_path="",
            wavelength=tuple(float(value) for value in record.wavelength),
            intensity=tuple(float(value) for value in record.intensity),
        )
        if len(attempt.wavelength) != len(attempt.intensity):
            raise ValueError("波长和强度数据长度必须一致")
        self._pending_attempts.append(attempt)
        self.pending_records[current] = record
        self.exported_currents.discard(current)
        self.database_saved_currents.discard(current)

    def record_invalid_attempt(
        self,
        current_a: float,
        validity: AttemptValidity,
        reason: str,
        *,
        wavelength: Any = (),
        intensity: Any = (),
        integration_time_us: int | None = None,
    ) -> _PendingAttempt | None:
        if self.archive is None or self.current_session is None:
            return None
        sequence_index = self._sequence_index(current_a)
        point_id = f"{self.current_session.session_id}:{sequence_index}"
        attempt_no = 1 + sum(
            attempt.point_id == point_id for attempt in self._pending_attempts
        )
        wavelength_values = tuple(float(value) for value in wavelength)
        intensity_values = tuple(float(value) for value in intensity)
        if len(wavelength_values) != len(intensity_values):
            raise ValueError("波长和强度数据长度必须一致")
        attempt = _PendingAttempt(
            attempt_id=f"pending:{uuid.uuid4()}",
            session_id=self.current_session.session_id,
            point_id=point_id,
            sequence_index=sequence_index,
            target_current_a=float(current_a),
            attempt_no=attempt_no,
            created_at_utc=utc_now_iso(),
            validity=validity,
            invalid_reason=str(reason),
            selected=False,
            current_a=float(current_a),
            actual_current_a=math.nan,
            voltage_raw_v=math.nan,
            voltage_v=math.nan,
            power_w=math.nan,
            efficiency=math.nan,
            peak_wavelength_nm=math.nan,
            centroid_nm=math.nan,
            fwhm_nm=math.nan,
            pib=math.nan,
            smsr_db=math.nan,
            stable_span_w=math.nan,
            stable_window_s=math.nan,
            stable_tolerance_w=math.nan,
            integration_time_us=integration_time_us,
            spectrum_path="",
            wavelength=wavelength_values,
            intensity=intensity_values,
        )
        self._pending_attempts.append(attempt)
        return attempt

    def pending_database_count(self) -> int:
        if not self.database_commit_allowed:
            return 0
        return len(self._pending_attempts)

    def commit_pending_records(self) -> int:
        if (
            self.archive is None
            or self.current_session is None
            or not self.database_commit_allowed
        ):
            return 0
        pending = tuple(self._pending_attempts)
        for attempt in pending:
            self.archive.record_attempt(
                self.current_session.session_id,
                sequence_index=attempt.sequence_index,
                target_current_a=attempt.target_current_a,
                validity=attempt.validity,
                invalid_reason=attempt.invalid_reason,
                selected=attempt.selected,
                current_a=attempt.current_a,
                actual_current_a=attempt.actual_current_a,
                voltage_raw_v=attempt.voltage_raw_v,
                voltage_v=attempt.voltage_v,
                power_w=attempt.power_w,
                efficiency=attempt.efficiency,
                peak_wavelength_nm=attempt.peak_wavelength_nm,
                centroid_nm=attempt.centroid_nm,
                fwhm_nm=attempt.fwhm_nm,
                pib=attempt.pib,
                smsr_db=attempt.smsr_db,
                stable_span_w=attempt.stable_span_w,
                stable_window_s=attempt.stable_window_s,
                stable_tolerance_w=attempt.stable_tolerance_w,
                integration_time_us=attempt.integration_time_us,
                wavelength=attempt.wavelength,
                intensity=attempt.intensity,
                allow_closed=True,
            )
        self._pending_attempts.clear()
        self.database_saved_currents.update(self.recorded_currents)
        return sum(attempt.validity is AttemptValidity.VALID for attempt in pending)

    def discard_pending(self, current_a: float) -> None:
        if float(current_a) not in self.recorded_currents:
            self.pending_records.pop(float(current_a), None)

    def unsaved_records(self) -> tuple[ExcelTestRecord, ...]:
        if (
            self.current_session is not None
            and self.current_session.mode == "automatic"
            and not self.excel_export_allowed
        ):
            return ()
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
                current = float(saved_record.current_a)
                self.exported_currents.add(current)
                self.recorded_currents.add(current)
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
        self.database_commit_allowed = (
            self.current_session.mode != "automatic"
            or status is SessionStatus.COMPLETED
        )
        self.excel_export_allowed = (
            self.current_session.mode != "automatic"
            or status is SessionStatus.COMPLETED
        )
        export_state = (
            ExportState.EXPORTED
            if self.pending_records and self.exported_currents.issuperset(self.pending_records)
            else ExportState.PENDING
        )
        self.archive.mark_export_state(self.current_session.session_id, export_state)

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
    ) -> tuple[Any, ...]:
        persisted = () if self.archive is None else self.archive.list_attempts(
            session_id,
            selected_only=selected_only,
        )
        if session_id != (self.current_session.session_id if self.current_session else None):
            return persisted
        pending = tuple(
            attempt
            for attempt in self._pending_attempts
            if not selected_only or attempt.selected
        )
        return persisted + pending

    def resume_session(self, session_id: str) -> TestSession:
        if self.archive is None:
            raise RuntimeError("测试档案尚未打开")
        session = self.archive.resume_session(session_id)
        self.current_session = session
        self.database_commit_allowed = session.mode != "automatic"
        self.excel_export_allowed = session.mode != "automatic"
        self.workbook_path = session.workbook_path
        self.pending_records.clear()
        self.recorded_currents.clear()
        self.exported_currents.clear()
        self.database_saved_currents.clear()
        self._pending_attempts.clear()
        for record in self.records_for_session(session_id):
            self.pending_records[record.current_a] = record
            self.recorded_currents.add(record.current_a)
            self.database_saved_currents.add(record.current_a)
        if session.export_state is ExportState.EXPORTED:
            self.exported_currents.update(self.recorded_currents)
        self.start_power_trace()
        return session

    def records_for_session(self, session_id: str) -> tuple[ExcelTestRecord, ...]:
        if self.archive is None:
            return ()
        session = self.archive.get_session(session_id)
        records: list[ExcelTestRecord] = []
        for attempt in self.list_attempts(session_id, selected_only=True):
            wavelength: list[float] = []
            intensity: list[float] = []
            if getattr(attempt, "wavelength", ()):
                wavelength = list(attempt.wavelength)
                intensity = list(attempt.intensity)
            elif attempt.spectrum_path:
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
