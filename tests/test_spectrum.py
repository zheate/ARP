import unittest

from combined_test.spectrum import (
    _centered_window_minimums,
    detect_spectrum_saturation,
    find_spectrum_peak_annotations,
)


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

    def test_peak_annotations_keep_height_order_and_centroids(self) -> None:
        points = list(
            zip(
                [850.0, 851.0, 852.0, 853.0, 854.0, 855.0, 856.0, 857.0, 858.0, 859.0, 860.0, 861.0, 862.0],
                [0.0, 5.0, 80.0, 5.0, 0.0, 10.0, 300.0, 10.0, 0.0, 20.0, 200.0, 20.0, 0.0],
            )
        )

        annotations = find_spectrum_peak_annotations(points)

        self.assertEqual(
            [(item.label, round(item.centroid_nm, 3)) for item in annotations],
            [("P1", 856.0), ("P2", 860.0), ("P3", 852.0)],
        )

    def test_saturation_requires_consecutive_near_peak_pixels(self) -> None:
        saturated = detect_spectrum_saturation([0.0, 16000.0, 16020.0, 16010.0, 0.0])
        spike = detect_spectrum_saturation([0.0, 17000.0, 0.0])

        self.assertTrue(saturated.saturated)
        self.assertEqual(saturated.consecutive_pixels, 3)
        self.assertFalse(spike.saturated)


if __name__ == "__main__":
    unittest.main()
