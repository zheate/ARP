from __future__ import annotations

import unittest

from matplotlib.colors import to_hex
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from combined_test.plots import LivePlots
from combined_test.theme import (
    DARK_SEMANTIC_COLORS,
    LIGHT_SEMANTIC_COLORS,
    apply_application_theme,
    semantic_colors_for_palette,
)


def make_palette(window: str, base: str) -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(window))
    palette.setColor(QPalette.ColorRole.Base, QColor(base))
    return palette


class NativeThemeTests(unittest.TestCase):
    def test_semantic_colors_follow_the_supplied_system_palette(self) -> None:
        self.assertIs(
            semantic_colors_for_palette(make_palette("#ffffff", "#ffffff")),
            LIGHT_SEMANTIC_COLORS,
        )
        self.assertIs(
            semantic_colors_for_palette(make_palette("#202020", "#282828")),
            DARK_SEMANTIC_COLORS,
        )

    def test_plot_surface_follows_the_parent_qt_palette(self) -> None:
        QApplication.instance() or QApplication([])
        parent = QWidget()
        parent.setPalette(make_palette("#202020", "#282828"))
        plots = LivePlots(parent)

        self.assertEqual(to_hex(plots.power_curve_figure.get_facecolor()), "#202020")
        self.assertEqual(to_hex(plots.power_curve_axis.get_facecolor()), "#282828")
        plots.group.close()
        parent.close()

    def test_compatibility_theme_function_does_not_override_native_qt(self) -> None:
        app = QApplication.instance() or QApplication([])
        style_name = app.style().objectName()
        palette_cache_key = app.palette().cacheKey()
        font_description = app.font().toString()
        stylesheet = app.styleSheet()

        result = apply_application_theme(app, force_dark=True, mode="dark")

        self.assertEqual(result, app.palette().color(QPalette.ColorRole.Window).lightness() < 128)
        self.assertEqual(app.style().objectName(), style_name)
        self.assertEqual(app.palette().cacheKey(), palette_cache_key)
        self.assertEqual(app.font().toString(), font_description)
        self.assertEqual(app.styleSheet(), stylesheet)


if __name__ == "__main__":
    unittest.main()
