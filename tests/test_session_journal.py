from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from combined_test.excel_export import ExcelTestRecord
from combined_test.record_store import SessionRecordStore, SessionStatus
from combined_test.session_journal import SessionJournalError


def _record() -> ExcelTestRecord:
    return ExcelTestRecord(
        current_a=2.0,
        voltage_v=8.1,
        power_w=5.2,
        efficiency=0.32,
        peak_wavelength_nm=976.1,
        centroid_nm=976.2,
        fwhm_nm=0.4,
        pib=0.95,
        wavelength=[975.0, 976.0, 977.0],
        intensity=[10.0, 20.0, 11.0],
        test_station="工位 1",
    )


class SessionJournalTests(unittest.TestCase):
    def test_committed_point_is_fsynced_to_long_lived_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = SessionRecordStore(root / "journals")
            store.begin_session(
                root / "excel",
                "SN-JSONL",
                datetime(2026, 8, 23, 9, 30),
                test_station="工位 1",
                mode="automatic",
            )

            store.queue(_record(), actual_current_a=2.01, integration_time_us=20_000)
            store.complete_session(
                SessionStatus.COMPLETED,
                "完成",
                shutdown_confirmed=True,
            )

            self.assertIsNotNone(store.journal_path)
            events = [
                json.loads(line)
                for line in store.journal_path.read_text(encoding="utf-8").splitlines()  # type: ignore[union-attr]
            ]
            self.assertEqual([item["type"] for item in events], ["session_start", "point_committed", "session_completed"])
            self.assertEqual(events[1]["record"]["wavelength"], [975.0, 976.0, 977.0])
            self.assertEqual(events[1]["record"]["intensity"], [10.0, 20.0, 11.0])
            self.assertEqual(store.recorded_currents, {2.0})
            self.assertEqual(store.exported_currents, set())

    def test_failed_journal_append_does_not_accept_point(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = SessionRecordStore(root / "journals")
            store.begin_session(root / "excel", "SN-FAIL", datetime.now(), test_station="工位 1")

            with patch.object(store._journal, "append", side_effect=SessionJournalError("disk full")):  # type: ignore[arg-type]
                with self.assertRaisesRegex(SessionJournalError, "disk full"):
                    store.queue(_record())

            self.assertEqual(store.snapshot(), ())
            self.assertEqual(store.recorded_currents, set())

    def test_excel_materialization_is_recorded_without_deleting_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = SessionRecordStore(root / "journals")
            store.begin_session(root / "excel", "SN-EXCEL", datetime.now(), test_station="工位 1")
            record = _record()
            store.queue(record)
            journal_path = store.journal_path

            store.mark_saved((record,))

            self.assertTrue(journal_path is not None and journal_path.exists())
            self.assertEqual(store.exported_currents, {2.0})
            last_event = json.loads(journal_path.read_text(encoding="utf-8").splitlines()[-1])  # type: ignore[union-attr]
            self.assertEqual(last_event, {"type": "excel_materialized", "currentsA": [2.0]})


if __name__ == "__main__":
    unittest.main()
