"""Persistent local test archive for sessions, attempts, events, and artifacts."""

from __future__ import annotations

import csv
import json
import math
import os
import queue
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping


ARCHIVE_SCHEMA_VERSION = 1
APP_VERSION = "1.0.0"
CALCULATION_VERSION = "2026-07"
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class SessionStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED_BY_OPERATOR = "stopped_by_operator"
    ABORTED_SAFELY = "aborted_safely"
    INCOMPLETE = "incomplete"


class AttemptValidity(str, Enum):
    VALID = "valid"
    SATURATED = "saturated"
    WEAK_SIGNAL = "weak_signal"
    MISSING = "missing"
    DEVICE_ERROR = "device_error"
    TIMEOUT = "timeout"


class EventSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"
    SAFETY = "safety"


class ExportState(str, Enum):
    PENDING = "pending"
    EXPORTED = "exported"
    FAILED = "failed"


@dataclass(frozen=True)
class DeviceSnapshot:
    role: str
    kind: str = ""
    resource: str = ""
    detail: str = ""
    settings: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class TestSession:
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
    session_dir: Path
    workbook_path: Path
    export_state: ExportState
    export_error: str


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
    spectrum_path: str


@dataclass(frozen=True)
class SessionFilters:
    sn: str = ""
    product_model: str = ""
    batch: str = ""
    station: str = ""
    mode: str = ""
    status: str = ""
    date_from: str = ""
    date_to: str = ""
    limit: int = 500


@dataclass(frozen=True)
class TestEvent:
    event_id: int
    session_id: str
    created_at_utc: str
    code: str
    severity: EventSeverity
    message: str
    current_a: float | None
    details: Mapping[str, Any]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def safe_path_part(value: str, fallback: str) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub("_", str(value).strip()).rstrip(". ")
    return cleaned or fallback


class TestArchive:
    """SQLite-backed single-station archive rooted beside result artifacts."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database_path = self.root / "index.sqlite3"
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS archive_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    sn TEXT NOT NULL,
                    station TEXT NOT NULL,
                    product_model TEXT NOT NULL DEFAULT '',
                    batch TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL,
                    started_at_utc TEXT NOT NULL,
                    ended_at_utc TEXT,
                    status TEXT NOT NULL,
                    termination_reason TEXT NOT NULL DEFAULT '',
                    shutdown_confirmed INTEGER,
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    devices_json TEXT NOT NULL DEFAULT '[]',
                    software_version TEXT NOT NULL,
                    calculation_version TEXT NOT NULL,
                    session_dir TEXT NOT NULL,
                    workbook_path TEXT NOT NULL,
                    export_state TEXT NOT NULL,
                    export_error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS test_points (
                    point_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    sequence_index INTEGER NOT NULL,
                    target_current_a REAL NOT NULL,
                    UNIQUE(session_id, sequence_index)
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id TEXT PRIMARY KEY,
                    point_id TEXT NOT NULL REFERENCES test_points(point_id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    attempt_no INTEGER NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    validity TEXT NOT NULL,
                    invalid_reason TEXT NOT NULL DEFAULT '',
                    selected INTEGER NOT NULL DEFAULT 0,
                    current_a REAL,
                    actual_current_a REAL,
                    voltage_raw_v REAL,
                    voltage_v REAL,
                    power_w REAL,
                    efficiency REAL,
                    peak_wavelength_nm REAL,
                    centroid_nm REAL,
                    fwhm_nm REAL,
                    pib REAL,
                    smsr_db REAL,
                    stable_span_w REAL,
                    stable_window_s REAL,
                    stable_tolerance_w REAL,
                    integration_time_us INTEGER,
                    spectrum_path TEXT NOT NULL DEFAULT '',
                    UNIQUE(point_id, attempt_no)
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    created_at_utc TEXT NOT NULL,
                    code TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    current_a REAL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    state TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    updated_at_utc TEXT NOT NULL,
                    UNIQUE(session_id, kind, relative_path)
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_sessions_sn ON sessions(sn);
                CREATE INDEX IF NOT EXISTS idx_sessions_batch ON sessions(batch);
                CREATE INDEX IF NOT EXISTS idx_attempts_session ON attempts(session_id);
                CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, event_id);
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO archive_meta(key, value) VALUES('schema_version', ?)",
                (str(ARCHIVE_SCHEMA_VERSION),),
            )

    def begin_session(
        self,
        *,
        sn: str,
        station: str,
        mode: str,
        started_at: datetime | None = None,
        product_model: str = "",
        batch: str = "",
        settings: Mapping[str, Any] | None = None,
        devices: Iterable[DeviceSnapshot] = (),
    ) -> TestSession:
        local_started = started_at or datetime.now()
        session_id = str(uuid.uuid4())
        short_id = session_id.split("-", 1)[0]
        session_dir = (
            self.root
            / safe_path_part(sn, "unknown-sn")
            / safe_path_part(station, "unknown-station")
            / local_started.strftime("%Y-%m-%d")
            / f"{local_started.strftime('%H%M%S')}_{short_id}"
        )
        session_dir.mkdir(parents=True, exist_ok=False)
        workbook_path = session_dir / "result.xlsx"
        started_at_utc = (
            local_started.astimezone(timezone.utc).isoformat(timespec="milliseconds")
            if local_started.tzinfo is not None
            else local_started.astimezone().astimezone(timezone.utc).isoformat(timespec="milliseconds")
        )
        device_payload = [asdict(device) for device in devices]
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions(
                    session_id, sn, station, product_model, batch, mode,
                    started_at_utc, status, settings_json, devices_json,
                    software_version, calculation_version, session_dir,
                    workbook_path, export_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    str(sn),
                    str(station),
                    str(product_model),
                    str(batch),
                    str(mode),
                    started_at_utc,
                    SessionStatus.RUNNING.value,
                    json.dumps(settings or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(device_payload, ensure_ascii=False, sort_keys=True),
                    APP_VERSION,
                    CALCULATION_VERSION,
                    str(session_dir),
                    str(workbook_path),
                    ExportState.PENDING.value,
                ),
            )
        self.append_event(session_id, "session.started", EventSeverity.INFO, "测试会话已创建")
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> TestSession:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"未找到测试会话：{session_id}")
        return self._session_from_row(row)

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> TestSession:
        shutdown_value = row["shutdown_confirmed"]
        return TestSession(
            session_id=row["session_id"],
            sn=row["sn"],
            station=row["station"],
            product_model=row["product_model"],
            batch=row["batch"],
            mode=row["mode"],
            started_at_utc=row["started_at_utc"],
            ended_at_utc=row["ended_at_utc"],
            status=SessionStatus(row["status"]),
            termination_reason=row["termination_reason"],
            shutdown_confirmed=None if shutdown_value is None else bool(shutdown_value),
            settings=json.loads(row["settings_json"] or "{}"),
            devices=tuple(json.loads(row["devices_json"] or "[]")),
            software_version=row["software_version"],
            calculation_version=row["calculation_version"],
            session_dir=Path(row["session_dir"]),
            workbook_path=Path(row["workbook_path"]),
            export_state=ExportState(row["export_state"]),
            export_error=row["export_error"],
        )

    def record_attempt(
        self,
        session_id: str,
        *,
        sequence_index: int,
        target_current_a: float,
        validity: AttemptValidity,
        invalid_reason: str = "",
        selected: bool = False,
        current_a: float = math.nan,
        actual_current_a: float = math.nan,
        voltage_raw_v: float = math.nan,
        voltage_v: float = math.nan,
        power_w: float = math.nan,
        efficiency: float = math.nan,
        peak_wavelength_nm: float = math.nan,
        centroid_nm: float = math.nan,
        fwhm_nm: float = math.nan,
        pib: float = math.nan,
        smsr_db: float = math.nan,
        stable_span_w: float = math.nan,
        stable_window_s: float = math.nan,
        stable_tolerance_w: float = math.nan,
        integration_time_us: int | None = None,
        wavelength: Iterable[float] = (),
        intensity: Iterable[float] = (),
        allow_closed: bool = False,
    ) -> MeasurementAttempt:
        session = self.get_session(session_id)
        if not allow_closed and session.status not in (SessionStatus.RUNNING, SessionStatus.INCOMPLETE):
            raise ValueError("只能向运行中或待恢复的会话写入测试点")
        point_id = f"{session_id}:{int(sequence_index)}"
        spectrum_path = ""
        wavelength_values = [float(value) for value in wavelength]
        intensity_values = [float(value) for value in intensity]
        if len(wavelength_values) != len(intensity_values):
            raise ValueError("波长和强度数据长度必须一致")

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO test_points(point_id, session_id, sequence_index, target_current_a)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id, sequence_index)
                DO UPDATE SET target_current_a = excluded.target_current_a
                """,
                (point_id, session_id, int(sequence_index), float(target_current_a)),
            )
            row = connection.execute(
                "SELECT COALESCE(MAX(attempt_no), 0) + 1 AS attempt_no FROM attempts WHERE point_id = ?",
                (point_id,),
            ).fetchone()
            attempt_no = int(row["attempt_no"])
            attempt_id = str(uuid.uuid4())
            if wavelength_values:
                spectra_dir = session.session_dir / "spectra"
                spectra_dir.mkdir(parents=True, exist_ok=True)
                spectrum_file = spectra_dir / (
                    f"point_{int(sequence_index) + 1:04d}_attempt_{attempt_no:02d}.csv"
                )
                temporary = spectrum_file.with_suffix(".tmp")
                try:
                    with temporary.open("w", newline="", encoding="utf-8") as file:
                        writer = csv.writer(file)
                        writer.writerow(("wavelength_nm", "intensity"))
                        writer.writerows(zip(wavelength_values, intensity_values))
                        file.flush()
                        os.fsync(file.fileno())
                    os.replace(temporary, spectrum_file)
                finally:
                    if temporary.exists():
                        temporary.unlink()
                spectrum_path = str(spectrum_file.relative_to(session.session_dir))
            if selected:
                connection.execute("UPDATE attempts SET selected = 0 WHERE point_id = ?", (point_id,))
            connection.execute(
                """
                INSERT INTO attempts(
                    attempt_id, point_id, session_id, attempt_no, created_at_utc,
                    validity, invalid_reason, selected, current_a, actual_current_a,
                    voltage_raw_v, voltage_v, power_w, efficiency, peak_wavelength_nm,
                    centroid_nm, fwhm_nm, pib, smsr_db, stable_span_w,
                    stable_window_s, stable_tolerance_w, integration_time_us, spectrum_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    point_id,
                    session_id,
                    attempt_no,
                    utc_now_iso(),
                    validity.value,
                    str(invalid_reason),
                    int(bool(selected)),
                    _finite_or_none(current_a),
                    _finite_or_none(actual_current_a),
                    _finite_or_none(voltage_raw_v),
                    _finite_or_none(voltage_v),
                    _finite_or_none(power_w),
                    _finite_or_none(efficiency),
                    _finite_or_none(peak_wavelength_nm),
                    _finite_or_none(centroid_nm),
                    _finite_or_none(fwhm_nm),
                    _finite_or_none(pib),
                    _finite_or_none(smsr_db),
                    _finite_or_none(stable_span_w),
                    _finite_or_none(stable_window_s),
                    _finite_or_none(stable_tolerance_w),
                    None if integration_time_us is None else int(integration_time_us),
                    spectrum_path,
                ),
            )
            connection.execute(
                "UPDATE sessions SET export_state = ?, export_error = '' WHERE session_id = ?",
                (ExportState.PENDING.value, session_id),
            )
        code = "attempt.valid" if validity is AttemptValidity.VALID else "attempt.invalid"
        severity = EventSeverity.INFO if validity is AttemptValidity.VALID else EventSeverity.BLOCKING
        self.append_event(
            session_id,
            code,
            severity,
            invalid_reason or f"{target_current_a:g} A 测量已保存",
            current_a=target_current_a,
            details={"attempt_id": attempt_id, "attempt_no": attempt_no, "validity": validity.value},
        )
        return self.get_attempt(attempt_id)

    def get_attempt(self, attempt_id: str) -> MeasurementAttempt:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT a.*, p.sequence_index, p.target_current_a
                FROM attempts a JOIN test_points p ON p.point_id = a.point_id
                WHERE a.attempt_id = ?
                """,
                (attempt_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"未找到测量记录：{attempt_id}")
        return self._attempt_from_row(row)

    @staticmethod
    def _attempt_from_row(row: sqlite3.Row) -> MeasurementAttempt:
        def number(name: str) -> float:
            value = row[name]
            return math.nan if value is None else float(value)

        return MeasurementAttempt(
            attempt_id=row["attempt_id"],
            session_id=row["session_id"],
            point_id=row["point_id"],
            sequence_index=int(row["sequence_index"]),
            target_current_a=float(row["target_current_a"]),
            attempt_no=int(row["attempt_no"]),
            created_at_utc=row["created_at_utc"],
            validity=AttemptValidity(row["validity"]),
            invalid_reason=row["invalid_reason"],
            selected=bool(row["selected"]),
            current_a=number("current_a"),
            actual_current_a=number("actual_current_a"),
            voltage_raw_v=number("voltage_raw_v"),
            voltage_v=number("voltage_v"),
            power_w=number("power_w"),
            efficiency=number("efficiency"),
            peak_wavelength_nm=number("peak_wavelength_nm"),
            centroid_nm=number("centroid_nm"),
            fwhm_nm=number("fwhm_nm"),
            pib=number("pib"),
            smsr_db=number("smsr_db"),
            stable_span_w=number("stable_span_w"),
            stable_window_s=number("stable_window_s"),
            stable_tolerance_w=number("stable_tolerance_w"),
            integration_time_us=row["integration_time_us"],
            spectrum_path=row["spectrum_path"],
        )

    def list_attempts(self, session_id: str, *, selected_only: bool = False) -> tuple[MeasurementAttempt, ...]:
        selected_clause = "AND a.selected = 1" if selected_only else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT a.*, p.sequence_index, p.target_current_a
                FROM attempts a JOIN test_points p ON p.point_id = a.point_id
                WHERE a.session_id = ? {selected_clause}
                ORDER BY p.sequence_index, a.attempt_no
                """,
                (session_id,),
            ).fetchall()
        return tuple(self._attempt_from_row(row) for row in rows)

    def append_event(
        self,
        session_id: str,
        code: str,
        severity: EventSeverity,
        message: str,
        *,
        current_a: float | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO events(session_id, created_at_utc, code, severity, message, current_a, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    utc_now_iso(),
                    str(code),
                    severity.value,
                    str(message),
                    _finite_or_none(current_a),
                    json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                ),
            )

    def list_events(self, session_id: str) -> tuple[TestEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE session_id = ? ORDER BY event_id", (session_id,)
            ).fetchall()
        return tuple(
            TestEvent(
                event_id=int(row["event_id"]),
                session_id=row["session_id"],
                created_at_utc=row["created_at_utc"],
                code=row["code"],
                severity=EventSeverity(row["severity"]),
                message=row["message"],
                current_a=None if row["current_a"] is None else float(row["current_a"]),
                details=json.loads(row["details_json"] or "{}"),
            )
            for row in rows
        )

    def complete_session(
        self,
        session_id: str,
        status: SessionStatus,
        reason: str,
        *,
        shutdown_confirmed: bool | None,
    ) -> None:
        if status in (SessionStatus.RUNNING, SessionStatus.INCOMPLETE):
            raise ValueError("结束会话时必须使用终态")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET ended_at_utc = ?, status = ?, termination_reason = ?, shutdown_confirmed = ?
                WHERE session_id = ?
                """,
                (
                    utc_now_iso(),
                    status.value,
                    str(reason),
                    None if shutdown_confirmed is None else int(bool(shutdown_confirmed)),
                    session_id,
                ),
            )
        self.append_event(session_id, "session.completed", EventSeverity.INFO, reason)

    def mark_running_sessions_incomplete(self) -> tuple[TestSession, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT session_id FROM sessions WHERE status = ? ORDER BY started_at_utc",
                (SessionStatus.RUNNING.value,),
            ).fetchall()
            ids = [row["session_id"] for row in rows]
            connection.executemany(
                "UPDATE sessions SET status = ?, termination_reason = ? WHERE session_id = ?",
                [
                    (SessionStatus.INCOMPLETE.value, "应用在会话完成前退出", session_id)
                    for session_id in ids
                ],
            )
        for session_id in ids:
            self.append_event(
                session_id,
                "session.interrupted",
                EventSeverity.WARNING,
                "检测到未完成测试会话",
            )
        return tuple(self.get_session(session_id) for session_id in ids)

    def resume_session(self, session_id: str) -> TestSession:
        session = self.get_session(session_id)
        if session.status is not SessionStatus.INCOMPLETE:
            raise ValueError("只能恢复未完成的自动测试会话")
        if session.mode != "automatic":
            raise ValueError("手动测试会话不能自动续测")
        with self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET status = ?, ended_at_utc = NULL, termination_reason = '' WHERE session_id = ?",
                (SessionStatus.RUNNING.value, session_id),
            )
        self.append_event(session_id, "session.resumed", EventSeverity.INFO, "操作者恢复未完成测试")
        return self.get_session(session_id)

    def mark_export_state(self, session_id: str, state: ExportState, error: str = "") -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET export_state = ?, export_error = ? WHERE session_id = ?",
                (state.value, str(error), session_id),
            )

    def list_sessions(self, filters: SessionFilters | None = None) -> tuple[TestSession, ...]:
        filters = filters or SessionFilters()
        clauses: list[str] = []
        values: list[Any] = []
        for field, value in (
            ("sn", filters.sn),
            ("product_model", filters.product_model),
            ("batch", filters.batch),
            ("station", filters.station),
        ):
            if value:
                clauses.append(f"{field} LIKE ?")
                values.append(f"%{value}%")
        for field, value in (("mode", filters.mode), ("status", filters.status)):
            if value:
                clauses.append(f"{field} = ?")
                values.append(value)
        if filters.date_from:
            clauses.append("started_at_utc >= ?")
            values.append(filters.date_from)
        if filters.date_to:
            clauses.append("started_at_utc <= ?")
            values.append(filters.date_to)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(int(filters.limit), 5000)))
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM sessions {where} ORDER BY started_at_utc DESC LIMIT ?", values
            ).fetchall()
        return tuple(self._session_from_row(row) for row in rows)

    def session_statistics(self, filters: SessionFilters | None = None) -> dict[str, float]:
        sessions = self.list_sessions(filters or SessionFilters(limit=5000))
        if not sessions:
            return {
                "sessions": 0.0,
                "completion_rate": math.nan,
                "invalid_attempt_rate": math.nan,
                "retest_rate": math.nan,
                "median_duration_s": math.nan,
            }
        ids = [session.session_id for session in sessions]
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as connection:
            attempt_rows = connection.execute(
                f"SELECT point_id, validity, COUNT(*) OVER(PARTITION BY point_id) AS attempts FROM attempts WHERE session_id IN ({placeholders})",
                ids,
            ).fetchall()
        total_attempts = len(attempt_rows)
        invalid_attempts = sum(row["validity"] != AttemptValidity.VALID.value for row in attempt_rows)
        point_attempt_counts: dict[str, int] = {}
        for row in attempt_rows:
            point_attempt_counts[row["point_id"]] = int(row["attempts"])
        durations = []
        for session in sessions:
            if session.ended_at_utc:
                start = datetime.fromisoformat(session.started_at_utc)
                end = datetime.fromisoformat(session.ended_at_utc)
                durations.append((end - start).total_seconds())
        durations.sort()
        if durations:
            midpoint = len(durations) // 2
            median_duration = (
                durations[midpoint]
                if len(durations) % 2
                else (durations[midpoint - 1] + durations[midpoint]) / 2.0
            )
        else:
            median_duration = math.nan
        completed = sum(session.status is SessionStatus.COMPLETED for session in sessions)
        points = len(point_attempt_counts)
        retested = sum(count > 1 for count in point_attempt_counts.values())
        return {
            "sessions": float(len(sessions)),
            "completion_rate": completed / len(sessions),
            "invalid_attempt_rate": invalid_attempts / total_attempts if total_attempts else math.nan,
            "retest_rate": retested / points if points else math.nan,
            "median_duration_s": median_duration,
        }


class PowerTraceWriter:
    """Bounded background CSV writer for the complete session power trace."""

    HEADER = (
        "timestamp_utc",
        "elapsed_s",
        "state",
        "target_current_a",
        "actual_current_a",
        "power_w",
        "stable",
        "stable_span_w",
        "stable_tolerance_w",
    )

    def __init__(self, path: Path, *, max_pending_rows: int = 50_000) -> None:
        self.path = Path(path)
        self._queue: queue.Queue[tuple[Any, ...] | None] = queue.Queue(max_pending_rows)
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="arp-power-trace", daemon=True)
        self._started = False

    @property
    def error(self) -> BaseException | None:
        return self._error

    def start(self) -> None:
        if self._started:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._started = True
        self._thread.start()

    def append(
        self,
        *,
        elapsed_s: float,
        state: str,
        target_current_a: float | None,
        actual_current_a: float | None,
        power_w: float,
        stable: bool,
        stable_span_w: float,
        stable_tolerance_w: float,
    ) -> None:
        if self._error is not None:
            raise RuntimeError(f"功率原始曲线保存失败：{self._error}") from self._error
        if not self._started:
            raise RuntimeError("功率原始曲线记录器尚未启动")
        row = (
            utc_now_iso(),
            f"{float(elapsed_s):.6f}",
            str(state),
            "" if target_current_a is None else f"{float(target_current_a):.6f}",
            "" if actual_current_a is None else f"{float(actual_current_a):.6f}",
            f"{float(power_w):.9f}",
            "1" if stable else "0",
            "" if not math.isfinite(float(stable_span_w)) else f"{float(stable_span_w):.9f}",
            "" if not math.isfinite(float(stable_tolerance_w)) else f"{float(stable_tolerance_w):.9f}",
        )
        try:
            self._queue.put_nowait(row)
        except queue.Full as exc:
            raise RuntimeError("功率原始曲线写入队列已满") from exc

    def stop(self, timeout_s: float = 5.0) -> None:
        if not self._started:
            return
        self._queue.put(None)
        self._thread.join(timeout_s)
        if self._thread.is_alive():
            raise RuntimeError("功率原始曲线写入线程未能及时停止")
        if self._error is not None:
            raise RuntimeError(f"功率原始曲线保存失败：{self._error}") from self._error

    def _run(self) -> None:
        try:
            write_header = not self.path.exists() or self.path.stat().st_size == 0
            with self.path.open("a", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                if write_header:
                    writer.writerow(self.HEADER)
                last_flush = time.monotonic()
                while True:
                    try:
                        row = self._queue.get(timeout=0.25)
                    except queue.Empty:
                        row = ...
                    if row is None:
                        file.flush()
                        os.fsync(file.fileno())
                        return
                    if row is not ...:
                        writer.writerow(row)
                    now = time.monotonic()
                    if now - last_flush >= 1.0:
                        file.flush()
                        last_flush = now
        except BaseException as exc:
            self._error = exc
