from __future__ import annotations

import csv
import io
import unittest
from pathlib import Path

from combined_test.automatic_samples import (
    AutomaticSampleRecorder,
    AutomaticSecondSample,
    automatic_sample_path,
)


class AutomaticSampleRecorderTests(unittest.TestCase):
    def test_writes_complete_one_second_sample_and_efficiency(self) -> None:
        buffer = io.StringIO()
        recorder = AutomaticSampleRecorder(Path("unused.csv"), file=buffer)
        recorder.write(
            AutomaticSecondSample(
                point_index=1,
                point_elapsed_s=1.0,
                target_current_a=3.0,
                output_current_a=3.0,
                output_voltage_raw_v=23.0,
                output_voltage_v=23.0,
                optical_power_w=30.0,
                peak_wavelength_nm=976.0,
                centroid_nm=976.1,
                fwhm_nm=2.0,
                spectrum_wavelengths_nm=(975.0, 976.0, 977.0),
                spectrum_intensities=(1.0, 5.0, 1.0),
            ),
            timestamp="2026-08-12T12:00:00+08:00",
        )

        rows = list(csv.DictReader(io.StringIO(buffer.getvalue())))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["output_current_a"], "3")
        self.assertEqual(rows[0]["output_voltage_v"], "23")
        self.assertEqual(rows[0]["optical_power_w"], "30")
        self.assertAlmostEqual(float(rows[0]["efficiency_percent"]), 30.0 / 69.0 * 100.0)
        self.assertEqual(rows[0]["spectrum_point_count"], "3")
        self.assertEqual(rows[0]["spectrum_wavelengths_nm"], "975;976;977")
        self.assertEqual(rows[0]["spectrum_intensities"], "1;5;1")

    def test_output_path_lives_next_to_automatic_workbook(self) -> None:
        self.assertEqual(
            automatic_sample_path(Path("result/2026_08_12_12_00.xlsx")),
            Path("result/2026_08_12_12_00_1s_samples.csv"),
        )

if __name__ == "__main__":
    unittest.main()
