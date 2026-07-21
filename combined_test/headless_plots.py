"""Small plot substitutes used by the hidden Tauri compatibility window.

The Tauri frontend owns the visible charts.  Keeping Matplotlib canvases in the
hidden Qt compatibility window duplicates every live chart and is particularly
expensive on Windows.  These objects retain the data and method surface used by
the controller without allocating a native plotting stack.
"""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Any

from PySide6.QtWidgets import QWidget

from .plot_types import PlotLayoutContext


POWER_PLOT_HISTORY_S = 60.0
POWER_DISPLAY_SMOOTHING_WINDOW_S = 0.2
MAX_CURVE_POINTS = 10_000


class _TextValue:
    def __init__(self, text: str = "") -> None:
        self._text = text

    def set_text(self, text: str) -> None:
        self._text = str(text)

    def get_text(self) -> str:
        return self._text

    def set_color(self, _color: Any) -> None:
        return

    def set_visible(self, _visible: bool) -> None:
        return


class NullLivePlots:
    """Data-only replacement for :class:`combined_test.plots.LivePlots`."""

    COMPATIBILITY_ATTRIBUTES = (
        "curves_layout",
        "chart_tabs",
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
        self.group = QWidget(parent)
        self.layout_context = PlotLayoutContext.AUTOMATIC
        self.power_curve_times: deque[float] = deque(maxlen=MAX_CURVE_POINTS)
        self.power_curve_values: deque[float] = deque(maxlen=MAX_CURVE_POINTS)
        self._power_display_samples: deque[tuple[float, float]] = deque()
        self.spectrum_peak_annotations: list[Any] = []
        self.spectrum_peak_annotation_artists: list[Any] = []
        self._power_stable = False
        self._stable_window_target_s = 0.0
        self._power_line_color = "#63b3ed"
        self._stable_line_color = "#5fd07a"
        self._power_revision = 0
        self._stable_revision = 0
        self.power_value_text = _TextValue("-- W")
        self.stability_status_text = _TextValue("STABILIZING")
        self.stability_detail_text = _TextValue("0.00 / -- s  |  ΔP -- W ≤ -- W")
        self.spectrum_centroid_text = _TextValue("Center wavelength   -- nm")
        self.spectrum_fwhm_text = _TextValue("FWHM   -- nm")
        self.spectrum_pib_text = _TextValue("PIB   -- %")
        self.spectrum_smsr_text = _TextValue("SMSR   -- dB")
        self.spectrum_saturation_text = _TextValue()
        for name in self.COMPATIBILITY_ATTRIBUTES:
            if not hasattr(self, name):
                setattr(self, name, None)

    @property
    def power_revision(self) -> int:
        return self._power_revision

    def expose_compatibility_attributes(self, target: Any) -> None:
        for name in self.COMPATIBILITY_ATTRIBUTES:
            setattr(target, name, getattr(self, name))

    def set_layout_context(self, context: PlotLayoutContext) -> None:
        self.layout_context = context

    def relayout(self, _available_width: int) -> None:
        return

    def set_power_value(self, power_w: float | None) -> None:
        if power_w is None or not math.isfinite(float(power_w)):
            self.power_value_text.set_text("-- W")
        else:
            self.power_value_text.set_text(f"{float(power_w):.3f} W")

    def set_power_stability(
        self,
        stable: bool,
        covered_window_s: float,
        target_window_s: float,
        span_w: float,
        tolerance_w: float,
    ) -> None:
        self._power_stable = bool(stable)
        self._stable_window_target_s = max(0.0, float(target_window_s))
        displayed_window_s = min(max(float(covered_window_s), 0.0), self._stable_window_target_s)
        self.stability_status_text.set_text("STABLE" if stable else "STABILIZING")
        self.stability_detail_text.set_text(
            f"{displayed_window_s:.2f} / {self._stable_window_target_s:.2f} s"
            f"  |  ΔP {float(span_w):.4f} W ≤ {float(tolerance_w):.4f} W"
        )

    def set_spectrum_metrics(
        self,
        centroid_nm: float | None = None,
        fwhm_nm: float | None = None,
        pib: float | None = None,
        smsr_db: float | None = None,
        saturated: bool | None = None,
    ) -> None:
        if centroid_nm is not None:
            self.spectrum_centroid_text.set_text(
                "Center wavelength   -- nm"
                if not math.isfinite(float(centroid_nm))
                else f"Center wavelength   {float(centroid_nm):.3f} nm"
            )
        if fwhm_nm is not None:
            self.spectrum_fwhm_text.set_text(
                "FWHM   -- nm"
                if not math.isfinite(float(fwhm_nm))
                else f"FWHM   {float(fwhm_nm):.3f} nm"
            )
        if pib is not None:
            self.spectrum_pib_text.set_text(
                "PIB   -- %"
                if not math.isfinite(float(pib))
                else f"PIB   {float(pib) * 100.0:.2f} %"
            )
        if smsr_db is not None:
            self.spectrum_smsr_text.set_text(
                "SMSR   -- dB"
                if not math.isfinite(float(smsr_db))
                else f"SMSR   {float(smsr_db):.2f} dB"
            )
        if saturated is not None:
            self.spectrum_saturation_text.set_visible(bool(saturated))

    def reset_integrated_metrics(self) -> None:
        self.set_power_value(None)
        self.set_power_stability(False, 0.0, 0.0, math.nan, math.nan)
        self.set_spectrum_metrics(
            centroid_nm=math.nan,
            fwhm_nm=math.nan,
            pib=math.nan,
            smsr_db=math.nan,
            saturated=False,
        )

    def reset_power(self) -> None:
        self.power_curve_times.clear()
        self.power_curve_values.clear()
        self._power_display_samples.clear()
        self._power_revision += 1
        self.set_power_value(None)

    def reset_spectrum(self) -> None:
        self.spectrum_peak_annotations.clear()
        self.spectrum_peak_annotation_artists.clear()

    def update_power(self, elapsed_s: float, power_w: float) -> None:
        elapsed = float(elapsed_s)
        power = float(power_w)
        if not math.isfinite(elapsed) or not math.isfinite(power):
            return
        if self._power_display_samples and elapsed < self._power_display_samples[-1][0]:
            self._power_display_samples.clear()
        self._power_display_samples.append((elapsed, power))
        cutoff = elapsed - POWER_DISPLAY_SMOOTHING_WINDOW_S
        while len(self._power_display_samples) > 1 and self._power_display_samples[0][0] < cutoff:
            self._power_display_samples.popleft()
        display_power = round(
            math.fsum(value for _timestamp, value in self._power_display_samples)
            / len(self._power_display_samples),
            3,
        )
        self.power_curve_times.append(elapsed)
        self.power_curve_values.append(display_power)
        history_cutoff = max(0.0, elapsed - POWER_PLOT_HISTORY_S)
        while self.power_curve_times and self.power_curve_times[0] < history_cutoff:
            self.power_curve_times.popleft()
            self.power_curve_values.popleft()
        self._power_revision += 1
        self.set_power_value(display_power)

    def update_stable(self, _power_by_current: Any, _efficiency_by_current: Any) -> None:
        self._stable_revision += 1

    def reset_integrated_metrics_and_curves(self) -> None:
        self.reset_integrated_metrics()
        self.reset_power()
        self.reset_spectrum()

    def update_spectrum(self, _wavelength: Any, _intensity: Any, _locked_center_nm: float | None) -> None:
        return


class _NoOpTabs:
    def setCurrentWidget(self, _widget: Any) -> None:
        return


class NullHistoryAnalysisPlots(QWidget):
    """Placeholder for the hidden records page; the Tauri UI renders analysis."""

    MAX_COMPARISON_SESSIONS = 5

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tabs = _NoOpTabs()
        self.comparison_canvas = object()

    def clear(self) -> None:
        return

    def show_session(self, _session: Any, _attempts: Any) -> None:
        return

    def show_comparison(self, _comparison: Any) -> None:
        return
