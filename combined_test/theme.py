"""Application palette helpers for consistent Windows dark-mode rendering."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


DARK_WINDOW = "#1e1f22"
DARK_BASE = "#27282c"
DARK_ALTERNATE_BASE = "#2d2f34"
DARK_BUTTON = "#303238"
DARK_BORDER = "#50535a"
DARK_TEXT = "#f1f3f5"
DARK_DISABLED_TEXT = "#85888f"
DARK_ACCENT = "#329ad6"


def is_dark_palette(palette: QPalette) -> bool:
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def build_dark_palette() -> QPalette:
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: DARK_WINDOW,
        QPalette.ColorRole.WindowText: DARK_TEXT,
        QPalette.ColorRole.Base: DARK_BASE,
        QPalette.ColorRole.AlternateBase: DARK_ALTERNATE_BASE,
        QPalette.ColorRole.ToolTipBase: DARK_ALTERNATE_BASE,
        QPalette.ColorRole.ToolTipText: DARK_TEXT,
        QPalette.ColorRole.Text: DARK_TEXT,
        QPalette.ColorRole.Button: DARK_BUTTON,
        QPalette.ColorRole.ButtonText: DARK_TEXT,
        QPalette.ColorRole.BrightText: "#ff7b72",
        QPalette.ColorRole.Highlight: DARK_ACCENT,
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.Link: "#66b7e8",
        QPalette.ColorRole.Light: "#3b3d43",
        QPalette.ColorRole.Midlight: "#34363b",
        QPalette.ColorRole.Mid: DARK_BORDER,
        QPalette.ColorRole.Dark: "#18191c",
        QPalette.ColorRole.Shadow: "#101114",
        QPalette.ColorRole.PlaceholderText: "#9a9da4",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))

    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(DARK_DISABLED_TEXT))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, QColor("#292a2e"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor("#242529"))
    return palette


def apply_application_theme(app: QApplication, force_dark: bool | None = None) -> bool:
    """Apply a curated dark theme only when the operating-system palette is dark."""
    use_dark = is_dark_palette(app.palette()) if force_dark is None else force_dark
    if not use_dark:
        return False

    app.setStyle("Fusion")
    app.setPalette(build_dark_palette())
    app.setStyleSheet(
        f"""
        QToolTip {{
            color: {DARK_TEXT};
            background-color: {DARK_ALTERNATE_BASE};
            border: 1px solid {DARK_BORDER};
            padding: 4px 6px;
        }}
        QScrollBar:vertical {{
            background: {DARK_WINDOW};
            width: 10px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: #5b5e65;
            min-height: 36px;
            border-radius: 5px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: #71747c;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        """
    )
    return True
