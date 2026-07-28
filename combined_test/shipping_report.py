"""Excel-backed A4 shipping report inspection and PDF generation."""

from __future__ import annotations

import base64
import gc
import math
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from openpyxl import load_workbook
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPainterPath,
    QPdfWriter,
    QPen,
    QPolygonF,
)
from PySide6.QtPdf import QPdfDocument
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from .excel_export import EXPORT_WORKBOOK_SCHEMA_VERSION, sanitize_sn


REPORT_WORKBOOK_SCHEMA_VERSION = EXPORT_WORKBOOK_SCHEMA_VERSION
REPORT_DATA_SHEET = "测试数据"
REPORT_SESSION_SHEET = "会话信息"
_REPORT_QT_APPLICATION: QApplication | None = None


_RESULT_COLUMN_ALIASES = {
    "current": ("电流", "工作电流", "设定电流", "current", "operating current", "current a"),
    "voltage": ("电压", "工作电压", "输出电压", "voltage", "operating voltage", "voltage v"),
    "power": ("功率", "输出功率", "光功率", "power", "output power", "optical power", "power w"),
    "efficiency": (
        "电光效率",
        "光电效率",
        "转换效率",
        "效率",
        "efficiency",
        "e-o efficiency",
        "eo efficiency",
        "electro optical efficiency",
        "wall plug efficiency",
    ),
    "peak_wavelength": ("中心波长", "峰值波长", "peak wavelength", "center wavelength", "central wavelength"),
    "centroid": ("质心波长", "光谱质心", "centroid", "centroid wavelength"),
    "fwhm": ("fwhm", "半高全宽", "光谱宽度", "spectral width"),
    "pib": ("pib", "桶中功率", "power in bucket"),
    "smsr": ("smsr", "边模抑制比", "side mode suppression ratio"),
}
_REQUIRED_RESULT_COLUMNS = frozenset(("current", "voltage", "power", "efficiency"))


@dataclass(frozen=True)
class _WorkbookDataLayout:
    sheet: Any
    header_row: int
    columns: Mapping[str, int]
    detected_from_headers: bool


class ShippingReportType(str, Enum):
    SPECTRUM = "spectrum"
    POLE = "pole"


class SpectrumAxisMode(str, Enum):
    COUNTS = "counts"
    RELATIVE_DB = "relative_db"


@dataclass(frozen=True)
class ReportFieldDefinition:
    key: str
    label: str
    unit: str
    side: str
    measured_key: str | None = None


SPECTRUM_FIELD_DEFINITIONS = (
    ReportFieldDefinition("power", "Output Power (CW)", "W", "left", "powerW"),
    ReportFieldDefinition("operatingCurrent", "Operating Current", "A", "left", "currentA"),
    ReportFieldDefinition("centerWavelength", "Center Wavelength", "nm", "left", "peakWavelengthNm"),
    ReportFieldDefinition("fwhm", "Spectral Width (FWHM)", "nm", "left", "fwhmNm"),
    ReportFieldDefinition(
        "wavelengthCurrentCoefficient",
        "Wavelength-Current Coefficient",
        "nm/A",
        "left",
        "wavelengthCurrentCoefficientNmPerA",
    ),
    ReportFieldDefinition("coolingTemperature", "Cooling Temperature", "°C", "left"),
    ReportFieldDefinition("powerFluctuation", "Power Fluctuation", "%", "left"),
    ReportFieldDefinition("thresholdCurrent", "Threshold Current", "A", "right"),
    ReportFieldDefinition("operatingVoltage", "Operating Voltage", "V", "right", "voltageV"),
    ReportFieldDefinition("slopeEfficiency", "Slope Efficiency", "W/A", "right", "slopeEfficiencyWPerA"),
    ReportFieldDefinition("electroOpticalEfficiency", "E-O efficiency", "%", "right", "efficiencyPercent"),
    ReportFieldDefinition("na017Energy", "NA 0.17 Encircled Energy", "%", "right"),
    ReportFieldDefinition("fiberCoreDiameter", "Fiber Core Diameter", "μm", "right"),
    ReportFieldDefinition("fiberNa", "Fiber NA", "", "right"),
)

POLE_FIELD_DEFINITIONS = (
    ReportFieldDefinition("power", "Output Power", "W", "single", "powerW"),
    ReportFieldDefinition("operatingCurrent", "Operating Current", "A", "single", "currentA"),
    ReportFieldDefinition("operatingVoltage", "Operating Voltage", "V", "single", "voltageV"),
    ReportFieldDefinition("slopeEfficiency", "Slope Efficiency", "W/A", "single", "slopeEfficiencyWPerA"),
    ReportFieldDefinition("electroOpticalEfficiency", "E-O efficiency", "%", "single", "efficiencyPercent"),
    ReportFieldDefinition("coolingTemperature", "Cooling Temperature", "°C", "single"),
)

# The approved Pole report sample presents this complete six-row summary.
# Keep it as the first-use default while preserving any explicit user choice
# (including an intentionally saved empty selection) in QSettings.
DEFAULT_POLE_SELECTED_FIELD_KEYS = tuple(field.key for field in POLE_FIELD_DEFINITIONS)


@dataclass(frozen=True)
class ReportPoint:
    current_a: float
    voltage_v: float
    power_w: float
    efficiency: float
    peak_wavelength_nm: float | None
    centroid_nm: float | None
    fwhm_nm: float | None
    pib: float | None
    smsr_db: float | None
    wavelength: tuple[float, ...]
    intensity: tuple[float, ...]

    @property
    def has_complete_spectrum(self) -> bool:
        return (
            len(self.wavelength) >= 2
            and len(self.wavelength) == len(self.intensity)
            and all(math.isfinite(value) for value in self.wavelength)
            and all(math.isfinite(value) for value in self.intensity)
        )

    def suggestions(self) -> dict[str, float | None]:
        slope_efficiency = None
        if not math.isclose(self.current_a, 0.0, abs_tol=1e-12):
            calculated = self.power_w / self.current_a
            if math.isfinite(calculated):
                slope_efficiency = calculated
        spectrum_peak_counts = (
            max(self.intensity)
            if self.has_complete_spectrum and self.intensity
            else None
        )
        return {
            "currentA": self.current_a,
            "voltageV": self.voltage_v,
            "powerW": self.power_w,
            "slopeEfficiencyWPerA": slope_efficiency,
            "efficiencyPercent": self.efficiency * 100.0,
            "peakWavelengthNm": self.peak_wavelength_nm,
            "centroidNm": self.centroid_nm,
            "fwhmNm": self.fwhm_nm,
            "pibPercent": None if self.pib is None else self.pib * 100.0,
            "smsrDb": self.smsr_db,
            "spectrumPeakCounts": spectrum_peak_counts,
        }


@dataclass(frozen=True)
class WorkbookCompatibility:
    kind: str
    status: str
    message: str
    requires_legacy_confirmation: bool


@dataclass(frozen=True)
class WorkbookInspection:
    source_path: Path
    schema_version: str
    sn: str
    product_name: str
    session: Mapping[str, str]
    points: tuple[ReportPoint, ...]
    compatibility: WorkbookCompatibility

    @property
    def spectrum_complete(self) -> bool:
        return bool(self.points) and all(point.has_complete_spectrum for point in self.points)

    @property
    def spectrum_wavelength_range(self) -> tuple[float, float] | None:
        values = [
            value
            for point in self.points
            for value in point.wavelength
            if math.isfinite(value)
        ]
        if len(values) < 2:
            return None
        minimum, maximum = min(values), max(values)
        return (minimum, minimum + 1.0) if math.isclose(minimum, maximum) else (minimum, maximum)

    @property
    def wavelength_current_coefficient_nm_per_a(self) -> float | None:
        """Return the least-squares slope of center wavelength versus current."""

        samples = [
            (point.current_a, point.peak_wavelength_nm)
            for point in self.points
            if point.peak_wavelength_nm is not None
            and math.isfinite(point.current_a)
            and math.isfinite(point.peak_wavelength_nm)
        ]
        if len(samples) < 2:
            return None
        mean_current = sum(current for current, _wavelength in samples) / len(samples)
        mean_wavelength = sum(wavelength for _current, wavelength in samples) / len(samples)
        denominator = sum((current - mean_current) ** 2 for current, _wavelength in samples)
        if math.isclose(denominator, 0.0, abs_tol=1e-18):
            return None
        slope = sum(
            (current - mean_current) * (wavelength - mean_wavelength)
            for current, wavelength in samples
        ) / denominator
        return slope if math.isfinite(slope) else None

    @property
    def allowed_report_types(self) -> tuple[str, ...]:
        return (
            (ShippingReportType.SPECTRUM.value, ShippingReportType.POLE.value)
            if self.spectrum_complete
            else (ShippingReportType.POLE.value,)
        )

    def point_for_current(self, current_a: float) -> ReportPoint:
        requested = float(current_a)
        for point in self.points:
            if math.isclose(point.current_a, requested, rel_tol=0.0, abs_tol=1e-9):
                return point
        raise ValueError(f"Excel 中不存在 {requested:g} A 测试点")

    def to_payload(self) -> dict[str, Any]:
        spectrum_wavelength_range = self.spectrum_wavelength_range
        wavelength_current_coefficient = self.wavelength_current_coefficient_nm_per_a
        return {
            "sourcePath": str(self.source_path),
            "schemaVersion": self.schema_version,
            "sn": self.sn,
            "productName": self.product_name,
            "currents": [point.current_a for point in self.points],
            "points": [
                {
                    **point.suggestions(),
                    "wavelengthCurrentCoefficientNmPerA": wavelength_current_coefficient,
                    "hasSpectrum": point.has_complete_spectrum,
                }
                for point in self.points
            ],
            "spectrumComplete": self.spectrum_complete,
            "spectrumWavelengthRange": (
                {"minimum": spectrum_wavelength_range[0], "maximum": spectrum_wavelength_range[1]}
                if spectrum_wavelength_range is not None
                else None
            ),
            "allowedReportTypes": list(self.allowed_report_types),
            "compatibility": {
                "kind": self.compatibility.kind,
                "status": self.compatibility.status,
                "message": self.compatibility.message,
                "requiresLegacyConfirmation": self.compatibility.requires_legacy_confirmation,
            },
            "fieldDefinitions": {
                ShippingReportType.SPECTRUM.value: [_field_payload(item) for item in SPECTRUM_FIELD_DEFINITIONS],
                ShippingReportType.POLE.value: [_field_payload(item) for item in POLE_FIELD_DEFINITIONS],
            },
        }


@dataclass(frozen=True)
class BatchWorkbookInspection:
    source_path: Path
    modified_time_ns: int
    sn: str
    product_name: str
    currents: tuple[float, ...]
    allowed_report_types: tuple[str, ...]
    compatibility: WorkbookCompatibility | None
    status: str
    message: str

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def to_payload(self) -> dict[str, Any]:
        compatibility = self.compatibility
        return {
            "sourcePath": str(self.source_path),
            "modifiedAtMs": self.modified_time_ns / 1_000_000,
            "sn": self.sn,
            "productName": self.product_name,
            "currents": list(self.currents),
            "allowedReportTypes": list(self.allowed_report_types),
            "status": self.status,
            "message": self.message,
            "compatibility": (
                {
                    "kind": compatibility.kind,
                    "status": compatibility.status,
                    "message": compatibility.message,
                    "requiresLegacyConfirmation": compatibility.requires_legacy_confirmation,
                }
                if compatibility is not None
                else None
            ),
        }


@dataclass(frozen=True)
class ShippingReportBatchInspection:
    root_path: Path
    items: tuple[BatchWorkbookInspection, ...]

    @property
    def ready_items(self) -> tuple[BatchWorkbookInspection, ...]:
        return tuple(item for item in self.items if item.ready)

    @property
    def union_currents(self) -> tuple[float, ...]:
        return tuple(sorted({current for item in self.ready_items for current in item.currents}))

    @property
    def common_currents(self) -> tuple[float, ...]:
        ready = self.ready_items
        if not ready:
            return ()
        common = set(ready[0].currents)
        for item in ready[1:]:
            common.intersection_update(item.currents)
        return tuple(sorted(common))

    def to_payload(self) -> dict[str, Any]:
        ready = self.ready_items
        return {
            "rootPath": str(self.root_path),
            "items": [item.to_payload() for item in self.items],
            "unionCurrents": list(self.union_currents),
            "commonCurrents": list(self.common_currents),
            "productNames": sorted({item.product_name for item in ready if item.product_name}),
            "allReadySupportSpectrum": bool(ready)
            and all(ShippingReportType.SPECTRUM.value in item.allowed_report_types for item in ready),
        }


@dataclass(frozen=True)
class SelectedReportField:
    include: bool
    value: str


@dataclass(frozen=True)
class SpectrumAxisSettings:
    mode: SpectrumAxisMode
    minimum: float
    maximum: float
    wavelength_minimum: float | None = None
    wavelength_maximum: float | None = None


@dataclass(frozen=True)
class ShippingReportRequest:
    report_type: ShippingReportType
    product_name: str
    sn: str
    operating_current_a: float
    fields: Mapping[str, SelectedReportField]
    legacy_completion_confirmed: bool
    custom_fields: tuple[ReportFieldDefinition, ...] = ()
    spectrum_axis: SpectrumAxisSettings | None = None

    @staticmethod
    def from_mapping(values: Mapping[str, Any]) -> "ShippingReportRequest":
        try:
            report_type = ShippingReportType(str(values.get("reportType", "")))
        except ValueError as exc:
            raise ValueError("请选择有效的报告类型") from exc
        raw_fields = values.get("fields")
        if not isinstance(raw_fields, Mapping):
            raise ValueError("报告参数格式无效")
        raw_custom_fields = values.get("customFields", [])
        if not isinstance(raw_custom_fields, list):
            raise ValueError("自定义报告参数格式无效")
        custom_fields: list[ReportFieldDefinition] = []
        default_side = "left" if report_type is ShippingReportType.SPECTRUM else "single"
        for raw in raw_custom_fields:
            if not isinstance(raw, Mapping):
                raise ValueError("自定义报告参数格式无效")
            custom_fields.append(
                ReportFieldDefinition(
                    key=str(raw.get("key", "")).strip(),
                    label=str(raw.get("label", "")).strip(),
                    unit=str(raw.get("unit", "")).strip(),
                    side=default_side,
                )
            )
        fields: dict[str, SelectedReportField] = {}
        for key, raw in raw_fields.items():
            if not isinstance(raw, Mapping):
                raise ValueError(f"参数 {key} 的格式无效")
            fields[str(key)] = SelectedReportField(
                include=bool(raw.get("include")),
                value=str(raw.get("value", "")).strip(),
            )
        spectrum_axis: SpectrumAxisSettings | None = None
        if report_type is ShippingReportType.SPECTRUM:
            raw_axis = values.get("spectrumAxis")
            if not isinstance(raw_axis, Mapping):
                raise ValueError("请填写光谱纵轴设置")
            try:
                spectrum_axis = SpectrumAxisSettings(
                    mode=SpectrumAxisMode(str(raw_axis.get("mode", ""))),
                    minimum=float(raw_axis.get("minimum")),
                    maximum=float(raw_axis.get("maximum")),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("光谱纵轴设置无效") from exc
            raw_wavelength_minimum = raw_axis.get("wavelengthMinimum")
            raw_wavelength_maximum = raw_axis.get("wavelengthMaximum")
            if raw_wavelength_minimum is not None or raw_wavelength_maximum is not None:
                try:
                    if raw_wavelength_minimum is None or raw_wavelength_maximum is None:
                        raise ValueError
                    spectrum_axis = SpectrumAxisSettings(
                        mode=spectrum_axis.mode,
                        minimum=spectrum_axis.minimum,
                        maximum=spectrum_axis.maximum,
                        wavelength_minimum=float(raw_wavelength_minimum),
                        wavelength_maximum=float(raw_wavelength_maximum),
                    )
                except (TypeError, ValueError) as exc:
                    raise ValueError("光谱横轴设置无效") from exc
        try:
            operating_current_a = float(values.get("operatingCurrentA"))
        except (TypeError, ValueError) as exc:
            raise ValueError("请选择工作电流点") from exc
        return ShippingReportRequest(
            report_type=report_type,
            product_name=str(values.get("productName", "")).strip(),
            sn=str(values.get("sn", "")).strip(),
            operating_current_a=operating_current_a,
            fields=fields,
            legacy_completion_confirmed=bool(values.get("legacyCompletionConfirmed")),
            custom_fields=tuple(custom_fields),
            spectrum_axis=spectrum_axis,
        )


def _field_payload(field: ReportFieldDefinition) -> dict[str, str | None]:
    return {
        "key": field.key,
        "label": field.label,
        "unit": field.unit,
        "side": field.side,
        "measuredKey": field.measured_key,
    }


def _finite_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _required_number(value: Any, name: str) -> float:
    result = _finite_or_none(value)
    if result is None:
        raise ValueError(f"{name} 必须是有效数值")
    return result


def _report_definitions(
    report_type: ShippingReportType,
    custom_fields: Iterable[ReportFieldDefinition] = (),
) -> tuple[ReportFieldDefinition, ...]:
    standard = SPECTRUM_FIELD_DEFINITIONS if report_type is ShippingReportType.SPECTRUM else POLE_FIELD_DEFINITIONS
    return (*standard, *tuple(custom_fields))


def _parse_session_information(workbook: Any) -> dict[str, str]:
    if REPORT_SESSION_SHEET not in workbook.sheetnames:
        return {}
    sheet = workbook[REPORT_SESSION_SHEET]
    output: dict[str, str] = {}
    for row in sheet.iter_rows(min_col=1, max_col=2, values_only=True):
        label, value = row
        if label is None:
            continue
        output[str(label).strip()] = "" if value is None else str(value).strip()
    return output


def _normalize_result_header(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower().replace("％", "%")
    text = re.sub(r"[（(][^）)]*[）)]", " ", text)
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


_NORMALIZED_RESULT_COLUMN_ALIASES = {
    key: frozenset(_normalize_result_header(alias) for alias in aliases)
    for key, aliases in _RESULT_COLUMN_ALIASES.items()
}


def _result_column_key(value: Any) -> str | None:
    normalized = _normalize_result_header(value)
    if not normalized:
        return None
    for key, aliases in _NORMALIZED_RESULT_COLUMN_ALIASES.items():
        if normalized in aliases:
            return key
    return None


def _find_workbook_data_layout(workbook: Any) -> _WorkbookDataLayout:
    candidates: list[tuple[int, int, int, _WorkbookDataLayout]] = []
    for sheet in workbook.worksheets:
        if sheet.title == REPORT_SESSION_SHEET:
            continue
        scan_columns = min(sheet.max_column, 200)
        nonempty_rows_scanned = 0
        for row in range(1, sheet.max_row + 1):
            columns: dict[str, int] = {}
            row_has_content = False
            for column in range(1, scan_columns + 1):
                value = sheet.cell(row=row, column=column).value
                row_has_content = row_has_content or value not in (None, "")
                key = _result_column_key(value)
                if key is not None and key not in columns:
                    columns[key] = column
            if row_has_content:
                nonempty_rows_scanned += 1
            if not _REQUIRED_RESULT_COLUMNS.issubset(columns):
                if nonempty_rows_scanned >= 100:
                    break
                continue
            optional_count = len(columns) - len(_REQUIRED_RESULT_COLUMNS)
            numeric_rows = 0
            for data_row in range(row + 1, min(sheet.max_row, row + 20) + 1):
                if _finite_or_none(sheet.cell(data_row, columns["current"]).value) is not None:
                    numeric_rows += 1
            candidates.append(
                (
                    len(columns),
                    numeric_rows,
                    optional_count,
                    _WorkbookDataLayout(sheet, row, columns, True),
                )
            )
            break
    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return candidates[0][3]

    # Headerless exports produced by older ARP versions cannot be identified by
    # column names. Preserve their historical fixed-layout fallback only when
    # no header-driven layout exists anywhere in the workbook.
    sheet = workbook[REPORT_DATA_SHEET] if REPORT_DATA_SHEET in workbook.sheetnames else workbook[workbook.sheetnames[0]]
    return _WorkbookDataLayout(
        sheet=sheet,
        header_row=2,
        columns={
            "current": 1,
            "voltage": 2,
            "power": 3,
            "efficiency": 4,
            "peak_wavelength": 5,
            "centroid": 6,
            "fwhm": 7,
            "pib": 8,
            "smsr": 9,
        },
        detected_from_headers=False,
    )


def _compatibility(session: Mapping[str, str], schema_version: str) -> WorkbookCompatibility:
    status = str(session.get("流程状态", "")).strip().lower()
    explicit_failures = {"stopped_by_operator", "aborted_safely", "failed", "error"}
    if status in explicit_failures:
        return WorkbookCompatibility(
            kind="rejected",
            status=status,
            message="该 Excel 明确记录为中止或失败测试，不能生成出货报告。",
            requires_legacy_confirmation=False,
        )
    if schema_version and schema_version != REPORT_WORKBOOK_SCHEMA_VERSION:
        return WorkbookCompatibility(
            kind="rejected",
            status=status,
            message=f"该 Excel 的出货报告格式版本 {schema_version} 不受当前软件支持。",
            requires_legacy_confirmation=False,
        )
    if schema_version == REPORT_WORKBOOK_SCHEMA_VERSION:
        if status == "completed":
            return WorkbookCompatibility(
                kind="verified_success",
                status=status,
                message="已验证为完整成功测试。",
                requires_legacy_confirmation=False,
            )
        return WorkbookCompatibility(
            kind="rejected",
            status=status,
            message="该 Excel 尚未记录完整成功状态，不能生成出货报告。",
            requires_legacy_confirmation=False,
        )
    return WorkbookCompatibility(
        kind="legacy_needs_confirmation",
        status=status,
        message="旧版 Excel 无法可靠验证完成状态，请确认该文件来自完整成功测试。",
        requires_legacy_confirmation=True,
    )


def inspect_shipping_workbook(path: Path | str) -> WorkbookInspection:
    source = Path(path).expanduser().resolve()
    if source.suffix.lower() != ".xlsx":
        raise ValueError("请选择 .xlsx 测试文件")
    if not source.is_file():
        raise ValueError("所选 Excel 文件不存在")
    try:
        # Random access is required to pair result rows with many spectrum
        # columns. Normal mode avoids repeatedly re-streaming the worksheet for
        # every cell, which becomes prohibitively slow for real spectrometers.
        workbook = load_workbook(source, read_only=False, data_only=True)
    except Exception as exc:
        raise ValueError(f"无法读取 Excel：{exc}") from exc
    try:
        session = _parse_session_information(workbook)
        schema_version = str(session.get("出货报告格式版本", "")).strip()
        layout = _find_workbook_data_layout(workbook)
        sheet = layout.sheet
        columns = layout.columns

        def cell_value(row: int, key: str) -> Any:
            column = columns.get(key)
            return None if column is None else sheet.cell(row=row, column=column).value

        points_by_current: dict[float, ReportPoint] = {}
        for row in range(layout.header_row + 1, sheet.max_row + 1):
            raw_current = cell_value(row, "current")
            if raw_current is None or str(raw_current).strip() == "":
                continue
            current = _finite_or_none(raw_current)
            if current is None:
                if layout.detected_from_headers:
                    continue
                raise ValueError(f"第 {row} 行电流必须是有效数值")
            if current in points_by_current:
                raise ValueError(f"Excel 中存在重复电流点：{current:g} A")
            point = ReportPoint(
                current_a=current,
                voltage_v=_required_number(cell_value(row, "voltage"), f"{current:g} A 电压"),
                power_w=_required_number(cell_value(row, "power"), f"{current:g} A 功率"),
                efficiency=_required_number(cell_value(row, "efficiency"), f"{current:g} A 效率"),
                peak_wavelength_nm=_finite_or_none(cell_value(row, "peak_wavelength")),
                centroid_nm=_finite_or_none(cell_value(row, "centroid")),
                fwhm_nm=_finite_or_none(cell_value(row, "fwhm")),
                pib=_finite_or_none(cell_value(row, "pib")),
                smsr_db=_finite_or_none(cell_value(row, "smsr")),
                wavelength=(),
                intensity=(),
            )
            points_by_current[current] = point
        if not points_by_current:
            raise ValueError("Excel 中没有有效测试点")

        spectra: dict[float, tuple[tuple[float, ...], tuple[float, ...]]] = {}
        result_columns = set(columns.values())
        for column in range(1, sheet.max_column):
            if column in result_columns or column + 1 in result_columns:
                continue
            label = sheet.cell(row=layout.header_row, column=column).value
            if label is None:
                continue
            match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*A\s*", str(label), re.IGNORECASE)
            if match is None:
                continue
            current = float(match.group(1))
            if current in spectra:
                raise ValueError(f"Excel 中存在重复光谱列：{current:g} A")
            wavelength: list[float] = []
            intensity: list[float] = []
            invalid_pair = False
            for spectrum_row in range(layout.header_row + 1, sheet.max_row + 1):
                x = _finite_or_none(sheet.cell(row=spectrum_row, column=column).value)
                y = _finite_or_none(sheet.cell(row=spectrum_row, column=column + 1).value)
                if x is None and y is None:
                    continue
                if x is None or y is None:
                    invalid_pair = True
                    break
                wavelength.append(x)
                intensity.append(y)
            spectra[current] = ((), ()) if invalid_pair else (tuple(wavelength), tuple(intensity))

        points = []
        for current in sorted(points_by_current):
            point = points_by_current[current]
            wavelength, intensity = spectra.get(current, ((), ()))
            points.append(
                ReportPoint(
                    current_a=point.current_a,
                    voltage_v=point.voltage_v,
                    power_w=point.power_w,
                    efficiency=point.efficiency,
                    peak_wavelength_nm=point.peak_wavelength_nm,
                    centroid_nm=point.centroid_nm,
                    fwhm_nm=point.fwhm_nm,
                    pib=point.pib,
                    smsr_db=point.smsr_db,
                    wavelength=wavelength,
                    intensity=intensity,
                )
            )
        return WorkbookInspection(
            source_path=source,
            schema_version=schema_version,
            sn=session.get("SN", "").strip(),
            product_name=session.get("产品型号", "").strip(),
            session=session,
            points=tuple(points),
            compatibility=_compatibility(session, schema_version),
        )
    finally:
        workbook.close()


def inspect_shipping_report_batch(root_path: Path | str) -> ShippingReportBatchInspection:
    root = Path(root_path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("请选择有效的 Excel 批量根目录")

    source_paths = sorted(
        (
            path.resolve()
            for path in root.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() == ".xlsx"
            and not path.name.startswith("~$")
        ),
        key=lambda path: str(path).casefold(),
    )
    candidates: list[BatchWorkbookInspection] = []
    for source_path in source_paths:
        try:
            modified_time_ns = source_path.stat().st_mtime_ns
        except OSError as exc:
            candidates.append(
                BatchWorkbookInspection(
                    source_path=source_path,
                    modified_time_ns=0,
                    sn="",
                    product_name="",
                    currents=(),
                    allowed_report_types=(),
                    compatibility=None,
                    status="invalid",
                    message=f"无法读取文件信息：{exc}",
                )
            )
            continue
        try:
            inspection = inspect_shipping_workbook(source_path)
        except Exception as exc:
            candidates.append(
                BatchWorkbookInspection(
                    source_path=source_path,
                    modified_time_ns=modified_time_ns,
                    sn="",
                    product_name="",
                    currents=(),
                    allowed_report_types=(),
                    compatibility=None,
                    status="invalid",
                    message=str(exc),
                )
            )
            continue
        if not inspection.sn:
            status = "invalid"
            message = "Excel 会话信息中缺少 SN"
        else:
            status = "candidate"
            message = inspection.compatibility.message
        candidates.append(
            BatchWorkbookInspection(
                source_path=source_path,
                modified_time_ns=modified_time_ns,
                sn=inspection.sn,
                product_name=inspection.product_name,
                currents=tuple(point.current_a for point in inspection.points),
                allowed_report_types=inspection.allowed_report_types,
                compatibility=inspection.compatibility,
                status=status,
                message=message,
            )
        )

    by_sn: dict[str, list[int]] = {}
    for index, item in enumerate(candidates):
        if item.status == "candidate":
            by_sn.setdefault(item.sn, []).append(index)
    for indices in by_sn.values():
        selected_index = max(
            indices,
            key=lambda index: (candidates[index].modified_time_ns, str(candidates[index].source_path)),
        )
        selected = candidates[selected_index]
        if selected.compatibility is not None and selected.compatibility.kind == "rejected":
            candidates[selected_index] = replace(selected, status="invalid")
        else:
            candidates[selected_index] = replace(selected, status="ready")
        for index in indices:
            if index == selected_index:
                continue
            candidates[index] = replace(
                candidates[index],
                status="ignored_duplicate",
                message=f"同一 SN 已选择较新的文件：{selected.source_path.name}",
            )
    return ShippingReportBatchInspection(root_path=root, items=tuple(candidates))


def validate_shipping_report_request(inspection: WorkbookInspection, request: ShippingReportRequest) -> None:
    if inspection.compatibility.kind == "rejected":
        raise ValueError(inspection.compatibility.message)
    if inspection.compatibility.requires_legacy_confirmation and not request.legacy_completion_confirmed:
        raise ValueError("请先确认旧版 Excel 来自完整成功测试")
    if not request.product_name:
        raise ValueError("产品名称不能为空")
    if not request.sn:
        raise ValueError("SN 不能为空")
    if request.report_type.value not in inspection.allowed_report_types:
        raise ValueError("该 Excel 的光谱数据不完整，只能生成 Pole 无光谱报告")
    inspection.point_for_current(request.operating_current_a)
    definitions = _report_definitions(request.report_type, request.custom_fields)
    standard_keys = {field.key for field in _report_definitions(request.report_type)}
    custom_keys = [field.key for field in request.custom_fields]
    if any(not field.key or not field.label for field in request.custom_fields):
        raise ValueError("自定义报告参数的名称不能为空")
    if len(custom_keys) != len(set(custom_keys)) or standard_keys.intersection(custom_keys):
        raise ValueError("自定义报告参数名称不能重复或使用已有参数名称")
    if len(request.custom_fields) > 30:
        raise ValueError("自定义报告参数不能超过 30 项")
    allowed_keys = {field.key for field in definitions}
    custom_key_set = set(custom_keys)
    included = []
    for key, selected in request.fields.items():
        if key not in allowed_keys:
            raise ValueError(f"未知报告参数：{key}")
        if selected.include:
            included.append(key)
            if not selected.value:
                definition = next(item for item in definitions if item.key == key)
                raise ValueError(f"请填写已勾选参数“{definition.label}”")
            if key not in custom_key_set:
                _required_number(selected.value, next(item.label for item in definitions if item.key == key))
    if not included:
        raise ValueError("请至少勾选一个报告参数")
    if request.report_type is ShippingReportType.SPECTRUM:
        axis = request.spectrum_axis
        if axis is None or not math.isfinite(axis.minimum) or not math.isfinite(axis.maximum):
            raise ValueError("请填写有效的光谱纵轴范围")
        if axis.minimum >= axis.maximum:
            raise ValueError("光谱纵轴下限必须小于上限")
        if axis.mode is SpectrumAxisMode.COUNTS and axis.minimum < 0:
            raise ValueError("原始强度纵轴下限不能小于 0")
        if axis.mode is SpectrumAxisMode.RELATIVE_DB and axis.maximum > 0:
            raise ValueError("相对强度纵轴上限不能大于 0 dB")
        if axis.wavelength_minimum is not None or axis.wavelength_maximum is not None:
            if (
                axis.wavelength_minimum is None
                or axis.wavelength_maximum is None
                or not math.isfinite(axis.wavelength_minimum)
                or not math.isfinite(axis.wavelength_maximum)
            ):
                raise ValueError("请填写有效的光谱横轴范围")
            if axis.wavelength_minimum >= axis.wavelength_maximum:
                raise ValueError("光谱横轴下限必须小于上限")


def suggested_field_values(
    inspection: WorkbookInspection,
    report_type: ShippingReportType,
    operating_current_a: float,
) -> dict[str, str]:
    point = inspection.point_for_current(operating_current_a)
    suggestions = point.suggestions()
    suggestions["wavelengthCurrentCoefficientNmPerA"] = (
        inspection.wavelength_current_coefficient_nm_per_a
    )
    definitions = SPECTRUM_FIELD_DEFINITIONS if report_type is ShippingReportType.SPECTRUM else POLE_FIELD_DEFINITIONS
    output: dict[str, str] = {}
    for definition in definitions:
        if not definition.measured_key:
            continue
        value = suggestions.get(definition.measured_key)
        if value is None:
            continue
        if definition.key in {"operatingCurrent"}:
            output[definition.key] = f"{value:g}"
        elif definition.key in {"power"}:
            output[definition.key] = f"{value:.1f}"
        elif definition.key in {"operatingVoltage", "electroOpticalEfficiency"}:
            output[definition.key] = f"{value:.2f}"
        elif definition.key == "slopeEfficiency":
            output[definition.key] = f"{value:.3f}"
        else:
            output[definition.key] = f"{value:.3f}".rstrip("0").rstrip(".")
    return output


def build_batch_shipping_report_request(
    inspection: WorkbookInspection,
    values: Mapping[str, Any],
) -> ShippingReportRequest:
    if not inspection.sn:
        raise ValueError("Excel 会话信息中缺少 SN")
    normalized = dict(values)
    normalized["sn"] = inspection.sn
    request = ShippingReportRequest.from_mapping(normalized)
    suggestions = suggested_field_values(
        inspection,
        request.report_type,
        request.operating_current_a,
    )
    definitions = (
        SPECTRUM_FIELD_DEFINITIONS
        if request.report_type is ShippingReportType.SPECTRUM
        else POLE_FIELD_DEFINITIONS
    )
    fields = dict(request.fields)
    for definition in definitions:
        if definition.measured_key is None or definition.key not in fields:
            continue
        selected = fields[definition.key]
        fields[definition.key] = SelectedReportField(
            include=selected.include,
            value=suggestions.get(definition.key, ""),
        )
    return replace(request, sn=inspection.sn, fields=fields)


def next_shipping_report_output_path(output_directory: Path | str, sn: str) -> Path:
    directory = Path(output_directory).expanduser().resolve()
    if directory.exists() and not directory.is_dir():
        raise ValueError("PDF 输出位置不是文件夹")
    safe_sn = sanitize_sn(sn)
    base = directory / f"{safe_sn}_出货报告.pdf"
    if not base.exists():
        return base
    index = 2
    while True:
        candidate = directory / f"{safe_sn}_出货报告_{index}.pdf"
        if not candidate.exists():
            return candidate
        index += 1


def generate_shipping_report_batch_item(
    source_path: Path | str,
    output_directory: Path | str,
    values: Mapping[str, Any],
) -> tuple[Path, ShippingReportRequest]:
    inspection = inspect_shipping_workbook(source_path)
    request = build_batch_shipping_report_request(inspection, values)
    target = next_shipping_report_output_path(output_directory, request.sn)
    return generate_shipping_report(source_path, target, request), request


def load_shipping_report_preferences(settings: Any) -> dict[str, Any]:
    """Return report preferences from QSettings without trusting stored shapes."""

    def stored_float(key: str, default: float) -> float:
        try:
            value = float(settings.value(key, default))
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    output: dict[str, Any] = {}
    for report_type in ShippingReportType:
        prefix = f"shipping_report/{report_type.value}/"
        raw_selected = settings.value(prefix + "selected_fields", None)
        try:
            selected = json.loads(str(raw_selected)) if raw_selected is not None else []
            manual_values = json.loads(str(settings.value(prefix + "manual_values", "{}")))
        except (TypeError, ValueError, json.JSONDecodeError):
            selected, manual_values = [], {}
        if not isinstance(selected, list):
            selected = []
        if raw_selected is None and report_type is ShippingReportType.POLE:
            selected = list(DEFAULT_POLE_SELECTED_FIELD_KEYS)
        if not isinstance(manual_values, dict):
            manual_values = {}
        try:
            custom_fields = json.loads(str(settings.value(prefix + "custom_fields", "[]")))
        except (TypeError, ValueError, json.JSONDecodeError):
            custom_fields = []
        if not isinstance(custom_fields, list):
            custom_fields = []
        normalized_custom_fields = []
        for item in custom_fields:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip()
            label = str(item.get("label", "")).strip()
            if not key or not label:
                continue
            normalized_custom_fields.append({
                "key": key,
                "label": label,
                "unit": str(item.get("unit", "")).strip(),
                "side": "left" if report_type is ShippingReportType.SPECTRUM else "single",
            })
        preference: dict[str, Any] = {
            "selectedFields": [str(value) for value in selected],
            "manualValues": {str(key): str(value) for key, value in manual_values.items()},
            "customFields": normalized_custom_fields,
        }
        if report_type is ShippingReportType.SPECTRUM:
            mode = str(settings.value(prefix + "axis_mode", SpectrumAxisMode.RELATIVE_DB.value))
            if mode not in {item.value for item in SpectrumAxisMode}:
                mode = SpectrumAxisMode.RELATIVE_DB.value
            preference["spectrumAxis"] = {
                "mode": mode,
                "minimum": stored_float(prefix + "axis_minimum", -80.0),
                "maximum": stored_float(prefix + "axis_maximum", 0.0),
            }
            wavelength_minimum = _finite_or_none(settings.value(prefix + "wavelength_minimum", None))
            wavelength_maximum = _finite_or_none(settings.value(prefix + "wavelength_maximum", None))
            if (
                wavelength_minimum is not None
                and wavelength_maximum is not None
                and wavelength_minimum < wavelength_maximum
            ):
                preference["spectrumAxis"]["wavelengthMinimum"] = wavelength_minimum
                preference["spectrumAxis"]["wavelengthMaximum"] = wavelength_maximum
        output[report_type.value] = preference
    return output


def save_shipping_report_preferences(settings: Any, request: ShippingReportRequest) -> None:
    definitions = _report_definitions(request.report_type, request.custom_fields)
    manual_keys = {item.key for item in definitions if item.measured_key is None}
    selected = [key for key, value in request.fields.items() if value.include]
    manual_values = {
        key: value.value
        for key, value in request.fields.items()
        if key in manual_keys and value.value
    }
    prefix = f"shipping_report/{request.report_type.value}/"
    settings.setValue(prefix + "selected_fields", json.dumps(selected, ensure_ascii=False))
    settings.setValue(prefix + "manual_values", json.dumps(manual_values, ensure_ascii=False))
    settings.setValue(
        prefix + "custom_fields",
        json.dumps([_field_payload(item) for item in request.custom_fields], ensure_ascii=False),
    )
    if request.spectrum_axis is not None:
        settings.setValue(prefix + "axis_mode", request.spectrum_axis.mode.value)
        settings.setValue(prefix + "axis_minimum", request.spectrum_axis.minimum)
        settings.setValue(prefix + "axis_maximum", request.spectrum_axis.maximum)
        if (
            request.spectrum_axis.wavelength_minimum is not None
            and request.spectrum_axis.wavelength_maximum is not None
        ):
            settings.setValue(prefix + "wavelength_minimum", request.spectrum_axis.wavelength_minimum)
            settings.setValue(prefix + "wavelength_maximum", request.spectrum_axis.wavelength_maximum)
        else:
            settings.remove(prefix + "wavelength_minimum")
            settings.remove(prefix + "wavelength_maximum")
    settings.sync()


def generate_shipping_report(
    source_path: Path | str,
    output_path: Path | str,
    request: ShippingReportRequest,
) -> Path:
    inspection = inspect_shipping_workbook(source_path)
    validate_shipping_report_request(inspection, request)
    target = Path(output_path).expanduser()
    if target.suffix.lower() != ".pdf":
        target = target.with_suffix(".pdf")
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.stem}.tmp.pdf")
    if temporary.exists():
        temporary.unlink()
    try:
        renderer = _ShippingReportRenderer(temporary, inspection, request)
        renderer.render()
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("PDF 生成结果为空")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def render_shipping_report_preview(
    source_path: Path | str,
    request: ShippingReportRequest,
    *,
    page_width_px: int = 720,
) -> list[str]:
    """Render the final report layout into compact PNG pages for live preview.

    The preview first uses the same PDF renderer as the saved report, rather
    than maintaining a second HTML approximation.  This keeps page geometry,
    charts, fonts, and the approved header/footer artwork in sync with export.
    The temporary PDF and rendered images stay in a private temporary directory.
    """

    if page_width_px < 240 or page_width_px > 1600:
        raise ValueError("预览宽度必须在 240 到 1600 像素之间")
    inspection = inspect_shipping_workbook(source_path)
    validate_shipping_report_request(inspection, request)
    with tempfile.TemporaryDirectory(prefix="shipping-report-preview-") as directory:
        temporary = Path(directory) / "preview.pdf"
        renderer = _ShippingReportRenderer(temporary, inspection, request)
        renderer.render()
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("出货报告预览生成失败")

        document = QPdfDocument()
        pages: list[str] = []
        try:
            if document.load(str(temporary)) != QPdfDocument.Error.None_:
                raise RuntimeError("无法读取出货报告预览")
            if document.pageCount() < 1:
                raise RuntimeError("出货报告预览没有页面")

            for page_number in range(document.pageCount()):
                page_size = document.pagePointSize(page_number)
                if page_size.width() <= 0 or page_size.height() <= 0:
                    raise RuntimeError("出货报告预览页面尺寸无效")
                page_height_px = max(1, round(page_width_px * page_size.height() / page_size.width()))
                image = document.render(page_number, QSize(page_width_px, page_height_px))
                if image.isNull():
                    raise RuntimeError("无法绘制出货报告预览页面")
                data = QByteArray()
                buffer = QBuffer(data)
                try:
                    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
                        raise RuntimeError("无法写入出货报告预览")
                    if not image.save(buffer, "PNG"):
                        raise RuntimeError("无法编码出货报告预览")
                finally:
                    buffer.close()
                pages.append(base64.b64encode(bytes(data)).decode("ascii"))
        finally:
            document.close()
            del document
            gc.collect()
        return pages


def _resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / relative


def _load_report_font_family(
    paths: Iterable[Path],
    preferred_families: Iterable[str],
) -> str | None:
    """Register a report font and return its preferred Qt family name."""

    preferred = tuple(preferred_families)
    loaded_families: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id >= 0:
            loaded_families.extend(QFontDatabase.applicationFontFamilies(font_id))

    available = set(QFontDatabase.families())
    for candidate in preferred:
        if candidate in loaded_families or candidate in available:
            return candidate
    return loaded_families[0] if loaded_families else None


def _available_font_families() -> tuple[str, str]:
    """Load the Latin and Chinese fonts used by dynamically drawn report text.

    Some headless Qt Windows platforms expose an empty system font database.  In
    that case QPainter silently falls back to a non-embeddable placeholder font,
    which Poppler renders as square glyphs. Registering concrete font files before
    QPdfWriter starts drawing makes both GUI and headless output deterministic.

    Times New Roman is the primary family, so Latin letters, numbers and symbols
    use it. Microsoft YaHei is the fallback family and supplies all Chinese
    glyphs in mixed-language strings.
    """

    windows_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    latin_family = _load_report_font_family(
        (
            windows_fonts / "times.ttf",
            windows_fonts / "timesbd.ttf",
        ),
        ("Times New Roman",),
    )
    chinese_family = _load_report_font_family(
        (
            windows_fonts / "msyh.ttc",
            windows_fonts / "msyhbd.ttc",
            _resource_path("assets/report/report_font.ttf"),
        ),
        ("Microsoft YaHei", "微软雅黑"),
    )
    default_family = QFont().defaultFamily()
    return latin_family or default_family, chinese_family or default_family


def _nice_ceiling(value: float) -> float:
    if value <= 0 or not math.isfinite(value):
        return 1.0
    exponent = math.floor(math.log10(value))
    fraction = value / (10**exponent)
    nice = 1.0 if fraction <= 1 else 2.0 if fraction <= 2 else 5.0 if fraction <= 5 else 10.0
    return nice * (10**exponent)


def _compact_axis_maximum(value: float, *, target_intervals: int = 7) -> tuple[float, float]:
    """Return a readable axis maximum without leaving a large empty tail."""
    if value <= 0 or not math.isfinite(value):
        return 1.0, 0.2
    raw_step = value / max(1, target_intervals)
    exponent = math.floor(math.log10(raw_step))
    scale = 10**exponent
    fraction = raw_step / scale
    step_fraction = min((1.0, 2.0, 2.5, 5.0, 10.0), key=lambda item: abs(item - fraction))
    step = step_fraction * scale
    maximum = math.ceil((value * 1.03) / step) * step
    if maximum <= value:
        maximum += step
    return maximum, step


def _reference_axis_step(span: float, *, target_intervals: int = 5) -> float:
    """Choose a 1/2/2.5/5 scale interval like the approved chart sample."""
    if span <= 0 or not math.isfinite(span):
        return 1.0
    raw_step = span / max(1, target_intervals)
    exponent = math.floor(math.log10(raw_step))
    scale = 10**exponent
    candidates = tuple(multiplier * scale for multiplier in (1.0, 2.0, 2.5, 5.0, 10.0))
    return min(candidates, key=lambda candidate: abs(candidate - raw_step))


def _reference_axis_bounds(
    values: Iterable[float],
    *,
    singleton_span_fraction: float,
    padding_fraction: float = 0.15,
    minimum: float | None = None,
    snap_to_major_ticks: bool = False,
) -> tuple[float, float, float]:
    """Return compact non-zero bounds with five journal-style major intervals.

    The reference supplied for the report intentionally focuses the vertical
    axes around the measured data rather than always forcing a zero baseline.
    A zero lower limit is still retained when it is needed to include a
    non-positive measurement.
    """
    finite_values = [float(value) for value in values if math.isfinite(value)]
    if not finite_values:
        return 0.0, 1.0, 0.2
    lower = min(finite_values)
    upper = max(finite_values)
    if math.isclose(lower, upper, rel_tol=1e-12, abs_tol=1e-12):
        span = max(abs(lower) * singleton_span_fraction, 1.0)
        lower -= span / 2.0
        upper += span / 2.0
    else:
        padding = (upper - lower) * padding_fraction
        lower -= padding
        upper += padding
    if minimum is not None:
        lower = max(minimum, lower)
    if upper <= lower:
        upper = lower + max(abs(lower) * singleton_span_fraction, 1.0)
    major_step = _reference_axis_step(upper - lower)
    if snap_to_major_ticks:
        lower = math.floor(lower / major_step) * major_step
        upper = math.ceil(upper / major_step) * major_step
        if upper <= lower:
            upper = lower + major_step
    return lower, upper, major_step


def _axis_tick_values(minimum: float, maximum: float, step: float) -> list[float]:
    """Return stable tick values inside a floating-point axis interval."""
    if step <= 0 or not math.isfinite(step):
        return []
    first = math.ceil(minimum / step - 1e-9)
    last = math.floor(maximum / step + 1e-9)
    return [index * step for index in range(first, last + 1)]


def _format_axis_tick(value: float, step: float, *, force_decimal: bool = False) -> str:
    """Keep tick labels compact while preserving the sample's decimal style."""
    decimals = max(0, int(math.ceil(-math.log10(abs(step)) - 1e-9))) if step else 0
    if force_decimal:
        decimals = max(1, decimals)
    return f"{value:.{decimals}f}"


def _format_scientific_axis_tick(value: float) -> str:
    """Format spectrum counts with three significant digits in compact e notation."""

    if math.isclose(value, 0.0, abs_tol=1e-12):
        return "0"
    mantissa, exponent = f"{value:.2e}".split("e")
    return f"{mantissa.rstrip('0').rstrip('.')}e{int(exponent)}"


def _format_pole_summary_value(definition: ReportFieldDefinition, value: str) -> str:
    """Render Pole summary values at a compact, report-appropriate precision."""

    precision = {
        "power": 1,
        "operatingCurrent": 2,
        "operatingVoltage": 2,
        "slopeEfficiency": 2,
        "electroOpticalEfficiency": 2,
        "coolingTemperature": 1,
    }.get(definition.key)
    if precision is None:
        return value

    numeric_value = _finite_or_none(value)
    if numeric_value is None:
        return value
    if math.isclose(numeric_value, 0.0, abs_tol=10 ** -(precision + 1)):
        numeric_value = 0.0
    return f"{numeric_value:.{precision}f}".rstrip("0").rstrip(".") or "0"


def _format_performance_parameters_title(operating_current_a: float) -> str:
    """Return the English performance-summary heading for the selected current."""

    return f"Performance @{operating_current_a:g}A"


def _balanced_report_columns(
    fields: list[tuple[ReportFieldDefinition, str]],
) -> tuple[
    list[tuple[ReportFieldDefinition, str]],
    list[tuple[ReportFieldDefinition, str]],
]:
    """Split selected fields evenly, leaving the extra odd row on the left."""

    split_index = (len(fields) + 1) // 2
    return fields[:split_index], fields[split_index:]


class _ShippingReportRenderer:
    A4_WIDTH_MM = 210.0
    A4_HEIGHT_MM = 297.0
    CONTENT_LEFT_MM = 18.0
    CONTENT_RIGHT_MM = 192.0
    FOOTER_TOP_MM = 266.0
    HEADER_ARTWORK_TOP_MM = 17.5
    TITLE_TOP_MM = 32.5
    SERIAL_NUMBER_TOP_MM = 44.5
    TITLE_DIVIDER_Y_MM = 55.0
    POLE_SUMMARY_TOP_MM = 62.0
    POLE_TABLE_VALUE_FONT_SIZE = 10.7
    DETAIL_TABLE_VALUE_FONT_SIZE = POLE_TABLE_VALUE_FONT_SIZE

    def __init__(self, path: Path, inspection: WorkbookInspection, request: ShippingReportRequest) -> None:
        global _REPORT_QT_APPLICATION
        application = QApplication.instance()
        if application is None:
            _REPORT_QT_APPLICATION = QApplication([])
            application = _REPORT_QT_APPLICATION
        self._application = application
        self.path = path
        self.inspection = inspection
        self.request = request
        self.latin_font_family, self.chinese_font_family = _available_font_families()
        self.writer = QPdfWriter(str(path))
        self.writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        self.writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
        self.writer.setResolution(144)
        self.writer.setTitle(f"{request.product_name} - {request.sn} Shipping Report")
        self.writer.setCreator("Pump Driver Integrated Test")
        self.painter = QPainter()
        self.page_number = 0
        self.scale = self.writer.resolution() / 25.4

    def mm(self, value: float) -> float:
        return value * self.scale

    def rect(self, x: float, y: float, width: float, height: float) -> QRectF:
        return QRectF(self.mm(x), self.mm(y), self.mm(width), self.mm(height))

    def font(self, size: float, *, bold: bool = False) -> QFont:
        result = QFont(self.latin_font_family)
        result.setFamilies((self.latin_font_family, self.chinese_font_family))
        result.setPointSizeF(size)
        result.setBold(bold)
        return result

    def chart_font(self, size: float, *, bold: bool = False) -> QFont:
        """Return the shared sans-serif font used by every report chart."""

        result = QFont("Arial")
        result.setFamilies(("Arial", "Helvetica", self.chinese_font_family))
        result.setPointSizeF(size)
        result.setBold(bold)
        return result

    def _reference_image(self, relative_path: str) -> QImage:
        """Load approved report artwork without recreating any of its content."""

        path = _resource_path(relative_path)
        image = QImage(str(path))
        if image.isNull():
            raise RuntimeError(f"无法读取出货报告原始图片：{path}")
        return image

    def render(self) -> None:
        if not self.painter.begin(self.writer):
            raise RuntimeError("无法启动 PDF 绘制")
        try:
            self.painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self.painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            if self.request.report_type is ShippingReportType.SPECTRUM:
                self._render_spectrum_report()
            else:
                self._render_pole_report()
        finally:
            self.painter.end()

    def _new_page(self, continuation_title: str = "") -> None:
        if self.page_number:
            self.writer.newPage()
        self.page_number += 1
        self._draw_header()
        self._draw_title(continuation_title)
        self._draw_footer()

    def _draw_header(self) -> None:
        # Keep the supplied ribbon mark as vector artwork.  The corporate
        # EVERBRIGHT wordmark is cropped from the approved source artwork so
        # its custom letterforms are not replaced by a generic font.
        logo = QSvgRenderer(str(_resource_path("assets/report/everbright_logo.svg")))
        if not logo.isValid():
            raise RuntimeError("无法读取出货报告矢量品牌标志")
        logo.render(
            self.painter,
            self.rect(self.CONTENT_LEFT_MM, self.HEADER_ARTWORK_TOP_MM, 13.5, 11.9),
        )
        approved_header = self._reference_image("assets/report/everbright_header_reference.png")
        wordmark_width = 49.0
        wordmark_height = wordmark_width * 53.0 / 443.0
        self.painter.drawImage(
            self.rect(
                33.0,
                self.HEADER_ARTWORK_TOP_MM + (11.9 - wordmark_height) / 2.0,
                wordmark_width,
                wordmark_height,
            ),
            approved_header,
            QRectF(254.0, 95.0, 443.0, 53.0),
        )

        company_x = 137.0
        company_width = self.CONTENT_RIGHT_MM - company_x
        self.painter.setPen(QColor("#161616"))
        self.painter.setFont(self.chart_font(7.2, bold=True))
        self.painter.drawText(
            self.rect(company_x, self.HEADER_ARTWORK_TOP_MM - 0.3, company_width, 4.6),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "Suzhou Everbright Photonics Co., Ltd.",
        )
        self.painter.setFont(self.chart_font(6.8))
        self.painter.drawText(
            self.rect(company_x, self.HEADER_ARTWORK_TOP_MM + 4.5, company_width, 9.0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
            "No. 56 Lijiang Road, Suzhou New District\n"
            "Tel: +86-512-69372570\n"
            "Fax: +86-512-69372559",
        )

    def _draw_title(self, continuation_title: str = "") -> None:
        self.painter.setPen(QColor("#090909"))
        self.painter.setFont(self.font(16, bold=True))
        title = self.request.product_name if not continuation_title else f"{self.request.product_name} - {continuation_title}"
        self.painter.drawText(
            self.rect(self.CONTENT_LEFT_MM, self.TITLE_TOP_MM, self.CONTENT_RIGHT_MM - self.CONTENT_LEFT_MM, 10),
            int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter),
            title,
        )
        self.painter.setFont(self.font(16, bold=True))
        self.painter.drawText(
            self.rect(self.CONTENT_LEFT_MM, self.SERIAL_NUMBER_TOP_MM, self.CONTENT_RIGHT_MM - self.CONTENT_LEFT_MM, 8),
            int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter),
            f"TEST SN: {self.request.sn}",
        )
        self.painter.setPen(QPen(QColor("#d7dade"), self.mm(0.18)))
        self.painter.drawLine(
            QPointF(self.mm(self.CONTENT_LEFT_MM), self.mm(self.TITLE_DIVIDER_Y_MM)),
            QPointF(self.mm(self.CONTENT_RIGHT_MM), self.mm(self.TITLE_DIVIDER_Y_MM)),
        )

    def _draw_footer(self) -> None:
        top = self.FOOTER_TOP_MM
        self.painter.setPen(QPen(QColor("#d1d1d1"), self.mm(0.25)))
        self.painter.drawLine(
            QPointF(self.mm(self.CONTENT_LEFT_MM), self.mm(top)),
            QPointF(self.mm(self.CONTENT_RIGHT_MM), self.mm(top)),
        )
        self.painter.setPen(QColor("#1a1a1a"))
        self.painter.setFont(self.font(8.0))
        notice = (
            "Notice: Everbright Photonics continuously improves its products to provide customers with outstanding "
            "quality and reliability. Specifications and product descriptions may be changed at any time without "
            "notice. Warranty support is provided to ensure customer satisfaction. For complete information, "
            "please contact our sales representative."
        )
        self.painter.drawText(
            self.rect(self.CONTENT_LEFT_MM, 269, 142, 20),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap),
            notice,
        )
        # The warning artwork includes all of its original text.  Do not overlay
        # translated/retyped content or stretch it away from its source ratio.
        warning = self._reference_image("assets/report/laser_warning_reference.png")
        footer_content_bottom_mm = 289.0
        warning_height_mm = 18.0
        warning_width_mm = warning_height_mm * warning.width() / warning.height()
        warning_x_mm = self.CONTENT_RIGHT_MM - warning_width_mm
        warning_y_mm = footer_content_bottom_mm - warning_height_mm
        self.painter.drawImage(
            self.rect(warning_x_mm, warning_y_mm, warning_width_mm, warning_height_mm),
            warning,
        )
        self.painter.setFont(self.font(6.3))
        self.painter.setPen(QColor("#666666"))
        self.painter.drawText(
            self.rect(90, 291, 30, 4),
            int(Qt.AlignmentFlag.AlignCenter),
            f"第 {self.page_number} 页",
        )

    def _selected_fields(self, definitions: Iterable[ReportFieldDefinition]) -> list[tuple[ReportFieldDefinition, str]]:
        output = []
        for definition in definitions:
            selected = self.request.fields.get(definition.key, SelectedReportField(False, ""))
            if selected.include:
                output.append((definition, selected.value))
        return output

    def _definitions(self, report_type: ShippingReportType) -> tuple[ReportFieldDefinition, ...]:
        return _report_definitions(report_type, self.request.custom_fields)

    def _render_spectrum_report(self) -> None:
        self._new_page()
        fields = self._selected_fields(self._definitions(ShippingReportType.SPECTRUM))
        left, right = _balanced_report_columns(fields)
        self._draw_section_title(
            self.POLE_SUMMARY_TOP_MM,
            _format_performance_parameters_title(self.request.operating_current_a),
            height=8.0,
            font_size=14.0,
            accent_width=0.75,
            accent_height=5.0,
        )
        summary_height = self._draw_spectrum_summary(72, left, right)
        chart_top = min(116.0, 75.0 + summary_height + 4.0)
        available = self.FOOTER_TOP_MM - chart_top - 7.0
        section_gap = 2.0
        section_title_height = 7.0
        chart_gap = 1.0
        chart_height = max(
            45.0,
            (available - section_gap - section_title_height - chart_gap) / 2.0,
        )
        self._draw_power_efficiency_chart(self.rect(23, chart_top, 164, chart_height), title="")
        spectrum_title_top = chart_top + chart_height + section_gap
        self._draw_section_title(
            spectrum_title_top,
            "Spectrum",
            height=section_title_height,
            font_size=14.0,
            accent_width=0.75,
            accent_height=5.0,
        )
        self._draw_spectrum_chart(
            self.rect(
                23,
                spectrum_title_top + section_title_height + chart_gap,
                164,
                chart_height,
            )
        )

    def _draw_spectrum_summary(
        self,
        top: float,
        left: list[tuple[ReportFieldDefinition, str]],
        right: list[tuple[ReportFieldDefinition, str]],
    ) -> float:
        row_count = max(len(left), len(right), 1)
        row_height = min(6.1, 36.0 / row_count)
        height = row_count * row_height
        x, width = 21.0, 168.0
        self.painter.setPen(QPen(QColor("#333333"), self.mm(0.2)))
        self.painter.drawLine(QPointF(self.mm(x), self.mm(top)), QPointF(self.mm(x + width), self.mm(top)))
        self.painter.drawLine(QPointF(self.mm(x), self.mm(top + height)), QPointF(self.mm(x + width), self.mm(top + height)))
        self.painter.drawLine(QPointF(self.mm(105), self.mm(top)), QPointF(self.mm(105), self.mm(top + height)))
        for side_x, values in ((23.0, left), (107.0, right)):
            for index, (definition, value) in enumerate(values):
                y = top + index * row_height
                self.painter.setPen(QColor("#161616"))
                self.painter.setFont(self.font(8.5, bold=True))
                self.painter.drawText(self.rect(side_x, y, 43, row_height), int(Qt.AlignmentFlag.AlignVCenter), definition.label)
                self.painter.setFont(self.font(8.6, bold=True))
                self.painter.drawText(self.rect(side_x + 47, y, 20, row_height), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), value)
                self.painter.setFont(self.font(8.2))
                self.painter.drawText(self.rect(side_x + 69, y, 13, row_height), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), definition.unit)
        return height

    def _render_pole_report(self) -> None:
        fields = self._selected_fields(self._definitions(ShippingReportType.POLE))
        points = list(self.inspection.points)
        first_capacity = 12
        remaining = points
        self._new_page()
        summary_height = self._draw_pole_summary(self.POLE_SUMMARY_TOP_MM, fields)
        chart_top = self.POLE_SUMMARY_TOP_MM + summary_height + 6.0
        chart_height = 54.0
        self._draw_power_efficiency_chart(self.rect(31, chart_top, 148, chart_height), title="")
        table_top = chart_top + chart_height
        room_rows = max(1, min(first_capacity, int((self.FOOTER_TOP_MM - table_top - 5.0) / 6.0) - 1))
        current_page = remaining[:room_rows]
        remaining = remaining[room_rows:]
        self._draw_detail_table(table_top, current_page)
        while remaining:
            self._new_page("Detailed Measurement Data (Continued)")
            capacity = max(1, int((self.FOOTER_TOP_MM - 76.0) / 6.0) - 1)
            current_page = remaining[:capacity]
            remaining = remaining[capacity:]
            self._draw_detail_table(74.0, current_page)

    def _draw_pole_summary(self, top: float, fields: list[tuple[ReportFieldDefinition, str]]) -> float:
        title_height = 8.0
        title_gap = 3.0
        header_height = 7.2
        row_height = 7.4
        table_x, table_width = 18.0, 174.0
        parameter_width = table_width * 0.45
        value_width = table_width * 0.30
        unit_width = table_width - parameter_width - value_width
        cell_padding = 3.5
        divider_pen = QPen(QColor("#dfe4e8"), self.mm(0.18))

        self._draw_section_title(
            top,
            _format_performance_parameters_title(self.request.operating_current_a),
            height=title_height,
            font_size=14.0,
            accent_width=0.75,
            accent_height=5.0,
        )
        table_top = top + title_height + title_gap
        table_height = header_height + len(fields) * row_height

        # A lightly tinted header and horizontal dividers establish the columns
        # without the heavy grid or alternating body fills of the old layout.
        self.painter.fillRect(self.rect(table_x, table_top, table_width, header_height), QColor("#f6f8fa"))
        self.painter.setPen(divider_pen)
        self.painter.drawLine(
            QPointF(self.mm(table_x), self.mm(table_top)),
            QPointF(self.mm(table_x + table_width), self.mm(table_top)),
        )
        self.painter.drawLine(
            QPointF(self.mm(table_x), self.mm(table_top + header_height)),
            QPointF(self.mm(table_x + table_width), self.mm(table_top + header_height)),
        )

        self.painter.setPen(QColor("#0b4b82"))
        self.painter.setFont(self.font(10.3, bold=True))
        self.painter.drawText(
            self.rect(table_x + cell_padding, table_top, parameter_width - 2 * cell_padding, header_height),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "Parameter",
        )
        self.painter.drawText(
            self.rect(
                table_x + parameter_width + cell_padding,
                table_top,
                value_width - 2 * cell_padding,
                header_height,
            ),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            "Value",
        )
        self.painter.drawText(
            self.rect(
                table_x + parameter_width + value_width + cell_padding,
                table_top,
                unit_width - 2 * cell_padding,
                header_height,
            ),
            int(Qt.AlignmentFlag.AlignCenter),
            "Unit",
        )

        for index, (definition, value) in enumerate(fields):
            y = table_top + header_height + index * row_height
            self.painter.setPen(QColor("#252a2e"))
            self.painter.setFont(self.font(self.POLE_TABLE_VALUE_FONT_SIZE))
            self.painter.drawText(
                self.rect(table_x + cell_padding, y, parameter_width - 2 * cell_padding, row_height),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                definition.label,
            )
            self.painter.drawText(
                self.rect(
                    table_x + parameter_width + cell_padding,
                    y,
                    value_width - 2 * cell_padding,
                    row_height,
                ),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                _format_pole_summary_value(definition, value),
            )
            self.painter.setFont(self.font(10.5))
            self.painter.drawText(
                self.rect(
                    table_x + parameter_width + value_width + cell_padding,
                    y,
                    unit_width - 2 * cell_padding,
                    row_height,
                ),
                int(Qt.AlignmentFlag.AlignCenter),
                definition.unit,
            )
            self.painter.setPen(divider_pen)
            self.painter.drawLine(
                QPointF(self.mm(table_x), self.mm(y + row_height)),
                QPointF(self.mm(table_x + table_width), self.mm(y + row_height)),
            )
        return title_height + title_gap + table_height

    def _draw_power_efficiency_chart(self, outer: QRectF, *, title: str) -> None:
        points = self.inspection.points
        currents = [point.current_a for point in points]
        powers = [point.power_w for point in points]
        efficiencies = [point.efficiency * 100.0 for point in points]

        left_margin = self.mm(17)
        right_margin = self.mm(18)
        top_margin = self.mm(10 if title else 3)
        bottom_margin = self.mm(12)
        plot = QRectF(
            outer.left() + left_margin,
            outer.top() + top_margin,
            outer.width() - left_margin - right_margin,
            outer.height() - top_margin - bottom_margin,
        )
        if title:
            self.painter.setPen(QColor("#343434"))
            self.painter.setFont(self.chart_font(10.2, bold=True))
            self.painter.drawText(
                QRectF(outer.left(), outer.top(), outer.width(), self.mm(8)),
                int(Qt.AlignmentFlag.AlignCenter),
                title,
            )
        # Default shipment-report style: retain a compact dual-Y layout and
        # label the measured current points directly instead of adding a
        # synthetic zero-current tick when it was not measured.
        x_data_min = min(currents)
        x_data_max = max(currents)
        if math.isclose(x_data_min, x_data_max, rel_tol=1e-12, abs_tol=1e-12):
            x_step = _reference_axis_step(max(abs(x_data_min), 1.0), target_intervals=4)
            x_min = x_data_min - 0.75 * x_step
            x_max = x_data_max + 0.75 * x_step
        else:
            unique_currents = sorted(set(currents))
            measured_steps = [
                current - previous
                for previous, current in zip(unique_currents, unique_currents[1:])
                if current > previous
            ]
            x_step = _reference_axis_step(min(measured_steps), target_intervals=1)
            x_padding = max(0.25 * x_step, 0.04 * (x_data_max - x_data_min))
            x_min = x_data_min - x_padding
            x_max = x_data_max + x_padding
        power_min, power_max, power_step = _reference_axis_bounds(
            powers,
            singleton_span_fraction=1.2,
            minimum=0.0,
        )
        efficiency_min, efficiency_max, efficiency_step = _reference_axis_bounds(
            efficiencies,
            singleton_span_fraction=0.5,
            minimum=0.0,
            snap_to_major_ticks=True,
        )

        def map_x(value: float) -> float:
            return plot.left() + (value - x_min) / (x_max - x_min) * plot.width()

        def map_power(value: float) -> float:
            return plot.bottom() - (value - power_min) / (power_max - power_min) * plot.height()

        def map_efficiency(value: float) -> float:
            return plot.bottom() - (value - efficiency_min) / (efficiency_max - efficiency_min) * plot.height()

        axis_color = QColor("#202020")
        power_color = QColor("#0072B2")
        efficiency_label_color = QColor("#D55E00")
        major_tick_pen = QPen(power_color, self.mm(0.32))
        minor_tick_pen = QPen(power_color, self.mm(0.22))
        right_major_tick_pen = QPen(efficiency_label_color, self.mm(0.32))
        right_minor_tick_pen = QPen(efficiency_label_color, self.mm(0.22))
        x_major_tick_pen = QPen(axis_color, self.mm(0.32))
        x_minor_tick_pen = QPen(axis_color, self.mm(0.22))
        major_tick_length = self.mm(1.55)
        minor_tick_length = self.mm(0.85)

        def is_major_tick(value: float, major_values: list[float]) -> bool:
            return any(math.isclose(value, major, rel_tol=0.0, abs_tol=1e-8) for major in major_values)

        power_ticks = _axis_tick_values(power_min, power_max, power_step)
        power_minor_ticks = _axis_tick_values(power_min, power_max, power_step / 2.0)
        self.painter.setFont(self.chart_font(8.0))
        for value in power_minor_ticks:
            if is_major_tick(value, power_ticks):
                continue
            y = map_power(value)
            self.painter.setPen(minor_tick_pen)
            self.painter.drawLine(QPointF(plot.left(), y), QPointF(plot.left() + minor_tick_length, y))
        for value in power_ticks:
            y = map_power(value)
            self.painter.setPen(major_tick_pen)
            self.painter.drawLine(QPointF(plot.left(), y), QPointF(plot.left() + major_tick_length, y))
            self.painter.setPen(power_color)
            self.painter.drawText(
                QRectF(plot.left() - self.mm(14), y - self.mm(2.2), self.mm(12), self.mm(4.4)),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                _format_axis_tick(value, power_step),
            )

        efficiency_ticks = _axis_tick_values(efficiency_min, efficiency_max, efficiency_step)
        efficiency_minor_ticks = _axis_tick_values(efficiency_min, efficiency_max, efficiency_step / 2.0)
        for value in efficiency_minor_ticks:
            if is_major_tick(value, efficiency_ticks):
                continue
            y = map_efficiency(value)
            self.painter.setPen(right_minor_tick_pen)
            self.painter.drawLine(QPointF(plot.right(), y), QPointF(plot.right() - minor_tick_length, y))
        for value in efficiency_ticks:
            y = map_efficiency(value)
            self.painter.setPen(right_major_tick_pen)
            self.painter.drawLine(QPointF(plot.right(), y), QPointF(plot.right() - major_tick_length, y))
            self.painter.setPen(efficiency_label_color)
            self.painter.drawText(
                QRectF(plot.right() + self.mm(2), y - self.mm(2.2), self.mm(13), self.mm(4.4)),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                _format_axis_tick(value, efficiency_step),
            )

        x_ticks = _axis_tick_values(x_min, x_max, x_step)
        x_minor_ticks = _axis_tick_values(x_min, x_max, x_step / 2.0)
        for value in x_minor_ticks:
            if is_major_tick(value, x_ticks):
                continue
            x = map_x(value)
            self.painter.setPen(x_minor_tick_pen)
            self.painter.drawLine(QPointF(x, plot.bottom()), QPointF(x, plot.bottom() - minor_tick_length))
            self.painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.top() + minor_tick_length))
        for value in x_ticks:
            x = map_x(value)
            self.painter.setPen(x_major_tick_pen)
            self.painter.drawLine(QPointF(x, plot.bottom()), QPointF(x, plot.bottom() - major_tick_length))
            self.painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.top() + major_tick_length))
            self.painter.setPen(axis_color)
            self.painter.drawText(
                QRectF(x - self.mm(7), plot.bottom() + self.mm(1.5), self.mm(14), self.mm(4.5)),
                int(Qt.AlignmentFlag.AlignCenter),
                _format_axis_tick(value, x_step),
            )

        # Frame and axis labels mirror the standard shipment-report reference:
        # neutral horizontal frame, blue left power axis, and orange right axis.
        frame_pen = QPen(axis_color, self.mm(0.42))
        self.painter.setPen(frame_pen)
        self.painter.drawLine(QPointF(plot.left(), plot.top()), QPointF(plot.right(), plot.top()))
        self.painter.drawLine(QPointF(plot.left(), plot.bottom()), QPointF(plot.right(), plot.bottom()))
        self.painter.setPen(QPen(power_color, self.mm(0.42)))
        self.painter.drawLine(QPointF(plot.left(), plot.top()), QPointF(plot.left(), plot.bottom()))
        self.painter.setPen(QPen(efficiency_label_color, self.mm(0.42)))
        self.painter.drawLine(QPointF(plot.right(), plot.top()), QPointF(plot.right(), plot.bottom()))

        self.painter.setFont(self.chart_font(8.8))
        self.painter.setPen(axis_color)
        self.painter.drawText(
            QRectF(plot.left(), plot.bottom() + self.mm(6.5), plot.width(), self.mm(5.5)),
            int(Qt.AlignmentFlag.AlignCenter),
            "Drive current (A)",
        )
        vertical_label_span = outer.height() - self.mm(2.0)
        vertical_label_height = self.mm(6.5)
        self.painter.save()
        self.painter.translate(outer.left() + self.mm(3.2), plot.center().y())
        self.painter.rotate(-90)
        self.painter.setPen(power_color)
        self.painter.drawText(
            QRectF(-vertical_label_span / 2, -vertical_label_height / 2, vertical_label_span, vertical_label_height),
            int(Qt.AlignmentFlag.AlignCenter),
            "Output power (W)",
        )
        self.painter.restore()
        self.painter.save()
        self.painter.translate(outer.right() - self.mm(3.2), plot.center().y())
        self.painter.rotate(90)
        self.painter.setPen(efficiency_label_color)
        self.painter.drawText(
            QRectF(-vertical_label_span / 2, -vertical_label_height / 2, vertical_label_span, vertical_label_height),
            int(Qt.AlignmentFlag.AlignCenter),
            "E-O efficiency (%)",
        )
        self.painter.restore()

        power_points = QPolygonF([QPointF(map_x(x), map_power(y)) for x, y in zip(currents, powers)])
        efficiency_points = QPolygonF([QPointF(map_x(x), map_efficiency(y)) for x, y in zip(currents, efficiencies)])
        marker_outline = QPen(QColor("#ffffff"), self.mm(0.24))
        self.painter.setPen(QPen(power_color, self.mm(0.60)))
        self.painter.drawPolyline(power_points)
        self.painter.setBrush(power_color)
        self.painter.setPen(marker_outline)
        for point in power_points:
            self.painter.drawEllipse(point, self.mm(1.15), self.mm(1.15))
        self.painter.setPen(QPen(efficiency_label_color, self.mm(0.60)))
        self.painter.drawPolyline(efficiency_points)
        self.painter.setBrush(efficiency_label_color)
        self.painter.setPen(marker_outline)
        for point in efficiency_points:
            self.painter.drawRect(QRectF(point.x() - self.mm(1.05), point.y() - self.mm(1.05), self.mm(2.1), self.mm(2.1)))

        # Position the legend from its measured text bounds so its right and
        # bottom whitespace are visually equal instead of relying on a fixed
        # horizontal offset.
        legend_labels = ("Output power", "E-O efficiency")
        legend_font = self.chart_font(8.0)
        legend_metrics = QFontMetricsF(legend_font, self.painter.device())
        legend_label_width = max(legend_metrics.horizontalAdvance(label) for label in legend_labels)
        legend_margin = self.mm(2.5)
        legend_label_height = self.mm(5.0)
        legend_row_gap = self.mm(6.3)
        legend_line_width = self.mm(11.0)
        legend_label_offset = self.mm(13.0)
        legend_x = plot.right() - legend_margin - legend_label_offset - legend_label_width
        legend_label_x = legend_x + legend_label_offset
        second_legend_y = plot.bottom() - legend_margin - legend_label_height / 2.0
        legend_y = second_legend_y - legend_row_gap
        self.painter.setFont(legend_font)

        def draw_legend_entry(y: float, color: QColor, label: str, marker: str) -> None:
            line_end = legend_x + legend_line_width
            marker_center = (legend_x + line_end) / 2
            self.painter.setPen(QPen(color, self.mm(0.56)))
            self.painter.drawLine(QPointF(legend_x, y), QPointF(line_end, y))
            self.painter.setBrush(color)
            self.painter.setPen(marker_outline)
            if marker == "circle":
                self.painter.drawEllipse(QPointF(marker_center, y), self.mm(1.05), self.mm(1.05))
            else:
                self.painter.drawRect(
                    QRectF(marker_center - self.mm(1.0), y - self.mm(1.0), self.mm(2.0), self.mm(2.0))
                )
            self.painter.setPen(axis_color)
            self.painter.drawText(
                QRectF(legend_label_x, y - legend_label_height / 2.0, legend_label_width, legend_label_height),
                int(Qt.AlignmentFlag.AlignVCenter),
                label,
            )

        draw_legend_entry(legend_y, power_color, "Output power", "circle")
        draw_legend_entry(second_legend_y, efficiency_label_color, "E-O efficiency", "square")
        self.painter.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_spectrum_chart(self, outer: QRectF) -> None:
        axis = self.request.spectrum_axis
        assert axis is not None
        left_margin = self.mm(17)
        right_margin = self.mm(18)
        top_margin = self.mm(10)
        bottom_margin = self.mm(12)
        plot = QRectF(
            outer.left() + left_margin,
            outer.top() + top_margin,
            outer.width() - left_margin - right_margin,
            outer.height() - top_margin - bottom_margin,
        )
        self.painter.setPen(QColor("#343434"))
        self.painter.setFont(self.chart_font(10.2, bold=True))
        self.painter.drawText(
            QRectF(outer.left(), outer.top(), outer.width(), self.mm(8)),
            int(Qt.AlignmentFlag.AlignCenter),
            "Spectrum at Different Currents",
        )
        default_x_range = self.inspection.spectrum_wavelength_range
        assert default_x_range is not None
        if axis.wavelength_minimum is None:
            x_min, x_max = default_x_range
        else:
            x_min, x_max = axis.wavelength_minimum, axis.wavelength_maximum

        def map_x(value: float) -> float:
            return plot.left() + (value - x_min) / (x_max - x_min) * plot.width()

        def map_y(value: float) -> float:
            return plot.bottom() - (value - axis.minimum) / (axis.maximum - axis.minimum) * plot.height()

        axis_color = QColor("#202020")
        major_tick_pen = QPen(axis_color, self.mm(0.32))
        major_tick_length = self.mm(1.55)
        self.painter.setFont(self.chart_font(8.0))
        for index in range(5):
            ratio = index / 4
            y = plot.bottom() - ratio * plot.height()
            value = axis.minimum + ratio * (axis.maximum - axis.minimum)
            self.painter.setPen(QPen(QColor("#d7dade"), self.mm(0.13)))
            self.painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            self.painter.setPen(major_tick_pen)
            self.painter.drawLine(QPointF(plot.left(), y), QPointF(plot.left() + major_tick_length, y))
            self.painter.setPen(axis_color)
            label = (
                _format_scientific_axis_tick(value)
                if axis.mode is SpectrumAxisMode.COUNTS
                else f"{value:g}"
            )
            self.painter.drawText(QRectF(plot.left() - self.mm(14), y - self.mm(2.2), self.mm(12), self.mm(4.4)), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), label)
        for index in range(5):
            ratio = index / 4
            x = plot.left() + ratio * plot.width()
            self.painter.setPen(QPen(QColor("#e0e3e6"), self.mm(0.12)))
            self.painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            self.painter.setPen(axis_color)
            self.painter.drawText(QRectF(x - self.mm(7), plot.bottom() + self.mm(1.5), self.mm(14), self.mm(4.5)), int(Qt.AlignmentFlag.AlignCenter), f"{x_min + ratio * (x_max - x_min):.1f}")
        self.painter.setPen(QPen(axis_color, self.mm(0.42)))
        self.painter.drawRect(plot)
        self.painter.setFont(self.chart_font(8.8))
        self.painter.setPen(axis_color)
        self.painter.drawText(
            QRectF(plot.left(), plot.bottom() + self.mm(6.5), plot.width(), self.mm(5.5)),
            int(Qt.AlignmentFlag.AlignCenter),
            "Wavelength (nm)",
        )
        self.painter.save()
        self.painter.translate(outer.left() + self.mm(3.2), plot.center().y())
        self.painter.rotate(-90)
        y_label = "Intensity"
        vertical_label_span = outer.height() - self.mm(2.0)
        self.painter.drawText(QRectF(-vertical_label_span / 2, -self.mm(3.25), vertical_label_span, self.mm(6.5)), int(Qt.AlignmentFlag.AlignCenter), y_label)
        self.painter.restore()
        colors = (
            "#376f9f", "#c54e4e", "#799442", "#72548c", "#3f9298", "#d27b36",
            "#254f69", "#8b493d", "#536d2d", "#60456f", "#32767a", "#9b5c22",
        )
        self.painter.save()
        self.painter.setClipRect(plot)
        for index, point in enumerate(self.inspection.points):
            values = list(point.intensity)
            if axis.mode is SpectrumAxisMode.RELATIVE_DB:
                peak = max(values)
                if peak <= 0:
                    transformed = [axis.minimum for _value in values]
                else:
                    floor = peak * 1e-12
                    transformed = [10.0 * math.log10(max(value, floor) / peak) for value in values]
            else:
                transformed = values
            visible_points = [
                (x, y)
                for x, y in zip(point.wavelength, transformed)
                if math.isfinite(x) and math.isfinite(y) and x_min <= x <= x_max
            ]
            stride = max(1, len(visible_points) // 800)
            polygon = QPolygonF()
            for x, y in visible_points[::stride]:
                polygon.append(QPointF(map_x(x), map_y(y)))
            self.painter.setPen(QPen(QColor(colors[index % len(colors)]), self.mm(0.35)))
            self.painter.drawPolyline(polygon)
        self.painter.restore()
        legend_width = self.mm(20.0)
        legend_x = plot.right() - legend_width - self.mm(2.0)
        legend_y = plot.top() + self.mm(1.0)
        legend_row = min(self.mm(4.6), (plot.height() - self.mm(2.0)) / max(1, len(self.inspection.points)))
        legend_number_x = legend_x + self.mm(8.0)
        legend_number_width = self.mm(7.0)
        legend_unit_gap = self.mm(0.8)
        legend_unit_x = legend_number_x + legend_number_width + legend_unit_gap
        self.painter.setFont(self.chart_font(8.0))
        for index, point in enumerate(self.inspection.points):
            y = legend_y + index * legend_row + legend_row / 2
            self.painter.setPen(QPen(QColor(colors[index % len(colors)]), self.mm(0.4)))
            self.painter.drawLine(QPointF(legend_x, y), QPointF(legend_x + self.mm(6.5), y))
            self.painter.setPen(axis_color)
            self.painter.drawText(
                QRectF(legend_number_x, y - legend_row / 2, legend_number_width, legend_row),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                f"{point.current_a:g}",
            )
            self.painter.drawText(
                QRectF(legend_unit_x, y - legend_row / 2, self.mm(3.0), legend_row),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                "A",
            )

    def _draw_detail_table(self, top: float, points: list[ReportPoint]) -> None:
        self._draw_section_title(top, "Detailed Measurement Data")
        table_top = top + 9
        row_height = 6.0
        column_widths = (43.5, 43.5, 43.5, 43.5)
        headers = ("Current (A)", "Power (W)", "Voltage (V)", "E-O efficiency (%)")
        x = 18.0
        self.painter.fillRect(self.rect(x, table_top, sum(column_widths), row_height), QColor("#0b4b82"))
        self.painter.setPen(QColor("#ffffff"))
        self.painter.setFont(self.font(7.7, bold=True))
        cursor = x
        for width, header in zip(column_widths, headers):
            self.painter.drawText(self.rect(cursor, table_top, width, row_height), int(Qt.AlignmentFlag.AlignCenter), header)
            cursor += width
        self.painter.setFont(self.font(self.DETAIL_TABLE_VALUE_FONT_SIZE))
        for row_index, point in enumerate(points, start=1):
            y = table_top + row_index * row_height
            self.painter.fillRect(self.rect(x, y, sum(column_widths), row_height), QColor("#f3f4f5" if row_index % 2 else "#ffffff"))
            values = (
                f"{point.current_a:g}",
                f"{point.power_w:.1f}",
                f"{point.voltage_v:.2f}",
                f"{point.efficiency * 100.0:.2f}%",
            )
            cursor = x
            for width, value in zip(column_widths, values):
                self.painter.setPen(QColor("#d0d4d8"))
                self.painter.drawRect(self.rect(cursor, y, width, row_height))
                self.painter.setPen(QColor("#202020"))
                self.painter.drawText(self.rect(cursor, y, width, row_height), int(Qt.AlignmentFlag.AlignCenter), value)
                cursor += width

    def _draw_section_title(
        self,
        top: float,
        title: str,
        *,
        height: float = 7.0,
        font_size: float = 10.5,
        accent_width: float = 1.4,
        accent_height: float | None = None,
    ) -> None:
        accent_height = height if accent_height is None else min(accent_height, height)
        accent_top = top + (height - accent_height) / 2
        self.painter.setPen(QColor("#0b4b82"))
        self.painter.fillRect(self.rect(self.CONTENT_LEFT_MM, accent_top, accent_width, accent_height), QColor("#0b4b82"))
        self.painter.setFont(self.font(font_size, bold=True))
        self.painter.drawText(self.rect(21, top, 100, height), int(Qt.AlignmentFlag.AlignVCenter), title)
