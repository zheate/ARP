import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from combined_test.excel_export import ExcelTestRecord, append_test_record, build_test_workbook_path, save_test_records


class ExcelExportTests(unittest.TestCase):
    def _record(self, current_a: float) -> ExcelTestRecord:
        return ExcelTestRecord(
            current_a=current_a,
            voltage_v=50.5,
            power_w=33.0,
            efficiency=33.0 / current_a / 50.5,
            peak_wavelength_nm=976.1,
            centroid_nm=976.0,
            fwhm_nm=1.2,
            pib=0.995,
            wavelength=[974.0, 976.0, 978.0],
            intensity=[10.0, 100.0, 20.0],
        )

    def test_path_uses_sn_and_test_time(self) -> None:
        path = build_test_workbook_path(Path("records"), "SN:001", datetime(2026, 7, 10, 14, 30, 25))

        self.assertEqual(path, Path("records/SN_001_2026_07_10_14_30_25.xlsx"))

    def test_appends_liv_and_full_spectra_to_same_reference_layout_workbook(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "SN001_2026_07_10_14_30_25.xlsx"
            append_test_record(path, self._record(4.0))
            append_test_record(path, self._record(3.0))

            workbook = load_workbook(path, data_only=False)
            sheet = workbook[workbook.sheetnames[0]]
            self.assertEqual(sheet["A1"].value, "LIV")
            self.assertEqual(sheet["J1"].value, "Spectra")
            self.assertEqual([sheet.cell(2, column).value for column in range(1, 9)], [
                "电流(A)", "电压(V)", "功率(W)", "电光效率",
                "中心波长(nm)", "质心波长(nm)", "FWHM(nm)", "PIB",
            ])
            self.assertEqual(sheet["A3"].value, 3.0)
            self.assertEqual(sheet["A4"].value, 4.0)
            self.assertEqual(sheet["J2"].value, "3.0A")
            self.assertEqual(sheet["L2"].value, "4.0A")
            self.assertEqual([sheet.cell(row, 10).value for row in range(3, 6)], [974.0, 976.0, 978.0])
            self.assertEqual([sheet.cell(row, 11).value for row in range(3, 6)], [10.0, 100.0, 20.0])
            self.assertEqual(sheet["D3"].number_format, "0.00%")
            self.assertEqual(sheet["H3"].number_format, "0.00%")
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


if __name__ == "__main__":
    unittest.main()
