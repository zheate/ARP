"""History analysis charts backed exclusively by persisted archive data."""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path
from typing import Iterable

from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties

from .test_archive import AttemptValidity, MeasurementAttempt, TestSession


CJK_FONT = FontProperties(
    family="Microsoft YaHei" if sys.platform == "win32" else "PingFang SC",
)


class HistoryAnalysisPlots(QWidget):
    """Compact single-session and comparison charts for the records page."""

    MAX_COMPARISON_SESSIONS = 5
    MAX_SPECTRA = 5

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)

        self.metrics_figure = Figure(figsize=(8.4, 5.4), dpi=100)
        self.metrics_canvas = FigureCanvas(self.metrics_figure)
        self.tabs.addTab(self.metrics_canvas, "单轮指标")

        self.spectrum_figure = Figure(figsize=(8.4, 5.0), dpi=100)
        self.spectrum_canvas = FigureCanvas(self.spectrum_figure)
        self.tabs.addTab(self.spectrum_canvas, "光谱叠加")

        self.power_figure = Figure(figsize=(8.4, 4.6), dpi=100)
        self.power_canvas = FigureCanvas(self.power_figure)
        self.tabs.addTab(self.power_canvas, "功率全程")

        self.comparison_figure = Figure(figsize=(8.4, 5.0), dpi=100)
        self.comparison_canvas = FigureCanvas(self.comparison_figure)
        self.tabs.addTab(self.comparison_canvas, "多轮对比")
        self.clear()

    @staticmethod
    def _finite_points(
        attempts: Iterable[MeasurementAttempt],
        attribute: str,
    ) -> tuple[list[float], list[float]]:
        points = []
        for attempt in attempts:
            value = float(getattr(attempt, attribute))
            if math.isfinite(value):
                points.append((attempt.target_current_a, value))
        points.sort()
        return [point[0] for point in points], [point[1] for point in points]

    @staticmethod
    def _style(axis: object, title: str, x_label: str, y_label: str) -> None:
        axis.set_title(title)
        axis.set_xlabel(x_label)
        axis.set_ylabel(y_label)
        axis.grid(True, alpha=0.2)

    def clear(self) -> None:
        for figure, canvas, message in (
            (self.metrics_figure, self.metrics_canvas, "选择一轮测试查看指标曲线"),
            (self.spectrum_figure, self.spectrum_canvas, "选择包含光谱的测试记录"),
            (self.power_figure, self.power_canvas, "选择包含功率原始曲线的测试记录"),
            (self.comparison_figure, self.comparison_canvas, "勾选最多五轮测试进行对比"),
        ):
            figure.clear()
            axis = figure.add_subplot(111)
            axis.text(
                0.5,
                0.5,
                message,
                ha="center",
                va="center",
                transform=axis.transAxes,
                fontproperties=CJK_FONT,
            )
            axis.set_axis_off()
            canvas.draw_idle()

    def show_session(
        self,
        session: TestSession,
        attempts: Iterable[MeasurementAttempt],
    ) -> None:
        selected = [
            attempt
            for attempt in attempts
            if attempt.selected and attempt.validity is AttemptValidity.VALID
        ]
        selected.sort(key=lambda attempt: attempt.target_current_a)
        self._draw_metrics(selected)
        self._draw_spectra(session, selected)
        self._draw_power_trace(session)

    def _draw_metrics(self, attempts: list[MeasurementAttempt]) -> None:
        figure = self.metrics_figure
        figure.clear()
        axes = figure.subplots(2, 2)

        current, power = self._finite_points(attempts, "power_w")
        axes[0][0].plot(current, power, marker="o", color="#2f79bd")
        self._style(axes[0][0], "P-I", "Current (A)", "Power (W)")

        current, voltage = self._finite_points(attempts, "voltage_v")
        axes[0][1].plot(current, voltage, marker="o", color="#6b7280", label="Voltage")
        self._style(axes[0][1], "V-I / Efficiency", "Current (A)", "Voltage (V)")
        efficiency_axis = axes[0][1].twinx()
        efficiency_current, efficiency = self._finite_points(attempts, "efficiency")
        efficiency_axis.plot(
            efficiency_current,
            [value * 100.0 for value in efficiency],
            marker="s",
            color="#d58a00",
            label="Efficiency",
        )
        efficiency_axis.set_ylabel("Efficiency (%)")

        for attribute, label, color in (
            ("centroid_nm", "Centroid", "#2f79bd"),
            ("fwhm_nm", "FWHM", "#7c3aed"),
        ):
            x_values, y_values = self._finite_points(attempts, attribute)
            axes[1][0].plot(x_values, y_values, marker="o", label=label, color=color)
        self._style(axes[1][0], "Wavelength / FWHM", "Current (A)", "nm")
        axes[1][0].legend(loc="best", fontsize=8)

        for attribute, label, color, scale in (
            ("pib", "PIB", "#2f8f46", 100.0),
            ("smsr_db", "SMSR", "#b42318", 1.0),
        ):
            x_values, y_values = self._finite_points(attempts, attribute)
            axes[1][1].plot(
                x_values,
                [value * scale for value in y_values],
                marker="o",
                label=label,
                color=color,
            )
        self._style(axes[1][1], "PIB / SMSR", "Current (A)", "% / dB")
        axes[1][1].legend(loc="best", fontsize=8)
        figure.tight_layout()
        self.metrics_canvas.draw_idle()

    @staticmethod
    def _read_spectrum(path: Path) -> tuple[list[float], list[float]]:
        wavelength: list[float] = []
        intensity: list[float] = []
        if not path.is_file():
            return wavelength, intensity
        with path.open(newline="", encoding="utf-8") as file:
            for row in csv.DictReader(file):
                wavelength.append(float(row["wavelength_nm"]))
                intensity.append(float(row["intensity"]))
        return wavelength, intensity

    def _draw_spectra(self, session: TestSession, attempts: list[MeasurementAttempt]) -> None:
        figure = self.spectrum_figure
        figure.clear()
        axis = figure.add_subplot(111)
        plotted = 0
        integration_values = {
            attempt.integration_time_us
            for attempt in attempts
            if attempt.spectrum_path and attempt.integration_time_us is not None
        }
        normalize = len(integration_values) > 1
        for attempt in attempts[-self.MAX_SPECTRA :]:
            if not attempt.spectrum_path:
                continue
            wavelength, intensity = self._read_spectrum(session.session_dir / attempt.spectrum_path)
            if not wavelength:
                continue
            if normalize:
                maximum = max(intensity) if intensity else 0.0
                values = [value / maximum for value in intensity] if maximum > 0.0 else intensity
            else:
                values = intensity
            integration = (
                f", {attempt.integration_time_us} us"
                if attempt.integration_time_us is not None
                else ""
            )
            axis.plot(wavelength, values, label=f"{attempt.target_current_a:g} A{integration}")
            plotted += 1
        if plotted:
            axis.axvspan(956.0, 996.0, color="#2f79bd", alpha=0.05, label="Analysis band")
            axis.axvspan(974.5, 977.5, color="#2f8f46", alpha=0.08, label="PIB band")
            axis.legend(loc="best", fontsize=8)
            self._style(
                axis,
                "Spectrum overlay" + (" (normalized)" if normalize else ""),
                "Wavelength (nm)",
                "Normalized intensity" if normalize else "Intensity (counts)",
            )
        else:
            axis.text(
                0.5,
                0.5,
                "本轮测试没有可显示的光谱",
                ha="center",
                va="center",
                fontproperties=CJK_FONT,
            )
            axis.set_axis_off()
        figure.tight_layout()
        self.spectrum_canvas.draw_idle()

    def _draw_power_trace(self, session: TestSession) -> None:
        figure = self.power_figure
        figure.clear()
        axis = figure.add_subplot(111)
        path = session.session_dir / "power_trace.csv"
        elapsed: list[float] = []
        power: list[float] = []
        if path.is_file():
            with path.open(newline="", encoding="utf-8") as file:
                for row in csv.DictReader(file):
                    try:
                        elapsed.append(float(row["elapsed_s"]))
                        power.append(float(row["power_w"]))
                    except (KeyError, TypeError, ValueError):
                        continue
        if elapsed:
            axis.plot(elapsed, power, linewidth=1.0, color="#2f79bd")
            self._style(axis, "Full-session power", "Time (s)", "Power (W)")
        else:
            axis.text(
                0.5,
                0.5,
                "本轮测试没有功率原始曲线",
                ha="center",
                va="center",
                fontproperties=CJK_FONT,
            )
            axis.set_axis_off()
        figure.tight_layout()
        self.power_canvas.draw_idle()

    def show_comparison(
        self,
        sessions: Iterable[tuple[TestSession, Iterable[MeasurementAttempt]]],
    ) -> None:
        figure = self.comparison_figure
        figure.clear()
        axes = figure.subplots(2, 2)
        session_values = list(sessions)[: self.MAX_COMPARISON_SESSIONS]
        for session, attempts in session_values:
            selected = [
                attempt
                for attempt in attempts
                if attempt.selected and attempt.validity is AttemptValidity.VALID
            ]
            label = f"{session.sn} {session.started_at_utc[:16]}"
            for axis, attribute, scale in (
                (axes[0][0], "power_w", 1.0),
                (axes[0][1], "efficiency", 100.0),
                (axes[1][0], "centroid_nm", 1.0),
                (axes[1][1], "fwhm_nm", 1.0),
            ):
                x_values, y_values = self._finite_points(selected, attribute)
                axis.plot(x_values, [value * scale for value in y_values], marker="o", label=label)
        titles = (
            (axes[0][0], "Power", "W"),
            (axes[0][1], "Efficiency", "%"),
            (axes[1][0], "Centroid", "nm"),
            (axes[1][1], "FWHM", "nm"),
        )
        for axis, title, unit in titles:
            self._style(axis, title, "Current (A)", unit)
            if session_values:
                axis.legend(loc="best", fontsize=7)
        figure.tight_layout()
        self.comparison_canvas.draw_idle()
