"""Realtime Matplotlib views with bounded history and throttled rendering."""

from __future__ import annotations

import math
import sys
import time
from collections import deque
from collections.abc import Mapping
from typing import Any

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QGridLayout, QGroupBox, QSizePolicy, QWidget
from matplotlib import rcParams
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.font_manager import FontProperties
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator, MultipleLocator, ScalarFormatter

from .models import SpectrumPeakAnnotation
from .spectrum import SPECTRUM_CENTER_LOCK_HALF_RANGE_NM, find_spectrum_peak_annotations


MAX_CURVE_POINTS = 10000
POWER_PLOT_HISTORY_S = 60.0
PLOT_REFRESH_INTERVAL_S = 0.2
CHART_FIGURE_SIZE = (4.2, 2.8)
CHART_MINIMUM_HEIGHT = 180
CHART_LAYOUT_MARGIN = 12
CHART_LAYOUT_SPACING = 12
DASHBOARD_LAYOUT_MIN_WIDTH = 820
HALF_WIDTH_CHART_LEFT_MARGIN = 0.17
HALF_WIDTH_CHART_RIGHT_MARGIN = 0.84

# Journal-style chart typography. Chinese remains available as a fallback for
# annotations originating from device data, while all Latin text and numbers
# use Times New Roman.
rcParams["font.family"] = "Times New Roman"
rcParams["axes.unicode_minus"] = False
rcParams["mathtext.fontset"] = "stix"
rcParams["font.size"] = 11
rcParams["axes.titlesize"] = 11
rcParams["axes.labelsize"] = 12
rcParams["xtick.labelsize"] = 11
rcParams["ytick.labelsize"] = 11
rcParams["axes.linewidth"] = 0.8
rcParams["xtick.direction"] = "in"
rcParams["ytick.direction"] = "in"
rcParams["xtick.major.size"] = 4
rcParams["ytick.major.size"] = 4

CHINESE_TITLE_FONT = FontProperties(
    family="Microsoft YaHei" if sys.platform == "win32" else "PingFang SC",
    size=11,
    weight="semibold",
)

POWER_LINE_COLOR = "#2f79bd"
STABLE_LINE_COLOR = "#2f8f46"
EFFICIENCY_LINE_COLOR = "#d58a00"
SPECTRUM_LINE_COLOR = "#6b8e23"


class OneDecimalScalarFormatter(ScalarFormatter):
    """Scientific formatter whose mantissa never exceeds one decimal place."""

    def _set_format(self) -> None:
        format_string = "%1.1f"
        if self._usetex or self._useMathText:
            format_string = rf"$\mathdefault{{{format_string}}}$"
        # Matplotlib 3.8+ reads ``format`` while older releases used the
        # private ``_format`` attribute.  Populate both so a failed formatter
        # cannot abort the canvas draw and silently remove the whole y-axis.
        self.format = format_string
        self._format = format_string


class LivePlots:
    """Own the three realtime charts behind a small update/reset interface."""

    COMPATIBILITY_ATTRIBUTES = (
        "curves_layout",
        "power_curve_figure",
        "power_curve_canvas",
        "power_curve_axis",
        "power_curve_line",
        "stable_power_figure",
        "stable_power_canvas",
        "stable_power_axis",
        "stable_power_line",
        "efficiency_axis",
        "efficiency_line",
        "spectrum_curve_figure",
        "spectrum_curve_canvas",
        "spectrum_curve_axis",
        "spectrum_curve_line",
        "power_curve_times",
        "power_curve_values",
        "spectrum_peak_annotations",
        "spectrum_peak_annotation_artists",
    )

    def __init__(self, parent: QWidget) -> None:
        self.palette = parent.palette()
        self._text_color = self.palette.color(QPalette.ColorRole.Text).name()
        window_is_light = self.palette.color(QPalette.ColorRole.Window).lightness() >= 128
        self._danger_color = "#b42318" if window_is_light else "#ff7b72"
        self._power_line_color = POWER_LINE_COLOR if window_is_light else "#63b3ed"
        self._stable_line_color = STABLE_LINE_COLOR if window_is_light else "#5fd07a"
        self._efficiency_line_color = EFFICIENCY_LINE_COLOR if window_is_light else "#f2a51a"
        self._spectrum_line_color = SPECTRUM_LINE_COLOR if window_is_light else "#a8c95f"
        self._grid_color = (
            self.palette.color(QPalette.ColorRole.Mid).name() if window_is_light else "#555860"
        )
        self.group = QGroupBox("实时曲线", parent)
        self.curves_layout = QGridLayout(self.group)
        self.curves_layout.setContentsMargins(
            CHART_LAYOUT_MARGIN,
            CHART_LAYOUT_MARGIN,
            CHART_LAYOUT_MARGIN,
            CHART_LAYOUT_MARGIN,
        )
        self.curves_layout.setHorizontalSpacing(CHART_LAYOUT_SPACING)
        self.curves_layout.setVerticalSpacing(CHART_LAYOUT_SPACING)
        self.power_curve_times: deque[float] = deque(maxlen=MAX_CURVE_POINTS)
        self.power_curve_values: deque[float] = deque(maxlen=MAX_CURVE_POINTS)
        self.spectrum_peak_annotations: list[SpectrumPeakAnnotation] = []
        self.spectrum_peak_annotation_artists: list[Any] = []
        self._last_power_draw_s = -math.inf
        self._last_stable_draw_s = -math.inf
        self._last_spectrum_draw_s = -math.inf
        self._power_stable = False
        self._stable_window_target_s = 0.0
        self._stable_region_artist: Any | None = None
        self._layout_mode = ""
        self._build_charts()
        self.relayout(parent.width())

    def expose_compatibility_attributes(self, target: Any) -> None:
        """Keep historical window attributes while callers migrate to this Module."""
        for name in self.COMPATIBILITY_ATTRIBUTES:
            setattr(target, name, getattr(self, name))

    def _build_charts(self) -> None:
        self.power_curve_figure = Figure(figsize=CHART_FIGURE_SIZE, dpi=100)
        self.power_curve_canvas = FigureCanvas(self.power_curve_figure)
        self._configure_canvas(self.power_curve_canvas)
        self.power_curve_axis = self.power_curve_figure.add_subplot(111)
        (self.power_curve_line,) = self.power_curve_axis.plot(
            [], [], color=self._power_line_color, linewidth=1.35, zorder=2
        )
        self._style_axis(
            self.power_curve_figure,
            self.power_curve_axis,
            title="",
            x_label="Time (s)",
            y_label="Power (W)",
        )
        self.power_value_text = self.power_curve_axis.text(
            0.975,
            0.95,
            "-- W",
            transform=self.power_curve_axis.transAxes,
            ha="right",
            va="top",
            fontsize=15,
            fontweight="bold",
            color=self._text_color,
        )
        self.stability_status_text = self.power_curve_axis.text(
            0.025,
            0.95,
            "STABILIZING",
            transform=self.power_curve_axis.transAxes,
            ha="left",
            va="top",
            fontsize=10,
            fontweight="bold",
            color=self._text_color,
        )
        self.stability_detail_text = self.power_curve_axis.text(
            0.025,
            0.865,
            "0.00 / -- s  |  ΔP -- W ≤ -- W",
            transform=self.power_curve_axis.transAxes,
            ha="left",
            va="top",
            fontsize=8,
            color=self._text_color,
        )
        self.power_curve_figure.subplots_adjust(
            left=HALF_WIDTH_CHART_LEFT_MARGIN,
            right=HALF_WIDTH_CHART_RIGHT_MARGIN,
            top=0.95,
            bottom=0.14,
        )
        self._format_power_axis(self.power_curve_axis)

        self.stable_power_figure = Figure(figsize=CHART_FIGURE_SIZE, dpi=100)
        self.stable_power_canvas = FigureCanvas(self.stable_power_figure)
        self._configure_canvas(self.stable_power_canvas)
        self.stable_power_axis = self.stable_power_figure.add_subplot(111)
        self.efficiency_axis = self.stable_power_axis.twinx()
        (self.stable_power_line,) = self.stable_power_axis.plot(
            [], [], color=self._power_line_color, marker="o", markersize=4, linewidth=1.25
        )
        (self.efficiency_line,) = self.efficiency_axis.plot(
            [], [], color=self._efficiency_line_color, marker="s", markersize=4, linewidth=1.25
        )
        self._style_axis(
            self.stable_power_figure,
            self.stable_power_axis,
            title="",
            x_label="Current (A)",
            y_label="Stable Power (W)",
        )
        self.efficiency_axis.set_ylabel("Efficiency (%)", color=self._efficiency_line_color)
        self.efficiency_axis.set_ylim(20.0, 60.0)
        self.efficiency_axis.yaxis.set_major_locator(MultipleLocator(10.0))
        self.efficiency_axis.minorticks_off()
        self.efficiency_axis.tick_params(
            axis="y",
            colors=self._efficiency_line_color,
            direction="in",
            labelsize=11,
            length=5,
            width=0.9,
        )
        self.efficiency_axis.spines["right"].set_color(self._efficiency_line_color)
        self.stable_power_figure.subplots_adjust(
            left=HALF_WIDTH_CHART_LEFT_MARGIN,
            right=HALF_WIDTH_CHART_RIGHT_MARGIN,
            top=0.95,
            bottom=0.14,
        )
        self._format_power_axis(self.stable_power_axis)

        self.spectrum_curve_figure = Figure(figsize=CHART_FIGURE_SIZE, dpi=100)
        self.spectrum_curve_canvas = FigureCanvas(self.spectrum_curve_figure)
        self._configure_canvas(self.spectrum_curve_canvas)
        self.spectrum_curve_axis = self.spectrum_curve_figure.add_subplot(111)
        (self.spectrum_curve_line,) = self.spectrum_curve_axis.plot(
            [], [], color=self._spectrum_line_color, linewidth=1.25
        )
        self._style_axis(
            self.spectrum_curve_figure,
            self.spectrum_curve_axis,
            title="",
            x_label="",
            y_label="Intensity (counts)",
        )
        self.spectrum_centroid_text = self.spectrum_curve_figure.text(
            0.28,
            0.055,
            "Center wavelength   -- nm",
            ha="center",
            va="center",
            fontsize=10,
            color=self._text_color,
        )
        self.spectrum_fwhm_text = self.spectrum_curve_figure.text(
            0.50,
            0.055,
            "FWHM   -- nm",
            ha="center",
            va="center",
            fontsize=10,
            color=self._text_color,
        )
        self.spectrum_pib_text = self.spectrum_curve_figure.text(
            0.68,
            0.055,
            "PIB   -- %",
            ha="center",
            va="center",
            fontsize=10,
            color=self._text_color,
        )
        self.spectrum_saturation_text = self.spectrum_curve_figure.text(
            0.94,
            0.055,
            "SATURATED",
            ha="right",
            va="center",
            fontsize=9,
            fontweight="bold",
            color=self._danger_color,
            visible=False,
        )
        self.spectrum_curve_figure.subplots_adjust(left=0.08, right=0.98, top=0.95, bottom=0.19)

    @staticmethod
    def _configure_canvas(canvas: FigureCanvas) -> None:
        canvas.setMinimumHeight(CHART_MINIMUM_HEIGHT)
        canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        canvas.setStyleSheet("border: none;")

    @staticmethod
    def _format_power_axis(axis: Any) -> None:
        formatter = OneDecimalScalarFormatter(useMathText=True)
        formatter.set_scientific(True)
        formatter.set_powerlimits((-2, 2))
        formatter.set_useOffset(False)
        axis.yaxis.set_major_formatter(formatter)

    def relayout(self, available_width: int) -> None:
        mode = "dashboard" if available_width >= DASHBOARD_LAYOUT_MIN_WIDTH else "stacked"
        if mode == self._layout_mode:
            return
        self._layout_mode = mode

        while self.curves_layout.count():
            self.curves_layout.takeAt(0)
        for row in range(3):
            self.curves_layout.setRowStretch(row, 0)
        for column in range(3):
            self.curves_layout.setColumnStretch(column, 0)

        if mode == "dashboard":
            self.curves_layout.addWidget(self.power_curve_canvas, 0, 0)
            self.curves_layout.addWidget(self.stable_power_canvas, 0, 1)
            self.curves_layout.addWidget(self.spectrum_curve_canvas, 1, 0, 1, 2)
            self.curves_layout.setRowStretch(0, 3)
            self.curves_layout.setRowStretch(1, 2)
            self.curves_layout.setColumnStretch(0, 1)
            self.curves_layout.setColumnStretch(1, 1)
            return

        self.curves_layout.addWidget(self.power_curve_canvas, 0, 0)
        self.curves_layout.addWidget(self.stable_power_canvas, 1, 0)
        self.curves_layout.addWidget(self.spectrum_curve_canvas, 2, 0)
        self.curves_layout.setRowStretch(0, 1)
        self.curves_layout.setRowStretch(1, 1)
        self.curves_layout.setRowStretch(2, 1)
        self.curves_layout.setColumnStretch(0, 1)

    def _style_axis(self, figure: Figure, axis: Any, title: str, x_label: str, y_label: str) -> None:
        window_color = self.palette.color(QPalette.ColorRole.Window).name()
        plot_color = self.palette.color(QPalette.ColorRole.Base).name()
        grid_color = self._grid_color
        figure.patch.set_facecolor(window_color)
        figure.patch.set_edgecolor(window_color)
        figure.patch.set_linewidth(0)
        axis.set_facecolor(plot_color)
        axis.set_title(title, color=self._text_color, fontproperties=CHINESE_TITLE_FONT)
        axis.set_xlabel(x_label, color=self._text_color)
        axis.set_ylabel(y_label, color=self._text_color)
        axis.xaxis.set_major_locator(MaxNLocator(nbins=6))
        axis.yaxis.set_major_locator(MaxNLocator(nbins=5))
        axis.minorticks_off()
        axis.tick_params(
            colors=self._text_color,
            which="major",
            direction="in",
            labelsize=11,
            length=5,
            width=0.9,
        )
        for spine in axis.spines.values():
            spine.set_color(grid_color)
            spine.set_linewidth(0.8)
        axis.grid(True, which="major", alpha=0.18, color=grid_color, linewidth=0.5)
        figure.tight_layout()

    def set_power_value(self, power_w: float | None) -> None:
        text = "-- W" if power_w is None or not math.isfinite(power_w) else f"{power_w:.3f} W"
        self.power_value_text.set_text(text)

    def set_power_stability(
        self,
        stable: bool,
        covered_window_s: float,
        target_window_s: float,
        span_w: float,
        tolerance_w: float,
    ) -> None:
        self._power_stable = stable
        self._stable_window_target_s = max(0.0, target_window_s)
        displayed_window_s = min(max(covered_window_s, 0.0), self._stable_window_target_s)
        self.stability_status_text.set_text("STABLE" if stable else "STABILIZING")
        self.stability_status_text.set_color(self._stable_line_color if stable else self._text_color)
        self.stability_detail_text.set_text(
            f"{displayed_window_s:.2f} / {self._stable_window_target_s:.2f} s"
            f"  |  ΔP {span_w:.4f} W ≤ {tolerance_w:.4f} W"
        )
        self.power_curve_line.set_color(self._stable_line_color if stable else self._power_line_color)
        self._update_stability_region()

    def set_spectrum_metrics(
        self,
        centroid_nm: float | None = None,
        fwhm_nm: float | None = None,
        pib: float | None = None,
        saturated: bool | None = None,
    ) -> None:
        if centroid_nm is not None:
            centroid_text = "-- nm" if not math.isfinite(centroid_nm) else f"{centroid_nm:.3f} nm"
            self.spectrum_centroid_text.set_text(f"Center wavelength   {centroid_text}")
        if fwhm_nm is not None:
            fwhm_text = "-- nm" if not math.isfinite(fwhm_nm) else f"{fwhm_nm:.3f} nm"
            self.spectrum_fwhm_text.set_text(f"FWHM   {fwhm_text}")
        if pib is not None:
            pib_text = "-- %" if not math.isfinite(pib) else f"{pib * 100.0:.2f} %"
            self.spectrum_pib_text.set_text(f"PIB   {pib_text}")
        if saturated is not None:
            self.spectrum_saturation_text.set_visible(saturated)

    def reset_integrated_metrics(self) -> None:
        self.set_power_value(None)
        self._power_stable = False
        self._stable_window_target_s = 0.0
        self.stability_status_text.set_text("STABILIZING")
        self.stability_status_text.set_color(self._text_color)
        self.stability_detail_text.set_text("0.00 / -- s  |  ΔP -- W ≤ -- W")
        self.power_curve_line.set_color(self._power_line_color)
        self.spectrum_centroid_text.set_text("Center wavelength   -- nm")
        self.spectrum_fwhm_text.set_text("FWHM   -- nm")
        self.spectrum_pib_text.set_text("PIB   -- %")
        self.spectrum_saturation_text.set_visible(False)
        self._remove_stability_region()

    def _remove_stability_region(self) -> None:
        if self._stable_region_artist is not None:
            self._stable_region_artist.remove()
            self._stable_region_artist = None

    def _update_stability_region(self) -> None:
        self._remove_stability_region()
        if not self._power_stable or not self.power_curve_times:
            return
        end_s = self.power_curve_times[-1]
        start_s = max(self.power_curve_times[0], end_s - self._stable_window_target_s)
        self._stable_region_artist = self.power_curve_axis.axvspan(
            start_s,
            end_s,
            color=self._stable_line_color,
            alpha=0.10,
            linewidth=0,
            zorder=0,
        )

    def reset_power(self) -> None:
        self.power_curve_times.clear()
        self.power_curve_values.clear()
        self.set_power_value(None)
        self._power_stable = False
        self.power_curve_line.set_color(self._power_line_color)
        self.stability_status_text.set_text("STABILIZING")
        self.stability_status_text.set_color(self._text_color)
        self.stability_detail_text.set_text("0.00 / -- s  |  ΔP -- W ≤ -- W")
        self._remove_stability_region()
        self.power_curve_line.set_data([], [])
        self.power_curve_axis.set_xlim(0, 10)
        self.power_curve_axis.set_ylim(-0.01, 0.01)
        self._last_power_draw_s = time.monotonic()
        self.power_curve_canvas.draw_idle()

    def update_power(self, elapsed_s: float, power_w: float) -> None:
        elapsed = float(elapsed_s)
        power = float(power_w)
        if not math.isfinite(elapsed) or not math.isfinite(power):
            return

        self.power_curve_times.append(elapsed)
        self.power_curve_values.append(power)
        cutoff = max(0.0, elapsed - POWER_PLOT_HISTORY_S)
        while self.power_curve_times and self.power_curve_times[0] < cutoff:
            self.power_curve_times.popleft()
            self.power_curve_values.popleft()

        times = list(self.power_curve_times)
        powers = list(self.power_curve_values)
        self.set_power_value(power)
        self.power_curve_line.set_data(times, powers)
        x_max = max(10.0, times[-1])
        y_min = min(powers)
        y_max = max(powers)
        y_pad = self._axis_padding(y_min, y_max, fallback=0.001)
        self.power_curve_axis.set_xlim(cutoff, x_max)
        self.power_curve_axis.set_ylim(y_min - y_pad, y_max + y_pad * 2.0)
        self._update_stability_region()
        now = time.monotonic()
        if now - self._last_power_draw_s >= PLOT_REFRESH_INTERVAL_S:
            self._last_power_draw_s = now
            self.power_curve_canvas.draw_idle()

    def update_stable(self, power_by_current: Mapping[float, float], efficiency_by_current: Mapping[float, float]) -> None:
        power_points = sorted(power_by_current.items())
        efficiency_points = sorted(efficiency_by_current.items())
        self.stable_power_line.set_data(
            [current_a for current_a, _power_w in power_points],
            [power_w for _current_a, power_w in power_points],
        )
        self.efficiency_line.set_data(
            [current_a for current_a, _efficiency_percent in efficiency_points],
            [efficiency_percent for _current_a, efficiency_percent in efficiency_points],
        )

        currents = [current_a for current_a, _value in power_points]
        if currents:
            x_min = min(currents)
            x_max = max(currents)
            x_pad = self._axis_padding(x_min, x_max, fallback=1.0)
            self.stable_power_axis.set_xlim(x_min - x_pad, x_max + x_pad)
        else:
            self.stable_power_axis.set_xlim(0.0, 1.0)

        if power_points:
            powers = [power_w for _current_a, power_w in power_points]
            y_min = min(powers)
            y_max = max(powers)
            y_pad = self._axis_padding(y_min, y_max, fallback=0.001)
            self.stable_power_axis.set_ylim(y_min - y_pad, y_max + y_pad)
        else:
            self.stable_power_axis.set_ylim(-0.01, 0.01)
        self.efficiency_axis.set_ylim(20.0, 60.0)

        now = time.monotonic()
        if now - self._last_stable_draw_s >= PLOT_REFRESH_INTERVAL_S:
            self._last_stable_draw_s = now
            self.stable_power_canvas.draw_idle()

    def reset_spectrum(self) -> None:
        self.clear_spectrum_annotations()
        self.spectrum_peak_annotations.clear()
        self.spectrum_centroid_text.set_text("Center wavelength   -- nm")
        self.spectrum_fwhm_text.set_text("FWHM   -- nm")
        self.spectrum_pib_text.set_text("PIB   -- %")
        self.spectrum_saturation_text.set_visible(False)
        self.spectrum_curve_line.set_data([], [])
        self.spectrum_curve_axis.set_xlim(0, 1)
        self.spectrum_curve_axis.set_ylim(0, 1)
        self._last_spectrum_draw_s = time.monotonic()
        self.spectrum_curve_canvas.draw_idle()

    def update_spectrum(self, wavelength: Any, intensity: Any, locked_center_nm: float | None) -> None:
        points: list[tuple[float, float]] = []
        for x_raw, y_raw in zip(wavelength, intensity):
            x = float(x_raw)
            y = float(y_raw)
            if math.isfinite(x) and math.isfinite(y):
                points.append((x, y))
        if not points:
            self.clear_spectrum_annotations()
            self.spectrum_peak_annotations.clear()
            return

        x_values = [item[0] for item in points]
        y_values = [item[1] for item in points]
        self.spectrum_curve_line.set_data(x_values, y_values)

        if locked_center_nm is not None and math.isfinite(locked_center_nm):
            x_min = locked_center_nm - SPECTRUM_CENTER_LOCK_HALF_RANGE_NM
            x_max = locked_center_nm + SPECTRUM_CENTER_LOCK_HALF_RANGE_NM
            visible_points = [(x, y) for x, y in points if x_min <= x <= x_max] or points
            visible_y = [item[1] for item in visible_points]
            y_min = min(visible_y)
            y_max = max(visible_y)
            x_pad = 0.0
        else:
            x_min = min(x_values)
            x_max = max(x_values)
            y_min = min(y_values)
            y_max = max(y_values)
            x_pad = self._axis_padding(x_min, x_max, fallback=1.0)
            visible_points = points
        spectrum_y_min, spectrum_y_max = self._spectrum_y_limits(y_min, y_max)
        self.spectrum_curve_axis.set_xlim(x_min - x_pad, x_max + x_pad)
        self.spectrum_curve_axis.set_ylim(spectrum_y_min, spectrum_y_max)
        self.spectrum_peak_annotations.clear()
        self.spectrum_peak_annotations.extend(find_spectrum_peak_annotations(visible_points))
        self.draw_spectrum_annotations(self.spectrum_peak_annotations)

        now = time.monotonic()
        if now - self._last_spectrum_draw_s >= PLOT_REFRESH_INTERVAL_S:
            self._last_spectrum_draw_s = now
            self.spectrum_curve_canvas.draw_idle()

    def _spectrum_y_limits(self, y_min: float, y_max: float) -> tuple[float, float]:
        y_pad = self._axis_padding(y_min, y_max, fallback=1.0)
        lower_limit = 0.0 if y_min >= 0.0 else float(y_min) - y_pad
        upper_limit = max(float(y_max) + y_pad, 1.0)
        return lower_limit, upper_limit

    def clear_spectrum_annotations(self) -> None:
        for artist in self.spectrum_peak_annotation_artists:
            try:
                artist.remove()
            except Exception:
                pass
        self.spectrum_peak_annotation_artists.clear()

    def draw_spectrum_annotations(self, annotations: list[SpectrumPeakAnnotation]) -> None:
        self.clear_spectrum_annotations()
        x_min, x_max = self.spectrum_curve_axis.get_xlim()
        y_min, y_max = self.spectrum_curve_axis.get_ylim()
        x_span = max(x_max - x_min, 1.0)
        y_span = max(y_max - y_min, 1.0)
        label_y_limit = y_min + y_span * 0.92
        label_y_offset = y_span * 0.05
        label_y_min = y_min + y_span * 0.08
        min_label_gap = y_span * 0.08
        min_label_x_gap = x_span * 0.04
        split_side_threshold = x_span * 0.05
        occupied_labels: list[tuple[float, float, float]] = []
        for index, annotation in enumerate(annotations):
            line = self.spectrum_curve_axis.axvline(
                annotation.centroid_nm,
                color="#7dd3fc",
                linestyle=":",
                linewidth=0.7,
                alpha=0.45,
            )
            marker = self.spectrum_curve_axis.plot(
                [annotation.centroid_nm],
                [annotation.peak_intensity],
                marker="o",
                color="#7dd3fc",
                markersize=3,
                linewidth=0,
                alpha=0.85,
            )[0]
            nearby_centroids = [
                item.centroid_nm
                for item in annotations
                if item is not annotation and abs(item.centroid_nm - annotation.centroid_nm) <= split_side_threshold
            ]
            if nearby_centroids:
                nearest = min(nearby_centroids, key=lambda centroid: abs(centroid - annotation.centroid_nm))
                right_side = annotation.centroid_nm > nearest
            else:
                right_side = annotation.centroid_nm <= x_min + x_span * 0.72
            x_offset = x_span * (0.012 + index * 0.004)
            label_x = annotation.centroid_nm + x_offset if right_side else annotation.centroid_nm - x_offset
            label_x = min(max(label_x, x_min + x_span * 0.02), x_max - x_span * 0.02)
            label_y = min(max(annotation.peak_intensity + label_y_offset, label_y_min), label_y_limit)
            close_x_threshold = x_span * 0.15
            for occupied_centroid_nm, occupied_x, occupied_y in occupied_labels:
                if (
                    abs(annotation.centroid_nm - occupied_centroid_nm) <= close_x_threshold
                    and abs(label_y - occupied_y) < min_label_gap
                ):
                    label_y = (
                        occupied_y + min_label_gap
                        if occupied_y + min_label_gap <= label_y_limit
                        else max(label_y_min, occupied_y - min_label_gap)
                    )
                if (
                    abs(annotation.centroid_nm - occupied_centroid_nm) <= close_x_threshold
                    and abs(label_x - occupied_x) < min_label_x_gap
                ):
                    label_x = occupied_x + min_label_x_gap if right_side else occupied_x - min_label_x_gap
                    label_x = min(max(label_x, x_min + x_span * 0.02), x_max - x_span * 0.02)
            occupied_labels.append((annotation.centroid_nm, label_x, label_y))
            text = self.spectrum_curve_axis.text(
                label_x,
                label_y,
                f"{annotation.label} {annotation.centroid_nm:.3f} nm",
                ha="left" if right_side else "right",
                va="bottom",
                fontsize=7,
                color=self._text_color,
                alpha=0.9,
            )
            self.spectrum_peak_annotation_artists.extend([line, marker, text])

    @staticmethod
    def _axis_padding(min_value: float, max_value: float, fallback: float) -> float:
        if math.isclose(min_value, max_value):
            return max(abs(min_value) * 0.1, fallback)
        return (max_value - min_value) * 0.12
