from __future__ import annotations

import math
import unittest
from unittest.mock import patch

import numpy as np

from combined_test.native_realtime import (
    NATIVE_ABI_VERSION,
    get_native_realtime_core,
    native_realtime_status,
)
from combined_test.spectrum_math import calculate_pib, calculate_stats


class NativeRealtimeCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        get_native_realtime_core.cache_clear()
        cls.core = get_native_realtime_core()

    def test_status_describes_the_optional_abi(self) -> None:
        status = native_realtime_status()
        self.assertEqual(status["abiVersion"], NATIVE_ABI_VERSION)
        self.assertEqual(status["available"], self.core is not None)

    def test_spectrum_stats_match_python_reference(self) -> None:
        if self.core is None:
            self.skipTest("Rust realtime DLL has not been built")
        wavelength = np.linspace(950.0, 1010.0, 3_648)
        for center_nm, width_nm, ripple in (
            (975.8, 0.18, 20.0),
            (976.2, 0.42, 300.0),
            (978.0, 0.85, 1_200.0),
        ):
            intensity = (
                75.0
                + 52_000.0 * np.exp(-0.5 * ((wavelength - center_nm) / width_nm) ** 2)
                + ripple * np.sin(wavelength * 9.0)
            )
            with patch("combined_test.spectrum_math.get_native_realtime_core", return_value=None):
                expected = calculate_stats(wavelength, intensity)
            with patch(
                "combined_test.spectrum_math.get_native_realtime_core",
                return_value=self.core,
            ):
                actual = calculate_stats(wavelength, intensity)
            for field in (
                "peak_wavelength_nm",
                "peak_intensity",
                "centroid_nm",
                "fwhm_nm",
            ):
                expected_value = getattr(expected, field)
                actual_value = getattr(actual, field)
                if math.isnan(expected_value):
                    self.assertTrue(math.isnan(actual_value), field)
                else:
                    self.assertAlmostEqual(actual_value, expected_value, places=9, msg=field)

    def test_pib_matches_python_reference(self) -> None:
        if self.core is None:
            self.skipTest("Rust realtime DLL has not been built")
        wavelength = np.linspace(950.0, 1010.0, 3_648)
        intensity = (
            120.0
            + 48_000.0 * np.exp(-0.5 * ((wavelength - 976.15) / 0.55) ** 2)
            + 350.0 * np.sin(wavelength * 7.0)
        )
        with patch("combined_test.spectrum_math.get_native_realtime_core", return_value=None):
            expected = calculate_pib(wavelength, intensity)
        with patch(
            "combined_test.spectrum_math.get_native_realtime_core",
            return_value=self.core,
        ):
            actual = calculate_pib(wavelength, intensity)
        self.assertAlmostEqual(actual, expected, places=9)


if __name__ == "__main__":
    unittest.main()
