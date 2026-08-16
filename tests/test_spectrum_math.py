import hashlib
import math
import struct
import unittest
from unittest.mock import patch

import numpy as np

from combined_test import spectrum_math
from combined_test.spectrum import (
    detect_spectrum_saturation,
    find_spectrum_peak_annotations,
)
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


def _float_hex(value: float) -> str:
    return "nan" if math.isnan(value) else value.hex()


def _dense_golden_spectrum(point_count: int) -> tuple[list[float], list[float]]:
    wavelength = [
        956.0 + index * (40.0 / (point_count - 1))
        for index in range(point_count)
    ]
    intensity = [float((index % 11) - 5) for index in range(point_count)]
    main_index = point_count // 2
    side_index = main_index - max(24, point_count // 20)
    intensity[main_index - 2 : main_index + 3] = [
        2500.0,
        7000.0,
        15000.0,
        7000.0,
        2500.0,
    ]
    intensity[side_index - 2 : side_index + 3] = [
        250.0,
        700.0,
        1500.0,
        700.0,
        250.0,
    ]
    return wavelength, intensity


DENSE_GOLDEN_RESULTS = {
    3648: {
        "input_sha256": "87678681d57f1ced417f3390dc35d33cad30d910bb13b8d6365a375f07c30aa0",
        "stats": (
            "0x1.e800b3b2c92d8p+9",
            "0x1.d4c0000000000p+13",
            "0x1.e7ea35e941132p+9",
            "0x1.50ef393568000p-6",
        ),
        "pib": "0x1.d1745d1745fb3p-1",
        "smsr": "0x1.4000000000000p+3",
        "annotations": (
            ("P1", "0x1.e800b3b2c92d8p+9", "0x1.e800b3b2c92d8p+9", "0x1.d4c0000000000p+13"),
            ("P2", "0x1.e701317cbc671p+9", "0x1.e701317cbc671p+9", "0x1.7700000000000p+10"),
        ),
    },
    8000: {
        "input_sha256": "5cc3abb3a11c024c17fd9f3c6c6fc718106b8d6032762c9461aa2091aff76612",
        "stats": (
            "0x1.e80051ee244aep+9",
            "0x1.d4c0000000000p+13",
            "0x1.e7e986ab46a87p+9",
            "0x1.333d0818d0000p-7",
        ),
        "pib": "0x1.d1745d17465fdp-1",
        "smsr": "0x1.4000000000000p+3",
        "annotations": (
            ("P1", "0x1.e80051ee244aep+9", "0x1.e80051ee244aep+9", "0x1.d4c0000000000p+13"),
            ("P2", "0x1.e70049bcba436p+9", "0x1.e70049bcba436p+9", "0x1.7700000000000p+10"),
        ),
    },
}


class SpectrumMathTests(unittest.TestCase):
    def test_dense_spectrum_golden_corpus_is_bitwise_stable(self) -> None:
        for point_count, expected in DENSE_GOLDEN_RESULTS.items():
            with self.subTest(point_count=point_count):
                wavelength, intensity = _dense_golden_spectrum(point_count)
                digest = hashlib.sha256(
                    b"".join(
                        struct.pack("!d", float(value))
                        for pair in zip(wavelength, intensity)
                        for value in pair
                    )
                ).hexdigest()
                stats = calculate_stats(wavelength, intensity)
                pib = calculate_pib(wavelength, intensity)
                smsr = calculate_smsr(wavelength, intensity)
                saturation = detect_spectrum_saturation(intensity, 64_000.0)
                annotations = find_spectrum_peak_annotations(
                    zip(wavelength, intensity),
                    stats=stats,
                    smsr_result=smsr,
                )

                self.assertEqual(digest, expected["input_sha256"])
                self.assertEqual(
                    tuple(
                        _float_hex(value)
                        for value in (
                            stats.peak_wavelength_nm,
                            stats.peak_intensity,
                            stats.centroid_nm,
                            stats.fwhm_nm,
                        )
                    ),
                    expected["stats"],
                )
                self.assertEqual(_float_hex(pib), expected["pib"])
                self.assertEqual(_float_hex(smsr.smsr_db), expected["smsr"])
                self.assertEqual(saturation.saturated, False)
                self.assertEqual(
                    tuple(
                        (
                            annotation.label,
                            _float_hex(annotation.centroid_nm),
                            _float_hex(annotation.peak_wavelength_nm),
                            _float_hex(annotation.peak_intensity),
                        )
                        for annotation in annotations
                    ),
                    expected["annotations"],
                )

    def test_flat_saturated_and_non_finite_golden_corpus_is_stable(self) -> None:
        flat_wavelength = [956.0 + index for index in range(41)]
        flat_intensity = [100.0] * 41
        flat_stats = calculate_stats(flat_wavelength, flat_intensity)
        flat_smsr = calculate_smsr(flat_wavelength, flat_intensity)
        self.assertEqual(
            tuple(
                _float_hex(value)
                for value in (
                    flat_stats.peak_wavelength_nm,
                    flat_stats.peak_intensity,
                    flat_stats.centroid_nm,
                    flat_stats.fwhm_nm,
                )
            ),
            ("nan", "0x1.9000000000000p+6", "nan", "nan"),
        )
        self.assertEqual(_float_hex(calculate_pib(flat_wavelength, flat_intensity)), "0x0.0p+0")
        self.assertEqual(_float_hex(flat_smsr.smsr_db), "nan")
        self.assertEqual(find_spectrum_peak_annotations(zip(flat_wavelength, flat_intensity)), [])

        saturated_intensity = [0.0] * 41
        saturated_intensity[18:23] = [1000.0, 64_000.0, 64_000.0, 64_000.0, 1000.0]
        saturation = detect_spectrum_saturation(saturated_intensity, 64_000.0)
        saturated_stats = calculate_stats(flat_wavelength, saturated_intensity)
        saturated_smsr = calculate_smsr(flat_wavelength, saturated_intensity)
        saturated_annotations = find_spectrum_peak_annotations(
            zip(flat_wavelength, saturated_intensity),
            stats=saturated_stats,
            smsr_result=saturated_smsr,
        )
        self.assertEqual(
            (
                saturation.saturated,
                _float_hex(saturation.peak_intensity),
                saturation.consecutive_pixels,
            ),
            (True, "0x1.f400000000000p+15", 3),
        )
        self.assertEqual(
            tuple(
                _float_hex(value)
                for value in (
                    saturated_stats.peak_wavelength_nm,
                    saturated_stats.peak_intensity,
                    saturated_stats.centroid_nm,
                    saturated_stats.fwhm_nm,
                )
            ),
            (
                "0x1.e800000000000p+9",
                "0x1.f400000000000p+15",
                "0x1.e800000000000p+9",
                "0x1.8208208208200p+1",
            ),
        )
        self.assertEqual(
            _float_hex(calculate_pib(flat_wavelength, saturated_intensity)),
            "0x1.51d07eae2f815p-1",
        )
        self.assertEqual(_float_hex(saturated_smsr.smsr_db), "nan")
        self.assertEqual(
            [
                (
                    annotation.label,
                    _float_hex(annotation.centroid_nm),
                    _float_hex(annotation.peak_wavelength_nm),
                    _float_hex(annotation.peak_intensity),
                )
                for annotation in saturated_annotations
            ],
            [
                (
                    "P1",
                    "0x1.e800000000000p+9",
                    "0x1.e800000000000p+9",
                    "0x1.f400000000000p+15",
                )
            ],
        )

        non_finite_wavelength = [975.0, 976.0, 977.0]
        non_finite_intensity = [0.0, math.nan, 0.0]
        non_finite_stats = calculate_stats(non_finite_wavelength, non_finite_intensity)
        self.assertEqual(
            tuple(
                _float_hex(value)
                for value in (
                    non_finite_stats.peak_wavelength_nm,
                    non_finite_stats.peak_intensity,
                    non_finite_stats.centroid_nm,
                    non_finite_stats.fwhm_nm,
                )
            ),
            ("nan", "nan", "nan", "nan"),
        )
        self.assertEqual(
            _float_hex(calculate_pib(non_finite_wavelength, non_finite_intensity)),
            "nan",
        )
        self.assertEqual(
            _float_hex(calculate_smsr(non_finite_wavelength, non_finite_intensity).smsr_db),
            "nan",
        )
        self.assertEqual(
            find_spectrum_peak_annotations(zip(non_finite_wavelength, non_finite_intensity)),
            [],
        )
        non_finite_saturation = detect_spectrum_saturation(
            non_finite_intensity,
            64_000.0,
        )
        self.assertEqual(
            (
                non_finite_saturation.saturated,
                _float_hex(non_finite_saturation.peak_intensity),
                non_finite_saturation.consecutive_pixels,
            ),
            (False, "0x0.0p+0", 0),
        )

    def test_quiet_window_threshold_golden_values_are_bitwise_stable(self) -> None:
        below = float.fromhex("0x1.1dc617f98523ep+9")
        above = float.fromhex("0x1.1dc617f98523fp+9")
        for label, boundary_value, expected_std, expected_centroid in (
            (
                "below",
                below,
                "0x1.8ffffffffffffp+7",
                "0x1.e3173debc5591p+9",
            ),
            (
                "above",
                above,
                "0x1.9000000000001p+7",
                "0x1.e3173debc5590p+9",
            ),
        ):
            with self.subTest(label=label):
                intensity = [0.0] * 24
                intensity[7] = boundary_value
                intensity[12] = 1000.0
                wavelength = [956.0 + index for index in range(len(intensity))]
                self.assertEqual(
                    _float_hex(float(np.std(np.asarray(intensity[1:8], dtype=float)))),
                    expected_std,
                )
                self.assertEqual(
                    _float_hex(calculate_stats(wavelength, intensity).centroid_nm),
                    expected_centroid,
                )

        exact_window = [
            float.fromhex(value)
            for value in (
                "-0x1.33d27319ebc64p+7",
                "0x1.ed203afe1f7a0p+6",
                "0x1.cf434593c6238p+6",
                "-0x1.5f72033296cf2p+8",
                "-0x1.3ce8cb54d2e06p+8",
                "-0x1.8196479e54d8ep+8",
                "-0x1.6d5e65a95ce71p+5",
            )
        ]
        intensity = [0.0] * 24
        intensity[0] = -200.0
        intensity[1:8] = exact_window
        intensity[12] = 1000.0
        wavelength = [956.0 + index for index in range(len(intensity))]
        self.assertEqual(
            _float_hex(float(np.std(np.asarray(intensity[1:8], dtype=float)))),
            "0x1.9000000000000p+7",
        )
        self.assertEqual(
            _float_hex(calculate_stats(wavelength, intensity).centroid_nm),
            "0x1.e5676fbd17128p+9",
        )

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

    def test_pib_matches_scripts_runner_signal_region_and_background_correction(self) -> None:
        wavelength = np.arange(956.0, 996.0001, 0.1)
        intensity = (
            100.0
            + 5000.0 * np.exp(-0.5 * ((wavelength - 976.0) / 0.35) ** 2)
            + 1000.0 * np.exp(-0.5 * ((wavelength - 980.0) / 0.35) ** 2)
        )

        pib = calculate_pib(wavelength, intensity)

        self.assertAlmostEqual(pib, 0.8332952200066381)

    def test_pib_uses_fixed_976_nm_band_instead_of_following_a_shifted_peak(self) -> None:
        wavelength = np.arange(956.0, 996.0001, 0.1)
        intensity = 100.0 + 5000.0 * np.exp(
            -0.5 * ((wavelength - 982.0) / 0.35) ** 2
        )

        self.assertEqual(calculate_pib(wavelength, intensity), 0.0)

    def test_pib_returns_zero_when_total_intensity_is_zero(self) -> None:
        self.assertEqual(calculate_pib([975.0, 976.0, 977.0], [0.0, 0.0, 0.0]), 0.0)

    def test_pib_returns_nan_for_empty_input(self) -> None:
        self.assertTrue(math.isnan(calculate_pib([], [])))

    def test_pib_supports_scripts_runner_dynamic_center_when_requested(self) -> None:
        wavelength = np.arange(956.0, 996.0001, 0.1)
        intensity = 100.0 + 5000.0 * np.exp(
            -0.5 * ((wavelength - 982.0) / 0.35) ** 2
        )

        self.assertAlmostEqual(calculate_pib(wavelength, intensity, center_nm=0.0), 0.9998772417169143)

    def test_pib_supports_user_configured_target_center_and_half_range(self) -> None:
        # 940nm locked laser peak
        wavelength = np.arange(920.0, 960.0001, 0.1)
        intensity = 100.0 + 5000.0 * np.exp(
            -0.5 * ((wavelength - 940.0) / 0.35) ** 2
        )

        # Default (976.0 nm) returns 0.0 because 940 nm is outside [974.5, 977.5]
        self.assertEqual(calculate_pib(wavelength, intensity), 0.0)

        # Configured for 940.0 nm with 1.5 nm half-range captures the full peak
        pib_940 = calculate_pib(wavelength, intensity, center_nm=940.0, half_range_nm=1.5)
        self.assertAlmostEqual(pib_940, 0.9999542642392277, places=6)

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
