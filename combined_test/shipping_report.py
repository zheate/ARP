"""Excel-backed A4 shipping report inspection and PDF generation."""

from __future__ import annotations

import math
import json
import os
import re
import sys
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from openpyxl import load_workbook
from .excel_export import EXPORT_WORKBOOK_SCHEMA_VERSION, sanitize_sn


REPORT_WORKBOOK_SCHEMA_VERSION = EXPORT_WORKBOOK_SCHEMA_VERSION
REPORT_DATA_SHEET = "测试数据"
REPORT_SESSION_SHEET = "会话信息"
DEFAULT_ENCIRCLED_ENERGY_NA = "0.17"
UNAVAILABLE_MEASURED_VALUE = "N/A"


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
# (including an intentionally saved empty selection) in the JSON settings store.
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
    def test_mode(self) -> str:
        return str(self.session.get("测试模式", "")).strip().lower()

    @property
    def single_point(self) -> bool:
        return len(self.points) == 1

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
            "testMode": self.test_mode,
            "singlePoint": self.single_point,
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
    encircled_energy_na: str = DEFAULT_ENCIRCLED_ENERGY_NA
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
            encircled_energy_na=str(
                values.get("encircledEnergyNa", DEFAULT_ENCIRCLED_ENERGY_NA)
            ).strip(),
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
    *,
    encircled_energy_na: str = DEFAULT_ENCIRCLED_ENERGY_NA,
) -> tuple[ReportFieldDefinition, ...]:
    standard = SPECTRUM_FIELD_DEFINITIONS if report_type is ShippingReportType.SPECTRUM else POLE_FIELD_DEFINITIONS
    if report_type is ShippingReportType.SPECTRUM:
        standard = tuple(
            replace(field, label=f"NA {encircled_energy_na} Encircled Energy")
            if field.key == "na017Energy"
            else field
            for field in standard
        )
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


def _compatibility(
    session: Mapping[str, str],
    schema_version: str,
    *,
    point_count: int,
) -> WorkbookCompatibility:
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
            if str(session.get("测试模式", "")).strip().lower() == "manual" and point_count == 1:
                return WorkbookCompatibility(
                    kind="verified_manual_single_point",
                    status=status,
                    message="已验证为手动单点测试，可生成出货报告。",
                    requires_legacy_confirmation=False,
                )
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
            compatibility=_compatibility(
                session,
                schema_version,
                point_count=len(points),
            ),
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
    if request.report_type is ShippingReportType.SPECTRUM:
        encircled_energy_na = _required_number(request.encircled_energy_na, "Encircled Energy NA")
        if not 0 < encircled_energy_na <= 1:
            raise ValueError("Encircled Energy NA 必须大于 0 且不大于 1")
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
    measured_suggestions = suggested_field_values(
        inspection,
        request.report_type,
        request.operating_current_a,
    )
    for key, selected in request.fields.items():
        if key not in allowed_keys:
            raise ValueError(f"未知报告参数：{key}")
        if selected.include:
            included.append(key)
            if not selected.value:
                definition = next(item for item in definitions if item.key == key)
                raise ValueError(f"请填写已勾选参数“{definition.label}”")
            if key not in custom_key_set:
                definition = next(item for item in definitions if item.key == key)
                unavailable_measured_value = (
                    definition.measured_key is not None
                    and key not in measured_suggestions
                    and selected.value.strip().upper() == UNAVAILABLE_MEASURED_VALUE
                )
                if not unavailable_measured_value:
                    _required_number(selected.value, definition.label)
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
            value=suggestions.get(definition.key, UNAVAILABLE_MEASURED_VALUE),
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
    """Return report preferences without trusting stored shapes."""

    def stored_float(key: str, default: float) -> float:
        try:
            value = float(settings.get(key, default))
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    output: dict[str, Any] = {}
    for report_type in ShippingReportType:
        prefix = f"shipping_report/{report_type.value}/"
        raw_selected = settings.get(prefix + "selected_fields", None)
        try:
            selected = json.loads(str(raw_selected)) if raw_selected is not None else []
            manual_values = json.loads(str(settings.get(prefix + "manual_values", "{}")))
        except (TypeError, ValueError, json.JSONDecodeError):
            selected, manual_values = [], {}
        if not isinstance(selected, list):
            selected = []
        if raw_selected is None and report_type is ShippingReportType.POLE:
            selected = list(DEFAULT_POLE_SELECTED_FIELD_KEYS)
        if not isinstance(manual_values, dict):
            manual_values = {}
        try:
            custom_fields = json.loads(str(settings.get(prefix + "custom_fields", "[]")))
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
            preference["encircledEnergyNa"] = (
                str(settings.get(prefix + "encircled_energy_na", DEFAULT_ENCIRCLED_ENERGY_NA)).strip()
                or DEFAULT_ENCIRCLED_ENERGY_NA
            )
            mode = str(settings.get(prefix + "axis_mode", SpectrumAxisMode.RELATIVE_DB.value))
            if mode not in {item.value for item in SpectrumAxisMode}:
                mode = SpectrumAxisMode.RELATIVE_DB.value
            preference["spectrumAxis"] = {
                "mode": mode,
                "minimum": stored_float(prefix + "axis_minimum", -80.0),
                "maximum": stored_float(prefix + "axis_maximum", 0.0),
            }
            wavelength_minimum = _finite_or_none(settings.get(prefix + "wavelength_minimum", None))
            wavelength_maximum = _finite_or_none(settings.get(prefix + "wavelength_maximum", None))
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
    settings.set(prefix + "selected_fields", json.dumps(selected, ensure_ascii=False))
    settings.set(prefix + "manual_values", json.dumps(manual_values, ensure_ascii=False))
    settings.set(
        prefix + "custom_fields",
        json.dumps([_field_payload(item) for item in request.custom_fields], ensure_ascii=False),
    )
    if request.report_type is ShippingReportType.SPECTRUM:
        settings.set(prefix + "encircled_energy_na", request.encircled_energy_na)
    if request.spectrum_axis is not None:
        settings.set(prefix + "axis_mode", request.spectrum_axis.mode.value)
        settings.set(prefix + "axis_minimum", request.spectrum_axis.minimum)
        settings.set(prefix + "axis_maximum", request.spectrum_axis.maximum)
        if (
            request.spectrum_axis.wavelength_minimum is not None
            and request.spectrum_axis.wavelength_maximum is not None
        ):
            settings.set(prefix + "wavelength_minimum", request.spectrum_axis.wavelength_minimum)
            settings.set(prefix + "wavelength_maximum", request.spectrum_axis.wavelength_maximum)
        else:
            settings.delete(prefix + "wavelength_minimum")
            settings.delete(prefix + "wavelength_maximum")
    settings.flush()


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
        from .shipping_report_headless import render_pdf

        render_pdf(temporary, inspection, request)
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
    page_width_px: int = 1440,
) -> list[str]:
    """Render the final report layout into PNG pages for live preview."""

    if page_width_px < 240 or page_width_px > 1600:
        raise ValueError("预览宽度必须在 240 到 1600 像素之间")
    inspection = inspect_shipping_workbook(source_path)
    validate_shipping_report_request(inspection, request)
    from .shipping_report_headless import render_preview_pages

    return render_preview_pages(inspection, request, page_width_px=page_width_px)


def _resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / relative


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
