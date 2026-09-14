"""Toolkit-free renderer for the approved shipping report layout."""

from __future__ import annotations

import base64
import io
import math
import os
import re
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from matplotlib import font_manager
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.offsetbox import AnnotationBbox, HPacker, TextArea
from matplotlib.patches import PathPatch, Rectangle
from matplotlib.path import Path as MatplotlibPath
from matplotlib.ticker import FixedLocator
from PIL import Image

from .shipping_report import (
    SelectedReportField,
    ShippingReportLanguage,
    ShippingReportType,
    SpectrumAxisMode,
    _axis_tick_values,
    _balanced_report_columns,
    _format_axis_tick,
    _format_performance_parameters_title,
    _format_pole_summary_value,
    _format_scientific_axis_tick,
    _reference_axis_bounds,
    _reference_axis_step,
    _report_definitions,
    _resource_path,
)


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
SPECTRUM_LEGEND_SINGLE_COLUMN_LIMIT = 10


def _spectrum_legend_column_count(series_count: int) -> int:
    """Keep short legends compact and split overflowing spectrum legends."""

    return 2 if series_count > SPECTRUM_LEGEND_SINGLE_COLUMN_LIMIT else 1


def _register_font(path: Path) -> None:
    if not path.is_file():
        return
    try:
        font_manager.fontManager.addfont(str(path))
    except (OSError, RuntimeError):
        pass


_WINDOWS_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
_TIMES_REGULAR = _WINDOWS_FONTS / "times.ttf"
_TIMES_BOLD = _WINDOWS_FONTS / "timesbd.ttf"
_ARIAL_REGULAR = _WINDOWS_FONTS / "arial.ttf"
_ARIAL_BOLD = _WINDOWS_FONTS / "arialbd.ttf"
_CHINESE_REGULAR = _WINDOWS_FONTS / "msyh.ttc"
for _font_path in (
    _TIMES_REGULAR,
    _TIMES_BOLD,
    _ARIAL_REGULAR,
    _ARIAL_BOLD,
    _CHINESE_REGULAR,
    _resource_path("assets/report/report_font.ttf"),
):
    _register_font(_font_path)


def _font(size: float, *, bold: bool = False, chart: bool = False, chinese: bool = False) -> font_manager.FontProperties:
    if chinese:
        families = ("Microsoft YaHei", "DejaVu Sans")
    elif chart:
        families = ("Arial", "Microsoft YaHei", "DejaVu Sans")
    else:
        families = ("Times New Roman", "Microsoft YaHei", "DejaVu Serif")
    return font_manager.FontProperties(family=families, size=size, weight="bold" if bold else "normal")


def _figure_box(x: float, y: float, width: float, height: float) -> list[float]:
    return [x / A4_WIDTH_MM, (A4_HEIGHT_MM - y - height) / A4_HEIGHT_MM, width / A4_WIDTH_MM, height / A4_HEIGHT_MM]


def _point(x: float, y: float) -> tuple[float, float]:
    return x / A4_WIDTH_MM, 1.0 - y / A4_HEIGHT_MM


def _line_width(width_mm: float) -> float:
    return width_mm * 72.0 / 25.4


def _new_figure() -> Figure:
    figure = Figure(figsize=(A4_WIDTH_MM / 25.4, A4_HEIGHT_MM / 25.4), dpi=144, facecolor="white")
    FigureCanvasAgg(figure)
    return figure


def _draw_line(
    figure: Figure,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    color: str,
    width_mm: float,
) -> None:
    first = _point(x1, y1)
    second = _point(x2, y2)
    figure.add_artist(Line2D(
        [first[0], second[0]],
        [first[1], second[1]],
        transform=figure.transFigure,
        color=color,
        linewidth=_line_width(width_mm),
        solid_capstyle="butt",
    ))


def _draw_rect(
    figure: Figure,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    facecolor: str = "none",
    edgecolor: str = "none",
    width_mm: float = 0.0,
) -> None:
    box = _figure_box(x, y, width, height)
    figure.add_artist(Rectangle(
        (box[0], box[1]),
        box[2],
        box[3],
        transform=figure.transFigure,
        facecolor=facecolor,
        edgecolor=edgecolor,
        linewidth=_line_width(width_mm),
        clip_on=False,
    ))


def _draw_text(
    figure: Figure,
    x: float,
    y: float,
    width: float,
    height: float,
    text: str,
    *,
    size: float,
    color: str = "#161616",
    bold: bool = False,
    chart: bool = False,
    chinese: bool = False,
    horizontal: str = "left",
    vertical: str = "center",
    linespacing: float = 1.0,
    letter_spacing_pt: float = 0.0,
) -> None:
    x_position = x if horizontal == "left" else x + width if horizontal == "right" else x + width / 2.0
    y_position = y if vertical == "top" else y + height if vertical == "bottom" else y + height / 2.0
    if letter_spacing_pt > 0 and text:
        properties = {"color": color, "fontproperties": _font(size, bold=bold, chart=chart, chinese=chinese)}
        letters = HPacker(
            children=[TextArea(character, textprops=properties) for character in text],
            align="baseline", pad=0, sep=letter_spacing_pt,
        )
        figure.add_artist(AnnotationBbox(
            letters, _point(x_position, y_position), xycoords=figure.transFigure,
            box_alignment=(
                {"left": 0, "center": 0.5, "right": 1}[horizontal],
                {"bottom": 0, "center": 0.5, "top": 1}[vertical],
            ),
            frameon=False, pad=0, annotation_clip=False,
        ))
        return
    figure.text(
        x_position / A4_WIDTH_MM,
        1.0 - y_position / A4_HEIGHT_MM,
        text,
        transform=figure.transFigure,
        color=color,
        fontproperties=_font(size, bold=bold, chart=chart, chinese=chinese),
        ha=horizontal,
        va=vertical,
        linespacing=linespacing,
    )


def _image_array(relative_path: str, crop: tuple[int, int, int, int] | None = None) -> np.ndarray:
    with Image.open(_resource_path(relative_path)) as source:
        image = source.convert("RGBA")
        if crop is not None:
            image = image.crop(crop)
        return np.asarray(image.copy())


def _draw_image(
    figure: Figure,
    relative_path: str,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    crop: tuple[int, int, int, int] | None = None,
) -> None:
    axes = figure.add_axes(_figure_box(x, y, width, height), frameon=False)
    axes.imshow(_image_array(relative_path, crop), interpolation="lanczos", aspect="auto")
    axes.set_axis_off()


def _draw_brand_mark(figure: Figure, x: float, y: float, width: float, height: float) -> None:
    """Draw the exact three paths from the approved SVG brand mark."""

    axes = figure.add_axes(_figure_box(x, y, width, height), frameon=False)
    axes.set_xlim(90.0, 674.0)
    axes.set_ylim(520.0, 112.0)
    axes.set_aspect("equal", adjustable="box", anchor="W")
    axes.set_axis_off()

    paths = (
        (
            "#e83228",
            (
                (174.0, 227.5),
                (180.0, 187.0), (190.0, 145.0), (207.0, 126.5),
                (221.0, 114.0), (250.0, 112.5), (278.0, 112.5),
                (673.5, 114.0),
                (672.0, 146.0), (660.0, 176.0), (630.0, 198.5),
                (620.0, 207.0), (604.0, 213.5), (594.0, 213.5),
                (213.0, 214.5),
                (196.0, 214.5), (182.0, 219.0), (174.0, 227.5),
                (174.0, 227.5),
            ),
            (
                MatplotlibPath.MOVETO,
                MatplotlibPath.CURVE4, MatplotlibPath.CURVE4, MatplotlibPath.CURVE4,
                MatplotlibPath.CURVE4, MatplotlibPath.CURVE4, MatplotlibPath.CURVE4,
                MatplotlibPath.LINETO,
                MatplotlibPath.CURVE4, MatplotlibPath.CURVE4, MatplotlibPath.CURVE4,
                MatplotlibPath.CURVE4, MatplotlibPath.CURVE4, MatplotlibPath.CURVE4,
                MatplotlibPath.LINETO,
                MatplotlibPath.CURVE4, MatplotlibPath.CURVE4, MatplotlibPath.CURVE4,
                MatplotlibPath.CLOSEPOLY,
            ),
        ),
        (
            "#6cb92c",
            (
                (129.0, 374.5), (148.5, 293.0),
                (153.0, 276.0), (166.0, 265.0), (183.0, 261.5),
                (588.5, 261.0),
                (583.0, 296.0), (565.0, 324.0), (534.0, 346.5),
                (519.0, 355.0), (501.0, 360.5), (487.0, 360.5),
                (181.0, 359.5),
                (159.0, 359.5), (142.0, 365.0), (129.0, 374.5),
                (129.0, 374.5),
            ),
            (
                MatplotlibPath.MOVETO, MatplotlibPath.LINETO,
                MatplotlibPath.CURVE4, MatplotlibPath.CURVE4, MatplotlibPath.CURVE4,
                MatplotlibPath.LINETO,
                MatplotlibPath.CURVE4, MatplotlibPath.CURVE4, MatplotlibPath.CURVE4,
                MatplotlibPath.CURVE4, MatplotlibPath.CURVE4, MatplotlibPath.CURVE4,
                MatplotlibPath.LINETO,
                MatplotlibPath.CURVE4, MatplotlibPath.CURVE4, MatplotlibPath.CURVE4,
                MatplotlibPath.CLOSEPOLY,
            ),
        ),
        (
            "#244a9e",
            (
                (91.0, 519.5),
                (95.0, 489.0), (106.0, 446.0), (124.0, 417.5),
                (133.0, 409.0), (145.0, 405.0), (156.0, 404.5),
                (503.5, 405.0),
                (497.0, 435.0), (480.0, 468.0), (459.0, 488.5),
                (447.0, 498.0), (431.0, 505.5), (419.0, 505.5),
                (123.0, 505.5),
                (108.0, 505.5), (98.0, 511.0), (91.0, 519.5),
                (91.0, 519.5),
            ),
            (
                MatplotlibPath.MOVETO,
                MatplotlibPath.CURVE4, MatplotlibPath.CURVE4, MatplotlibPath.CURVE4,
                MatplotlibPath.CURVE4, MatplotlibPath.CURVE4, MatplotlibPath.CURVE4,
                MatplotlibPath.LINETO,
                MatplotlibPath.CURVE4, MatplotlibPath.CURVE4, MatplotlibPath.CURVE4,
                MatplotlibPath.CURVE4, MatplotlibPath.CURVE4, MatplotlibPath.CURVE4,
                MatplotlibPath.LINETO,
                MatplotlibPath.CURVE4, MatplotlibPath.CURVE4, MatplotlibPath.CURVE4,
                MatplotlibPath.CLOSEPOLY,
            ),
        ),
    )
    for color, vertices, codes in paths:
        axes.add_patch(PathPatch(MatplotlibPath(vertices, codes), facecolor=color, edgecolor="none"))


class _HeadlessShippingReportRenderer:
    def __init__(self, inspection: Any, request: Any) -> None:
        self.inspection = inspection
        self.request = request
        operating_point = inspection.point_for_current(request.operating_current_a)
        self.chart_points = tuple(
            point for point in inspection.points if point.current_a <= operating_point.current_a
        )
        self.figures: list[Figure] = []

    @property
    def _chinese(self) -> bool:
        return self.request.report_language is ShippingReportLanguage.CHINESE

    def _localized(self, english: str, chinese: str) -> str:
        return chinese if self._chinese else english

    def build(self) -> list[Figure]:
        if self.request.report_type is ShippingReportType.SPECTRUM:
            self._render_spectrum_report()
        else:
            self._render_pole_report()
        return self.figures

    def _new_page(self, continuation_title: str = "") -> Figure:
        figure = _new_figure()
        self.figures.append(figure)
        self._draw_header(figure)
        self._draw_title(figure, continuation_title)
        self._draw_footer(figure, len(self.figures))
        return figure

    def _draw_header(self, figure: Figure) -> None:
        _draw_brand_mark(figure, CONTENT_LEFT_MM, HEADER_ARTWORK_TOP_MM, 13.5, 11.9)
        wordmark_width = 49.0
        wordmark_height = wordmark_width * 53.0 / 443.0
        _draw_image(
            figure,
            "assets/report/everbright_header_reference.png",
            33.0,
            HEADER_ARTWORK_TOP_MM + (11.9 - wordmark_height) / 2.0,
            wordmark_width,
            wordmark_height,
            crop=(254, 95, 697, 148),
        )
        company_x = 137.0
        company_width = CONTENT_RIGHT_MM - company_x
        company_lines = (
            (
                ("苏州长光华芯光电技术股份有限公司", True),
                ("江苏省苏州市高新区漓江路 56 号", False),
                ("电话：+86-512-69372570", False),
                ("传真：+86-512-69372559", False),
            )
            if self._chinese
            else (
                ("Suzhou Everbright Photonics Co., Ltd.", True),
                ("No. 56 Lijiang Road, Suzhou New District", False),
                ("Tel: +86-512-69372570", False),
                ("Fax: +86-512-69372559", False),
            )
        )
        company_row_height = 3.3
        company_row_spacing = 4.3
        for index, (line, bold) in enumerate(company_lines):
            _draw_text(
                figure,
                company_x,
                HEADER_ARTWORK_TOP_MM - 0.3 + index * company_row_spacing,
                company_width,
                company_row_height,
                line,
                size=7.2,
                bold=bold,
                chart=True,
                chinese=self._chinese,
                horizontal="right",
            )

    def _draw_title(self, figure: Figure, continuation_title: str) -> None:
        title = self.request.product_name if not continuation_title else f"{self.request.product_name} - {continuation_title}"
        _draw_text(figure, CONTENT_LEFT_MM, TITLE_TOP_MM, CONTENT_RIGHT_MM - CONTENT_LEFT_MM, 10.0, title, size=16.0, bold=True, color="#090909", chinese=self._chinese, horizontal="center")
        serial_number = self._localized(f"TEST SN: {self.request.sn}", f"测试 SN：{self.request.sn}")
        _draw_text(figure, CONTENT_LEFT_MM, SERIAL_NUMBER_TOP_MM, CONTENT_RIGHT_MM - CONTENT_LEFT_MM, 8.0, serial_number, size=16.0, bold=True, color="#090909", chinese=self._chinese, horizontal="center")
        _draw_line(figure, CONTENT_LEFT_MM, TITLE_DIVIDER_Y_MM, CONTENT_RIGHT_MM, TITLE_DIVIDER_Y_MM, color="#d7dade", width_mm=0.18)

    def _draw_footer(self, figure: Figure, page_number: int) -> None:
        _draw_line(figure, CONTENT_LEFT_MM, FOOTER_TOP_MM, CONTENT_RIGHT_MM, FOOTER_TOP_MM, color="#d1d1d1", width_mm=0.25)
        notice = self._localized(
            "Notice: Everbright Photonics continuously improves its products to provide customers with outstanding quality and reliability.\n"
            "Specifications and product descriptions may be changed at any time without notice. Warranty support is provided to ensure\n"
            "customer satisfaction. For complete information, please contact our sales representative.",
            "声明：长光华芯持续改进产品，为客户提供卓越的质量与可靠性。产品规格和说明可能随时变更，恕不另行通知。\n"
            "我们提供质保支持以确保客户满意。如需完整信息，请联系销售代表。",
        )
        _draw_text(figure, CONTENT_LEFT_MM, 269.0, 142.0, 20.0, notice, size=8.0, color="#1a1a1a", chinese=self._chinese, linespacing=1.5)
        warning = _image_array("assets/report/laser_warning_reference.png")
        warning_height = 18.0
        warning_width = warning_height * warning.shape[1] / warning.shape[0]
        _draw_image(figure, "assets/report/laser_warning_reference.png", CONTENT_RIGHT_MM - warning_width, 289.0 - warning_height, warning_width, warning_height)
        _draw_text(figure, 90.0, 291.0, 30.0, 4.0, f"第 {page_number} 页", size=6.3, color="#666666", chinese=True, horizontal="center")

    def _definitions(self, report_type: ShippingReportType) -> tuple[Any, ...]:
        return _report_definitions(
            report_type,
            self.request.custom_fields,
            encircled_energy_na=self.request.encircled_energy_na,
            report_language=self.request.report_language,
        )

    def _selected_fields(self, definitions: Iterable[Any]) -> list[tuple[Any, str]]:
        output: list[tuple[Any, str]] = []
        for definition in definitions:
            selected = self.request.fields.get(definition.key, SelectedReportField(False, ""))
            if selected.include:
                output.append((definition, selected.value))
        return output

    def _draw_section_title(
        self,
        figure: Figure,
        top: float,
        title: str,
        *,
        height: float = 7.0,
        font_size: float = 10.5,
        accent_width: float = 1.4,
        accent_height: float | None = None,
    ) -> None:
        actual_accent_height = height if accent_height is None else min(accent_height, height)
        accent_top = top + (height - actual_accent_height) / 2.0
        _draw_rect(figure, CONTENT_LEFT_MM, accent_top, accent_width, actual_accent_height, facecolor="#0b4b82")
        _draw_text(figure, 21.0, top, 100.0, height, title, size=font_size, color="#0b4b82", bold=True, chinese=self._chinese)

    def _render_pole_report(self) -> None:
        fields = self._selected_fields(self._definitions(ShippingReportType.POLE))
        remaining = list(self.inspection.points)
        figure = self._new_page()
        summary_height = self._draw_pole_summary(figure, POLE_SUMMARY_TOP_MM, fields)
        chart_top = POLE_SUMMARY_TOP_MM + summary_height + 6.0
        chart_height = 54.0
        self._draw_power_efficiency_chart(figure, 31.0, chart_top, 148.0, chart_height)
        table_top = chart_top + chart_height
        room_rows = max(1, min(12, int((FOOTER_TOP_MM - table_top - 5.0) / 6.0) - 1))
        self._draw_detail_table(figure, table_top, remaining[:room_rows])
        remaining = remaining[room_rows:]
        while remaining:
            figure = self._new_page(self._localized("Detailed Measurement Data (Continued)", "详细测量数据（续）"))
            capacity = max(1, int((FOOTER_TOP_MM - 76.0) / 6.0) - 1)
            self._draw_detail_table(figure, 74.0, remaining[:capacity])
            remaining = remaining[capacity:]

    def _draw_pole_summary(self, figure: Figure, top: float, fields: list[tuple[Any, str]]) -> float:
        title_height = 8.0
        title_gap = 3.0
        header_height = 7.2
        row_height = 7.4
        table_x, table_width = 18.0, 174.0
        parameter_width = table_width * 0.45
        value_width = table_width * 0.30
        unit_width = table_width - parameter_width - value_width
        cell_padding = 3.5
        self._draw_section_title(figure, top, _format_performance_parameters_title(self.request.operating_current_a, self.request.report_language), height=title_height, font_size=14.0, accent_width=0.75, accent_height=5.0)
        table_top = top + title_height + title_gap
        table_height = header_height + len(fields) * row_height
        _draw_rect(figure, table_x, table_top, table_width, header_height, facecolor="#f6f8fa")
        _draw_line(figure, table_x, table_top, table_x + table_width, table_top, color="#dfe4e8", width_mm=0.18)
        _draw_line(figure, table_x, table_top + header_height, table_x + table_width, table_top + header_height, color="#dfe4e8", width_mm=0.18)
        _draw_text(figure, table_x + cell_padding, table_top, parameter_width - 2 * cell_padding, header_height, self._localized("Parameter", "参数"), size=10.3, color="#0b4b82", bold=True, chinese=self._chinese)
        _draw_text(figure, table_x + parameter_width + cell_padding, table_top, value_width - 2 * cell_padding, header_height, self._localized("Value", "数值"), size=10.3, color="#0b4b82", bold=True, chinese=self._chinese, horizontal="center")
        _draw_text(figure, table_x + parameter_width + value_width + cell_padding, table_top, unit_width - 2 * cell_padding, header_height, self._localized("Unit", "单位"), size=10.3, color="#0b4b82", bold=True, chinese=self._chinese, horizontal="center")
        for index, (definition, value) in enumerate(fields):
            y = table_top + header_height + index * row_height
            _draw_text(figure, table_x + cell_padding, y, parameter_width - 2 * cell_padding, row_height, definition.label, size=POLE_TABLE_VALUE_FONT_SIZE, color="#252a2e", chinese=self._chinese)
            _draw_text(figure, table_x + parameter_width + cell_padding, y, value_width - 2 * cell_padding, row_height, _format_pole_summary_value(definition, value), size=POLE_TABLE_VALUE_FONT_SIZE, color="#252a2e", horizontal="center")
            _draw_text(figure, table_x + parameter_width + value_width + cell_padding, y, unit_width - 2 * cell_padding, row_height, definition.unit, size=10.5, color="#252a2e", horizontal="center")
            _draw_line(figure, table_x, y + row_height, table_x + table_width, y + row_height, color="#dfe4e8", width_mm=0.18)
        return title_height + title_gap + table_height

    def _render_spectrum_report(self) -> None:
        figure = self._new_page()
        fields = self._selected_fields(self._definitions(ShippingReportType.SPECTRUM))
        left, right = _balanced_report_columns(fields)
        self._draw_section_title(figure, POLE_SUMMARY_TOP_MM, _format_performance_parameters_title(self.request.operating_current_a, self.request.report_language), height=8.0, font_size=14.0, accent_width=0.75, accent_height=5.0)
        summary_height = self._draw_spectrum_summary(figure, 72.0, left, right)
        chart_top = min(116.0, 75.0 + summary_height + 4.0)
        available = FOOTER_TOP_MM - chart_top - 7.0
        section_gap = 2.0
        section_title_height = 7.0
        chart_gap = 1.0
        chart_height = max(45.0, (available - section_gap - section_title_height - chart_gap) / 2.0)
        self._draw_power_efficiency_chart(figure, 23.0, chart_top, 164.0, chart_height)
        spectrum_title_top = chart_top + chart_height + section_gap
        self._draw_section_title(figure, spectrum_title_top, self._localized("Spectrum", "光谱"), height=section_title_height, font_size=14.0, accent_width=0.75, accent_height=5.0)
        self._draw_spectrum_chart(figure, 23.0, spectrum_title_top + section_title_height + chart_gap, 164.0, chart_height)

    def _draw_spectrum_summary(
        self,
        figure: Figure,
        top: float,
        left: list[tuple[Any, str]],
        right: list[tuple[Any, str]],
    ) -> float:
        row_count = max(len(left), len(right), 1)
        row_height = min(6.1, 36.0 / row_count)
        height = row_count * row_height
        width = 152.0
        x = (A4_WIDTH_MM - width) / 2.0
        half_width = width / 2.0
        cell_padding, column_gap = 2.0, 2.0
        value_width, unit_width = 18.0, 13.0
        label_width = half_width - cell_padding - value_width - unit_width - 2 * column_gap
        value_offset = label_width + column_gap
        unit_offset = value_offset + value_width + column_gap
        _draw_line(figure, x, top, x + width, top, color="#333333", width_mm=0.2)
        _draw_line(figure, x, top + height, x + width, top + height, color="#333333", width_mm=0.2)
        _draw_line(figure, x + half_width, top, x + half_width, top + height, color="#333333", width_mm=0.2)
        for side_x, values in ((x + cell_padding, left), (x + half_width + cell_padding, right)):
            for index, (definition, value) in enumerate(values):
                y = top + index * row_height
                # A hair space loosens adjacent Chinese glyphs without spacing Latin abbreviations.
                label = re.sub(r"(?<=[\u3400-\u9fff])(?=[\u3400-\u9fff])", "\u200a", definition.label) if self._chinese else definition.label
                _draw_text(figure, side_x, y, label_width, row_height, label, size=8.5, bold=True, chinese=self._chinese)
                if re.fullmatch(r"[+-]?\d+\.\d+", value):
                    value = value.rstrip("0").rstrip(".")
                spacing = 0.35 if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value) else 0.0
                _draw_text(figure, side_x + value_offset, y, value_width, row_height, value, size=8.6, bold=True, horizontal="right", letter_spacing_pt=spacing)
                _draw_text(figure, side_x + unit_offset, y, unit_width, row_height, definition.unit, size=8.2)
        return height

    def _draw_power_efficiency_chart(self, figure: Figure, x: float, y: float, width: float, height: float) -> None:
        points = self.chart_points
        currents = [point.current_a for point in points]
        powers = [point.power_w for point in points]
        efficiencies = [point.efficiency * 100.0 for point in points]
        plot_x, plot_y = x + 17.0, y + 3.0
        plot_width, plot_height = width - 35.0, height - 15.0
        axes = figure.add_axes(_figure_box(plot_x, plot_y, plot_width, plot_height), facecolor="none")
        efficiency_axes = axes.twinx()

        x_data_min, x_data_max = min(currents), max(currents)
        if math.isclose(x_data_min, x_data_max, rel_tol=1e-12, abs_tol=1e-12):
            x_step = _reference_axis_step(max(abs(x_data_min), 1.0), target_intervals=4)
            x_min = x_data_min - 0.75 * x_step
            x_max = x_data_max + 0.75 * x_step
        else:
            unique_currents = sorted(set(currents))
            measured_steps = [current - previous for previous, current in zip(unique_currents, unique_currents[1:]) if current > previous]
            x_step = _reference_axis_step(min(measured_steps), target_intervals=1)
            x_padding = max(0.25 * x_step, 0.04 * (x_data_max - x_data_min))
            x_min = x_data_min - x_padding
            x_max = x_data_max + x_padding
        power_min, power_max, power_step = _reference_axis_bounds(powers, singleton_span_fraction=1.2, minimum=0.0)
        efficiency_min, efficiency_max, efficiency_step = _reference_axis_bounds(efficiencies, singleton_span_fraction=0.5, minimum=0.0, snap_to_major_ticks=True)

        x_ticks = _axis_tick_values(x_min, x_max, x_step)
        power_ticks = _axis_tick_values(power_min, power_max, power_step)
        efficiency_ticks = _axis_tick_values(efficiency_min, efficiency_max, efficiency_step)
        axes.set_xlim(x_min, x_max)
        axes.set_ylim(power_min, power_max)
        efficiency_axes.set_ylim(efficiency_min, efficiency_max)
        axes.xaxis.set_major_locator(FixedLocator(x_ticks))
        axes.xaxis.set_minor_locator(FixedLocator(_axis_tick_values(x_min, x_max, x_step / 2.0)))
        axes.yaxis.set_major_locator(FixedLocator(power_ticks))
        axes.yaxis.set_minor_locator(FixedLocator(_axis_tick_values(power_min, power_max, power_step / 2.0)))
        efficiency_axes.yaxis.set_major_locator(FixedLocator(efficiency_ticks))
        efficiency_axes.yaxis.set_minor_locator(FixedLocator(_axis_tick_values(efficiency_min, efficiency_max, efficiency_step / 2.0)))
        axes.set_xticklabels([_format_axis_tick(value, x_step) for value in x_ticks], fontproperties=_font(8.0, chart=True, chinese=self._chinese))
        axes.set_yticklabels([_format_axis_tick(value, power_step) for value in power_ticks], fontproperties=_font(8.0, chart=True, chinese=self._chinese), color="#0072B2")
        efficiency_axes.set_yticklabels([_format_axis_tick(value, efficiency_step) for value in efficiency_ticks], fontproperties=_font(8.0, chart=True, chinese=self._chinese), color="#D55E00")
        axes.tick_params(axis="x", which="major", direction="in", top=True, bottom=True, length=4.4, width=0.9, colors="#202020", pad=4)
        axes.tick_params(axis="x", which="minor", direction="in", top=True, bottom=True, length=2.4, width=0.62, colors="#202020")
        axes.tick_params(axis="y", which="major", direction="in", left=True, right=False, length=4.4, width=0.9, colors="#0072B2", pad=4)
        axes.tick_params(axis="y", which="minor", direction="in", left=True, right=False, length=2.4, width=0.62, colors="#0072B2")
        efficiency_axes.tick_params(axis="y", which="major", direction="in", left=False, right=True, length=4.4, width=0.9, colors="#D55E00", pad=4)
        efficiency_axes.tick_params(axis="y", which="minor", direction="in", left=False, right=True, length=2.4, width=0.62, colors="#D55E00")
        for label in axes.get_xticklabels():
            label.set_color("#202020")
        axes.spines["left"].set_color("#0072B2")
        axes.spines["right"].set_visible(False)
        axes.spines["top"].set_color("#202020")
        axes.spines["bottom"].set_color("#202020")
        efficiency_axes.spines["right"].set_color("#D55E00")
        efficiency_axes.spines["left"].set_visible(False)
        efficiency_axes.spines["top"].set_visible(False)
        efficiency_axes.spines["bottom"].set_visible(False)
        for spine in (*axes.spines.values(), *efficiency_axes.spines.values()):
            spine.set_linewidth(_line_width(0.42))

        single_point = len(points) == 1
        power_label = self._localized("Output power", "输出功率")
        efficiency_label = self._localized("E-O efficiency", "电光效率")
        power_line, = axes.plot(currents, powers, color="#0072B2", linewidth=_line_width(0.60), marker="o", markersize=6.5, markeredgecolor="white", markeredgewidth=_line_width(0.24), label=power_label, clip_on=True, zorder=3)
        efficiency_line, = efficiency_axes.plot(
            currents,
            efficiencies,
            color="#D55E00",
            linewidth=_line_width(0.60),
            marker="s",
            markersize=7.4 if single_point else 6.0,
            markerfacecolor="none" if single_point else "#D55E00",
            markeredgecolor="#D55E00" if single_point else "white",
            markeredgewidth=_line_width(0.52 if single_point else 0.24),
            label=efficiency_label,
            clip_on=True,
            zorder=4,
        )
        axes.set_xlabel(self._localized("Drive current (A)", "驱动电流 (A)"), color="#202020", fontproperties=_font(8.8, chart=True, chinese=self._chinese), labelpad=6)
        axes.set_ylabel(self._localized("Output power (W)", "输出功率 (W)"), color="#0072B2", fontproperties=_font(8.8, chart=True, chinese=self._chinese), labelpad=8)
        efficiency_axes.set_ylabel(self._localized("E-O efficiency (%)", "电光效率 (%)"), color="#D55E00", fontproperties=_font(8.8, chart=True, chinese=self._chinese), labelpad=8, rotation=270, va="bottom")
        axes.legend(
            [power_line, efficiency_line],
            [power_label, efficiency_label],
            loc="lower right",
            frameon=False,
            borderaxespad=0.6,
            handlelength=3.5,
            handletextpad=0.6,
            labelspacing=0.8,
            prop=_font(8.0, chart=True, chinese=self._chinese),
        )

    def _draw_spectrum_chart(self, figure: Figure, x: float, y: float, width: float, height: float) -> None:
        axis = self.request.spectrum_axis
        assert axis is not None
        _draw_text(figure, x, y, width, 8.0, self._localized("Spectrum at Different Currents", "不同电流下的光谱"), size=10.2, color="#343434", bold=True, chart=True, chinese=self._chinese, horizontal="center")
        plot_x, plot_y = x + 17.0, y + 10.0
        plot_width, plot_height = width - 35.0, height - 22.0
        axes = figure.add_axes(_figure_box(plot_x, plot_y, plot_width, plot_height), facecolor="none")
        default_x_range = self.inspection.spectrum_wavelength_range
        assert default_x_range is not None
        x_min, x_max = default_x_range if axis.wavelength_minimum is None else (axis.wavelength_minimum, axis.wavelength_maximum)
        axes.set_xlim(x_min, x_max)
        axes.set_ylim(axis.minimum, axis.maximum)
        x_ticks = [x_min + index / 4.0 * (x_max - x_min) for index in range(5)]
        y_ticks = [axis.minimum + index / 4.0 * (axis.maximum - axis.minimum) for index in range(5)]
        axes.xaxis.set_major_locator(FixedLocator(x_ticks))
        axes.yaxis.set_major_locator(FixedLocator(y_ticks))
        axes.set_xticklabels([f"{value:.1f}" for value in x_ticks], fontproperties=_font(9.0, chart=True, chinese=self._chinese))
        axes.set_yticklabels([_format_scientific_axis_tick(value) if axis.mode is SpectrumAxisMode.COUNTS else f"{value:g}" for value in y_ticks], fontproperties=_font(9.0, chart=True, chinese=self._chinese))
        axes.grid(True, which="major", color="#d7dade", linewidth=_line_width(0.13))
        axes.tick_params(axis="x", direction="in", length=4.4, width=0.9, colors="#202020", pad=9)
        axes.tick_params(axis="y", direction="in", length=4.4, width=0.9, colors="#202020", pad=9)
        for spine in axes.spines.values():
            spine.set_color("#202020")
            spine.set_linewidth(_line_width(0.42))
        axes.set_xlabel(self._localized("Wavelength (nm)", "波长 (nm)"), color="#202020", fontproperties=_font(8.8, chart=True, chinese=self._chinese), labelpad=6)
        axes.set_ylabel(self._localized("Intensity", "强度"), color="#202020", fontproperties=_font(8.8, chart=True, chinese=self._chinese), labelpad=8)
        colors = (
            "#376f9f", "#c54e4e", "#799442", "#72548c", "#3f9298", "#d27b36",
            "#254f69", "#8b493d", "#536d2d", "#60456f", "#32767a", "#9b5c22",
        )
        lines: list[Line2D] = []
        labels: list[str] = []
        for index, point in enumerate(self.chart_points):
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
            visible = [(wave, value) for wave, value in zip(point.wavelength, transformed) if math.isfinite(wave) and math.isfinite(value) and x_min <= wave <= x_max]
            stride = max(1, len(visible) // 800)
            line, = axes.plot(
                [item[0] for item in visible[::stride]],
                [item[1] for item in visible[::stride]],
                color=colors[index % len(colors)],
                linewidth=_line_width(0.35),
                clip_on=True,
            )
            lines.append(line)
            labels.append(f"{point.current_a:g} A")
        axes.legend(
            lines,
            labels,
            loc="upper right",
            ncols=_spectrum_legend_column_count(len(labels)),
            frameon=False,
            borderaxespad=0.35,
            handlelength=2.5,
            handletextpad=0.8,
            columnspacing=1.4,
            labelspacing=0.45,
            prop=_font(8.0, chart=True, chinese=self._chinese),
        )

    def _draw_detail_table(self, figure: Figure, top: float, points: list[Any]) -> None:
        self._draw_section_title(figure, top, self._localized("Detailed Measurement Data", "详细测量数据"))
        table_top = top + 9.0
        row_height = 6.0
        column_widths = (43.5, 43.5, 43.5, 43.5)
        headers = (
            ("电流 (A)", "功率 (W)", "电压 (V)", "电光效率 (%)")
            if self._chinese
            else ("Current (A)", "Power (W)", "Voltage (V)", "E-O efficiency (%)")
        )
        x = 18.0
        _draw_rect(figure, x, table_top, sum(column_widths), row_height, facecolor="#0b4b82")
        cursor = x
        for width, header in zip(column_widths, headers):
            _draw_text(figure, cursor, table_top, width, row_height, header, size=7.7, color="white", bold=True, chinese=self._chinese, horizontal="center")
            cursor += width
        for row_index, point in enumerate(points, start=1):
            y = table_top + row_index * row_height
            _draw_rect(figure, x, y, sum(column_widths), row_height, facecolor="#f3f4f5" if row_index % 2 else "white")
            values = (f"{point.current_a:g}", f"{point.power_w:.1f}", f"{point.voltage_v:.2f}", f"{point.efficiency * 100.0:.2f}%")
            cursor = x
            for width, value in zip(column_widths, values):
                _draw_rect(figure, cursor, y, width, row_height, edgecolor="#d0d4d8", width_mm=0.18)
                _draw_text(figure, cursor, y, width, row_height, value, size=DETAIL_TABLE_VALUE_FONT_SIZE, color="#202020", horizontal="center")
                cursor += width


def _build_figures(inspection: Any, request: Any) -> list[Figure]:
    return _HeadlessShippingReportRenderer(inspection, request).build()


def render_pdf(path: Path, inspection: Any, request: Any) -> None:
    figures = _build_figures(inspection, request)
    try:
        report_name = (
            "出货报告"
            if request.report_language is ShippingReportLanguage.CHINESE
            else "Shipping Report"
        )
        creator = (
            "泵驱一体测试系统"
            if request.report_language is ShippingReportLanguage.CHINESE
            else "Pump Driver Integrated Test"
        )
        with PdfPages(path, metadata={"Title": f"{request.product_name} - {request.sn} {report_name}", "Creator": creator}) as document:
            for figure in figures:
                document.savefig(figure, dpi=144, facecolor="white")
    finally:
        for figure in figures:
            figure.clear()


def render_preview_pages(inspection: Any, request: Any, *, page_width_px: int) -> list[str]:
    figures = _build_figures(inspection, request)
    pages: list[str] = []
    try:
        dpi = page_width_px / (A4_WIDTH_MM / 25.4)
        for figure in figures:
            buffer = io.BytesIO()
            figure.savefig(buffer, format="png", dpi=dpi, facecolor="white")
            pages.append(base64.b64encode(buffer.getvalue()).decode("ascii"))
    finally:
        for figure in figures:
            figure.clear()
    return pages
