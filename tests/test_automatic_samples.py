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

    def test_close_stops_the_writer_thread_and_raises_stored_write_errors(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "failing_samples.csv"
            original_writer = csv.writer
            row_count = 0

            class FailingWriter:
                def __init__(self, handle):
                    self._writer = original_writer(handle)

                def writerow(self, row):
                    nonlocal row_count
                    row_count += 1
                    if row_count > 1:
                        raise OSError("磁盘已满")
                    return self._writer.writerow(row)

            with patch("combined_test.automatic_samples.csv.writer", side_effect=FailingWriter):
                recorder = AutomaticSampleRecorder(path)
                writer_thread = recorder._writer_thread
                recorder.write(_sample(point_elapsed_s=1.0))
                with self.assertRaisesRegex(OSError, "磁盘已满"):
                    recorder.close()

            # The writer thread must be stopped even when rows failed, and
            # the recorder must no longer hold it open.
            self.assertIsNotNone(writer_thread)
            self.assertFalse(writer_thread.is_alive())
            self.assertIsNone(recorder._queue)
            self.assertIsNone(recorder._writer_thread)

    def test_close_never_blocks_when_append_open_fails_with_queued_rows(self) -> None:
        import threading as thread_module
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "unopenable_samples.csv"
            original_open = Path.open
            open_attempted = thread_module.Event()
            release_open = thread_module.Event()

            def guarded_open(self, mode: str = "r", *args, **kwargs):
                if "a" in mode:
                    open_attempted.set()
                    release_open.wait(timeout=5)
                    raise OSError("拒绝访问")
                return original_open(self, mode, *args, **kwargs)

            with patch.object(Path, "open", new=guarded_open):
                recorder = AutomaticSampleRecorder(path)
                writer_thread = recorder._writer_thread
                # Queue rows while the writer thread is stuck opening the
                # append stream, then let the open fail.
                self.assertTrue(open_attempted.wait(timeout=5))
                recorder.write(_sample(point_elapsed_s=1.0))
                recorder.write(_sample(point_elapsed_s=2.0))
                release_open.set()
                with self.assertRaisesRegex(OSError, "拒绝访问"):
                    recorder.close()

            # The dead writer accounted for the queued rows, so close()
            # returned instead of blocking in queue.join().
            self.assertIsNotNone(writer_thread)
            self.assertFalse(writer_thread.is_alive())

    def test_write_refuses_rows_after_the_writer_thread_died(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dead_writer_samples.csv"
            original_open = Path.open

            def failing_append_open(self, mode: str = "r", *args, **kwargs):
                if "a" in mode:
                    raise OSError("拒绝访问")
                return original_open(self, mode, *args, **kwargs)

            with patch.object(Path, "open", new=failing_append_open):
                recorder = AutomaticSampleRecorder(path)
                writer_thread = recorder._writer_thread
                writer_thread.join(timeout=5)
                self.assertFalse(writer_thread.is_alive())
                with self.assertRaisesRegex(RuntimeError, "写线程已退出"):
                    recorder.write(_sample(point_elapsed_s=1.0))

    def test_output_path_lives_next_to_automatic_workbook(self) -> None:
        self.assertEqual(
            automatic_sample_path(Path("result/2026_08_12_12_00.xlsx")),
            Path("result/2026_08_12_12_00_1s_samples.csv"),
        )

if __name__ == "__main__":
    unittest.main()
