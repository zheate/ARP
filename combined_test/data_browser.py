"""Read-only, on-demand browsing of exported test workbooks.

This index deliberately includes interrupted and legacy records. It must not
replace the completed-test filter used for automatic-test reference curves.
"""
from __future__ import annotations

import copy
import math
import os
import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from .shipping_report import REPORT_WORKBOOK_SCHEMA_VERSION, _parse_session_information, _result_column_key

MAX_DIRECTORIES = 4
MAX_DETAIL_CACHE = 32
MAX_INDEX_FILES = 20000
_LOCK = threading.RLock()
_INDEXES: OrderedDict[str, dict[str, Any]] = OrderedDict()
_DETAILS: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
_FIELDS = {
    "current": "currentA", "voltage": "voltageV", "power": "powerW",
    "efficiency": "efficiencyPercent", "peak_wavelength": "peakWavelengthNm",
    "centroid": "centroidNm", "fwhm": "fwhmNm", "pib": "pibPercent", "smsr": "smsrDb",
}


def clear_data_browser_cache() -> None:
    with _LOCK:
        _INDEXES.clear()
        _DETAILS.clear()


def _key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _version(path: Path) -> tuple[str, float]:
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}", stat.st_mtime * 1000


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _timestamp(value: str) -> float | None:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000
        return result if math.isfinite(result) else None
    except (ValueError, OverflowError, OSError):
        return None


def _empty_summary(path: Path, version: str, modified: float) -> dict[str, Any]:
    return {
        "id": _key(path), "sourcePath": str(path), "fileName": path.name,
        "version": version, "modifiedAtMs": modified, "timeMs": modified,
        "timeSource": "modified", "sn": "", "snSource": "missing", "station": "",
        "stationId": "", "temperature": "", "testMode": "", "sessionId": "",
        "flowStatus": "unknown", "readStatus": "readable", "error": "",
        "pointCount": 0, "validPointCount": 0, "minimumCurrentA": None, "maximumCurrentA": None,
    }


def _read_workbook(path: Path, version: str, modified: float) -> dict[str, Any]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        session = _parse_session_information(workbook)
        summary = _empty_summary(path, version, modified)
        summary.update({
            "sn": session.get("SN", ""), "station": session.get("测试站别", ""),
            "stationId": session.get("工位编号", session.get("stationId", "")),
            "temperature": session.get("测试温度", ""), "testMode": session.get("测试模式", ""),
            "sessionId": session.get("会话编号", ""),
            "flowStatus": session.get("流程状态", "").strip().lower() or "unknown",
        })
        summary["snSource"] = "metadata" if summary["sn"] else "missing"
        started = _timestamp(session.get("开始时间 UTC", ""))
        if started is not None:
            summary.update(timeMs=started, timeSource="session")
        warnings: list[str] = []
        schema = session.get("出货报告格式版本", "")
        if not schema:
            warnings.append("旧格式文件：部分测试信息可能未记录。")
        elif schema != REPORT_WORKBOOK_SCHEMA_VERSION:
            warnings.append(f"文件格式版本 {schema} 与当前版本不同，仅展示已识别的列。")

        # Scan only result headers and scalar columns; never build spectrum arrays.
        sheets = sorted(workbook.worksheets, key=lambda sheet: sheet.title != "测试数据")
        layout = None
        for sheet in sheets:
            if sheet.title in {"会话信息", "测量尝试"}:
                continue
            rows = sheet.iter_rows(min_row=1, max_row=min(sheet.max_row or 100, 100),
                                   max_col=min(sheet.max_column or 200, 200), values_only=True)
            first_rows = []
            for row_index, row in enumerate(rows, 1):
                first_rows.append(row)
                columns = {}
                for col, value in enumerate(row):
                    field = _result_column_key(value)
                    if field and field not in columns:
                        columns[field] = col
                if {"current", "power"}.issubset(columns):
                    layout = sheet, row_index, columns, row, first_rows
                    break
            if layout:
                break
        if layout is None:
            raise ValueError("未识别到电流、功率列，文件格式不支持")
        sheet, header_row, columns, headers, first_rows = layout
        # Legacy exports place metadata in the first row rather than a session sheet.
        for row in first_rows[:header_row - 1]:
            for col, value in enumerate(row[:-1]):
                field = {"SN": "sn", "测试站别": "station", "测试温度": "temperature"}.get(str(value).strip())
                if field and not summary[field] and row[col + 1] is not None:
                    summary[field] = str(row[col + 1]).strip()
                    if field == "sn":
                        summary["snSource"] = "metadata"
        points = []
        for row_index, row in enumerate(sheet.iter_rows(min_row=header_row + 1,
                max_col=max(columns.values()) + 1, values_only=True), header_row + 1):
            current = _number(row[columns["current"]])
            if current is None:
                continue
            point: dict[str, Any] = {"rowId": row_index, "currentA": current}
            for column_key, field in _FIELDS.items():
                col = columns.get(column_key)
                value = None if col is None else _number(row[col])
                # Native exports store ratios, like shipping_report. Explicit
                # percent headers in imported workbooks already contain percent.
                if column_key in {"efficiency", "pib"} and value is not None and "%" not in str(headers[col]):
                    value *= 100
                point[field] = value
            points.append(point)
        valid = [p for p in points if p["powerW"] is not None]
        currents = [p["currentA"] for p in valid]
        summary.update(pointCount=len(points), validPointCount=len(valid),
                       minimumCurrentA=min(currents) if currents else None,
                       maximumCurrentA=max(currents) if currents else None)
        if not valid:
            warnings.append("没有可绘制的有效电流/功率测试点。")
        if not summary["sn"]:
            # Recognize only the standard root/SN/station/date.xlsx layout.
            if summary["station"] and path.parent.name == summary["station"]:
                summary.update(sn=path.parent.parent.name, snSource="directory")
        if summary["snSource"] == "directory":
            warnings.append("SN 由标准目录结构推定，文件未记录 SN。")
        labels = {_FIELDS[key]: str(headers[col]) for key, col in columns.items() if key in _FIELDS}
        return {"record": summary, "points": points, "metadata": session, "warnings": warnings, "columnLabels": labels}
    finally:
        workbook.close()


def inspect_data_record(params: dict[str, Any]) -> dict[str, Any]:
    raw = str(params.get("path", "")).strip()
    if not raw:
        raise ValueError("请选择测试文件")
    path = Path(raw).expanduser().resolve()
    if path.suffix.lower() != ".xlsx":
        raise ValueError("仅支持 .xlsx 测试文件")
    try:
        version, modified = _version(path)
        cache_key = (_key(path), version)
        with _LOCK:
            cached = _DETAILS.get(cache_key)
            if cached is not None:
                _DETAILS.move_to_end(cache_key)
                return copy.deepcopy(cached)
        result = _read_workbook(path, version, modified)
        if _version(path)[0] != version:
            raise ValueError("文件正在更新，请稍后刷新重试")
        with _LOCK:
            for old in [key for key in _DETAILS if key[0] == cache_key[0]]:
                del _DETAILS[old]
            _DETAILS[cache_key] = result
            while len(_DETAILS) > MAX_DETAIL_CACHE:
                _DETAILS.popitem(last=False)
        return copy.deepcopy(result)
    except (FileNotFoundError, IsADirectoryError) as exc:
        raise ValueError("文件已移动或删除，请刷新目录") from exc
    except PermissionError as exc:
        raise ValueError("文件无法访问，请检查权限或占用状态") from exc
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"无法读取 Excel：{exc}") from exc


def _scan(root: Path, previous: dict[str, Any] | None) -> dict[str, Any]:
    old_records = {r["id"]: r for r in previous["records"]} if previous else {}
    records = []
    errors: list[str] = []
    def on_error(error: OSError) -> None:
        errors.append(f"目录读取失败：{error.filename} · {error.strerror}")
    for parent, directories, files in os.walk(root, followlinks=False, onerror=on_error):
        directories[:] = [d for d in directories if not Path(parent, d).is_symlink()
                          and not getattr(Path(parent, d), "is_junction", lambda: False)()]
        for name in sorted(files):
            if not name.lower().endswith(".xlsx") or name.startswith("~$"):
                continue
            path = Path(parent, name)
            if path.is_symlink():
                continue
            if len(records) >= MAX_INDEX_FILES:
                errors.append(f"目录超过 {MAX_INDEX_FILES} 个文件，请选择更小的数据目录。")
                return {"records": records, "indexedAtMs": time.time() * 1000, "warnings": errors}
            version, modified = "", 0.0
            try:
                version, modified = _version(path)
                old = old_records.get(_key(path))
                # Failed files are retried even if their timestamp is unchanged.
                if old and old["version"] == version and old["readStatus"] == "readable":
                    record = old
                else:
                    record = inspect_data_record({"path": str(path)})["record"]
            except Exception as exc:
                record = _empty_summary(path, version, modified)
                record.update(readStatus="error", error=str(exc))
            records.append(record)
    return {"records": records, "indexedAtMs": time.time() * 1000, "warnings": errors}


def list_data_records(params: dict[str, Any]) -> dict[str, Any]:
    raw = str(params.get("rootPath", "")).strip()
    if not raw:
        raise ValueError("请选择数据目录")
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("数据目录不存在或无法访问")
    refresh = params.get("refresh", False)
    if not isinstance(refresh, bool):
        raise ValueError("refresh 必须为布尔值")
    key = _key(root)
    with _LOCK:
        index = _INDEXES.get(key)
        if index is not None:
            _INDEXES.move_to_end(key)
    if index is None or refresh:
        index = _scan(root, index)
        with _LOCK:
            _INDEXES[key] = index
            _INDEXES.move_to_end(key)
            while len(_INDEXES) > MAX_DIRECTORIES:
                _INDEXES.popitem(last=False)
    def integer(name: str, default: int, maximum: int) -> int:
        value = params.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"{name} 必须为 1～{maximum} 的整数")
        return value
    page = integer("page", 1, MAX_INDEX_FILES)
    page_size = integer("pageSize", 50, 200)
    start = _number(params.get("fromMs"))
    end = _number(params.get("toMs"))
    for name in ("fromMs", "toMs"):
        if params.get(name) is not None and _number(params[name]) is None:
            raise ValueError("日期筛选必须为有效时间")
    if start is not None and end is not None and start >= end:
        raise ValueError("开始日期不能晚于结束日期")
    query = str(params.get("sn", "")).strip().casefold()
    station = str(params.get("station", ""))
    status = str(params.get("status", ""))
    all_records = index["records"]
    records = [r for r in all_records if (not query or query in r["sn"].casefold())
               and (not station or r["station"] == station)
               and (not status or (r["readStatus"] == "error" if status == "read_error" else r["flowStatus"] == status))
               and (start is None or r["timeMs"] >= start)
               and (end is None or r["timeMs"] < end)]
    records.sort(key=lambda r: (r["timeMs"], r["modifiedAtMs"], r["id"]), reverse=True)
    pages = max(1, math.ceil(len(records) / page_size))
    page = min(page, pages)
    return copy.deepcopy({
        "records": records[(page - 1) * page_size:page * page_size], "total": len(records),
        "fileCount": len(all_records), "page": page, "pageCount": pages,
        "rootPath": str(root), "indexedAtMs": index["indexedAtMs"], "warnings": index["warnings"],
        "errorCount": sum(r["readStatus"] == "error" for r in all_records),
        "stations": sorted({r["station"] for r in all_records if r["station"]}),
        "statuses": sorted({r["flowStatus"] for r in all_records}),
    })
