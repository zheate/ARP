from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from combined_test.persistence import build_spectrum_csv_path, save_spectrum_curve


class SpectrumCurveFileTests(unittest.TestCase):
    def test_build_spectrum_csv_path_uses_main_csv_sibling_directory(self) -> None:
        path = build_spectrum_csv_path(
            Path("records/main.csv"),
            datetime(2026, 7, 8, 12, 1, 2, 3456),
        )

        self.assertEqual(
            path,
            Path("records/main_spectra/spectrum_20260708_120102_003456.csv"),
        )

    def test_save_spectrum_curve_writes_full_wavelength_curve(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "curve.csv"

            save_spectrum_curve(path, [975.1, 975.2], [100, 200.5])

            self.assertEqual(
                path.read_text(encoding="utf-8").splitlines(),
                [
                    "wavelength_nm,intensity",
                    "975.100000,100.000000",
                    "975.200000,200.500000",
                ],
            )


if __name__ == "__main__":
    unittest.main()
