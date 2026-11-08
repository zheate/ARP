"""Read-only, on-demand browsing of exported test workbooks.

This index deliberately includes interrupted and legacy records. It must not
replace the completed-test filter used for automatic-test reference curves.
"""
from __future__ import annotations

import copy
import math
import os
import re
import sys
import threading
import time
from array import array
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from .shipping_report import REPORT_WORKBOOK_SCHEMA_VERSION, _parse_session_information, _result_column_key
from .xlsx_reader import iter_browser_rows

MAX_DIRECTORIES = 4
MAX_DETAIL_CACHE = 32
MAX_DETAIL_CACHE_BYTES = 64 * 1024 * 1024
MAX_INDEX_FILES = 20000
_LOCK = threading.RLock()
_INDEXES: OrderedDict[str, dict[str, Any]] = OrderedDict()
_DETAILS: OrderedDict[tuple[str, str, bool], dict[str, Any]] = OrderedDict()
_DETAIL_SIZES: dict[tuple[str, str, bool], int] = {}
_DETAIL_CACHE_BYTES = 0
_FIELDS = {
    "current": "currentA", "voltage": "voltageV", "power": "powerW",
    "efficiency": "efficiencyPercent", "peak_wavelength": "peakWavelengthNm",
    "centroid": "centroidNm", "fwhm": "fwhmNm", "pib": "pibPercent", "smsr": "smsrDb",
}
_SPECTRUM_COLUMN_PATTERN = re.compile(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*A\s*$", re.IGNORECASE)



def clear_data_browser_cache() -> None:
    global _DETAIL_CACHE_BYTES
    with _LOCK:
        _INDEXES.clear()
        _DETAILS.clear()
        _DETAIL_SIZES.clear()
        _DETAIL_CACHE_BYTES = 0


def _compact_spectrum_cache(detail: dict[str, Any]) -> None:
    """Pack freshly parsed samples; cache buffers never leave this module."""
    grids: dict[bytes, array] = {}
    for spectrum in detail.get("spectra") or []:
        wavelength = array("d", spectrum["wavelengthNm"])
        # Share only bit-identical grids inside this workbook. Public responses
        # still receive a separate list for every spectrum and every request.
        spectrum["wavelengthNm"] = grids.setdefault(wavelength.tobytes(), wavelength)
        spectrum["intensity"] = array("d", spectrum["intensity"])


def _detail_size(value: Any, seen: set[int] | None = None) -> int:
    """Account for owned containers/buffers once, without expanding samples."""
    if seen is None:
        seen = set()
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_detail_size(key, seen) + _detail_size(item, seen) for key, item in value.items())
    elif isinstance(value, (list, tuple)):
        size += sum(_detail_size(item, seen) for item in value)
    return size


def _remove_cached_detail(key: tuple[str, str, bool]) -> None:
    """Caller holds _LOCK."""
    global _DETAIL_CACHE_BYTES
    _DETAILS.pop(key)
    _DETAIL_CACHE_BYTES -= _DETAIL_SIZES.pop(key)


def _copy_public_detail(detail: dict[str, Any]) -> dict[str, Any]:
    output = copy.deepcopy({key: value for key, value in detail.items() if key != "spectra"})
    if "spectra" in detail:
        spectra = detail["spectra"]
        output["spectra"] = None if spectra is None else [
            {
                key: value.tolist() if key in {"wavelengthNm", "intensity"} else copy.deepcopy(value)
                for key, value in spectrum.items()
            }
            for spectrum in spectra
        ]
    return output


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


def _read_workbook(path: Path, version: str, modified: float, include_spectra: bool = False) -> dict[str, Any]:
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

        spectrum_cols: list[tuple[float, int]] = []
        if include_spectra:
            result_cols = set(columns.values())
            for col_idx, val in enumerate(headers):
                if col_idx in result_cols or (col_idx + 1) in result_cols:
                    continue
                if val is not None:
                    m = _SPECTRUM_COLUMN_PATTERN.match(str(val).strip())
                    if m:
                        try:
                            spectrum_cols.append((float(m.group(1)), col_idx))
                        except ValueError:
                            pass
            spectrum_cols.sort(key=lambda item: item[0])

        max_col_to_read = max(columns.values()) + 1
        if spectrum_cols:
            max_col_to_read = max(max_col_to_read, max(c + 2 for _, c in spectrum_cols))

        spectra_map: dict[float, dict[str, list[float]]] = {
            cur: {"wavelengthNm": [], "intensity": []} for cur, _ in spectrum_cols
        }

        points = []
        for row_index, row in enumerate(
            iter_browser_rows(
                sheet,
                min_row=header_row + 1,
                max_col=max_col_to_read,
            ),
            header_row + 1,
        ):
            current = _number(row[columns["current"]]) if columns["current"] < len(row) else None
            if current is not None:
                point: dict[str, Any] = {"rowId": row_index, "currentA": current}
                for column_key, field in _FIELDS.items():
                    col = columns.get(column_key)
                    value = None if col is None or col >= len(row) else _number(row[col])
                    # Native exports store ratios, like shipping_report. Explicit
                    # percent headers in imported workbooks already contain percent.
                    if column_key in {"efficiency", "pib"} and value is not None and "%" not in str(headers[col]):
                        value *= 100
                    point[field] = value
                points.append(point)

            if include_spectra:
                for cur, col in spectrum_cols:
                    if col < len(row) and (col + 1) < len(row):
                        wl = _number(row[col])
                        val = _number(row[col + 1])
                        if wl is not None and val is not None:
                            spectra_map[cur]["wavelengthNm"].append(wl)
                            spectra_map[cur]["intensity"].append(val)

        spectra_list: list[dict[str, Any]] | None = None
        if include_spectra and spectrum_cols:
            spectra_list = []
            for cur in sorted(spectra_map):
                wls = spectra_map[cur]["wavelengthNm"]
                ints = spectra_map[cur]["intensity"]
                if wls and ints:
                    max_int = max(ints)
                    max_idx = ints.index(max_int)
                    spectra_list.append({
                        "currentA": cur,
                        "wavelengthNm": wls,
                        "intensity": ints,
                        "peakWavelengthNm": wls[max_idx],
                        "maxIntensity": max_int,
                    })

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
        res: dict[str, Any] = {"record": summary, "points": points, "metadata": session, "warnings": warnings, "columnLabels": labels}
        if include_spectra:
            res["spectra"] = spectra_list
        return res
    finally:
        workbook.close()


def _describe_read_error(exc: Exception) -> str:
    """One user-facing reason for a workbook that cannot be read.

    The index and the detail request must describe the same failure the same
    way, and never surface a raw library exception to the operator.
    """
    if isinstance(exc, (FileNotFoundError, IsADirectoryError)):
        return "文件已移动或删除，请刷新目录"
    if isinstance(exc, PermissionError):
        return "文件无法访问，请检查权限或占用状态"
    if isinstance(exc, ValueError):
        return str(exc) or "无法读取 Excel"
    return f"无法读取 Excel：{exc}"


def _read_detail(path: Path, version: str, modified: float, include_spectra: bool = False) -> dict[str, Any]:
    """Read a workbook through the shared detail cache.

    Returns the cached entry itself rather than a copy, so the directory scan
    can read a single record without cloning every test point. Callers must
    use _copy_public_detail before handing the value out. Spectra use packed
    buffers internally; callers must never mutate the cache in place.
    """
    global _DETAIL_CACHE_BYTES
    cache_key = (_key(path), version, include_spectra)
    with _LOCK:
        cached = _DETAILS.get(cache_key)
        if cached is not None:
            _DETAILS.move_to_end(cache_key)
            return cached
    result = _read_workbook(path, version, modified, include_spectra=include_spectra)
    if _version(path)[0] != version:
        raise ValueError("文件正在更新，请稍后刷新重试")
    _compact_spectrum_cache(result)
    size = _detail_size(result)
    with _LOCK:
        for old in [key for key in _DETAILS if key[0] == cache_key[0] and key[2] == include_spectra]:
            _remove_cached_detail(old)
        # An oversized workbook must remain readable without displacing every
        # recent cache entry. Eviction only affects re-readable cached data.
        if size <= MAX_DETAIL_CACHE_BYTES:
            _DETAILS[cache_key] = result
            _DETAIL_SIZES[cache_key] = size
            _DETAIL_CACHE_BYTES += size
            while len(_DETAILS) > MAX_DETAIL_CACHE or _DETAIL_CACHE_BYTES > MAX_DETAIL_CACHE_BYTES:
                _remove_cached_detail(next(iter(_DETAILS)))
    return result


def inspect_data_record(params: dict[str, Any]) -> dict[str, Any]:
    raw = str(params.get("path", "")).strip()
    if not raw:
        raise ValueError("请选择测试文件")
    path = Path(raw).expanduser().resolve()
    if path.suffix.lower() != ".xlsx":
        raise ValueError("仅支持 .xlsx 测试文件")
    include_spectra = bool(params.get("includeSpectra", False))
    try:
        version, modified = _version(path)
        return _copy_public_detail(_read_detail(path, version, modified, include_spectra=include_spectra))
    except Exception as exc:
        raise ValueError(_describe_read_error(exc)) from exc


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
            lower_name = name.lower()
            if (
                not lower_name.endswith(".xlsx")
                or name.startswith("~$")
                or name.startswith("._")
                or lower_name.endswith(".tmp")
            ):
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
                    # Indexing only needs the summary; taking it from the cache
                    # entry avoids cloning every test point of every file.
                    record = dict(_read_detail(path, version, modified)["record"])
            except Exception as exc:
                record = _empty_summary(path, version, modified)
                record.update(readStatus="error", error=_describe_read_error(exc))
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
