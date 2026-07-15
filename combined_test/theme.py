"""Application palette and typography helpers for the operator UI."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication


LIGHT_WINDOW = "#f3f5f7"
LIGHT_BASE = "#ffffff"
LIGHT_ALTERNATE_BASE = "#eef1f4"
LIGHT_BUTTON = "#ffffff"
LIGHT_BORDER = "#d4dae1"
LIGHT_TEXT = "#18212b"
LIGHT_DISABLED_TEXT = "#717b86"
LIGHT_ACCENT = "#2563a6"

DARK_WINDOW = "#1e1f22"
DARK_BASE = "#27282c"
DARK_ALTERNATE_BASE = "#2d2f34"
DARK_BUTTON = "#303238"
DARK_BORDER = "#50535a"
DARK_TEXT = "#f1f3f5"
DARK_DISABLED_TEXT = "#85888f"
DARK_ACCENT = "#329ad6"


class FontRole(str, Enum):
    """Small, shared typography scale for application widgets."""

    BODY = "body"
    SECONDARY = "secondary"
    SECTION_TITLE = "section-title"
    PAGE_TITLE = "page-title"
    METRIC = "metric"


_FONT_ROLE_SPECS = {
    FontRole.BODY: (10.0, QFont.Weight.Normal),
    FontRole.SECONDARY: (9.0, QFont.Weight.Normal),
    FontRole.SECTION_TITLE: (11.0, QFont.Weight.DemiBold),
    FontRole.PAGE_TITLE: (16.0, QFont.Weight.DemiBold),
    FontRole.METRIC: (20.0, QFont.Weight.DemiBold),
}


def ui_font_families(platform_name: str | None = None) -> tuple[str, ...]:
    platform_name = sys.platform if platform_name is None else platform_name
    if platform_name == "win32":
        return ("Microsoft YaHei UI", "Segoe UI", "Microsoft YaHei")
    if platform_name == "darwin":
        return ("PingFang SC", "Helvetica Neue", "Arial")
    return ("Noto Sans CJK SC", "Noto Sans", "DejaVu Sans")


def font_for_role(role: FontRole, platform_name: str | None = None) -> QFont:
    point_size, weight = _FONT_ROLE_SPECS[role]
    font = QFont()
    font.setFamilies(list(ui_font_families(platform_name)))
    font.setPointSizeF(point_size)
    font.setWeight(weight)
    return font


@dataclass(frozen=True, slots=True)
class SemanticColors:
    """Theme-aware text colors for status and supporting copy."""

    secondary_text: str
    success_text: str
    warning_text: str
    error_text: str


LIGHT_SEMANTIC_COLORS = SemanticColors(
    secondary_text="#5f6975",
    success_text="#16783b",
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
    """Return reusable semantic text colors for the supplied Qt palette."""

    return DARK_SEMANTIC_COLORS if is_dark_palette(palette) else LIGHT_SEMANTIC_COLORS


def _build_palette(colors: dict[QPalette.ColorRole, str]) -> QPalette:
    palette = QPalette()
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    return palette


def build_light_palette() -> QPalette:
    palette = _build_palette(
        {
            QPalette.ColorRole.Window: LIGHT_WINDOW,
            QPalette.ColorRole.WindowText: LIGHT_TEXT,
            QPalette.ColorRole.Base: LIGHT_BASE,
            QPalette.ColorRole.AlternateBase: LIGHT_ALTERNATE_BASE,
            QPalette.ColorRole.ToolTipBase: LIGHT_TEXT,
            QPalette.ColorRole.ToolTipText: "#ffffff",
            QPalette.ColorRole.Text: LIGHT_TEXT,
            QPalette.ColorRole.Button: LIGHT_BUTTON,
            QPalette.ColorRole.ButtonText: LIGHT_TEXT,
            QPalette.ColorRole.BrightText: LIGHT_SEMANTIC_COLORS.error_text,
            QPalette.ColorRole.Highlight: LIGHT_ACCENT,
            QPalette.ColorRole.HighlightedText: "#ffffff",
            QPalette.ColorRole.Link: LIGHT_ACCENT,
            QPalette.ColorRole.Light: "#ffffff",
            QPalette.ColorRole.Midlight: "#e8ecf0",
            QPalette.ColorRole.Mid: LIGHT_BORDER,
            QPalette.ColorRole.Dark: "#aeb7c2",
            QPalette.ColorRole.Shadow: "#8d98a5",
            QPalette.ColorRole.PlaceholderText: LIGHT_SEMANTIC_COLORS.secondary_text,
        }
    )
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(LIGHT_DISABLED_TEXT))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, QColor("#edf0f3"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor("#f1f3f5"))
    return palette


def build_dark_palette() -> QPalette:
    palette = _build_palette(
        {
            QPalette.ColorRole.Window: DARK_WINDOW,
            QPalette.ColorRole.WindowText: DARK_TEXT,
            QPalette.ColorRole.Base: DARK_BASE,
            QPalette.ColorRole.AlternateBase: DARK_ALTERNATE_BASE,
            QPalette.ColorRole.ToolTipBase: DARK_ALTERNATE_BASE,
            QPalette.ColorRole.ToolTipText: DARK_TEXT,
            QPalette.ColorRole.Text: DARK_TEXT,
            QPalette.ColorRole.Button: DARK_BUTTON,
            QPalette.ColorRole.ButtonText: DARK_TEXT,
            QPalette.ColorRole.BrightText: DARK_SEMANTIC_COLORS.error_text,
            QPalette.ColorRole.Highlight: DARK_ACCENT,
            QPalette.ColorRole.HighlightedText: "#ffffff",
            QPalette.ColorRole.Link: "#66b7e8",
            QPalette.ColorRole.Light: "#3b3d43",
            QPalette.ColorRole.Midlight: "#34363b",
            QPalette.ColorRole.Mid: DARK_BORDER,
            QPalette.ColorRole.Dark: "#18191c",
            QPalette.ColorRole.Shadow: "#101114",
            QPalette.ColorRole.PlaceholderText: DARK_SEMANTIC_COLORS.secondary_text,
        }
    )
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(DARK_DISABLED_TEXT))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, QColor("#292a2e"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor("#242529"))
    return palette


def _light_stylesheet() -> str:
    return f"""
        QToolTip {{
            color: #ffffff;
            background-color: {LIGHT_TEXT};
            border: 1px solid {LIGHT_TEXT};
            padding: 4px 6px;
        }}
        QGroupBox {{
            background-color: {LIGHT_BASE};
            border: 1px solid {LIGHT_BORDER};
            border-radius: 6px;
            margin-top: 10px;
            padding-top: 8px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
            color: {LIGHT_TEXT};
        }}
        QLineEdit, QComboBox, QAbstractSpinBox {{
            background-color: {LIGHT_BASE};
            border: 1px solid {LIGHT_BORDER};
            border-radius: 4px;
            min-height: 26px;
            padding: 1px 6px;
            selection-background-color: {LIGHT_ACCENT};
        }}
        QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus {{
            border: 1px solid {LIGHT_ACCENT};
        }}
        QPushButton {{
            background-color: {LIGHT_BUTTON};
            border: 1px solid {LIGHT_BORDER};
            border-radius: 4px;
            min-height: 28px;
            padding: 2px 12px;
        }}
        QPushButton:hover {{ background-color: {LIGHT_ALTERNATE_BASE}; }}
        QPushButton:pressed {{ background-color: #e0e6ec; }}
        QPushButton:default {{
            background-color: {LIGHT_ACCENT};
            border-color: {LIGHT_ACCENT};
            color: #ffffff;
        }}
        QPushButton:disabled {{
            background-color: #edf0f3;
            border-color: {LIGHT_BORDER};
            color: {LIGHT_DISABLED_TEXT};
        }}
        QToolButton {{
            border: none;
            padding: 3px 4px;
            color: {LIGHT_ACCENT};
        }}
        QTabWidget::pane {{ border: 1px solid {LIGHT_BORDER}; }}
        QScrollArea {{ border: none; background: transparent; }}
        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: #b7c0ca;
            min-height: 36px;
            border-radius: 5px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        #navigationPanel {{
            background-color: #e8edf2;
            border-right: 1px solid {LIGHT_BORDER};
        }}
        #navigationTitle {{ color: {LIGHT_TEXT}; }}
        QPushButton[navigation="true"] {{
            background: transparent;
            border: none;
            border-radius: 4px;
            min-height: 38px;
            padding: 0 12px;
            text-align: left;
        }}
        QPushButton[navigation="true"]:hover {{ background-color: #dde5ed; }}
        QPushButton[navigation="true"]:checked {{
            background-color: #d7e5f3;
            color: #164e82;
            border-left: 3px solid {LIGHT_ACCENT};
            font-weight: 600;
        }}
        #pageHeader {{
            background-color: {LIGHT_WINDOW};
            border-bottom: 1px solid {LIGHT_BORDER};
        }}
        #resultOutcomePanel, #recordsEmptyState {{
            background-color: {LIGHT_BASE};
            border: 1px solid {LIGHT_BORDER};
            border-radius: 6px;
        }}
    """


def _dark_stylesheet() -> str:
    return f"""
        QToolTip {{
            color: {DARK_TEXT};
            background-color: {DARK_ALTERNATE_BASE};
            border: 1px solid {DARK_BORDER};
            padding: 4px 6px;
        }}
        QScrollBar:vertical {{ background: {DARK_WINDOW}; width: 10px; margin: 0; }}
        QScrollBar::handle:vertical {{
            background: #5b5e65;
            min-height: 36px;
            border-radius: 5px;
        }}
        QScrollBar::handle:vertical:hover {{ background: #71747c; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """


def apply_application_theme(
    app: QApplication,
    force_dark: bool | None = None,
    *,
    mode: str = "light",
) -> bool:
    """Apply the shared UI font and a curated light or dark application palette."""

    if force_dark is not None:
        use_dark = force_dark
    elif mode == "system":
        use_dark = is_dark_palette(app.palette())
    elif mode == "dark":
        use_dark = True
    else:
        use_dark = False

    app.setStyle("Fusion")
    app.setFont(font_for_role(FontRole.BODY))
    app.setPalette(build_dark_palette() if use_dark else build_light_palette())
    app.setStyleSheet(_dark_stylesheet() if use_dark else _light_stylesheet())
    return use_dark
