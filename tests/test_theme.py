from __future__ import annotations

import unittest

from matplotlib.colors import to_hex
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from combined_test.plots import LivePlots
from combined_test.theme import (
    DARK_BASE,
    DARK_SEMANTIC_COLORS,
    DARK_WINDOW,
    LIGHT_SEMANTIC_COLORS,
    apply_application_theme,
    build_dark_palette,
    semantic_colors_for_palette,
)


def _relative_luminance(color: QColor) -> float:
    def linearize(channel: float) -> float:
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue, _alpha = color.getRgbF()
    return 0.2126 * linearize(red) + 0.7152 * linearize(green) + 0.0722 * linearize(blue)


def _contrast_ratio(foreground: str, background: str) -> float:
    light, dark = sorted(
        (_relative_luminance(QColor(foreground)), _relative_luminance(QColor(background))),
        reverse=True,
    )
    return (light + 0.05) / (dark + 0.05)


class DarkThemeTests(unittest.TestCase):
    def test_semantic_colors_follow_light_and_dark_palettes(self) -> None:
        light_palette = QPalette()
        light_palette.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))

        self.assertIs(semantic_colors_for_palette(light_palette), LIGHT_SEMANTIC_COLORS)
        self.assertIs(semantic_colors_for_palette(build_dark_palette()), DARK_SEMANTIC_COLORS)

    def test_dark_semantic_text_colors_meet_normal_text_contrast(self) -> None:
        for role, foreground in (
            ("secondary", DARK_SEMANTIC_COLORS.secondary_text),
            ("success", DARK_SEMANTIC_COLORS.success_text),
            ("warning", DARK_SEMANTIC_COLORS.warning_text),
            ("error", DARK_SEMANTIC_COLORS.error_text),
        ):
            for background in (DARK_WINDOW, DARK_BASE):
                with self.subTest(role=role, background=background):
                    self.assertGreaterEqual(_contrast_ratio(foreground, background), 4.5)

    def test_light_semantic_text_colors_meet_normal_text_contrast(self) -> None:
        for role, foreground in (
            ("secondary", LIGHT_SEMANTIC_COLORS.secondary_text),
            ("success", LIGHT_SEMANTIC_COLORS.success_text),
            ("warning", LIGHT_SEMANTIC_COLORS.warning_text),
            ("error", LIGHT_SEMANTIC_COLORS.error_text),
        ):
            with self.subTest(role=role):
                self.assertGreaterEqual(_contrast_ratio(foreground, "#ffffff"), 4.5)

    def test_dark_palette_has_distinct_readable_surface_roles(self) -> None:
        palette = build_dark_palette()

        self.assertEqual(palette.color(QPalette.ColorRole.Window).name(), DARK_WINDOW)
        self.assertEqual(palette.color(QPalette.ColorRole.Base).name(), DARK_BASE)
        self.assertEqual(
            palette.color(QPalette.ColorRole.PlaceholderText).name(),
            DARK_SEMANTIC_COLORS.secondary_text,
        )
        self.assertEqual(
            palette.color(QPalette.ColorRole.BrightText).name(),
            DARK_SEMANTIC_COLORS.error_text,
        )
        self.assertGreater(palette.color(QPalette.ColorRole.Text).lightness(), 200)
        self.assertGreater(
            palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text).lightness(),
            palette.color(QPalette.ColorRole.Window).lightness(),
        )

    def test_dark_plot_colors_follow_the_qt_palette(self) -> None:
        app = QApplication.instance() or QApplication([])
        parent = QWidget()
        parent.setPalette(build_dark_palette())
        plots = LivePlots(parent)

        self.assertEqual(plots.power_curve_line.get_color(), "#63b3ed")
        self.assertEqual(plots.efficiency_line.get_color(), "#f2a51a")
        self.assertEqual(to_hex(plots.power_curve_figure.get_facecolor()), DARK_WINDOW)
        self.assertEqual(to_hex(plots.power_curve_axis.get_facecolor()), DARK_BASE)
        plots.group.close()
        parent.close()

    def test_forced_dark_theme_styles_scrollbars_and_tooltips(self) -> None:
        app = QApplication.instance() or QApplication([])
        old_style_name = app.style().objectName()
        old_palette = QPalette(app.palette())
        old_stylesheet = app.styleSheet()
        try:
            self.assertTrue(apply_application_theme(app, force_dark=True))
            self.assertEqual(app.palette().color(QPalette.ColorRole.Window).name(), DARK_WINDOW)
            self.assertIn("QScrollBar::handle:vertical", app.styleSheet())
            self.assertIn("QToolTip", app.styleSheet())
        finally:
            app.setStyle(old_style_name)
            app.setPalette(old_palette)
            app.setStyleSheet(old_stylesheet)


if __name__ == "__main__":
    unittest.main()
