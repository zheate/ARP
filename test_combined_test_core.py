import math
import unittest

from combined_test_core import (
    CombinedMeasurement,
    PowerStabilityDetector,
    build_set_current_command,
    decode_i2c_value,
    record_to_row,
    spectrum_curve_to_rows,
)


class PowerStabilityDetectorTests(unittest.TestCase):
    def test_requires_enough_time_before_reporting_stable(self) -> None:
        detector = PowerStabilityDetector(window_s=3.0, tolerance_w=0.05)

        self.assertFalse(detector.add_sample(0.0, 10.00).stable)
        self.assertFalse(detector.add_sample(1.0, 10.01).stable)
        result = detector.add_sample(2.0, 10.02)

        self.assertFalse(result.stable)
        self.assertEqual(result.sample_count, 3)
        self.assertLess(result.span_w, 0.05)

    def test_reports_stable_when_window_span_is_within_tolerance(self) -> None:
        detector = PowerStabilityDetector(window_s=3.0, tolerance_w=0.05)

        detector.add_sample(0.0, 10.00)
        detector.add_sample(1.0, 10.02)
        result = detector.add_sample(3.0, 10.03)

        self.assertTrue(result.stable)
        self.assertAlmostEqual(result.span_w, 0.03)

    def test_remains_waiting_before_the_exact_window_boundary(self) -> None:
        detector = PowerStabilityDetector(window_s=3.0, tolerance_w=0.05)

        detector.add_sample(0.0, 10.00)
        result = detector.add_sample(2.95, 10.02)

        self.assertFalse(result.stable)
        self.assertAlmostEqual(result.window_s, 2.95)
        result = detector.add_sample(3.0, 10.02)
        self.assertTrue(result.stable)

    def test_reports_stable_when_poll_jitter_would_otherwise_leave_coverage_at_2_99_seconds(self) -> None:
        detector = PowerStabilityDetector(window_s=3.0, tolerance_w=0.05)

        for elapsed_s in (0.0, 0.299, 0.598, 0.897, 1.196, 1.495, 1.794, 2.093, 2.392, 2.691, 2.990):
            result = detector.add_sample(elapsed_s, 10.00)

        self.assertFalse(result.stable)
        result = detector.add_sample(3.289, 10.01)

        self.assertTrue(result.stable)
        self.assertGreaterEqual(result.window_s, 3.0)
        self.assertLessEqual(result.span_w, 0.05)

    def test_reports_unstable_when_window_span_exceeds_tolerance(self) -> None:
        detector = PowerStabilityDetector(window_s=3.0, tolerance_w=0.05)

        detector.add_sample(0.0, 10.00)
        detector.add_sample(1.0, 10.02)
        result = detector.add_sample(3.0, 10.08)

        self.assertFalse(result.stable)
        self.assertAlmostEqual(result.span_w, 0.08)


class I2CHelperTests(unittest.TestCase):
    def test_builds_set_current_command(self) -> None:
        self.assertEqual(build_set_current_command(12), [0xB4, 0xFF, 0x0C, 0x00])

    def test_builds_set_current_command_with_decimal_current(self) -> None:
        self.assertEqual(build_set_current_command(3.5), [0xB4, 0xFF, 0x03, 0x32])

    def test_rejects_current_out_of_range(self) -> None:
        with self.assertRaises(ValueError):
            build_set_current_command(21)

    def test_decodes_i2c_integer_and_decimal_bytes(self) -> None:
        decoded = decode_i2c_value([0xB4, 0x8B, 12, 34])

        self.assertAlmostEqual(decoded, 12.34)


class RecordFormattingTests(unittest.TestCase):
    def test_record_to_row_formats_fixed_columns(self) -> None:
        measurement = CombinedMeasurement(
            elapsed_s=4.2,
            set_current_a=10,
            output_current_a=9.98,
            output_voltage_v=24.56,
            power_w=12.345,
            peak_wavelength_nm=976.123,
            centroid_nm=976.456,
            fwhm_nm=1.23,
            stable_span_w=0.02,
            stable_window_s=3.0,
            spectrum_csv_path="records_spectra/stable_20260708_120000.csv",
        )

        row = record_to_row("2026-07-08 12:00:00", measurement)

        self.assertEqual(
            row,
            [
                "2026-07-08 12:00:00",
                "4.200",
                "10",
                "9.980",
                "24.560",
                "12.345",
                "976.123",
                "976.456",
                "1.230",
                "0.020",
                "3.000",
                "records_spectra/stable_20260708_120000.csv",
            ],
        )

    def test_record_to_row_keeps_nan_blank(self) -> None:
        measurement = CombinedMeasurement(
            elapsed_s=1.0,
            set_current_a=2,
            output_current_a=math.nan,
            output_voltage_v=math.nan,
            power_w=3.0,
            peak_wavelength_nm=math.nan,
            centroid_nm=math.nan,
            fwhm_nm=math.nan,
            stable_span_w=0.0,
            stable_window_s=2.0,
            spectrum_csv_path="",
        )

        row = record_to_row("t", measurement)

        self.assertEqual(row[3], "")
        self.assertEqual(row[4], "")
        self.assertEqual(row[6], "")
        self.assertEqual(row[7], "")
        self.assertEqual(row[8], "")

    def test_record_to_row_keeps_decimal_set_current(self) -> None:
        measurement = CombinedMeasurement(
            elapsed_s=1.0,
            set_current_a=3.5,
            output_current_a=3.5,
            output_voltage_v=10.0,
            power_w=20.0,
            peak_wavelength_nm=976.0,
            centroid_nm=976.0,
            fwhm_nm=1.0,
            stable_span_w=0.01,
            stable_window_s=3.0,
        )

        self.assertEqual(record_to_row("t", measurement)[2], "3.5")

    def test_spectrum_curve_to_rows_exports_all_points(self) -> None:
        rows = spectrum_curve_to_rows([975.1, 975.2, 975.3], [100, 250.5, 120])

        self.assertEqual(
            rows,
            [
                ["wavelength_nm", "intensity"],
                ["975.100000", "100.000000"],
                ["975.200000", "250.500000"],
                ["975.300000", "120.000000"],
            ],
        )

    def test_spectrum_curve_to_rows_rejects_mismatched_lengths(self) -> None:
        with self.assertRaises(ValueError):
            spectrum_curve_to_rows([975.1, 975.2], [100])


if __name__ == "__main__":
    unittest.main()
