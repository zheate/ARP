from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from combined_test.data_library import DataLibrary
from combined_test.record_store import SessionRecordStore, SessionStatus
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings
from tauri_bridge.service import BACKGROUND_FILE_TASK_METHODS
from tests.test_data_browser import write_workbook
from tests.test_session_journal import _record


class DataLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.journals = self.root / "test-records" / "station-1"
        self.library = DataLibrary(self.journals.parent / "library.sqlite3")

    def store(self, status=SessionStatus.COMPLETED, sn="SN-001"):
        store = SessionRecordStore(self.journals)
        store.begin_session(self.root / "excel", sn, datetime(2026, 9, 17, 10),
                            test_station="封盖前", mode="automatic", settings={"testTemperatureC": "25", "stationId": "1"})
        store.queue(_record())
        store.complete_session(status, "test", shutdown_confirmed=True)
        return store

    def test_completed_session_is_saved_before_excel_and_survives_source_loss(self):
        store = self.store()
        self.assertFalse(store.workbook_path.exists())
        listing = self.library.list({})
        self.assertEqual(listing["total"], 1)
        record = listing["records"][0]
        self.assertEqual(record["temperature"], "25")
        self.assertEqual(record["storage"], "database")
        store.journal_path.unlink()
        detail = DataLibrary(self.library.path).inspect(record["id"])
        self.assertEqual(detail["points"][0]["efficiencyPercent"], 32)
        self.assertEqual(detail["points"][0]["pibPercent"], 95)
        self.assertNotIn("spectra", detail)
        spectral_detail = self.library.inspect(record["id"], include_spectra=True)
        self.assertEqual(spectral_detail["spectra"][0]["wavelengthNm"], [975, 976, 977])
        with sqlite3.connect(self.library.path) as db:
            raw, source = db.execute("SELECT detail, source FROM records").fetchone()
        self.assertEqual(json.loads(raw)["spectra"][0]["intensity"], [10, 20, 11])
        self.assertIn(b'point_committed', source)
        self.assertEqual(len(json.loads(raw)["attempts"]), 1)

    def test_stopped_sessions_never_enter_completed_library(self):
        self.store(SessionStatus.STOPPED_BY_OPERATOR)
        self.store(SessionStatus.ABORTED_SAFELY)
        self.store(SessionStatus.SHUTDOWN_UNCONFIRMED)
        self.library.recover(self.journals)
        self.assertEqual(self.library.list({})["total"], 0)

    def test_database_failure_does_not_change_completion_and_can_recover(self):
        with patch.object(DataLibrary, "ingest_journal", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertLogs("combined_test.session_journal", level="ERROR"):
                store = self.store()
        self.assertEqual(store.current_session.status, SessionStatus.COMPLETED)
        self.assertTrue(store.journal_path.exists())
        self.assertEqual(self.library.recover(self.journals), [])
        self.library.recover(self.journals)
        self.assertEqual(self.library.list({})["total"], 1)

    def test_import_filters_deduplicates_and_backs_up_entire_workbook(self):
        excel = self.root / "legacy"
        path = excel / "done.xlsx"
        write_workbook(path, extra_spectrum=True)
        write_workbook(excel / "stopped.xlsx", status="aborted_safely")
        write_workbook(excel / "unknown.xlsx", legacy=True)
        original = path.read_bytes()
        result = self.library.import_excel({"rootPath": str(excel)})
        self.assertEqual((result["imported"], result["skipped"]), (1, 2))
        self.assertEqual(self.library.import_excel({"rootPath": str(excel)})["imported"], 0)
        self.assertEqual(path.read_bytes(), original)
        path.unlink()
        record = self.library.list({})["records"][0]
        self.assertEqual(len(self.library.inspect(record["id"])["points"]), 2)
        with sqlite3.connect(self.library.path) as db:
            self.assertEqual(db.execute("SELECT source FROM records").fetchone()[0], original)

    def test_distinct_sessions_same_sn_and_literal_filters(self):
        self.store(sn="SN_A%")
        self.store(sn="SN_A%")
        self.store(sn="SN-OTHER")
        result = self.library.list({"sn": "_a%", "pageSize": 1, "page": 2})
        self.assertEqual((result["total"], result["pageCount"], result["page"]), (2, 2, 2))
        self.assertEqual(self.library.list({"station": "unknown"})["total"], 0)
        self.assertEqual(self.library.list({"status": "aborted_safely"})["total"], 0)
        for params in ({"page": True}, {"pageSize": 201}, {"fromMs": "no"}, {"fromMs": 2, "toMs": 1}):
            with self.assertRaises(ValueError):
                self.library.list(params)

    def test_background_dispatch_integration(self):
        self.store()
        backend = HeadlessBackend(JsonSettings(self.root / "settings.json"))
        self.addCleanup(backend.export_worker.graceful_stop)
        self.assertIn("dataBrowser.import", BACKGROUND_FILE_TASK_METHODS)
        listing = backend.dispatcher.dispatch_file_task("dataBrowser.list", {"source": "database"})
        self.assertEqual(listing["total"], 1)
        detail = backend.dispatcher.dispatch_file_task("dataBrowser.inspect", {"recordId": listing["records"][0]["id"]})
        self.assertEqual(detail["record"]["sn"], "SN-001")

    def test_inspect_projects_before_python_decode_and_preserves_stored_detail(self):
        self.store()
        record_id = self.library.list({})["records"][0]["id"]
        with sqlite3.connect(self.library.path) as db:
            original = db.execute("SELECT detail FROM records WHERE id=?", (record_id,)).fetchone()[0]
            detail = json.loads(original)
            detail["extension"] = {"中文": [None, True, 1.2345678901234567]}
            original = json.dumps(detail, ensure_ascii=False)
            db.execute("UPDATE records SET detail=? WHERE id=?", (original, record_id))
        decode = json.loads
        for include_spectra in (False, True):
            excluded = {"attempts", "session"} | (set() if include_spectra else {"spectra"})
            with patch("combined_test.data_library.json.loads", wraps=decode) as loads:
                result = self.library.inspect(record_id, include_spectra=include_spectra)
            self.assertEqual(result, {k: v for k, v in detail.items() if k not in excluded})
            # Assert the expensive discarded arrays never reach Python's decoder.
            decoded_input = decode(loads.call_args.args[0])
            if not include_spectra:
                self.assertTrue(excluded.isdisjoint(decoded_input))
        with sqlite3.connect(self.library.path) as db:
            self.assertEqual(db.execute("SELECT detail FROM records WHERE id=?", (record_id,)).fetchone()[0], original)

    def test_recovery_reuses_index_for_unchanged_journals_but_detects_completion(self):
        store = self.store(SessionStatus.STOPPED_BY_OPERATOR)
        self.library.recover(self.journals)
        with patch.object(self.library, "_connect", wraps=self.library._connect) as connect:
            self.assertEqual(self.library.recover(self.journals), [])
        self.assertEqual(connect.call_count, 1)
        events = [json.loads(line) for line in store.journal_path.read_text().splitlines()]
        completion = next(event for event in events if event["type"] == "session_completed")
        completion["session"]["status"] = "completed"
        with store.journal_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(completion) + "\n")
        self.assertEqual(self.library.recover(self.journals), [])
        self.assertEqual(self.library.list({})["total"], 1)

    def test_recovery_batches_unchanged_journals(self):
        for index in range(5):
            path = self.journals / str(index) / "events.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text("", encoding="utf-8")
        self.library.recover(self.journals)
        with patch.object(self.library, "_connect", wraps=self.library._connect) as connect:
            self.assertEqual(self.library.recover(self.journals), [])
        self.assertEqual(connect.call_count, 1)

    def test_inspect_without_sqlite_json_functions_falls_back_but_does_not_hide_errors(self):
        self.store()
        record_id = self.library.list({})["records"][0]["id"]
        expected = self.library.inspect(record_id)
        connect = self.library._connect

        @contextmanager
        def failing_projection(message):
            with connect() as db:
                proxy = Mock(wraps=db)

                def execute(sql, *args):
                    if "json_remove" in sql:
                        raise sqlite3.OperationalError(message)
                    return db.execute(sql, *args)

                proxy.execute.side_effect = execute
                yield proxy

        with patch.object(self.library, "_connect", lambda: failing_projection("no such function: json_remove")):
            self.assertEqual(self.library.inspect(record_id), expected)
        with patch.object(self.library, "_connect", lambda: failing_projection("database is locked")):
            with self.assertRaisesRegex(sqlite3.OperationalError, "database is locked"):
                self.library.inspect(record_id)

    def test_inspect_missing_optional_fields_and_missing_record(self):
        detail = {"record": {"id": "minimal", "sn": "SN", "station": "", "timeMs": 1,
                             "flowStatus": "completed", "validPointCount": 1},
                  "points": [{"currentA": 1, "powerW": 0}], "metadata": {"session": "keep nested key"}}
        self.library.save(detail)
        for include_spectra in (False, True):
            self.assertEqual(self.library.inspect("minimal", include_spectra=include_spectra), detail)
            with self.assertRaisesRegex(ValueError, "未找到"):
                self.library.inspect("missing", include_spectra=include_spectra)

    def test_recovery_retries_bad_journals_and_imports_new_ones(self):
        path = self.journals / "new" / "events.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("invalid json", encoding="utf-8")
        for _ in range(2):
            self.assertEqual(len(self.library.recover(self.journals)), 1)
        path.write_text("", encoding="utf-8")
        self.assertEqual(self.library.recover(self.journals), [])
        with sqlite3.connect(self.library.path) as db:
            self.assertIsNotNone(db.execute("SELECT version FROM journals WHERE path=?", (str(path),)).fetchone())
