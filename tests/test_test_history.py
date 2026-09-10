import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from combined_test import test_history as test_history_module
from combined_test.excel_export import ExcelTestRecord, save_test_records
from combined_test.record_store import SessionStatus
from combined_test.test_history import clear_test_history_cache, find_test_history
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings


class TestHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_test_history_cache()

    @staticmethod
    def _record(current_a: float, station: str) -> ExcelTestRecord:
        return ExcelTestRecord(
            current_a=current_a,
            voltage_v=50.0,
            power_w=current_a * 10.0,
            efficiency=0.5,
            peak_wavelength_nm=976.0,
            centroid_nm=976.0,
            fwhm_nm=0.4,
            pib=0.95,
            smsr_db=30.0,
            wavelength=[975.0, 976.0, 977.0],
            intensity=[10.0, 100.0, 12.0],
            test_station=station,
        )

    @staticmethod
    def _session(sn: str, station: str, started_at_utc: str, status: SessionStatus) -> SimpleNamespace:
        return SimpleNamespace(
            session_id=started_at_utc,
            sn=sn,
            product_model="",
            batch="",
            station=station,
            mode="automatic",
            started_at_utc=started_at_utc,
            ended_at_utc=started_at_utc,
            status=status,
            termination_reason="",
            shutdown_confirmed=True,
            software_version="1.0.0",
            calculation_version="2026-08",
            settings={},
            devices=(),
        )

    def _write(self, path: Path, sn: str, station: str, started_at_utc: str, status: SessionStatus) -> None:
        save_test_records(
            path,
            [self._record(2.0, station), self._record(4.0, station)],
            session=self._session(sn, station, started_at_utc, status),
        )

    def test_returns_completed_same_sn_records_newest_first_across_all_stations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sn = "SN-001"
            self._write(root / sn / "站别 A" / "older.xlsx", sn, "站别 A", "2026-08-01T01:00:00+00:00", SessionStatus.COMPLETED)
            self._write(root / sn / "站别 A" / "same-station.xlsx", sn, "站别 A", "2026-08-02T01:00:00+00:00", SessionStatus.COMPLETED)
            self._write(root / sn / "站别 B" / "latest.xlsx", sn, "站别 B", "2026-08-03T01:00:00+00:00", SessionStatus.COMPLETED)
            self._write(root / sn / "站别 C" / "aborted.xlsx", sn, "站别 C", "2026-08-04T01:00:00+00:00", SessionStatus.ABORTED_SAFELY)
            self._write(root / sn / "站别 D" / "wrong-sn.xlsx", "SN-OTHER", "站别 D", "2026-08-05T01:00:00+00:00", SessionStatus.COMPLETED)

            records = find_test_history(root, sn)

            self.assertEqual([record.station for record in records], ["站别 B", "站别 A", "站别 A"])
            self.assertEqual(records[0].reference_payload()["points"][0], {
                "currentA": 2.0,
                "powerW": 20.0,
            })
            self.assertFalse(hasattr(records[0].inspection.points[0], "wavelength"))

            backend = HeadlessBackend(JsonSettings(root / "settings.json"))
            backend.configuration.update({"outputDir": str(root), "sn": sn})
            backend.runtime._select_latest_test_reference({})
            self.assertEqual(backend._test_reference.station, "站别 B")
            self.assertEqual(
                backend.snapshot({"view": "automatic"})["automaticTest"]["reference"]["station"],
                "站别 B",
            )
            backend.runtime._select_latest_test_reference({"referencePath": str(records[-1].inspection.source_path)})
            self.assertEqual(backend._test_reference.station, "站别 B")

    def test_unchanged_history_workbooks_are_not_reopened(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sn = "SN-CACHE"
            self._write(
                root / sn / "station" / "first.xlsx",
                sn,
                "station",
                "2026-08-01T01:00:00+00:00",
                SessionStatus.COMPLETED,
            )
            self._write(
                root / sn / "station" / "second.xlsx",
                sn,
                "station",
                "2026-08-02T01:00:00+00:00",
                SessionStatus.COMPLETED,
            )

            with patch.object(
                test_history_module,
                "load_workbook",
                wraps=test_history_module.load_workbook,
            ) as load:
                first = find_test_history(root, sn)
                second = find_test_history(root, sn)

            self.assertEqual(load.call_count, 2)
            self.assertEqual(first, second)

    def test_history_index_is_reloaded_after_mtime_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sn = "SN-MTIME"
            source = root / sn / "station" / "result.xlsx"
            self._write(
                source,
                sn,
                "station",
                "2026-08-01T01:00:00+00:00",
                SessionStatus.COMPLETED,
            )

            with patch.object(
                test_history_module,
                "load_workbook",
                wraps=test_history_module.load_workbook,
            ) as load:
                find_test_history(root, sn)
                stat = source.stat()
                os.utime(
                    source,
                    ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000),
                )
                find_test_history(root, sn)

            self.assertEqual(load.call_count, 2)

    def test_history_index_cache_evicts_least_recently_used_workbooks(self) -> None:
        limit = test_history_module.HISTORY_INDEX_CACHE_MAX_ENTRIES
        paths = [Path(f"C:/history/result-{index}.xlsx") for index in range(limit + 1)]

        with patch.object(test_history_module, "_load_history_index", return_value=None):
            for source in paths:
                test_history_module._cached_history_index(source, 1)

        self.assertEqual(len(test_history_module._HISTORY_INDEX_CACHE), limit)
        self.assertNotIn((str(paths[0]), 1), test_history_module._HISTORY_INDEX_CACHE)
        self.assertIn((str(paths[-1]), 1), test_history_module._HISTORY_INDEX_CACHE)

    def test_explicit_history_params_are_forwarded_by_the_headless_backend(self) -> None:
        backend = HeadlessBackend(JsonSettings(Path("unused-settings.json")))
        with patch("combined_test.test_history.find_test_history", return_value=()) as find:
            self.assertEqual(
                backend._history_records({"outputDir": "C:/results", "sn": "SN-001"}),
                (),
            )
        find.assert_called_once_with("C:/results", "SN-001")


if __name__ == "__main__":
    unittest.main()
