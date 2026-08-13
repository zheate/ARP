import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openpyxl import Workbook, load_workbook
from xlsxwriter import Workbook as XlsxWriterWorkbook

from combined_test.automatic_samples import AutomaticSampleRecorder, AutomaticSecondSample
from combined_test.excel_export import (
    ExcelTestRecord,
    _sample_series,
    append_test_record,
    build_test_workbook_path,
    save_test_records,
)


class ExcelExportTests(unittest.TestCase):
    def _record(self, current_a: float, test_station: str = "") -> ExcelTestRecord:
        return ExcelTestRecord(
            current_a=current_a,
            voltage_v=50.5,
            power_w=33.0,
            efficiency=33.0 / current_a / 50.5,
            peak_wavelength_nm=976.1,
            centroid_nm=976.0,
            fwhm_nm=1.2,
            pib=0.995,
            smsr_db=32.5,
            wavelength=[974.0, 976.0, 978.0],
            intensity=[10.0, 100.0, 20.0],
            test_station=test_station,
        )

    def test_path_uses_sn_station_and_minute_precision_test_time(self) -> None:
        path = build_test_workbook_path(
            Path("records"),
            "SN:001",
            datetime(2026, 7, 10, 14, 30, 25, 123456),
            "老化站:1",
        )

        self.assertEqual(path, Path("records/SN_001/老化站_1/2026_07_10_14_30.xlsx"))

    def test_filename_ignores_seconds_and_subsecond_time(self) -> None:
        first = build_test_workbook_path(
            Path("records"), "SN001", datetime(2026, 7, 10, 14, 30, 25, 1), "老化站 1"
        )
        second = build_test_workbook_path(
            Path("records"), "SN001", datetime(2026, 7, 10, 14, 30, 59, 999999), "老化站 1"
        )

        self.assertEqual(first, second)

    def test_appends_liv_and_full_spectra_to_same_reference_layout_workbook(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "SN001_2026_07_10_14_30_25.xlsx"
            append_test_record(path, self._record(4.0))
            append_test_record(path, self._record(3.0))

            workbook = load_workbook(path, data_only=False)
            sheet = workbook[workbook.sheetnames[0]]
            self.assertEqual(sheet["A1"].value, "LIV")
            self.assertEqual(sheet["J1"].value, "光谱")
            self.assertEqual([sheet.cell(2, column).value for column in range(1, 10)], [
                "电流(A)", "电压(V)", "功率(W)", "电光效率",
                "中心波长(nm)", "质心波长(nm)", "FWHM(nm)", "PIB", "SMSR(dB)",
            ])
            self.assertEqual(sheet["A3"].value, 3.0)
            self.assertEqual(sheet["A4"].value, 4.0)
            self.assertEqual(sheet["J2"].value, "3.0A")
            self.assertEqual(sheet["L2"].value, "4.0A")
            self.assertEqual([sheet.cell(row, 10).value for row in range(3, 6)], [974.0, 976.0, 978.0])
            self.assertEqual([sheet.cell(row, 11).value for row in range(3, 6)], [10.0, 100.0, 20.0])
            self.assertEqual(sheet["D3"].number_format, "0.00%")
            self.assertEqual(sheet["H3"].number_format, "0.00%")
            self.assertEqual(sheet["I3"].value, 32.5)
            self.assertEqual(sheet["I3"].number_format, "0.00")
            workbook.close()

    def test_append_opens_existing_workbook_with_openpyxl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "existing.xlsx"
            save_test_records(path, [self._record(2.0)])

            with patch(
                "combined_test.excel_export.load_workbook",
                wraps=load_workbook,
            ) as loader:
                append_test_record(path, self._record(4.0))

            loader.assert_called_once_with(path)
            workbook = load_workbook(path, read_only=True)
            self.assertEqual(workbook["测试数据"]["A4"].value, 4.0)
            workbook.close()

    def test_batch_save_writes_all_records_once_in_current_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "batch.xlsx"

            save_test_records(path, [self._record(8.0), self._record(2.0), self._record(4.0)])

            workbook = load_workbook(path, data_only=False)
            sheet = workbook[workbook.sheetnames[0]]
            self.assertEqual([sheet.cell(row, 1).value for row in range(3, 6)], [2.0, 4.0, 8.0])
            self.assertEqual([sheet.cell(2, column).value for column in (10, 12, 14)], ["2.0A", "4.0A", "8.0A"])
            workbook.close()

    def test_batch_save_uses_xlsxwriter_constant_memory_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "batch.xlsx"

            with patch(
                "combined_test.excel_export.XlsxWriterWorkbook",
                side_effect=XlsxWriterWorkbook,
            ) as writer:
                save_test_records(path, [self._record(2.0)])

            writer.assert_called_once()
            self.assertEqual(
                writer.call_args.args[1],
                {"constant_memory": True, "strings_to_urls": False},
            )

    def test_batch_save_retries_a_transient_windows_replace_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "batch.xlsx"
            save_test_records(path, [self._record(2.0)])
            real_replace = __import__("os").replace
            replace_calls = 0

            def replace_after_one_lock(source: Path, target: Path) -> None:
                nonlocal replace_calls
                replace_calls += 1
                if replace_calls == 1:
                    raise PermissionError("workbook is temporarily locked")
                real_replace(source, target)

            with (
                patch("combined_test.excel_export.os.replace", side_effect=replace_after_one_lock),
                patch("combined_test.excel_export.time.sleep") as sleep,
            ):
                save_test_records(path, [self._record(2.0), self._record(4.0)])

            self.assertEqual(replace_calls, 2)
            sleep.assert_called_once_with(0.05)
            self.assertTrue(path.is_file())
            self.assertFalse((path.parent / ".batch.tmp.xlsx").exists())
            workbook = load_workbook(path, read_only=True)
            self.assertEqual(workbook["测试数据"]["A4"].value, 4.0)
            workbook.close()

    def test_batch_save_preserves_complete_temporary_file_after_persistent_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "batch.xlsx"
            save_test_records(path, [self._record(2.0)])
            temporary = path.parent / ".batch.tmp.xlsx"

            with (
                patch(
                    "combined_test.excel_export.os.replace",
                    side_effect=PermissionError("workbook remains locked"),
                ),
                patch("combined_test.excel_export.time.sleep"),
            ):
                with self.assertRaisesRegex(RuntimeError, "完整临时文件已保留"):
                    save_test_records(path, [self._record(2.0), self._record(4.0)])

            self.assertTrue(temporary.is_file())
            workbook = load_workbook(temporary, read_only=True)
            self.assertEqual(workbook["测试数据"]["A4"].value, 4.0)
            workbook.close()

    def test_batch_save_writes_test_station_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "station.xlsx"

            save_test_records(path, [self._record(2.0, "老化站 1"), self._record(4.0, "老化站 1")])

            workbook = load_workbook(path, data_only=False)
            sheet = workbook[workbook.sheetnames[0]]
            self.assertEqual(sheet["B1"].value, "测试站别")
            self.assertEqual(sheet["C1"].value, "老化站 1")
            workbook.close()

    def test_batch_save_writes_hidden_measurement_attempts_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "attempts.xlsx"
            attempt = SimpleNamespace(
                sequence_index=4,
                target_current_a=3.0,
                attempt_no=2,
                created_at_utc="2026-08-12T00:00:00Z",
                validity=SimpleNamespace(value="valid"),
                invalid_reason="",
                selected=True,
                voltage_v=20.0,
                power_w=6.0,
                efficiency=0.1,
                centroid_nm=975.0,
                fwhm_nm=0.281,
                pib=0.995,
                smsr_db=32.5,
                stable_span_w=0.01,
                stable_window_s=3.0,
                integration_time_us=3_800,
                spectrum_path="spectra/point.csv",
            )

            save_test_records(path, [self._record(3.0)], attempts=[attempt])

            workbook = load_workbook(path, data_only=False)
            sheet = workbook["测量尝试"]
            self.assertEqual(sheet.sheet_state, "hidden")
            self.assertEqual(
                [sheet.cell(2, column).value for column in (1, 2, 3, 7, 17, 18)],
                [5, 3.0, 2, "是", 3_800, "spectra/point.csv"],
            )
            workbook.close()

    def test_batch_save_rejects_mixed_test_stations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "station.xlsx"

            with self.assertRaisesRegex(ValueError, "测试站别必须一致"):
                save_test_records(path, [self._record(2.0, "站别 A"), self._record(4.0, "站别 B")])

    def test_batch_save_accepts_liv_record_without_spectrum(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "without_spectrum.xlsx"
            record = ExcelTestRecord(
                current_a=3.0,
                voltage_v=50.0,
                power_w=75.0,
                efficiency=0.5,
                peak_wavelength_nm=float("nan"),
                centroid_nm=float("nan"),
                fwhm_nm=float("nan"),
                pib=float("nan"),
                smsr_db=float("nan"),
                wavelength=[],
                intensity=[],
            )

            save_test_records(path, [record])

            workbook = load_workbook(path, data_only=False)
            sheet = workbook[workbook.sheetnames[0]]
            self.assertEqual([sheet.cell(3, column).value for column in range(1, 5)], [3.0, 50.0, 75.0, 0.5])
            self.assertEqual([sheet.cell(3, column).value for column in range(5, 10)], [None] * 5)
            self.assertIsNone(sheet["J2"].value)
            workbook.close()

    def test_final_workbook_has_two_tables_for_every_designated_hold_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            path = directory / "three_tables.xlsx"
            sample_path = directory / "three_tables_1s_samples.csv"
            recorder = AutomaticSampleRecorder(sample_path)
            recorder.write(
                AutomaticSecondSample(
                    point_index=4,
                    point_elapsed_s=1.02,
                    target_current_a=3.0,
                    output_current_a=3.0,
                    output_voltage_raw_v=20.0,
                    output_voltage_v=20.0,
                    optical_power_w=6.0,
                    peak_wavelength_nm=975.1,
                    centroid_nm=975.0,
                    fwhm_nm=0.281,
                    spectrum_wavelengths_nm=(974.0, 975.0, 976.0),
                    spectrum_intensities=(10.0, 100.0, 20.0),
                )
            )
            recorder.write(
                AutomaticSecondSample(
                    point_index=4,
                    point_elapsed_s=2.08,
                    target_current_a=3.0,
                    output_current_a=3.0,
                    output_voltage_raw_v=20.5,
                    output_voltage_v=20.5,
                    optical_power_w=6.15,
                    peak_wavelength_nm=975.2,
                    centroid_nm=975.05,
                    fwhm_nm=0.282,
                    spectrum_wavelengths_nm=(974.0, 975.0, 976.0),
                    spectrum_intensities=(11.0, 101.0, 21.0),
                )
            )
            recorder.write(
                AutomaticSecondSample(
                    point_index=7,
                    point_elapsed_s=1.0,
                    target_current_a=5.0,
                    output_current_a=5.0,
                    output_voltage_raw_v=21.0,
                    output_voltage_v=21.0,
                    optical_power_w=10.0,
                    peak_wavelength_nm=976.1,
                    centroid_nm=976.0,
                    fwhm_nm=0.351,
                    spectrum_wavelengths_nm=(975.0, 976.0, 977.0),
                    spectrum_intensities=(20.0, 200.0, 30.0),
                )
            )
            recorder.close()

            class Session:
                session_id = "session"
                sn = "SN001"
                product_model = ""
                batch = ""
                station = "熔接前"
                mode = "automatic"
                started_at_utc = "2026-08-12T00:00:00Z"
                ended_at_utc = "2026-08-12T00:05:00Z"
                status = "completed"
                termination_reason = ""
                shutdown_confirmed = True
                software_version = "1.0.0"
                calculation_version = "2026-08"
                settings = {}
                devices = ()

            save_test_records(
                path,
                [self._record(3.0), self._record(5.0)],
                session=Session(),
                automatic_samples_path=sample_path,
            )

            workbook = load_workbook(path, data_only=False)
            visible_sheets = [
                sheet.title for sheet in workbook.worksheets if sheet.sheet_state == "visible"
            ]
            self.assertEqual(
                visible_sheets,
                [
                    "测试数据",
                    "3A逐秒数据",
                    "3A光谱数据",
                    "5A逐秒数据",
                    "5A光谱数据",
                ],
            )
            self.assertEqual(workbook["会话信息"].sheet_state, "hidden")

            summary = workbook["3A逐秒数据"]
            self.assertEqual(
                [summary.cell(row=1, column=column).value for column in range(1, 8)],
                [
                    "时间(s)", "功率(W)", "效率", "电压(V)",
                    "中心波长(nm)", "质心波长(nm)", "FWHM(nm)",
                ],
            )
            self.assertEqual(
                [summary.cell(row=2, column=column).value for column in range(1, 8)],
                [1, 6.0, 0.1, 20.0, 975.1, 975.0, 0.281],
            )
            self.assertEqual(summary["A3"].value, 2)
            self.assertEqual(summary["C2"].number_format, "0.00%")

            spectrum = workbook["3A光谱数据"]
            self.assertEqual(
                [spectrum.cell(row=1, column=column).value for column in range(1, 5)],
                ["1s波长(nm)", "1s强度(counts)", "2s波长(nm)", "2s强度(counts)"],
            )
            self.assertEqual([spectrum.cell(row=row, column=1).value for row in range(2, 5)], [974.0, 975.0, 976.0])
            self.assertEqual(spectrum["A2"].number_format, "0.000")
            self.assertEqual([spectrum.cell(row=row, column=2).value for row in range(2, 5)], [10.0, 100.0, 20.0])
            self.assertEqual([spectrum.cell(row=row, column=3).value for row in range(2, 5)], [974.0, 975.0, 976.0])
            self.assertEqual([spectrum.cell(row=row, column=4).value for row in range(2, 5)], [11.0, 101.0, 21.0])

            summary_5a = workbook["5A逐秒数据"]
            self.assertEqual(summary_5a["A2"].value, 1)
            self.assertEqual(summary_5a["B2"].value, 10.0)
            self.assertAlmostEqual(summary_5a["C2"].value, 10.0 / (5.0 * 21.0))
            self.assertEqual(summary_5a["D2"].value, 21.0)
            self.assertEqual(summary_5a["E2"].value, 976.1)
            self.assertEqual(summary_5a["F2"].value, 976.0)
            self.assertEqual(summary_5a["G2"].value, 0.351)
            self.assertEqual(summary_5a["G2"].number_format, "0.000000")

            spectrum_5a = workbook["5A光谱数据"]
            self.assertEqual(spectrum_5a["A1"].value, "1s波长(nm)")
            self.assertEqual(spectrum_5a["B1"].value, "1s强度(counts)")
            self.assertEqual(
                [spectrum_5a.cell(row=row, column=1).value for row in range(2, 5)],
                [975.0, 976.0, 977.0],
            )
            self.assertEqual(
                [spectrum_5a.cell(row=row, column=2).value for row in range(2, 5)],
                [20.0, 200.0, 30.0],
            )
            workbook.close()

    def test_each_second_keeps_its_own_wavelength_axis_rounded_to_three_decimals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            path = directory / "axis_changed.xlsx"
            sample_path = directory / "axis_changed_1s_samples.csv"
            recorder = AutomaticSampleRecorder(sample_path)
            recorder.write(
                AutomaticSecondSample(
                    point_index=4,
                    point_elapsed_s=1.0,
                    target_current_a=3.0,
                    output_current_a=3.0,
                    output_voltage_raw_v=20.0,
                    output_voltage_v=20.0,
                    optical_power_w=6.0,
                    spectrum_wavelengths_nm=(974.0004, 975.0005, 976.0006),
                    spectrum_intensities=(10.0004, 100.0005, 20.0006),
                )
            )
            recorder.write(
                AutomaticSecondSample(
                    point_index=4,
                    point_elapsed_s=2.0,
                    target_current_a=3.0,
                    output_current_a=3.0,
                    output_voltage_raw_v=20.0,
                    output_voltage_v=20.0,
                    optical_power_w=6.0,
                    spectrum_wavelengths_nm=(974.1004, 975.1005, 976.1006),
                    spectrum_intensities=(11.0004, 101.0005, 21.0006),
                )
            )
            recorder.close()

            save_test_records(
                path,
                [self._record(3.0)],
                automatic_samples_path=sample_path,
            )

            workbook = load_workbook(path)
            sheet = workbook["3A光谱数据"]
            self.assertEqual(
                [sheet.cell(row=2, column=column).value for column in range(1, 5)],
                [974.0, 10.0, 974.1, 11.0],
            )
            self.assertEqual(
                [sheet.cell(row=4, column=column).value for column in range(1, 5)],
                [976.001, 20.001, 976.101, 21.001],
            )
            self.assertEqual(sheet["A2"].number_format, "0.000")
            self.assertEqual(sheet["B2"].number_format, "0.000")
            workbook.close()
            self.assertTrue(sample_path.is_file())
            self.assertTrue(path.exists())

    def test_sample_series_parses_batches_with_legacy_failure_semantics(self) -> None:
        row = {"spectrum_wavelengths_nm": "975;976.25;977", "spectrum_intensities": "1.5;2;3.25"}
        self.assertEqual(_sample_series(row, "spectrum_wavelengths_nm"), (975.0, 976.25, 977.0))
        self.assertEqual(_sample_series(row, "spectrum_intensities"), (1.5, 2.0, 3.25))
        self.assertEqual(_sample_series(row, "missing_key"), ())
        self.assertEqual(_sample_series({"v": "  "}, "v"), ())
        self.assertEqual(_sample_series({"v": "1;nan;3"}, "v"), ())
        self.assertEqual(_sample_series({"v": "1;inf"}, "v"), ())
        self.assertEqual(_sample_series({"v": "1;oops;3"}, "v"), ())
        self.assertEqual(_sample_series({"v": "1;"}, "v"), ())

    def test_append_migrates_legacy_workbook_smsr_header(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "测试数据"
            sheet["A1"] = "LIV"
            sheet["J1"] = "光谱"
            legacy_headers = (
                "电流(A)", "电压(V)", "功率(W)", "电光效率",
                "中心波长(nm)", "质心波长(nm)", "FWHM(nm)", "PIB",
            )
            for column, header in enumerate(legacy_headers, start=1):
                sheet.cell(2, column, header)
            workbook.save(path)
            workbook.close()

            append_test_record(path, self._record(3.0))

            migrated = load_workbook(path)
            sheet = migrated["测试数据"]
            self.assertEqual(sheet["I2"].value, "SMSR(dB)")
            self.assertEqual(sheet["I3"].value, 32.5)
            migrated.close()


if __name__ == "__main__":
    unittest.main()
