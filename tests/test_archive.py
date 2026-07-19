from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from combined_test.test_archive import (
    AttemptValidity,
    PowerTraceWriter,
    SessionFilters,
    SessionStatus,
    TestArchive,
)


class TestArchiveTests(unittest.TestCase):
    def test_sessions_created_in_same_minute_never_share_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = TestArchive(Path(temp_dir))
            started = datetime(2026, 7, 16, 9, 30, 10)
            first = archive.begin_session(sn="SN-1", station="站 1", mode="automatic", started_at=started)
            second = archive.begin_session(sn="SN-1", station="站 1", mode="automatic", started_at=started)

            self.assertNotEqual(first.session_id, second.session_id)
            self.assertNotEqual(first.session_dir, second.session_dir)
            self.assertTrue(first.session_dir.is_dir())
            self.assertTrue(second.session_dir.is_dir())

    def test_retest_preserves_attempts_and_selects_latest_valid_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = TestArchive(Path(temp_dir))
            session = archive.begin_session(sn="SN-2", station="站 1", mode="automatic")
            archive.record_attempt(
                session.session_id,
                sequence_index=0,
                target_current_a=1.0,
                validity=AttemptValidity.WEAK_SIGNAL,
                invalid_reason="信号过弱",
                wavelength=[975.0, 976.0],
                intensity=[10.0, 20.0],
            )
            selected = archive.record_attempt(
                session.session_id,
                sequence_index=0,
                target_current_a=1.0,
                validity=AttemptValidity.VALID,
                selected=True,
                power_w=2.5,
                wavelength=[975.0, 976.0],
                intensity=[1000.0, 2000.0],
            )

            attempts = archive.list_attempts(session.session_id)
            self.assertEqual([attempt.attempt_no for attempt in attempts], [1, 2])
            self.assertEqual([attempt.validity for attempt in attempts], [
                AttemptValidity.WEAK_SIGNAL,
                AttemptValidity.VALID,
            ])
            self.assertEqual(archive.list_attempts(session.session_id, selected_only=True), (selected,))
            self.assertTrue((session.session_dir / selected.spectrum_path).is_file())

    def test_reopen_filters_and_recovers_only_automatic_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = TestArchive(root)
            automatic = archive.begin_session(
                sn="SN-3", station="站 2", mode="automatic", product_model="M1", batch="B1"
            )
            manual = archive.begin_session(sn="SN-4", station="站 2", mode="manual")

            reopened = TestArchive(root)
            interrupted = reopened.mark_running_sessions_incomplete()
            self.assertEqual({session.session_id for session in interrupted}, {
                automatic.session_id,
                manual.session_id,
            })
            resumed = reopened.resume_session(automatic.session_id)
            self.assertEqual(resumed.status, SessionStatus.RUNNING)
            with self.assertRaisesRegex(ValueError, "手动"):
                reopened.resume_session(manual.session_id)
            filtered = reopened.list_sessions(SessionFilters(product_model="M1", batch="B1"))
            self.assertEqual([session.session_id for session in filtered], [automatic.session_id])

    def test_power_trace_writer_appends_and_flushes_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "power_trace.csv"
            writer = PowerTraceWriter(path)
            writer.start()
            writer.append(
                elapsed_s=1.25,
                state="waiting_stable",
                target_current_a=2.0,
                actual_current_a=1.99,
                power_w=3.5,
                stable=False,
                stable_span_w=0.02,
                stable_tolerance_w=0.05,
            )
            writer.stop()

            with path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["state"], "waiting_stable")
            self.assertEqual(rows[0]["target_current_a"], "2.000000")


if __name__ == "__main__":
    unittest.main()
