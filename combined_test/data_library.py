"""Local SQLite library of completed tests, independent of exported Excel files.

JSONL remains the durable acquisition journal. SQLite stores immutable completed
session snapshots, including spectra and attempts, and can recover from journals.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .data_browser import _empty_summary, _timestamp, _number, inspect_data_record, list_data_records

FIELDS = {
    "current_a": "currentA", "voltage_v": "voltageV", "power_w": "powerW",
    "efficiency": "efficiencyPercent", "peak_wavelength_nm": "peakWavelengthNm",
    "centroid_nm": "centroidNm", "fwhm_nm": "fwhmNm", "pib": "pibPercent", "smsr_db": "smsrDb",
}
LABELS = dict(zip(FIELDS.values(), ("电流", "电压", "功率", "效率 (%)", "峰值波长", "质心波长", "FWHM", "PIB (%)", "SMSR")))


class DataLibrary:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.executescript('''
            CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY, sn TEXT NOT NULL, station TEXT NOT NULL,
                time_ms REAL NOT NULL, summary TEXT NOT NULL, detail TEXT NOT NULL,
                source BLOB
            );
            CREATE INDEX IF NOT EXISTS records_time ON records(time_ms DESC);
            CREATE INDEX IF NOT EXISTS records_sn ON records(sn);
            CREATE TABLE IF NOT EXISTS journals (path TEXT PRIMARY KEY, version TEXT NOT NULL);
        ''')
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, detail: dict[str, Any], *, source: bytes | None = None) -> bool:
        record = detail["record"]
        if record["flowStatus"] != "completed" or record["validPointCount"] == 0:
            return False
        with self._connect() as db:
            result = db.execute(
                "INSERT OR IGNORE INTO records VALUES (?, ?, ?, ?, ?, ?, ?)",
                (record["id"], record["sn"], record["station"], record["timeMs"],
                 json.dumps(record, ensure_ascii=False, allow_nan=False),
                 json.dumps(detail, ensure_ascii=False, allow_nan=False), source),
            )
            return result.rowcount == 1

    def ingest_journal(self, path: Path) -> bool:
        version = str(path.stat().st_mtime_ns) + ':' + str(path.stat().st_size)
        with self._connect() as db:
            if db.execute("SELECT 1 FROM journals WHERE path=? AND version=?", (str(path), version)).fetchone():
                return False
        session = None
        records: dict[float, dict[str, Any]] = {}
        attempts = []
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                event = json.loads(line)
                if event.get("type") in {"session_start", "session_completed"}:
                    session = event["session"]
                elif event.get("type") == "point_committed":
                    record = event["record"]
                    records[record["current_a"]] = record
                    attempts.append(event["attempt"])
                elif event.get("type") == "attempt_rejected":
                    attempts.append(event["attempt"])
        added = False
        if session and session["status"] == "completed" and records:
            workbook = Path(session["workbook_path"])
            started = _timestamp(session["started_at_utc"]) or path.stat().st_mtime * 1000
            summary = _empty_summary(workbook, session["ended_at_utc"] or session["started_at_utc"], started)
            settings = session.get("settings", {})
            summary.update(id='session:' + session["session_id"], sessionId=session["session_id"],
                           sn=session["sn"], snSource="metadata", station=session["station"],
                           stationId=str(settings.get("stationId", "")),
                           temperature=str(settings.get("testTemperatureC", "")),
                           testMode=session["mode"], flowStatus="completed", timeSource="session", storage="database")
            points = []
            spectra = []
            for index, record in enumerate(sorted(records.values(), key=lambda r: r["current_a"]), 1):
                point = {field: _number(record.get(key)) for key, field in FIELDS.items()}
                for field in ("efficiencyPercent", "pibPercent"):
                    if point[field] is not None:
                        point[field] *= 100
                point["rowId"] = index
                points.append(point)
                wavelength = record.get("wavelength", [])
                intensity = record.get("intensity", [])
                pairs = [(w, i) for w, i in zip(wavelength, intensity) if _number(w) is not None and _number(i) is not None]
                if pairs:
                    peak = max(pairs, key=lambda pair: pair[1])
                    spectra.append({"currentA": record["current_a"], "wavelengthNm": [p[0] for p in pairs],
                                    "intensity": [p[1] for p in pairs], "peakWavelengthNm": peak[0], "maxIntensity": peak[1]})
            currents = [p["currentA"] for p in points if p["currentA"] is not None and p["powerW"] is not None]
            summary.update(pointCount=len(points), validPointCount=len(currents),
                           minimumCurrentA=min(currents) if currents else None, maximumCurrentA=max(currents) if currents else None)
            detail = {"record": summary, "points": points, "columnLabels": LABELS,
                      "metadata": {"会话编号": session["session_id"], "开始时间 UTC": session["started_at_utc"],
                                   "结束时间 UTC": session["ended_at_utc"] or "", "归档来源": "本地测试会话",
                                   "测试配置": json.dumps(settings, ensure_ascii=False),
                                   "设备信息": json.dumps(session.get("devices", []), ensure_ascii=False)},
                      "warnings": [], "spectra": spectra, "attempts": attempts, "session": session}
            added = self.save(detail, source=path.read_bytes())
        # Cache only a stable read. Concurrent completion will be picked up next time.
        after = str(path.stat().st_mtime_ns) + ':' + str(path.stat().st_size)
        if version == after:
            with self._connect() as db:
                db.execute("INSERT OR REPLACE INTO journals VALUES (?, ?)", (str(path), version))
        return added

    def recover(self, journal_root: Path) -> list[str]:
        warnings = []
        if journal_root.exists():
            # A refresh usually sees only unchanged journals. Read the index
            # once instead of opening and initializing SQLite for every file.
            # Changed files still pass through ingest_journal's stable-read check.
            with self._connect() as db:
                indexed_versions = dict(db.execute("SELECT path, version FROM journals"))
            for path in sorted(journal_root.glob("*/*.jsonl")):
                try:
                    stat = path.stat()
                    version = f"{stat.st_mtime_ns}:{stat.st_size}"
                    if indexed_versions.get(str(path)) == version:
                        continue
                    self.ingest_journal(path)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    warnings.append(f"会话 {path.stem} 尚未同步：{exc}")
        return warnings

    def import_excel(self, params: dict[str, Any]) -> dict[str, Any]:
        listing = list_data_records({"rootPath": params.get("rootPath", ""), "refresh": True, "pageSize": 200})
        imported = skipped = 0
        warnings = list(listing["warnings"])
        for page in range(1, listing["pageCount"] + 1):
            batch = listing if page == 1 else list_data_records({"rootPath": params["rootPath"], "page": page, "pageSize": 200})
            for record in batch["records"]:
                if record["flowStatus"] != "completed" or record["readStatus"] != "readable" or not record["validPointCount"]:
                    skipped += 1
                    continue
                try:
                    path = Path(record["sourcePath"])
                    before = path.stat()
                    detail = inspect_data_record({"path": str(path), "includeSpectra": True})
                    source = path.read_bytes()
                    after = path.stat()
                    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                        raise ValueError("文件正在更新，请稍后重试")
                    summary = detail["record"]
                    summary.update(id='session:' + summary["sessionId"] if summary["sessionId"] else 'excel:' + hashlib.sha256(source).hexdigest(), storage="database")
                    detail["metadata"]["归档来源"] = "Excel 导入（已备份完整工作簿）"
                    if self.save(detail, source=source):
                        imported += 1
                    else:
                        skipped += 1
                except (OSError, ValueError) as exc:
                    warnings.append(f'{record["fileName"]}：{exc}')
        return {"imported": imported, "skipped": skipped, "warnings": warnings}

    def inspect(self, record_id: str, *, include_spectra: bool = False) -> dict[str, Any]:
        with self._connect() as db:
            # Scalar charts do not use the full-resolution spectra or raw
            # attempts. Drop those fields before crossing into Python to avoid
            # allocating millions of floats only to discard them immediately.
            projection = "json_remove(detail, '$.spectra', '$.attempts', '$.session')"
            if include_spectra:
                projection = "detail"
            try:
                row = db.execute(f"SELECT {projection} FROM records WHERE id=?", (record_id,)).fetchone()
            except sqlite3.OperationalError as exc:
                # Retain compatibility with Python distributions built without
                # SQLite's optional JSON functions. Other database errors matter.
                if include_spectra or "no such function: json_remove" not in str(exc):
                    raise
                row = db.execute("SELECT detail FROM records WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise ValueError("数据库中未找到此测试记录，请刷新")
        detail = json.loads(row[0])
        # Spectra and raw attempts are retained in the database, not sent with scalar charts.
        return {key: value for key, value in detail.items() if key not in ({"attempts", "session"} if include_spectra else {"spectra", "attempts", "session"})}

    def list(self, params: dict[str, Any]) -> dict[str, Any]:
        page, page_size = params.get("page", 1), params.get("pageSize", 50)
        if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 200:
            raise ValueError("无效页码或每页记录数")
        clauses, values = [], []
        if params.get("sn"):
            # Literal substring matching, so underscores and percent signs in SN are not wildcards.
            clauses.append("instr(lower(sn), lower(?)) > 0")
            values.append(str(params["sn"]).strip())
        if params.get("station"):
            clauses.append("station=?")
            values.append(params["station"])
        for name, operator in (("fromMs", ">="), ("toMs", "<")):
            if params.get(name) is not None:
                value = _number(params[name])
                if value is None:
                    raise ValueError("日期筛选必须为有效时间")
                clauses.append(f"time_ms {operator} ?")
                values.append(value)
        if params.get("fromMs") is not None and params.get("toMs") is not None and float(params["fromMs"]) >= float(params["toMs"]):
            raise ValueError("开始日期不能晚于结束日期")
        if params.get("status") not in (None, "", "completed"):
            clauses.append("0")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as db:
            total = db.execute("SELECT COUNT(*) FROM records" + where, values).fetchone()[0]
            pages = max(1, (total + page_size - 1) // page_size)
            page = min(page, pages)
            rows = db.execute("SELECT summary FROM records" + where + " ORDER BY time_ms DESC, id LIMIT ? OFFSET ?", [*values, page_size, (page-1)*page_size]).fetchall()
            count = db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            stations = [row[0] for row in db.execute("SELECT DISTINCT station FROM records WHERE station != '' ORDER BY station")]
        return {"records": [json.loads(row[0]) for row in rows], "total": total, "fileCount": count,
                "page": page, "pageCount": pages, "rootPath": str(self.path), "indexedAtMs": time.time()*1000,
                "warnings": [], "errorCount": 0, "stations": stations, "statuses": ["completed"]}
