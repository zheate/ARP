"""Small palette helpers that do not override Qt's native application theme."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True, slots=True)
class SemanticColors:
    """Readable status colors selected from the current system palette."""

    secondary_text: str
    success_text: str
    warning_text: str
    error_text: str


LIGHT_SEMANTIC_COLORS = SemanticColors(
    secondary_text="#5f6368",
    success_text="#16803c",
    warning_text="#9a5a00",
    error_text="#b42318",
)
DARK_SEMANTIC_COLORS = SemanticColors(
    secondary_text="#b7bbc3",
    success_text="#5fd07a",
    warning_text="#f2b84b",
    error_text="#ff7b72",
)


def is_dark_palette(palette: QPalette) -> bool:
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def semantic_colors_for_palette(palette: QPalette) -> SemanticColors:
    """Return readable status colors without changing the supplied palette."""

    return DARK_SEMANTIC_COLORS if is_dark_palette(palette) else LIGHT_SEMANTIC_COLORS


def apply_application_theme(
    app: QApplication,
    force_dark: bool | None = None,
    *,
    mode: str = "system",
) -> bool:
    """Compatibility no-op: preserve Qt's native style, font, palette, and QSS."""

    del force_dark, mode
    return is_dark_palette(app.palette())
