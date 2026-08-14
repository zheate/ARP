"""Toolkit-free PDF and PNG renderer for shipping reports."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

from matplotlib import font_manager, rcParams
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D


for _font_path in (
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
):
    if _font_path.is_file():
        try:
            font_manager.fontManager.addfont(str(_font_path))
            _font_name = font_manager.FontProperties(fname=str(_font_path)).get_name()
            rcParams["font.family"] = [_font_name]
            rcParams["font.sans-serif"] = [_font_name, "DejaVu Sans"]
            rcParams["axes.unicode_minus"] = False
            break
        except (OSError, RuntimeError):
            pass


def _selected_fields(request: Any) -> list[tuple[str, str, str]]:
    definitions = {
        "power": ("Output Power", "W"),
        "operatingCurrent": ("Operating Current", "A"),
        "operatingVoltage": ("Operating Voltage", "V"),
        "slopeEfficiency": ("Slope Efficiency", "W/A"),
        "electroOpticalEfficiency": ("E-O efficiency", "%"),
        "coolingTemperature": ("Cooling Temperature", "°C"),
        "peakWavelength": ("Peak Wavelength", "nm"),
        "centroidWavelength": ("Centroid Wavelength", "nm"),
        "fwhm": ("FWHM", "nm"),
        "pib": ("PIB", "%"),
        "smsr": ("SMSR", "dB"),
    }
    custom = {item.key: (item.label, item.unit) for item in request.custom_fields}
    rows: list[tuple[str, str, str]] = []
    for key, field in request.fields.items():
        if not field.include:
            continue
        label, unit = custom.get(key, definitions.get(key, (key, "")))
        rows.append((label, field.value, unit))
    return rows


def build_figure(inspection: Any, request: Any) -> Figure:
    """Build the single-page A4 report shared by PDF export and preview."""

    figure = Figure(figsize=(8.27, 11.69), dpi=144, facecolor="white")
    canvas = FigureCanvasAgg(figure)
    figure.set_canvas(canvas)
    figure.text(0.08, 0.955, "EVERBRIGHT PHOTONICS", fontsize=11, weight="bold", color="#0b4b82")
    figure.text(0.92, 0.955, "Shipping Report", fontsize=9, ha="right", color="#555555")
    figure.text(0.5, 0.915, request.product_name or inspection.product_name, fontsize=17, weight="bold", ha="center")
    figure.text(0.5, 0.89, f"SN: {request.sn}", fontsize=10, ha="center")
    figure.add_artist(Line2D([0.08, 0.92], [0.872, 0.872], color="#d7dade", lw=0.8))

    rows = _selected_fields(request)
    summary = figure.add_axes([0.08, 0.69, 0.84, 0.16])
    summary.axis("off")
    if rows:
        cell_text = [[label, value, unit] for label, value, unit in rows]
        table = summary.table(cellText=cell_text, colWidths=[0.52, 0.28, 0.20], cellLoc="left", bbox=[0, 0, 1, 1])
        table.auto_set_font_size(False)
        table.set_fontsize(8.5)
        for (row, column), cell in table.get_celld().items():
            cell.set_edgecolor("#dfe4e8")
            cell.set_linewidth(0.5)
            if column == 0:
                cell.set_text_props(weight="bold", color="#222222")
    else:
        summary.text(0, 0.5, "No report parameters selected", fontsize=10)

    points = list(inspection.points)
    currents = [point.current_a for point in points]
    powers = [point.power_w for point in points]
    efficiencies = [point.efficiency * 100.0 for point in points]
    chart = figure.add_axes([0.11, 0.39, 0.78, 0.24])
    chart.set_title("Power and E-O Efficiency", fontsize=10, pad=8)
    chart.plot(currents, powers, "o-", color="#0072B2", label="Power (W)", linewidth=1.5, markersize=4)
    chart.set_xlabel("Current (A)", fontsize=8)
    chart.set_ylabel("Power (W)", fontsize=8, color="#0072B2")
    chart.tick_params(axis="both", labelsize=7)
    chart.grid(True, color="#e4e7ea", linewidth=0.5)
    efficiency_axis = chart.twinx()
    efficiency_axis.plot(currents, efficiencies, "s-", color="#D55E00", label="E-O efficiency (%)", linewidth=1.3, markersize=3.5)
    efficiency_axis.set_ylabel("Efficiency (%)", fontsize=8, color="#D55E00")
    efficiency_axis.tick_params(axis="y", labelsize=7)

    if getattr(request.report_type, "value", str(request.report_type)) == "spectrum":
        spectrum = figure.add_axes([0.11, 0.105, 0.78, 0.21])
        spectrum.set_title("Spectrum", fontsize=10, pad=8)
        for point in points:
            if point.has_complete_spectrum:
                values = list(point.intensity)
                if getattr(request.spectrum_axis.mode, "value", "counts") == "relative_db":
                    peak = max(values) if values else 1.0
                    values = [10.0 * __import__("math").log10(max(value, 1e-12) / max(peak, 1e-12)) for value in values]
                spectrum.plot(point.wavelength, values, linewidth=0.8, label=f"{point.current_a:g} A")
        spectrum.set_xlabel("Wavelength (nm)", fontsize=8)
        spectrum.set_ylabel("Relative intensity (dB)" if getattr(request.spectrum_axis.mode, "value", "counts") == "relative_db" else "Intensity (counts)", fontsize=8)
        spectrum.tick_params(axis="both", labelsize=7)
        spectrum.grid(True, color="#e4e7ea", linewidth=0.5)
        if len(points) <= 10:
            spectrum.legend(fontsize=6, ncol=2)
    else:
        detail = figure.add_axes([0.08, 0.105, 0.84, 0.21])
        detail.axis("off")
        detail_rows = [[f"{p.current_a:g}", f"{p.voltage_v:.3g}", f"{p.power_w:.4g}", f"{p.efficiency * 100.0:.3g}"] for p in points]
        table = detail.table(cellText=detail_rows, colLabels=["Current (A)", "Voltage (V)", "Power (W)", "Efficiency (%)"], cellLoc="center", bbox=[0, 0, 1, 1])
        table.auto_set_font_size(False)
        table.set_fontsize(7.5)

    figure.text(0.08, 0.045, "Pump Driver Integrated Test", fontsize=7, color="#666666")
    figure.text(0.92, 0.045, "Page 1 / 1", fontsize=7, ha="right", color="#666666")
    return figure


def render_pdf(path: Path, inspection: Any, request: Any) -> None:
    figure = build_figure(inspection, request)
    try:
        figure.savefig(path, format="pdf", dpi=144, metadata={"Title": f"{request.product_name} - {request.sn} Shipping Report"})
    finally:
        figure.clear()


def render_preview_pages(inspection: Any, request: Any, *, page_width_px: int) -> list[str]:
    figure = build_figure(inspection, request)
    try:
        buffer = io.BytesIO()
        dpi = page_width_px / 8.27
        figure.savefig(buffer, format="png", dpi=dpi, facecolor="white")
        return [base64.b64encode(buffer.getvalue()).decode("ascii")]
    finally:
        figure.clear()
