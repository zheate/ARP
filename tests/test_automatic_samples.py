from __future__ import annotations

import csv
import io
import tempfile
import unittest
from pathlib import Path

from combined_test.automatic_samples import (
    AutomaticSampleRecorder,
    AutomaticSecondSample,
    automatic_sample_path,
)


def _sample(point_elapsed_s: float) -> AutomaticSecondSample:
    return AutomaticSecondSample(
        point_index=1,
        point_elapsed_s=point_elapsed_s,
        target_current_a=3.0,
        output_current_a=3.0,
        output_voltage_raw_v=20.0,
        output_voltage_v=20.0,
        optical_power_w=6.0,
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

    def test_path_mode_flushes_queued_rows_in_submission_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "async_samples.csv"
            recorder = AutomaticSampleRecorder(path)
            recorder.write(_sample(point_elapsed_s=1.0))
            recorder.write(_sample(point_elapsed_s=2.0))
            recorder.flush_now()

            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(
                [row["point_elapsed_s"] for row in rows],
                ["1.000000", "2.000000"],
            )
            self.assertEqual([row["sample_index"] for row in rows], ["0", "1"])

    def test_close_drains_pending_rows_before_returning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "closed_samples.csv"
            recorder = AutomaticSampleRecorder(path)
            recorder.write(_sample(point_elapsed_s=1.0))
            recorder.write(_sample(point_elapsed_s=2.0))
            recorder.close()

            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)

    def test_output_path_lives_next_to_automatic_workbook(self) -> None:
        self.assertEqual(
            automatic_sample_path(Path("result/2026_08_12_12_00.xlsx")),
            Path("result/2026_08_12_12_00_1s_samples.csv"),
        )

if __name__ == "__main__":
    unittest.main()
