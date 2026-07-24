"""Create a publication-ready dual-Y-axis redraw of HEA413766's curves.

The data were transcribed from the measurement table in
HEA413766_出货报告.pdf.  The plot preserves the report's dual-Y-axis format:
output power is on the left axis and electro-optical efficiency is on the
right axis.
"""

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import imageio.v3 as iio
import tifffile
from matplotlib.ticker import MultipleLocator


HERE = Path(__file__).resolve().parent
DATA_FILE = HERE / "HEA413766_measurement_data.csv"
OUTPUT_STEM = HERE / "HEA413766_Power_Efficiency_SCI"

POWER_BLUE = "#0072B2"       # Okabe-Ito colour-blind-safe blue
EFFICIENCY_ORANGE = "#D55E00"  # Okabe-Ito colour-blind-safe vermilion


def load_data() -> np.ndarray:
    """Load the report's tabulated measurement values."""
    return np.genfromtxt(DATA_FILE, delimiter=",", names=True, dtype=float)


def style_primary_axis(ax: plt.Axes) -> None:
    """Apply the main SCI-style axis treatment."""
    for spine_name in ("left", "bottom", "top"):
        ax.spines[spine_name].set_linewidth(0.8)
        ax.spines[spine_name].set_color("#202020")
    ax.spines["right"].set_visible(False)
    ax.tick_params(
        axis="x",
        which="major",
        direction="in",
        length=3.4,
        width=0.75,
        colors="#202020",
        labelsize=9.3,
        top=True,
        pad=3,
    )
    ax.tick_params(axis="x", which="minor", direction="in", length=1.8, width=0.55, top=True)
    ax.set_facecolor("white")


def style_left_axis(ax: plt.Axes, color: str) -> None:
    """Format the left Y axis and link it visually to its data series."""
    ax.spines["left"].set_color(color)
    ax.tick_params(
        axis="y",
        which="major",
        direction="in",
        length=3.4,
        width=0.75,
        colors=color,
        labelsize=9.3,
        left=True,
        right=False,
        pad=3,
    )
    ax.tick_params(axis="y", which="minor", direction="in", length=1.8, width=0.55, colors=color, left=True, right=False)


def style_right_axis(ax: plt.Axes, color: str) -> None:
    """Format the right Y axis and link it visually to its data series."""
    for spine_name in ("left", "bottom", "top"):
        ax.spines[spine_name].set_visible(False)
    ax.spines["right"].set_linewidth(0.8)
    ax.spines["right"].set_color(color)
    ax.tick_params(
        axis="y",
        which="major",
        direction="in",
        length=3.4,
        width=0.75,
        colors=color,
        labelsize=9.3,
        left=False,
        right=True,
        pad=3,
    )
    ax.tick_params(axis="y", which="minor", direction="in", length=1.8, width=0.55, colors=color, left=False, right=True)


def draw_series(
    ax: plt.Axes,
    x: np.ndarray,
    y: np.ndarray,
    color: str,
    marker: str,
    label: str,
) -> None:
    """Draw measured points linked by a clean, print-safe line."""
    ax.plot(
        x,
        y,
        color=color,
        linewidth=1.8,
        marker=marker,
        markersize=5.6,
        markerfacecolor=color,
        markeredgecolor="white",
        markeredgewidth=0.65,
        solid_capstyle="round",
        solid_joinstyle="round",
        zorder=3,
        label=label,
    )


def main() -> None:
    # Embed TrueType fonts in PDF/PS and retain editable text in SVG.
    mpl.rcParams.update(
        {
            "font.family": "Arial",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
            "mathtext.fontset": "stix",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
            "axes.unicode_minus": False,
            "savefig.facecolor": "white",
            "savefig.edgecolor": "white",
        }
    )

    data = load_data()
    current = data["current_a"]
    power = data["power_w"]
    efficiency = data["electro_optical_efficiency_pct"]

    # 140 mm wide: an efficient two-axis format suitable for journal placement.
    fig, ax_power = plt.subplots(figsize=(5.80, 3.82), layout="constrained")
    ax_efficiency = ax_power.twinx()

    draw_series(ax_power, current, power, POWER_BLUE, "o", "Output power")
    draw_series(
        ax_efficiency,
        current,
        efficiency,
        EFFICIENCY_ORANGE,
        "s",
        "Electro-optical efficiency",
    )

    style_primary_axis(ax_power)
    style_left_axis(ax_power, POWER_BLUE)
    style_right_axis(ax_efficiency, EFFICIENCY_ORANGE)

    ax_power.set_xlim(0.75, 7.25)
    ax_power.set_ylim(0, 180)
    ax_power.xaxis.set_major_locator(MultipleLocator(1))
    ax_power.yaxis.set_major_locator(MultipleLocator(30))
    ax_power.set_xlabel(r"Drive current, $I$ (A)", fontsize=10.8, labelpad=6)
    ax_power.set_ylabel(r"Output power, $P$ (W)", fontsize=10.8, labelpad=8, color=POWER_BLUE)

    ax_efficiency.set_ylim(0, 32)
    ax_efficiency.yaxis.set_major_locator(MultipleLocator(5))
    ax_efficiency.set_ylabel(
        r"Electro-optical efficiency, $\eta_{\mathrm{EO}}$ (%)",
        fontsize=10.8,
        labelpad=8,
        color=EFFICIENCY_ORANGE,
    )

    lines = ax_power.get_lines() + ax_efficiency.get_lines()
    labels = [line.get_label() for line in lines]
    ax_power.legend(
        lines,
        labels,
        loc="upper left",
        frameon=False,
        fontsize=9.3,
        handlelength=2.1,
        handletextpad=0.6,
        borderaxespad=0.7,
    )

    fig.savefig(OUTPUT_STEM.with_suffix(".pdf"), bbox_inches="tight", pad_inches=0.02)
    fig.savefig(OUTPUT_STEM.with_suffix(".svg"), bbox_inches="tight", pad_inches=0.02)
    fig.savefig(OUTPUT_STEM.with_suffix(".png"), dpi=600, bbox_inches="tight", pad_inches=0.02)
    # TIFF is produced from the 600 dpi PNG so its raster dimensions and
    # resolution metadata agree.  Uncompressed RGB TIFF maximizes journal
    # compatibility while avoiding codec-specific artifacts.
    png_pixels = iio.imread(OUTPUT_STEM.with_suffix(".png"))
    tifffile.imwrite(
        OUTPUT_STEM.with_suffix(".tiff"),
        png_pixels[..., :3],
        photometric="rgb",
        resolution=(600, 600),
        resolutionunit="INCH",
    )
    plt.close(fig)
    print(f"Created: {OUTPUT_STEM.with_suffix('.pdf')}")
    print(f"Created: {OUTPUT_STEM.with_suffix('.svg')}")
    print(f"Created: {OUTPUT_STEM.with_suffix('.png')}")
    print(f"Created: {OUTPUT_STEM.with_suffix('.tiff')}")


if __name__ == "__main__":
    main()
