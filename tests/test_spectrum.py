import unittest

from combined_test.spectrum import (
    SPECTRUM_Y_AXIS_SHRINK_REQUIRED_FRAMES,
    _centered_window_minimums,
    detect_spectrum_saturation,
    find_spectrum_peak_annotations,
    stabilize_spectrum_y_axis_upper_limit,
)
from combined_test.spectrum_math import calculate_smsr


class SpectrumAnalysisTests(unittest.TestCase):
    def test_centered_window_minimums_match_direct_windows(self) -> None:
        values = [7.0, 3.0, 5.0, 2.0, 9.0, 4.0, 6.0]
        radius = 2

        minimums = _centered_window_minimums(values, radius)
        expected = [
            min(values[max(0, index - radius) : index + radius + 1])
            for index in range(len(values))
        ]

        self.assertEqual(minimums, expected)

    def test_peak_annotations_keep_height_order_and_peak_wavelengths(self) -> None:
        wavelength = [956.0 + index * 0.1 for index in range(401)]
        intensity = [float((index % 5) - 2) for index in range(401)]
        intensity[59:62] = [48.0, 80.0, 48.0]
        intensity[199:202] = [180.0, 300.0, 180.0]
        intensity[269:272] = [120.0, 200.0, 120.0]
        intensity[198] = intensity[202] = 0.0
        points = list(zip(wavelength, intensity))
        annotations = find_spectrum_peak_annotations(points)

        self.assertEqual(
            [(item.label, round(item.peak_wavelength_nm, 3)) for item in annotations],
            [
                ("P1", 976.0),
                ("P2", 983.0),
                ("P3", 962.0),
            ],
        )

    def test_peak_annotations_and_smsr_share_the_same_side_mode_values(self) -> None:
        wavelength = [956.0 + index * 0.1 for index in range(401)]
        intensity = [float((index % 5) - 2) for index in range(401)]
        intensity[199:202] = [600.0, 1000.0, 600.0]
        intensity[269:272] = [60.0, 100.0, 60.0]
        smsr = calculate_smsr(wavelength, intensity)

        annotations = find_spectrum_peak_annotations(
            zip(wavelength, intensity),
            smsr_result=smsr,
        )

        self.assertEqual(annotations[0].peak_wavelength_nm, smsr.main_wavelength_nm)
        self.assertEqual(annotations[1].peak_wavelength_nm, smsr.side_wavelength_nm)
        self.assertEqual(annotations[1].peak_intensity, smsr.side_intensity)

    def test_primary_peak_marker_uses_maximum_level_not_half_max_center(self) -> None:
        wavelength = [956.0 + index * 0.1 for index in range(401)]
        intensity = [0.0] * 401
        intensity[198:203] = [0.0, 25.0, 100.0, 60.0, 0.0]

        annotations = find_spectrum_peak_annotations(zip(wavelength, intensity))

        self.assertEqual(annotations[0].peak_wavelength_nm, 976.0)
        self.assertEqual(annotations[0].peak_intensity, 100.0)

    def test_signed_noise_ripple_only_keeps_p1(self) -> None:
        wavelength = [956.0 + index * 0.1 for index in range(401)]
        intensity = [float((index % 7) - 3) for index in range(401)]
        intensity[199:202] = [2400.0, 4000.0, 2400.0]

        annotations = find_spectrum_peak_annotations(zip(wavelength, intensity))

        self.assertEqual([annotation.label for annotation in annotations], ["P1"])

    def test_peak_markers_follow_visible_and_narrow_range_spectra(self) -> None:
        for lower_nm, count, peak_index in ((420.0, 501, 300), (972.0, 81, 40)):
            with self.subTest(lower_nm=lower_nm):
                wavelength = [lower_nm + index * 0.1 for index in range(count)]
                intensity = [float((index % 5) - 2) for index in range(count)]
                intensity[peak_index - 1:peak_index + 2] = [360.0, 600.0, 360.0]
                fixed_band_smsr = calculate_smsr(wavelength, intensity)
                self.assertEqual(fixed_band_smsr.peaks, ())
                for supplied_result in (None, fixed_band_smsr):
                    annotations = find_spectrum_peak_annotations(
                        zip(wavelength, intensity), smsr_result=supplied_result,
                    )
                    self.assertEqual(
                        [(item.label, item.peak_wavelength_nm) for item in annotations],
                        [("P1", wavelength[peak_index])],
                    )

    def test_visible_peak_markers_do_not_reuse_unrelated_infrared_modes(self) -> None:
        wavelength = [400.0 + index * 0.1 for index in range(6001)]
        intensity = [float((index % 5) - 2) for index in range(6001)]
        intensity[499:502] = [360.0, 600.0, 360.0]
        intensity[599:602] = [120.0, 200.0, 120.0]
        intensity[5759:5762] = [60.0, 100.0, 60.0]
        fixed_band_smsr = calculate_smsr(wavelength, intensity)
        self.assertEqual(fixed_band_smsr.main_wavelength_nm, 976.0)

        annotations = find_spectrum_peak_annotations(
            zip(wavelength, intensity), smsr_result=fixed_band_smsr,
        )
        self.assertEqual(
            [(item.label, item.peak_wavelength_nm) for item in annotations],
            [("P1", 450.0), ("P2", 460.0)],
        )

    def test_tied_visible_and_infrared_maxima_use_lowest_wavelength_on_unsorted_input(self) -> None:
        wavelength = [400.0 + index * 0.1 for index in range(6001)]
        intensity = [0.0] * len(wavelength)
        intensity[499:502] = [360.0, 600.0, 360.0]
        intensity[5759:5762] = [360.0, 600.0, 360.0]
        fixed_band_smsr = calculate_smsr(wavelength, intensity)
        self.assertEqual(fixed_band_smsr.main_wavelength_nm, 976.0)
        annotations = find_spectrum_peak_annotations(
            zip(reversed(wavelength), reversed(intensity)), smsr_result=fixed_band_smsr,
        )
        self.assertEqual([(item.label, item.peak_wavelength_nm) for item in annotations],
                         [("P1", 450.0)])

    def test_peak_markers_remain_empty_for_flat_noise_and_degenerate_axes(self) -> None:
        self.assertEqual(find_spectrum_peak_annotations([(450.0, 0.0)] * 3), [])
        self.assertEqual(find_spectrum_peak_annotations([(420 + i * 0.1, 2.0) for i in range(501)]), [])

    def test_saturation_requires_consecutive_near_peak_pixels(self) -> None:
        saturated = detect_spectrum_saturation([0.0, 64_000.0, 64_000.0, 64_000.0, 0.0])
        spike = detect_spectrum_saturation([0.0, 64_000.0, 0.0])

        self.assertTrue(saturated.saturated)
        self.assertEqual(saturated.consecutive_pixels, 3)
        self.assertFalse(spike.saturated)

    def test_non_finite_pixel_breaks_a_saturation_plateau(self) -> None:
        result = detect_spectrum_saturation([64_000.0, float("nan"), 64_000.0, 64_000.0])

        self.assertFalse(result.saturated)
        self.assertEqual(result.consecutive_pixels, 2)

    def test_midscale_flat_peak_is_not_reported_as_saturated(self) -> None:
        result = detect_spectrum_saturation([0.0, 20_000.0, 20_000.0, 20_000.0, 0.0])

        self.assertFalse(result.saturated)

    def test_saturation_uses_reported_device_full_scale(self) -> None:
        result = detect_spectrum_saturation(
            [0.0, 19_900.0, 19_900.0, 19_900.0, 0.0],
            full_scale_counts=20_000.0,
        )

        self.assertTrue(result.saturated)

    def test_spectrum_y_axis_expands_immediately_and_shrinks_after_sustained_drop(self) -> None:
        upper_limit, low_frame_count = stabilize_spectrum_y_axis_upper_limit(10_000, 1_000, 0)
        self.assertEqual((upper_limit, low_frame_count), (15_000.0, 0))

        upper_limit, low_frame_count = stabilize_spectrum_y_axis_upper_limit(
            16_000,
            upper_limit,
            low_frame_count,
        )
        self.assertEqual((upper_limit, low_frame_count), (20_000.0, 0))

        for _ in range(SPECTRUM_Y_AXIS_SHRINK_REQUIRED_FRAMES - 1):
            upper_limit, low_frame_count = stabilize_spectrum_y_axis_upper_limit(
                5_000,
                upper_limit,
                low_frame_count,
            )
            self.assertEqual(upper_limit, 20_000.0)

        upper_limit, low_frame_count = stabilize_spectrum_y_axis_upper_limit(
            5_000,
            upper_limit,
            low_frame_count,
        )
        self.assertEqual((upper_limit, low_frame_count), (6_000.0, 0))


if __name__ == "__main__":
    unittest.main()
