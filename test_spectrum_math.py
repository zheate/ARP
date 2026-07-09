import math
import unittest

from spectrum_math import calculate_centroid, calculate_fwhm, calculate_stats


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

    def test_centroid_weights_by_intensity(self) -> None:
        self.assertAlmostEqual(calculate_centroid([1.0, 2.0, 3.0], [0.0, 1.0, 3.0]), 2.75)

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


if __name__ == "__main__":
    unittest.main()
