"""In-memory test-point buffer used by the Excel export workflow."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from .excel_export import ExcelTestRecord, build_test_workbook_path


APP_VERSION = "1.0.0"
CALCULATION_VERSION = "2026-07"


class SessionStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED_BY_OPERATOR = "stopped_by_operator"
    ABORTED_SAFELY = "aborted_safely"


class AttemptValidity(str, Enum):
    VALID = "valid"
    SATURATED = "saturated"
    WEAK_SIGNAL = "weak_signal"
    MISSING = "missing"
    DEVICE_ERROR = "device_error"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class DeviceSnapshot:
    role: str
    kind: str = ""
    resource: str = ""
    detail: str = ""
    settings: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class TestSession:
    """Session metadata kept only for the lifetime of the running app."""

    session_id: str
    sn: str
    station: str
    product_model: str
    batch: str
    mode: str
    started_at_utc: str
    ended_at_utc: str | None
    status: SessionStatus
    termination_reason: str
    shutdown_confirmed: bool | None
    settings: Mapping[str, Any]
    devices: tuple[Mapping[str, Any], ...]
    software_version: str
    calculation_version: str
    workbook_path: Path


@dataclass(frozen=True)
class MeasurementAttempt:
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
    spectrum_path: str = ""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _started_at_utc(started_at: datetime) -> str:
    local_time = started_at if started_at.tzinfo is not None else started_at.astimezone()
    return local_time.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _device_payload(devices: tuple[DeviceSnapshot, ...]) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        {
            "role": device.role,
            "kind": device.kind,
            "resource": device.resource,
            "detail": device.detail,
            "settings": dict(device.settings or {}),
        }
        for device in devices
    )


@runtime_checkable
class RecordStore(Protocol):
    workbook_path: Path | None
    pending_records: dict[float, ExcelTestRecord]
    recorded_currents: set[float]
    current_session: TestSession | None

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

    def queue(self, record: ExcelTestRecord, **details: Any) -> None: ...

    def discard_pending(self, current_a: float) -> None: ...

    def unsaved_records(self) -> tuple[ExcelTestRecord, ...]: ...

    def snapshot(self) -> tuple[ExcelTestRecord, ...]: ...

    def mark_saved(self, records: tuple[ExcelTestRecord, ...]) -> None: ...


class SessionRecordStore:
    """Keep the active test in memory and persist only through Excel export."""

    def __init__(self) -> None:
        self.workbook_path: Path | None = None
        self.pending_records: dict[float, ExcelTestRecord] = {}
        self.recorded_currents: set[float] = set()
        self.exported_currents: set[float] = set()
        self.current_session: TestSession | None = None
        self.planned_currents: tuple[float, ...] = ()
        self._attempts: list[MeasurementAttempt] = []

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
        workbook_path = build_test_workbook_path(output_dir, sn, started_at, test_station)
        workbook_path.parent.mkdir(parents=True, exist_ok=True)
        self.current_session = TestSession(
            session_id=str(uuid.uuid4()),
            sn=str(sn),
            station=str(test_station),
            product_model=str(product_model),
            batch=str(batch),
            mode=str(mode),
            started_at_utc=_started_at_utc(started_at),
            ended_at_utc=None,
            status=SessionStatus.RUNNING,
            termination_reason="",
            shutdown_confirmed=None,
            settings=dict(settings or {}),
            devices=_device_payload(devices),
            software_version=APP_VERSION,
            calculation_version=CALCULATION_VERSION,
            workbook_path=workbook_path,
        )
        self.workbook_path = workbook_path
        if reset:
            self.pending_records.clear()
            self.recorded_currents.clear()
            self.exported_currents.clear()
            self._attempts.clear()
            self.planned_currents = ()
        return workbook_path

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

    def _next_attempt_number(self, point_id: str) -> int:
        return 1 + sum(attempt.point_id == point_id for attempt in self._attempts)

    def _deselect_point_attempts(self, point_id: str) -> None:
        self._attempts = [
            replace(attempt, selected=False)
            if attempt.point_id == point_id and attempt.selected
            else attempt
            for attempt in self._attempts
        ]

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
        session = self.current_session
        if session is None:
            raise RuntimeError("请先创建测试会话")
        current = float(record.current_a)
        sequence_index = self._sequence_index(current)
        point_id = f"{session.session_id}:{sequence_index}"
        self._deselect_point_attempts(point_id)
        self._attempts.append(
            MeasurementAttempt(
                attempt_id=str(uuid.uuid4()),
                session_id=session.session_id,
                point_id=point_id,
                sequence_index=sequence_index,
                target_current_a=current,
                attempt_no=self._next_attempt_number(point_id),
                created_at_utc=utc_now_iso(),
                validity=AttemptValidity.VALID,
                invalid_reason="",
                selected=True,
                current_a=current,
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
            )
        )
        self.pending_records[current] = record
        self.exported_currents.discard(current)

    def record_invalid_attempt(
        self,
        current_a: float,
        validity: AttemptValidity,
        reason: str,
        **_spectrum: Any,
    ) -> MeasurementAttempt | None:
        session = self.current_session
        if session is None:
            return None
        current = float(current_a)
        sequence_index = self._sequence_index(current)
        point_id = f"{session.session_id}:{sequence_index}"
        attempt = MeasurementAttempt(
            attempt_id=str(uuid.uuid4()),
            session_id=session.session_id,
            point_id=point_id,
            sequence_index=sequence_index,
            target_current_a=current,
            attempt_no=self._next_attempt_number(point_id),
            created_at_utc=utc_now_iso(),
            validity=validity,
            invalid_reason=str(reason),
            selected=False,
            current_a=current,
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
            integration_time_us=_spectrum.get("integration_time_us"),
        )
        self._attempts.append(attempt)
        return attempt

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
                current = float(saved_record.current_a)
                self.exported_currents.add(current)
                self.recorded_currents.add(current)

    def list_attempts(
        self,
        session_id: str,
        *,
        selected_only: bool = False,
    ) -> tuple[MeasurementAttempt, ...]:
        return tuple(
            attempt
            for attempt in self._attempts
            if attempt.session_id == session_id and (not selected_only or attempt.selected)
        )

    def complete_session(
        self,
        status: SessionStatus,
        reason: str,
        *,
        shutdown_confirmed: bool | None,
    ) -> None:
        if self.current_session is None:
            return
        self.current_session = replace(
            self.current_session,
            ended_at_utc=utc_now_iso(),
            status=status,
            termination_reason=str(reason),
            shutdown_confirmed=shutdown_confirmed,
        )
