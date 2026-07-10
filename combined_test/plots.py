"""Realtime Matplotlib views with bounded history and throttled rendering."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Mapping
from typing import Any

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QGridLayout, QGroupBox, QWidget
from matplotlib import rcParams
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from .models import SpectrumPeakAnnotation
from .spectrum import SPECTRUM_CENTER_LOCK_HALF_RANGE_NM, find_spectrum_peak_annotations


MAX_CURVE_POINTS = 10000
POWER_PLOT_HISTORY_S = 60.0
PLOT_REFRESH_INTERVAL_S = 0.2

# Prefer fonts available on the target Windows test stations, with macOS/Linux
# fallbacks for development. This keeps Chinese chart labels from rendering as
# empty boxes while preserving Matplotlib's final DejaVu fallback.
rcParams["font.sans-serif"] = [
    "Microsoft YaHei",
    "SimHei",
    "PingFang SC",
    "Noto Sans CJK SC",
    "Arial Unicode MS",
    "DejaVu Sans",
]
rcParams["axes.unicode_minus"] = False


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
        self.group = QGroupBox("实时曲线", parent)
        self.curves_layout = QGridLayout(self.group)
        self.power_curve_times: deque[float] = deque(maxlen=MAX_CURVE_POINTS)
        self.power_curve_values: deque[float] = deque(maxlen=MAX_CURVE_POINTS)
        self.spectrum_peak_annotations: list[SpectrumPeakAnnotation] = []
        self.spectrum_peak_annotation_artists: list[Any] = []
        self._last_power_draw_s = -math.inf
        self._last_stable_draw_s = -math.inf
        self._last_spectrum_draw_s = -math.inf
        self._build_charts()

    def expose_compatibility_attributes(self, target: Any) -> None:
        """Keep historical window attributes while callers migrate to this Module."""
        for name in self.COMPATIBILITY_ATTRIBUTES:
            setattr(target, name, getattr(self, name))

    def _build_charts(self) -> None:
        self.power_curve_figure = Figure(figsize=(3.8, 2.4), dpi=100)
        self.power_curve_canvas = FigureCanvas(self.power_curve_figure)
        self.power_curve_canvas.setMinimumHeight(180)
        self.power_curve_axis = self.power_curve_figure.add_subplot(111)
        (self.power_curve_line,) = self.power_curve_axis.plot([], [], color="#2f9cf4", linewidth=1.6)
        self._style_axis(
            self.power_curve_figure,
            self.power_curve_axis,
            title="功率",
            x_label="已用时间（s）",
            y_label="功率（W）",
        )

        self.stable_power_figure = Figure(figsize=(5.2, 2.4), dpi=100)
        self.stable_power_canvas = FigureCanvas(self.stable_power_figure)
        self.stable_power_canvas.setMinimumHeight(180)
        self.stable_power_axis = self.stable_power_figure.add_subplot(111)
        self.efficiency_axis = self.stable_power_axis.twinx()
        (self.stable_power_line,) = self.stable_power_axis.plot(
            [], [], color="#2f9cf4", marker="o", markersize=5, linewidth=1.6
        )
        (self.efficiency_line,) = self.efficiency_axis.plot(
            [], [], color="#f0b429", marker="s", markersize=5, linewidth=1.6
        )
        self._style_axis(
            self.stable_power_figure,
            self.stable_power_axis,
            title="稳定功率与效率",
            x_label="电流（A）",
            y_label="稳定功率（W）",
        )
        self.efficiency_axis.set_ylabel("效率（%）", color="#f0b429")
        self.efficiency_axis.tick_params(axis="y", colors="#f0b429")
        self.efficiency_axis.spines["right"].set_color("#f0b429")
        self.stable_power_figure.subplots_adjust(left=0.12, right=0.86, top=0.88, bottom=0.20)

        self.spectrum_curve_figure = Figure(figsize=(5, 2.4), dpi=100)
        self.spectrum_curve_canvas = FigureCanvas(self.spectrum_curve_figure)
        self.spectrum_curve_canvas.setMinimumHeight(180)
        self.spectrum_curve_axis = self.spectrum_curve_figure.add_subplot(111)
        (self.spectrum_curve_line,) = self.spectrum_curve_axis.plot([], [], color="#f0b429", linewidth=1.2)
        self._style_axis(
            self.spectrum_curve_figure,
            self.spectrum_curve_axis,
            title="光谱",
            x_label="波长（nm）",
            y_label="强度",
        )

        self.curves_layout.addWidget(self.power_curve_canvas, 0, 0)
        self.curves_layout.addWidget(self.spectrum_curve_canvas, 0, 1)
        self.curves_layout.addWidget(self.stable_power_canvas, 1, 0, 1, 2)
        self.curves_layout.setRowStretch(0, 1)
        self.curves_layout.setRowStretch(1, 1)
        self.curves_layout.setColumnStretch(0, 4)
        self.curves_layout.setColumnStretch(1, 6)

    def _style_axis(self, figure: Figure, axis: Any, title: str, x_label: str, y_label: str) -> None:
        window_color = self.palette.color(QPalette.ColorRole.Window).name()
        plot_color = self.palette.color(QPalette.ColorRole.Base).name()
        grid_color = self.palette.color(QPalette.ColorRole.Mid).name()
        figure.patch.set_facecolor(window_color)
        axis.set_facecolor(plot_color)
        axis.set_title(title, color=self._text_color)
        axis.set_xlabel(x_label, color=self._text_color)
        axis.set_ylabel(y_label, color=self._text_color)
        axis.tick_params(colors=self._text_color)
        for spine in axis.spines.values():
            spine.set_color(grid_color)
        axis.grid(True, alpha=0.28, color=grid_color)
        figure.tight_layout()

    def reset_power(self) -> None:
        self.power_curve_times.clear()
        self.power_curve_values.clear()
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
        self.power_curve_line.set_data(times, powers)
        x_max = max(10.0, times[-1])
        y_min = min(powers)
        y_max = max(powers)
        y_pad = self._axis_padding(y_min, y_max, fallback=0.001)
        self.power_curve_axis.set_xlim(cutoff, x_max)
        self.power_curve_axis.set_ylim(y_min - y_pad, y_max + y_pad)
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
