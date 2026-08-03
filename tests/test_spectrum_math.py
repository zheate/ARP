import math
import unittest
from unittest.mock import patch

from combined_test import spectrum_math
from combined_test.spectrum_math import (
    calculate_central,
    calculate_centroid,
    calculate_fwhm,
    calculate_pib,
    calculate_smsr,
    calculate_stats,
)


SCRIPTS_RUNNER_2A_WAVELENGTH = [
    971.5644584611462, 971.680853201268, 971.7972417640513,
    971.9136241490803, 972.0300003559394, 972.1463703842126,
    972.2627342334843, 972.3790919033383, 972.4954433933594,
    972.6117887031315, 972.7281278322389, 972.8444607802658,
    972.9607875467962, 973.0771081314147, 973.1934225337052,
    973.3097307532521, 973.4260327896396, 973.5423286424518,
    973.6586183112731, 973.7749017956876, 973.8911790952795,
    974.0074502096331, 974.1237151383325, 974.2399738809621,
    974.356226437106, 974.4724728063484, 974.5887129882734,
    974.7049469824656, 974.8211747885088, 974.9373964059873,
    975.0536118344858, 975.1698210735879, 975.286024122878,
    975.4022209819403, 975.5184116503592,
]
SCRIPTS_RUNNER_2A_INTENSITY = [
    -68.22900705954635, 130.2687906017591, -16.34304766595577,
    28.99627687698802, -14.18275165025022, -46.6007568571763,
    105.5146831726302, 32.23265079435033, 132.420555273169,
    65.65856129150653, 108.7444020494562, 382.2887017265423,
    1278.750732663168, 2711.229197246604, 4436.325802847912,
    4122.634557539346, 2774.925121748412, 1730.091392780778,
    1297.627052488407, 810.9550904508993, 354.4841791277832,
    119.5081308902276, 21.44362822003623, -20.66402609691797,
    -69.31076294581275, -43.35764416128555, 69.96938498005885,
    3.094992511949345, -114.7742755408574, -47.6818594744294,
    -8.78257432992833, 75.3572119677183, 60.26932781377624,
    14.96869055449673, -22.82470870698469,
]


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

    def test_scripts_runner_2a_golden_values(self) -> None:
        stats = calculate_stats(
            SCRIPTS_RUNNER_2A_WAVELENGTH,
            SCRIPTS_RUNNER_2A_INTENSITY,
        )

        self.assertAlmostEqual(stats.peak_wavelength_nm, 973.2625367737977, places=10)
        self.assertAlmostEqual(stats.centroid_nm, 973.2958400343559, places=10)
        self.assertAlmostEqual(stats.fwhm_nm, 0.45093354588107104, places=10)

    def test_absolute_half_max_is_not_baseline_corrected(self) -> None:
        wavelength = [0.0, 1.0, 2.0, 3.0, 4.0]

        self.assertAlmostEqual(
            calculate_fwhm(wavelength, [20.0, 20.0, 100.0, 20.0, 20.0]),
            1.25,
        )
        self.assertAlmostEqual(
            calculate_fwhm(wavelength, [-20.0, -20.0, 100.0, -20.0, -20.0]),
            5.0 / 6.0,
        )

    def test_asymmetric_peak_uses_interpolated_outer_half_max_crossings(self) -> None:
        wavelength = [0.0, 1.0, 2.0, 3.0, 4.0]
        intensity = [0.0, 25.0, 100.0, 60.0, 0.0]

        self.assertAlmostEqual(calculate_central(wavelength, intensity), 2.25)
        self.assertAlmostEqual(calculate_fwhm(wavelength, intensity), 11.0 / 6.0)

    def test_multiple_peaks_use_the_first_and_last_half_max_crossings(self) -> None:
        wavelength = [0.0, 1.0, 2.0, 3.0, 4.0]
        intensity = [0.0, 100.0, 0.0, 80.0, 0.0]

        self.assertAlmostEqual(calculate_central(wavelength, intensity), 1.9375)
        self.assertAlmostEqual(calculate_fwhm(wavelength, intensity), 2.875)

    def test_centroid_returns_nan_without_effective_intensity(self) -> None:
        self.assertTrue(
            math.isnan(calculate_centroid([1.0, 2.0, 3.0], [10.0, 10.0, 10.0]))
        )

    def test_fwhm_interpolates_half_max_crossings(self) -> None:
        self.assertAlmostEqual(calculate_fwhm([0.0, 1.0, 2.0], [0.0, 10.0, 0.0]), 1.0)

    def test_center_and_fwhm_return_nan_when_one_crossing_is_missing(self) -> None:
        wavelength = [0.0, 1.0, 2.0]
        intensity = [0.0, 2.0, 3.0]

        self.assertTrue(math.isnan(calculate_central(wavelength, intensity)))
        self.assertTrue(math.isnan(calculate_fwhm(wavelength, intensity)))

    def test_non_finite_input_is_rejected_as_one_invalid_frame(self) -> None:
        stats = calculate_stats([975.0, 976.0, 977.0], [0.0, math.nan, 0.0])

        self.assertTrue(math.isnan(stats.peak_wavelength_nm))
        self.assertTrue(math.isnan(stats.centroid_nm))
        self.assertTrue(math.isnan(stats.fwhm_nm))

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
        intensity[80] = 500.0

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

    def test_smsr_uses_signed_samples_for_a_nonzero_mad_noise_floor(self) -> None:
        wavelength = [956.0 + index * 0.1 for index in range(401)]
        intensity = [float((index % 5) - 2) for index in range(401)]
        intensity[169:172] = [60.0, 100.0, 60.0]
        intensity[199:202] = [600.0, 1000.0, 600.0]

        result = calculate_smsr(wavelength, intensity)

        self.assertAlmostEqual(result.smsr_db, 10.0)
        self.assertEqual(len(result.peaks), 2)

    def test_smsr_keeps_the_main_peak_when_no_side_mode_is_resolved(self) -> None:
        wavelength = [956.0 + index * 0.1 for index in range(401)]
        intensity = [float((index % 5) - 2) for index in range(401)]
        intensity[199:202] = [600.0, 1000.0, 600.0]

        result = calculate_smsr(wavelength, intensity)

        self.assertTrue(math.isnan(result.smsr_db))
        self.assertEqual(len(result.peaks), 1)
        self.assertAlmostEqual(result.peaks[0].wavelength_nm, 976.0)


if __name__ == "__main__":
    unittest.main()
