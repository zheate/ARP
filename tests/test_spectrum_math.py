import math
import unittest
from unittest.mock import patch

from combined_test import spectrum_math
from combined_test.spectrum_math import calculate_centroid, calculate_fwhm, calculate_pib, calculate_smsr, calculate_stats


class SpectrumMathTests(unittest.TestCase):
    def test_calculate_stats_reports_peak_centroid_and_fwhm(self) -> None:
        stats = calculate_stats(
            [974.0, 975.0, 976.0, 977.0, 978.0],
            [0.0, 5.0, 10.0, 5.0, 0.0],
        )

        self.assertEqual(stats.peak_wavelength_nm, 976.0)
        self.assertEqual(stats.peak_intensity, 10.0)
        self.assertAlmostEqual(stats.centroid_nm, 976.0)
        self.assertAlmostEqual(stats.fwhm_nm, 2.0)

    def test_calculate_stats_normalizes_the_spectrum_once(self) -> None:
        with patch.object(spectrum_math, "_as_float_lists", wraps=spectrum_math._as_float_lists) as normalize:
            calculate_stats([975.0, 976.0, 977.0], [0.0, 10.0, 0.0])

        self.assertEqual(normalize.call_count, 1)

    def test_centroid_weights_by_intensity(self) -> None:
        self.assertAlmostEqual(calculate_centroid([1.0, 2.0, 3.0], [0.0, 1.0, 3.0]), 2.75)

    def test_centroid_ignores_disconnected_background_and_secondary_noise(self) -> None:
        centroid = calculate_centroid(
            [900.0, 975.0, 976.0, 977.0, 1050.0],
            [-10.0, 0.0, 100.0, 0.0, 20.0],
        )

        self.assertAlmostEqual(centroid, 976.0)

    def test_fwhm_interpolates_half_max_crossings(self) -> None:
        self.assertAlmostEqual(calculate_fwhm([0.0, 1.0, 2.0], [0.0, 10.0, 0.0]), 1.0)

    def test_invalid_or_empty_inputs_return_nan_stats(self) -> None:
        stats = calculate_stats([], [])

        self.assertTrue(math.isnan(stats.peak_wavelength_nm))
        self.assertTrue(math.isnan(stats.peak_intensity))
        self.assertTrue(math.isnan(stats.centroid_nm))
        self.assertTrue(math.isnan(stats.fwhm_nm))

        bad = calculate_stats([1.0], [1.0, 2.0])
        self.assertTrue(math.isnan(bad.peak_wavelength_nm))

    def test_pib_integrates_974_5_to_977_5_over_fixed_956_to_996_analysis_band(self) -> None:
        pib = calculate_pib(
            [950.0, 956.0, 974.5, 976.0, 977.5, 996.0, 1000.0],
            [1000.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1000.0],
        )

        self.assertAlmostEqual(pib, 3.0 / 40.0)

    def test_pib_returns_nan_when_total_intensity_is_zero(self) -> None:
        self.assertTrue(math.isnan(calculate_pib([975.0, 976.0, 977.0], [0.0, 0.0, 0.0])))

    def test_pib_returns_nan_when_analysis_band_is_not_fully_covered(self) -> None:
        self.assertTrue(math.isnan(calculate_pib([974.5, 976.0, 977.5], [1.0, 2.0, 1.0])))

    def test_smsr_uses_main_peak_over_highest_other_local_peak(self) -> None:
        wavelength = [973.0 + index * 0.1 for index in range(41)]
        intensity = [0.0] * len(wavelength)
        intensity[9:12] = [60.0, 100.0, 60.0]
        intensity[29:32] = [600.0, 1000.0, 600.0]
        result = calculate_smsr(
            wavelength,
            intensity,
            analysis_lower_nm=973.0,
            analysis_upper_nm=977.0,
        )

        self.assertAlmostEqual(result.smsr_db, 10.0)
        self.assertEqual(result.main_wavelength_nm, 976.0)
        self.assertEqual(result.side_wavelength_nm, 974.0)

    def test_smsr_returns_nan_without_a_resolved_side_mode(self) -> None:
        wavelength = [975.0 + index * 0.1 for index in range(21)]
        intensity = [0.0] * len(wavelength)
        intensity[9:12] = [60.0, 100.0, 60.0]
        result = calculate_smsr(
            wavelength,
            intensity,
            analysis_lower_nm=975.0,
            analysis_upper_nm=977.0,
        )

        self.assertTrue(math.isnan(result.smsr_db))

    def test_smsr_returns_nan_when_analysis_band_is_not_fully_covered(self) -> None:
        result = calculate_smsr(
            [974.0, 975.0, 976.0, 977.0],
            [0.0, 10.0, 100.0, 0.0],
        )

        self.assertTrue(math.isnan(result.smsr_db))

    def test_smsr_rejects_noise_ripple_as_an_unresolved_side_mode(self) -> None:
        wavelength = [970.0 + index * 0.1 for index in range(121)]
        intensity = [10.0 + (index % 3) for index in range(121)]
        intensity[59:62] = [600.0, 1000.0, 600.0]

        result = calculate_smsr(
            wavelength,
            intensity,
            analysis_lower_nm=970.0,
            analysis_upper_nm=982.0,
        )

        self.assertTrue(math.isnan(result.smsr_db))

    def test_smsr_rejects_an_isolated_hot_pixel_as_a_side_mode(self) -> None:
        wavelength = [970.0 + index * 0.1 for index in range(121)]
        intensity = [10.0] * len(wavelength)
        intensity[39:42] = [600.0, 1000.0, 600.0]
        intensity[80] = 12.0

        result = calculate_smsr(
            wavelength,
            intensity,
            analysis_lower_nm=970.0,
            analysis_upper_nm=982.0,
        )

        self.assertTrue(math.isnan(result.smsr_db))

    def test_smsr_preserves_a_resolved_40_db_side_mode_above_a_flat_noise_floor(self) -> None:
        wavelength = [973.0 + index * 0.1 for index in range(41)]
        intensity = [0.0] * len(wavelength)
        intensity[9:12] = [0.6, 1.0, 0.6]
        intensity[29:32] = [6000.0, 10000.0, 6000.0]
        result = calculate_smsr(
            wavelength,
            intensity,
            analysis_lower_nm=973.0,
            analysis_upper_nm=977.0,
        )

        self.assertAlmostEqual(result.smsr_db, 40.0)


if __name__ == "__main__":
    unittest.main()
