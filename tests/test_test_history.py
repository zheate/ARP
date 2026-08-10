import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from combined_test.excel_export import ExcelTestRecord, save_test_records
from combined_test.record_store import SessionStatus
from combined_test.test_history import find_test_history
from tauri_bridge.legacy_backend import LegacyWindowBackend


class TestHistoryTests(unittest.TestCase):
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

            backend = object.__new__(LegacyWindowBackend)
            backend.window = SimpleNamespace(
                output_dir_field=SimpleNamespace(text=lambda: str(root)),
                sn_field=SimpleNamespace(text=lambda: sn),
            )
            backend._select_test_reference({"referencePath": "latest"})
            self.assertEqual(backend._test_reference.station, "站别 B")
            backend._select_test_reference({"referencePath": str(records[-1].inspection.source_path)})
            self.assertEqual(backend._test_reference.station, "站别 A")
            with self.assertRaisesRegex(ValueError, "不属于当前壳体"):
                backend._select_test_reference({"referencePath": str(root / "other.xlsx")})


if __name__ == "__main__":
    unittest.main()
