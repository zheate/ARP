from __future__ import annotations

import unittest

from matplotlib.colors import to_hex
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QWidget

from combined_test.plots import LivePlots
from combined_test.theme import DARK_BASE, DARK_WINDOW, apply_application_theme, build_dark_palette


class DarkThemeTests(unittest.TestCase):
    def test_dark_palette_has_distinct_readable_surface_roles(self) -> None:
        palette = build_dark_palette()

        self.assertEqual(palette.color(QPalette.ColorRole.Window).name(), DARK_WINDOW)
        self.assertEqual(palette.color(QPalette.ColorRole.Base).name(), DARK_BASE)
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
