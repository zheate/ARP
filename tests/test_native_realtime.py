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

class NativeRealtimeBoundaryTests(unittest.TestCase):
    def test_wrapper_rejects_nonfinite_and_mismatched_inputs(self):
        from combined_test.native_realtime import NativeRealtimeCore
        for values in ([float("nan")], [float("inf")], []):
            with self.assertRaises(ValueError):
                NativeRealtimeCore._array(values)
        core = get_native_realtime_core()
        self.assertIsNotNone(core, "Build the native library before boundary acceptance")
        with self.assertRaises(ValueError):
            core.calculate_spectrum_stats([975, 976], [1])

    def test_load_failure_is_reported_and_cached(self):
        from combined_test import native_realtime as native
        from pathlib import Path
        native.get_native_realtime_core.cache_clear()
        try:
            with patch.object(native, "_candidate_paths", return_value=(Path(__file__),)), patch.object(native.ctypes, "CDLL", side_effect=OSError("missing dependency")) as load:
                self.assertIsNone(native.get_native_realtime_core())
                self.assertIsNone(native.get_native_realtime_core())
                self.assertEqual(native.native_realtime_status()["reason"], "load_or_abi_failed")
                load.assert_called_once()
        finally:
            native.get_native_realtime_core.cache_clear()

    def test_known_native_failure_falls_back_but_value_error_is_not_hidden(self):
        from combined_test.native_realtime import NativeRealtimeUnavailable
        from unittest.mock import Mock
        x, y = [975, 976, 977], [1, 10, 1]
        core = Mock()
        core.calculate_spectrum_stats.side_effect = NativeRealtimeUnavailable("failure")
        with patch("combined_test.spectrum_math.get_native_realtime_core", return_value=core):
            self.assertEqual(calculate_stats(x, y).peak_intensity, 10)
        core.calculate_spectrum_stats.side_effect = ValueError("invalid input")
        with patch("combined_test.spectrum_math.get_native_realtime_core", return_value=core):
            with self.assertRaisesRegex(ValueError, "invalid input"):
                calculate_stats(x, y)

    def test_flat_and_zero_signals_preserve_unavailable_results(self):
        core = get_native_realtime_core()
        self.assertIsNotNone(core)
        x = np.linspace(950, 1010, 1024)
        for y in (np.zeros(1024), np.full(1024, 75.0)):
            with patch("combined_test.spectrum_math.get_native_realtime_core", return_value=None):
                expected = calculate_stats(x, y)
                expected_pib = calculate_pib(x, y)
            with patch("combined_test.spectrum_math.get_native_realtime_core", return_value=core):
                actual = calculate_stats(x, y)
                actual_pib = calculate_pib(x, y)
            np.testing.assert_allclose(list(vars(actual).values()), list(vars(expected).values()), atol=1e-9, rtol=1e-12, equal_nan=True)
            self.assertAlmostEqual(actual_pib, expected_pib, places=9)

    def test_dynamic_center_and_sparse_window_match_python(self):
        core = get_native_realtime_core()
        self.assertIsNotNone(core)
        x = np.arange(956.0, 996.0001, 0.1)
        y = 100 + 5000 * np.exp(-0.5 * ((x - 982) / 0.35) ** 2)
        for wavelength, intensity, center in ((x, y, 0.0), ([900, 976, 1050], [10, 100, 10], 976.0)):
            with patch("combined_test.spectrum_math.get_native_realtime_core", return_value=None):
                expected = calculate_pib(wavelength, intensity, center_nm=center)
            with patch("combined_test.spectrum_math.get_native_realtime_core", return_value=core):
                actual = calculate_pib(wavelength, intensity, center_nm=center)
            self.assertAlmostEqual(actual, expected, places=9)


if __name__ == "__main__":
    unittest.main()
