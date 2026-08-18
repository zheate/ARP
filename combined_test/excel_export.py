"""Excel workbook export for combined test sessions."""

from __future__ import annotations

import csv
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from openpyxl import Workbook as OpenpyxlWorkbook, load_workbook
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.worksheet import Worksheet as OpenpyxlWorksheet
from xlsxwriter import Workbook as XlsxWriterWorkbook
from xlsxwriter.format import Format as XlsxWriterFormat
from xlsxwriter.worksheet import Worksheet as XlsxWriterWorksheet


RESULT_HEADERS = (
    "电流(A)",
    "电压(V)",
    "功率(W)",
    "电光效率",
    "中心波长(nm)",
    "质心波长(nm)",
    "FWHM(nm)",
    "PIB",
    "SMSR(dB)",
)
EXPORT_WORKBOOK_SCHEMA_VERSION = "1"
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_REPLACE_RETRY_DELAYS_S = (0.05, 0.10, 0.20, 0.40, 0.80)
SECOND_SAMPLE_HEADERS = (
    "时间(s)",
    "功率(W)",
    "效率",
    "电压(V)",
    "中心波长(nm)",
    "质心波长(nm)",
    "FWHM(nm)",
    "PIB",
)
_DATA_HEADER_COLOR = "#D9EAF7"


@dataclass(frozen=True)
class ExcelTestRecord:
    current_a: float
    voltage_v: float
    power_w: float
    efficiency: float
    peak_wavelength_nm: float
    centroid_nm: float
    fwhm_nm: float
    pib: float
    wavelength: Iterable[float]
    intensity: Iterable[float]
    smsr_db: float = math.nan
    test_station: str = ""


def sanitize_sn(sn: str) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub("_", sn.strip()).rstrip(". ")
    if not cleaned:
        raise ValueError("SN 不能为空")
    return cleaned


def sanitize_test_station(test_station: str) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub("_", test_station.strip()).rstrip(". ")
    if not cleaned:
        raise ValueError("测试站别不能为空")
    return cleaned


def build_test_workbook_path(
    output_dir: Path,
    sn: str,
    test_time: datetime,
    test_station: str = "",
) -> Path:
    """Build ``root/SN/station/YYYY_MM_DD_HH_MM.xlsx`` for a test session."""
    session_dir = Path(output_dir).expanduser() / sanitize_sn(sn)
    if test_station.strip():
        session_dir /= sanitize_test_station(test_station)
    filename = f"{test_time.strftime('%Y_%m_%d_%H_%M')}.xlsx"
    return session_dir / filename


def _finite_or_none(value: float) -> float | None:
    number = float(value)
    return number if math.isfinite(number) else None


def _replace_file_with_retry(source: Path, target: Path) -> None:
    """Atomically replace a workbook despite short-lived Windows file locks."""
    for attempt in range(len(WINDOWS_REPLACE_RETRY_DELAYS_S) + 1):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt >= len(WINDOWS_REPLACE_RETRY_DELAYS_S):
                raise
            time.sleep(WINDOWS_REPLACE_RETRY_DELAYS_S[attempt])


def _save_openpyxl_workbook_atomically(workbook: OpenpyxlWorkbook, target: Path) -> None:
    temporary = target.with_name(f".{target.stem}.tmp.xlsx")
    try:
        workbook.save(temporary)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        # Release every openpyxl/ZipFile handle before Windows replaces the
        # existing workbook. This also makes antivirus/indexer locks shorter.
        workbook.close()

    try:
        _replace_file_with_retry(temporary, target)
    except PermissionError as exc:
        raise RuntimeError(
            f"Excel 文件暂时被占用，无法更新 {target.name}；"
            f"完整临时文件已保留：{temporary}"
        ) from exc


def _ensure_result_headers(sheet: OpenpyxlWorksheet) -> None:
    for column, header in enumerate(RESULT_HEADERS, start=1):
        cell = sheet.cell(row=2, column=column, value=header)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
    sheet.column_dimensions["I"].width = 12


def _create_openpyxl_workbook() -> OpenpyxlWorkbook:
    workbook = OpenpyxlWorkbook()
    sheet = workbook.active
    sheet.title = "测试数据"
    sheet["A1"] = "LIV"
    sheet["J1"] = "光谱"
    _ensure_result_headers(sheet)

    sheet.freeze_panes = "A3"
    sheet.column_dimensions["A"].width = 12
    sheet.column_dimensions["B"].width = 12
    sheet.column_dimensions["C"].width = 12
    sheet.column_dimensions["D"].width = 12
    sheet.column_dimensions["E"].width = 16
    sheet.column_dimensions["F"].width = 16
    sheet.column_dimensions["G"].width = 12
    sheet.column_dimensions["H"].width = 12
    sheet["A1"].font = Font(bold=True)
    sheet["J1"].font = Font(bold=True)
    return workbook


def _write_test_station(sheet: OpenpyxlWorksheet, test_station: str) -> None:
    station = str(test_station).strip()
    if not station:
        return
    sheet["B1"] = "测试站别"
    sheet["B1"].font = Font(bold=True)
    sheet["C1"] = station
    sheet.column_dimensions["C"].width = max(sheet.column_dimensions["C"].width or 0, 16)


def _sample_number(row: dict[str, str], key: str) -> float | None:
    value = str(row.get(key, "")).strip()
    if not value:
        return None
    try:
        return _finite_or_none(float(value))
    except (TypeError, ValueError):
        return None


def _sample_series(row: dict[str, str], key: str) -> tuple[float, ...]:
    raw = str(row.get(key, "")).strip()
    if not raw:
        return ()
    try:
        # Tokenizes exactly like the previous split(";") loop, but parses the
        # whole per-second spectrum (thousands of points) in one numpy call.
        values = np.asarray(raw.split(";"), dtype=float)
    except ValueError:
        return ()
    if not bool(np.all(np.isfinite(values))):
        return ()
    return tuple(values.tolist())


def _read_designated_second_samples(path: Path) -> dict[float, list[dict[str, Any]]]:
    source = Path(path)
    if not source.is_file():
        return {}
    samples_by_current: dict[float, dict[int, dict[str, Any]]] = {}
    with source.open("r", newline="", encoding="utf-8-sig") as input_file:
        for row in csv.DictReader(input_file):
            target_current_a = _sample_number(row, "target_current_a")
            elapsed_s = _sample_number(row, "point_elapsed_s")
            if target_current_a is None or elapsed_s is None:
                continue
            elapsed_second = max(1, int(round(elapsed_s)))
            wavelengths = _sample_series(row, "spectrum_wavelengths_nm")
            intensities = _sample_series(row, "spectrum_intensities")
            if len(wavelengths) != len(intensities):
                wavelengths = ()
                intensities = ()
            efficiency_percent = _sample_number(row, "efficiency_percent")
            samples_by_second = samples_by_current.setdefault(target_current_a, {})
            samples_by_second[elapsed_second] = {
                "elapsed_second": elapsed_second,
                "power_w": _sample_number(row, "optical_power_w"),
                "efficiency": (
                    efficiency_percent / 100.0
                    if efficiency_percent is not None
                    else None
                ),
                "voltage_v": _sample_number(row, "output_voltage_v"),
                "peak_wavelength_nm": _sample_number(row, "peak_wavelength_nm"),
                "centroid_nm": _sample_number(row, "centroid_nm"),
                "fwhm_nm": _sample_number(row, "fwhm_nm"),
                "pib": _sample_number(row, "pib"),
                "wavelengths_nm": wavelengths,
                "intensities": intensities,
            }
    return {
        current_a: [samples_by_second[second] for second in sorted(samples_by_second)]
        for current_a, samples_by_second in sorted(samples_by_current.items())
    }


def _current_sheet_prefix(current_a: float) -> str:
    return f"{float(current_a):.12g}A"


def _create_xlsxwriter_formats(
    workbook: XlsxWriterWorkbook,
) -> dict[str, XlsxWriterFormat]:
    return {
        "bold": workbook.add_format({"bold": True}),
        "result_header": workbook.add_format({"bold": True, "align": "center"}),
        "data_header": workbook.add_format(
            {
                "bold": True,
                "align": "center",
                "valign": "vcenter",
                "bg_color": _DATA_HEADER_COLOR,
            }
        ),
        "integer": workbook.add_format({"num_format": "0"}),
        "number_2": workbook.add_format({"num_format": "0.00"}),
        "number_3": workbook.add_format({"num_format": "0.000"}),
        "number_6": workbook.add_format({"num_format": "0.000000"}),
        "percent_2": workbook.add_format({"num_format": "0.00%"}),
    }


def _write_xlsx_number(
    sheet: XlsxWriterWorksheet,
    row: int,
    column: int,
    value: Any,
    cell_format: XlsxWriterFormat,
) -> None:
    number = None if value is None else _finite_or_none(value)
    if number is None:
        sheet.write_blank(row, column, None, cell_format)
    else:
        sheet.write_number(row, column, number, cell_format)


def _write_xlsx_test_data_sheet(
    workbook: XlsxWriterWorkbook,
    formats: dict[str, XlsxWriterFormat],
    records_by_current: dict[float, tuple[ExcelTestRecord, list[float], list[float]]],
    test_station: str,
) -> None:
    sheet = workbook.add_worksheet("测试数据")
    sheet.freeze_panes(2, 0)
    sheet.set_column(0, 3, 12)
    sheet.set_column(4, 5, 16)
    sheet.set_column(6, 8, 12)
    sheet.write(0, 0, "LIV", formats["bold"])
    sheet.write(0, 9, "光谱", formats["bold"])
    if test_station:
        sheet.write(0, 1, "测试站别", formats["bold"])
        sheet.write(0, 2, test_station)
        sheet.set_column(2, 2, 16)
    sheet.write_row(1, 0, RESULT_HEADERS, formats["result_header"])

    record_items = sorted(records_by_current.items())
    spectra = [
        (current, wavelengths, intensities)
        for current, (_record, wavelengths, intensities) in record_items
        if wavelengths
    ]
    for spectrum_index, (current, _wavelengths, _intensities) in enumerate(spectra):
        spectrum_column = 9 + spectrum_index * 2
        sheet.write(1, spectrum_column, f"{current:.1f}A")
        sheet.set_column(spectrum_column, spectrum_column + 1, 14)

    result_formats = (
        formats["number_3"],
        formats["number_3"],
        formats["number_3"],
        formats["percent_2"],
        formats["number_3"],
        formats["number_3"],
        formats["number_3"],
        formats["percent_2"],
        formats["number_2"],
    )
    maximum_spectrum_points = max((len(wavelengths) for _, wavelengths, _ in spectra), default=0)
    for data_index in range(max(len(record_items), maximum_spectrum_points)):
        row = data_index + 2
        if data_index < len(record_items):
            record = record_items[data_index][1][0]
            values = (
                record.current_a,
                record.voltage_v,
                record.power_w,
                record.efficiency,
                record.peak_wavelength_nm,
                record.centroid_nm,
                record.fwhm_nm,
                record.pib,
                record.smsr_db,
            )
            for column, (value, cell_format) in enumerate(zip(values, result_formats)):
                _write_xlsx_number(sheet, row, column, value, cell_format)
        for spectrum_index, (_current, wavelengths, intensities) in enumerate(spectra):
            if data_index >= len(wavelengths):
                continue
            spectrum_column = 9 + spectrum_index * 2
            _write_xlsx_number(
                sheet,
                row,
                spectrum_column,
                wavelengths[data_index],
                formats["number_6"],
            )
            _write_xlsx_number(
                sheet,
                row,
                spectrum_column + 1,
                intensities[data_index],
                formats["number_6"],
            )


def _write_xlsx_session_information(
    workbook: XlsxWriterWorkbook,
    formats: dict[str, XlsxWriterFormat],
    session: Any,
) -> None:
    sheet = workbook.add_worksheet("会话信息")
    sheet.set_column(0, 0, 24)
    sheet.set_column(1, 1, 72)
    rows = (
        ("出货报告格式版本", EXPORT_WORKBOOK_SCHEMA_VERSION),
        ("会话编号", getattr(session, "session_id", "")),
        ("SN", getattr(session, "sn", "")),
        ("产品型号", getattr(session, "product_model", "")),
        ("批次", getattr(session, "batch", "")),
        ("测试站别", getattr(session, "station", "")),
        ("测试模式", getattr(session, "mode", "")),
        ("开始时间 UTC", getattr(session, "started_at_utc", "")),
        ("结束时间 UTC", getattr(session, "ended_at_utc", "") or ""),
        (
            "流程状态",
            getattr(
                getattr(session, "status", ""),
                "value",
                getattr(session, "status", ""),
            ),
        ),
        ("结束原因", getattr(session, "termination_reason", "")),
        ("安全下电确认", getattr(session, "shutdown_confirmed", "")),
        ("软件版本", getattr(session, "software_version", "")),
        ("算法版本", getattr(session, "calculation_version", "")),
    )
    row = 0
    for label, value in rows:
        sheet.write(row, 0, label, formats["bold"])
        sheet.write(row, 1, str(value))
        row += 1
    row += 1
    sheet.write(row, 0, "测试参数", formats["bold"])
    for key, value in sorted(dict(getattr(session, "settings", {}) or {}).items()):
        row += 1
        sheet.write(row, 0, str(key))
        sheet.write(row, 1, str(value))
    row += 2
    sheet.write(row, 0, "设备快照", formats["bold"])
    for device in tuple(getattr(session, "devices", ()) or ()):
        row += 1
        if isinstance(device, dict):
            label = device.get("role", "设备")
            value = " | ".join(
                str(device.get(key, ""))
                for key in ("kind", "resource", "detail")
                if str(device.get(key, "")).strip()
            )
        else:
            label = getattr(device, "role", "设备")
            value = str(device)
        sheet.write(row, 0, str(label))
        sheet.write(row, 1, value)
    sheet.hide()


def _write_xlsx_measurement_attempts(
    workbook: XlsxWriterWorkbook,
    formats: dict[str, XlsxWriterFormat],
    attempts: tuple[Any, ...],
) -> None:
    sheet = workbook.add_worksheet("测量尝试")
    headers = (
        "点序号",
        "目标电流(A)",
        "尝试次数",
        "时间 UTC",
        "数据状态",
        "原因",
        "采用",
        "电压(V)",
        "功率(W)",
        "效率",
        "中心波长(nm)",
        "FWHM(nm)",
        "PIB",
        "SMSR(dB)",
        "判稳波动(W)",
        "判稳窗口(s)",
        "积分时间(us)",
        "光谱文件",
    )
    sheet.freeze_panes(1, 0)
    sheet.set_column(0, len(headers) - 1, 16)
    sheet.set_column(3, 3, 28)
    sheet.set_column(5, 5, 42)
    sheet.set_column(17, 17, 42)
    sheet.write_row(0, 0, headers, formats["result_header"])
    for row, attempt in enumerate(attempts, start=1):
        validity = getattr(attempt, "validity", "")
        integration_time_us = getattr(attempt, "integration_time_us", None)
        values = (
            int(getattr(attempt, "sequence_index", 0)) + 1,
            _finite_or_none(getattr(attempt, "target_current_a", math.nan)),
            int(getattr(attempt, "attempt_no", 1)),
            getattr(attempt, "created_at_utc", ""),
            getattr(validity, "value", validity),
            getattr(attempt, "invalid_reason", ""),
            "是" if bool(getattr(attempt, "selected", False)) else "否",
            _finite_or_none(getattr(attempt, "voltage_v", math.nan)),
            _finite_or_none(getattr(attempt, "power_w", math.nan)),
            _finite_or_none(getattr(attempt, "efficiency", math.nan)),
            _finite_or_none(getattr(attempt, "centroid_nm", math.nan)),
            _finite_or_none(getattr(attempt, "fwhm_nm", math.nan)),
            _finite_or_none(getattr(attempt, "pib", math.nan)),
            _finite_or_none(getattr(attempt, "smsr_db", math.nan)),
            _finite_or_none(getattr(attempt, "stable_span_w", math.nan)),
            _finite_or_none(getattr(attempt, "stable_window_s", math.nan)),
            None if integration_time_us is None else int(integration_time_us),
            getattr(attempt, "spectrum_path", ""),
        )
        sheet.write_row(row, 0, values)
    sheet.hide()


def _write_xlsx_designated_second_sheets(
    workbook: XlsxWriterWorkbook,
    formats: dict[str, XlsxWriterFormat],
    samples_by_current: dict[float, list[dict[str, Any]]],
) -> None:
    summary_formats = (
        formats["integer"],
        formats["number_6"],
        formats["percent_2"],
        formats["number_6"],
        formats["number_6"],
        formats["number_6"],
        formats["number_6"],
        formats["percent_2"],
    )
    for current_a, samples in samples_by_current.items():
        sheet_prefix = _current_sheet_prefix(current_a)
        summary_sheet = workbook.add_worksheet(f"{sheet_prefix}逐秒数据")
        summary_sheet.freeze_panes(1, 0)
        for column, width in enumerate((12, 16, 14, 16, 18, 18, 14, 12)):
            summary_sheet.set_column(column, column, width)
        summary_sheet.autofilter(0, 0, len(samples), len(SECOND_SAMPLE_HEADERS) - 1)
        summary_sheet.write_row(0, 0, SECOND_SAMPLE_HEADERS, formats["data_header"])
        for row, sample in enumerate(samples, start=1):
            values = (
                sample["elapsed_second"],
                sample["power_w"],
                sample["efficiency"],
                sample["voltage_v"],
                sample["peak_wavelength_nm"],
                sample["centroid_nm"],
                sample["fwhm_nm"],
                sample["pib"],
            )
            for column, (value, cell_format) in enumerate(zip(values, summary_formats)):
                _write_xlsx_number(summary_sheet, row, column, value, cell_format)

        spectrum_sheet = workbook.add_worksheet(f"{sheet_prefix}光谱数据")
        spectrum_sheet.freeze_panes(1, 0)
        for sample_index, sample in enumerate(samples):
            wavelength_column = sample_index * 2
            elapsed_second = sample["elapsed_second"]
            spectrum_sheet.set_column(wavelength_column, wavelength_column, 16)
            spectrum_sheet.set_column(wavelength_column + 1, wavelength_column + 1, 18)
            spectrum_sheet.write(
                0,
                wavelength_column,
                f"{elapsed_second}s波长(nm)",
                formats["data_header"],
            )
            spectrum_sheet.write(
                0,
                wavelength_column + 1,
                f"{elapsed_second}s强度(counts)",
                formats["data_header"],
            )
        maximum_spectrum_points = max(
            (len(sample["wavelengths_nm"]) for sample in samples),
            default=0,
        )
        for point_index in range(maximum_spectrum_points):
            row = point_index + 1
            row_values: list[Any] = []
            for sample in samples:
                wavelengths = sample["wavelengths_nm"]
                if point_index < len(wavelengths):
                    row_values.append(round(float(wavelengths[point_index]), 3))
                    row_values.append(round(float(sample["intensities"][point_index]), 3))
                else:
                    row_values.append(None)
                    row_values.append(None)
            # One batched row across every sample's wavelength/intensity
            # column pair. xlsxwriter 3.x applies a single format per row;
            # None cells become number_3 blanks instead of being skipped,
            # which is visually identical and keeps column alignment.
            spectrum_sheet.write_row(row, 0, row_values, formats["number_3"])


def _discard_xlsxwriter_workbook(
    workbook: XlsxWriterWorkbook,
    temporary: Path,
) -> None:
    try:
        workbook.close()
    except Exception:
        pass
    try:
        temporary.unlink(missing_ok=True)
    except OSError:
        pass


def save_test_records(
    path: Path,
    records: Iterable[ExcelTestRecord],
    *,
    session: Any | None = None,
    attempts: Iterable[Any] = (),
    automatic_samples_path: Path | None = None,
) -> Path:
    """Generate a complete current-sorted workbook with XlsxWriter."""
    records_by_current: dict[float, tuple[ExcelTestRecord, list[float], list[float]]] = {}
    for record in records:
        wavelength_values = [float(value) for value in record.wavelength]
        intensity_values = [float(value) for value in record.intensity]
        if len(wavelength_values) != len(intensity_values):
            raise ValueError("波长和强度数据长度必须一致")
        records_by_current[float(record.current_a)] = (record, wavelength_values, intensity_values)
    if not records_by_current:
        raise ValueError("至少需要一个测试记录")

    test_stations = {
        str(record.test_station).strip()
        for record, _wavelength, _intensity in records_by_current.values()
        if str(record.test_station).strip()
    }
    if len(test_stations) > 1:
        raise ValueError("同一测试文件中的测试站别必须一致")

    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    attempt_values = tuple(attempts)
    samples_by_current = (
        _read_designated_second_samples(automatic_samples_path)
        if automatic_samples_path is not None
        else {}
    )
    temporary = target.with_name(f".{target.stem}.tmp.xlsx")
    temporary.unlink(missing_ok=True)
    workbook = XlsxWriterWorkbook(
        str(temporary),
        {
            "constant_memory": True,
            "strings_to_urls": False,
        },
    )
    try:
        formats = _create_xlsxwriter_formats(workbook)
        _write_xlsx_test_data_sheet(
            workbook,
            formats,
            records_by_current,
            next(iter(test_stations)) if test_stations else "",
        )
        if session is not None:
            _write_xlsx_session_information(workbook, formats, session)
        if attempt_values:
            _write_xlsx_measurement_attempts(workbook, formats, attempt_values)
        _write_xlsx_designated_second_sheets(workbook, formats, samples_by_current)
        workbook.close()
    except Exception:
        _discard_xlsxwriter_workbook(workbook, temporary)
        raise

    try:
        _replace_file_with_retry(temporary, target)
    except PermissionError as exc:
        raise RuntimeError(
            f"Excel 文件暂时被占用，无法更新 {target.name}；"
            f"完整临时文件已保留：{temporary}"
        ) from exc
    return target


def append_test_record(path: Path, record: ExcelTestRecord) -> None:
    wavelength_values = [float(value) for value in record.wavelength]
    intensity_values = [float(value) for value in record.intensity]
    if len(wavelength_values) != len(intensity_values):
        raise ValueError("波长和强度数据长度必须一致")

    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    workbook = (
        load_workbook(target)
        if target.exists()
        else _create_openpyxl_workbook()
    )
    sheet = workbook[workbook.sheetnames[0]]
    _ensure_result_headers(sheet)
    existing_station = str(sheet["C1"].value or "").strip() if sheet["B1"].value == "测试站别" else ""
    record_station = str(record.test_station).strip()
    if existing_station and record_station and existing_station != record_station:
        workbook.close()
        raise ValueError("同一测试文件中的测试站别必须一致")
    _write_test_station(sheet, record_station or existing_station)

    result_by_current: dict[float, tuple[float | None, ...]] = {}
    row = 3
    while sheet.cell(row=row, column=1).value is not None:
        current = float(sheet.cell(row=row, column=1).value)
        result_by_current[current] = tuple(sheet.cell(row=row, column=column).value for column in range(1, 10))
        row += 1
    previous_result_end_row = row - 1
    result_by_current[float(record.current_a)] = (
        record.current_a,
        record.voltage_v,
        record.power_w,
        record.efficiency,
        record.peak_wavelength_nm,
        record.centroid_nm,
        record.fwhm_nm,
        record.pib,
        record.smsr_db,
    )

    for clear_row in range(3, previous_result_end_row + 1):
        for column in range(1, 10):
            sheet.cell(row=clear_row, column=column).value = None
    for result_row, current in enumerate(sorted(result_by_current), start=3):
        for column, value in enumerate(result_by_current[current], start=1):
            sheet.cell(row=result_row, column=column, value=_finite_or_none(value) if value is not None else None)
        for column in (1, 2, 3, 5, 6, 7):
            sheet.cell(row=result_row, column=column).number_format = "0.000"
        for column in (4, 8):
            sheet.cell(row=result_row, column=column).number_format = "0.00%"
        sheet.cell(row=result_row, column=9).number_format = "0.00"

    spectra_by_current: dict[float, list[tuple[float | None, float | None]]] = {}
    previous_max_row = sheet.max_row
    previous_max_column = sheet.max_column
    for spectrum_column in range(10, previous_max_column + 1, 2):
        label = sheet.cell(row=2, column=spectrum_column).value
        if label is None:
            continue
        match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*A\s*", str(label), re.IGNORECASE)
        if match is None:
            continue
        current = float(match.group(1))
        points: list[tuple[float | None, float | None]] = []
        for spectrum_row in range(3, previous_max_row + 1):
            wavelength_nm = sheet.cell(row=spectrum_row, column=spectrum_column).value
            intensity = sheet.cell(row=spectrum_row, column=spectrum_column + 1).value
            if wavelength_nm is None and intensity is None:
                continue
            points.append((wavelength_nm, intensity))
        spectra_by_current[current] = points
    if wavelength_values:
        spectra_by_current[float(record.current_a)] = list(zip(wavelength_values, intensity_values))
    else:
        spectra_by_current.pop(float(record.current_a), None)

    for clear_row in range(2, previous_max_row + 1):
        for column in range(10, previous_max_column + 1):
            sheet.cell(row=clear_row, column=column).value = None
    sheet["J1"] = "光谱"
    for spectrum_index, current in enumerate(sorted(spectra_by_current)):
        spectrum_column = 10 + spectrum_index * 2
        sheet.cell(row=2, column=spectrum_column, value=f"{current:.1f}A")
        sheet.column_dimensions[sheet.cell(row=1, column=spectrum_column).column_letter].width = 14
        sheet.column_dimensions[sheet.cell(row=1, column=spectrum_column + 1).column_letter].width = 14
        for spectrum_row, (wavelength_nm, intensity) in enumerate(spectra_by_current[current], start=3):
            sheet.cell(row=spectrum_row, column=spectrum_column, value=_finite_or_none(wavelength_nm) if wavelength_nm is not None else None)
            sheet.cell(row=spectrum_row, column=spectrum_column + 1, value=_finite_or_none(intensity) if intensity is not None else None)
            sheet.cell(row=spectrum_row, column=spectrum_column).number_format = "0.000000"
            sheet.cell(row=spectrum_row, column=spectrum_column + 1).number_format = "0.000000"

    _save_openpyxl_workbook_atomically(workbook, target)
