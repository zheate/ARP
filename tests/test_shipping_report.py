import base64
import hashlib
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from openpyxl import Workbook, load_workbook

from combined_test.excel_export import ExcelTestRecord, append_test_record, save_test_records
from combined_test.record_store import SessionStatus
from combined_test.shipping_report import (
    POLE_FIELD_DEFINITIONS,
    ReportFieldDefinition,
    SPECTRUM_FIELD_DEFINITIONS,
    REPORT_WORKBOOK_SCHEMA_VERSION,
    SelectedReportField,
    ShippingReportLanguage,
    ShippingReportRequest,
    ShippingReportType,
    SpectrumAxisMode,
    SpectrumAxisSettings,
    _balanced_report_columns,
    _format_performance_parameters_title,
    _format_pole_summary_value,
    _report_definitions,
    _format_scientific_axis_tick,
    build_batch_shipping_report_request,
    generate_shipping_report,
    generate_shipping_report_batch_item,
    inspect_shipping_report_batch,
    inspect_shipping_workbook,
    load_shipping_report_preferences,
    next_shipping_report_output_path,
    render_shipping_report_preview,
    save_shipping_report_preferences,
    suggested_field_values,
    validate_shipping_report_request,
)
from combined_test.shipping_report_headless import (
    _HeadlessShippingReportRenderer,
    _spectrum_legend_column_count,
)
from tauri_bridge.settings import JsonSettings


class ShippingReportTests(unittest.TestCase):
    def _record(self, current_a: float, *, spectrum: bool = True) -> ExcelTestRecord:
        return ExcelTestRecord(
            current_a=current_a,
            voltage_v=10.0 + current_a,
            power_w=20.0 * current_a,
            efficiency=(20.0 * current_a) / (current_a * (10.0 + current_a)),
            peak_wavelength_nm=976.0 + current_a / 100.0 if spectrum else float("nan"),
            centroid_nm=976.0 if spectrum else float("nan"),
            fwhm_nm=0.3 if spectrum else float("nan"),
            pib=0.95 if spectrum else float("nan"),
            smsr_db=35.0 if spectrum else float("nan"),
            wavelength=[974.0, 975.0, 976.0, 977.0, 978.0] if spectrum else [],
            intensity=[10.0, 100.0, 1000.0, 120.0, 12.0] if spectrum else [],
            test_station="出货站 1",
        )

    def _session(
        self,
        status: SessionStatus = SessionStatus.COMPLETED,
        *,
        mode: str = "automatic",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            session_id="session-1",
            sn="HEA000001",
            product_model="500W 976nm 光纤耦合模块",
            batch="B01",
            station="出货站 1",
            mode=mode,
            started_at_utc="2026-07-24T01:00:00+00:00",
            ended_at_utc="2026-07-24T01:10:00+00:00",
            status=status,
            termination_reason="所有计划测试点均已保存",
            shutdown_confirmed=True,
            software_version="1.0.0",
            calculation_version="2026-07",
            settings={},
            devices=(),
        )

    def _write(self, path: Path, *, status: SessionStatus = SessionStatus.COMPLETED, mixed=False) -> None:
        records = [self._record(1.0), self._record(2.0), self._record(3.0, spectrum=not mixed)]
        save_test_records(path, records, session=self._session(status))

    def _set_session_value(self, path: Path, label: str, value: str) -> None:
        workbook = load_workbook(path)
        sheet = workbook["会话信息"]
        for row in range(1, sheet.max_row + 1):
            if sheet.cell(row, 1).value == label:
                sheet.cell(row, 2, value)
                break
        workbook.save(path)
        workbook.close()

    @staticmethod
    def _request_payload(request: ShippingReportRequest) -> dict[str, object]:
        return {
            "reportType": request.report_type.value,
            "reportLanguage": request.report_language.value,
            "productName": request.product_name,
            "sn": "SHOULD-BE-IGNORED",
            "operatingCurrentA": request.operating_current_a,
            "fields": {
                key: {"include": selected.include, "value": selected.value}
                for key, selected in request.fields.items()
            },
            "encircledEnergyNa": request.encircled_energy_na,
            "legacyCompletionConfirmed": request.legacy_completion_confirmed,
            "customFields": [
                {"key": field.key, "label": field.label, "unit": field.unit, "side": field.side}
                for field in request.custom_fields
            ],
            **(
                {
                    "spectrumAxis": {
                        "mode": request.spectrum_axis.mode.value,
                        "minimum": request.spectrum_axis.minimum,
                        "maximum": request.spectrum_axis.maximum,
                        "wavelengthMinimum": request.spectrum_axis.wavelength_minimum,
                        "wavelengthMaximum": request.spectrum_axis.wavelength_maximum,
                    }
                }
                if request.spectrum_axis is not None
                else {}
            ),
        }

    def _request(self, report_type: ShippingReportType) -> ShippingReportRequest:
        definitions = SPECTRUM_FIELD_DEFINITIONS if report_type is ShippingReportType.SPECTRUM else POLE_FIELD_DEFINITIONS
        fields = {
            definition.key: SelectedReportField(
                include=definition.key in {"power", "operatingCurrent", "coolingTemperature"},
                value=("25" if definition.key == "coolingTemperature" else "3" if definition.key == "operatingCurrent" else "60"),
            )
            for definition in definitions
        }
        return ShippingReportRequest(
            report_type=report_type,
            product_name="500W 976nm 光纤耦合模块",
            sn="HEA000001",
            operating_current_a=3.0,
            fields=fields,
            legacy_completion_confirmed=False,
            spectrum_axis=(
                SpectrumAxisSettings(SpectrumAxisMode.RELATIVE_DB, -80.0, 0.0, 974.0, 978.0)
                if report_type is ShippingReportType.SPECTRUM
                else None
            ),
        )

    def test_report_charts_stop_at_selected_operating_current(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "current-limit.xlsx"
            currents = list(range(2, 21, 2))
            save_test_records(source, [self._record(current) for current in currents], session=self._session())
            inspection = inspect_shipping_workbook(source)
            for report_type in (ShippingReportType.SPECTRUM, ShippingReportType.POLE):
                for selected in (2.0, 18.0, 20.0, 18.0 - 5e-10):
                    with self.subTest(report_type=report_type, selected=selected):
                        request = replace(self._request(report_type), operating_current_a=selected)
                        expected = [current for current in currents if current <= round(selected)]
                        figures = _HeadlessShippingReportRenderer(inspection, request).build()
                        try:
                            axes = [axis for figure in figures for axis in figure.axes]
                            power_axis = next(axis for axis in axes if axis.get_xlabel() == "Drive current (A)")
                            efficiency_axis = next(axis for axis in axes if axis.get_ylabel() == "E-O efficiency (%)")
                            for axis in (power_axis, efficiency_axis):
                                self.assertEqual(list(axis.lines[0].get_xdata()), expected)
                            if selected == 18.0:
                                self.assertEqual(max(power_axis.get_xticks()), 18.0)
                            if report_type is ShippingReportType.SPECTRUM:
                                spectrum_axis = next(axis for axis in axes if axis.get_xlabel() == "Wavelength (nm)")
                                self.assertEqual(len(spectrum_axis.lines), len(expected))
                                self.assertEqual(
                                    [text.get_text() for text in spectrum_axis.get_legend().get_texts()],
                                    [f"{current} A" for current in expected],
                                )
                        finally:
                            for figure in figures:
                                figure.clear()
            self.assertEqual([point.current_a for point in inspection.points], currents)

    def test_approved_report_artwork_is_unchanged(self) -> None:
        assets = Path(__file__).resolve().parents[1] / "assets" / "report"
        expected_hashes = {
            "everbright_header_reference.png": "2b3f2d3cb32ce1bdf50d73f8a53ee646e3793b686cd8409a54037ac5c4343954",
            "laser_warning_reference.png": "448da5e8c41a75e166e1d5d3871886bac7494b7af8c2bb5d3f8a105c52f264f2",
        }
        for filename, expected_hash in expected_hashes.items():
            with self.subTest(filename=filename):
                self.assertEqual(hashlib.sha256((assets / filename).read_bytes()).hexdigest(), expected_hash)

    def test_inspection_accepts_verified_success_and_returns_suggestions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "test.xlsx"
            self._write(path)

            inspection = inspect_shipping_workbook(path)

            self.assertEqual(inspection.schema_version, REPORT_WORKBOOK_SCHEMA_VERSION)
            self.assertEqual(inspection.compatibility.kind, "verified_success")
            self.assertTrue(inspection.spectrum_complete)
            self.assertEqual(inspection.allowed_report_types, ("spectrum", "pole"))
            self.assertEqual(inspection.sn, "HEA000001")
            self.assertEqual(inspection.spectrum_wavelength_range, (974.0, 978.0))
            self.assertAlmostEqual(inspection.wavelength_current_coefficient_nm_per_a, 0.01)
            self.assertEqual(
                inspection.to_payload()["spectrumWavelengthRange"],
                {"minimum": 974.0, "maximum": 978.0},
            )
            self.assertAlmostEqual(
                inspection.to_payload()["points"][-1]["wavelengthCurrentCoefficientNmPerA"],
                0.01,
            )
            self.assertEqual(
                inspection.to_payload()["points"][-1]["spectrumPeakCounts"],
                1000.0,
            )
            suggestions = suggested_field_values(inspection, ShippingReportType.SPECTRUM, 3.0)
            self.assertEqual(suggestions["operatingCurrent"], "3")
            self.assertEqual(suggestions["power"], "60.0")
            self.assertEqual(suggestions["operatingVoltage"], "13.00")
            self.assertEqual(suggestions["slopeEfficiency"], "20.000")
            self.assertEqual(suggestions["electroOpticalEfficiency"], "153.85")
            self.assertEqual(suggestions["wavelengthCurrentCoefficient"], "0.01")
            pole_suggestions = suggested_field_values(inspection, ShippingReportType.POLE, 3.0)
            self.assertEqual(pole_suggestions["slopeEfficiency"], "20.000")
            zero_current = replace(inspection.point_for_current(1.0), current_a=0.0, power_w=0.0)
            self.assertIsNone(zero_current.suggestions()["slopeEfficiencyWPerA"])

    def test_centiampere_spectra_keep_their_liv_current_when_saved_and_appended(self) -> None:
        for append in (False, True):
            with self.subTest(append=append), tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "fine-current.xlsx"
                records = [
                    replace(self._record(2.01), intensity=[1, 2, 3, 4, 5]),
                    replace(self._record(2.02), intensity=[10, 20, 30, 40, 50]),
                ]
                if append:
                    save_test_records(path, records[:1], session=self._session())
                    append_test_record(path, records[1])
                else:
                    save_test_records(path, records, session=self._session())

                inspection = inspect_shipping_workbook(path)

                self.assertEqual(inspection.allowed_report_types, ("spectrum", "pole"))
                self.assertEqual([point.current_a for point in inspection.points], [2.01, 2.02])
                for record in records:
                    point = inspection.point_for_current(record.current_a)
                    self.assertEqual(point.wavelength, tuple(record.wavelength))
                    self.assertEqual(point.intensity, tuple(record.intensity))

    def test_manual_single_point_workbook_generates_spectrum_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "manual-single-point.xlsx"
            target = root / "manual-single-point.pdf"
            record = self._record(20.0)
            save_test_records(
                source,
                [record],
                session=self._session(mode="manual"),
            )

            inspection = inspect_shipping_workbook(source)

            self.assertTrue(inspection.single_point)
            self.assertEqual(inspection.test_mode, "manual")
            self.assertEqual(
                inspection.compatibility.kind,
                "verified_manual_single_point",
            )
            self.assertEqual(inspection.allowed_report_types, ("spectrum", "pole"))
            self.assertIsNone(inspection.wavelength_current_coefficient_nm_per_a)
            payload = inspection.to_payload()
            self.assertTrue(payload["singlePoint"])
            self.assertEqual(payload["testMode"], "manual")

            fields = {
                definition.key: SelectedReportField(
                    include=definition.key
                    in {
                        "power",
                        "operatingCurrent",
                        "wavelengthCurrentCoefficient",
                    },
                    value=(
                        "N/A"
                        if definition.key == "wavelengthCurrentCoefficient"
                        else "20"
                        if definition.key == "operatingCurrent"
                        else "400.0"
                    ),
                )
                for definition in SPECTRUM_FIELD_DEFINITIONS
            }
            request = ShippingReportRequest(
                report_type=ShippingReportType.SPECTRUM,
                product_name="虚拟单点产品",
                sn="HEA000001",
                operating_current_a=20.0,
                fields=fields,
                legacy_completion_confirmed=False,
                spectrum_axis=SpectrumAxisSettings(
                    SpectrumAxisMode.COUNTS,
                    0.0,
                    2000.0,
                    974.0,
                    978.0,
                ),
            )

            validate_shipping_report_request(inspection, request)
            result = generate_shipping_report(source, target, request)
            preview = render_shipping_report_preview(source, request)

            self.assertTrue(result.read_bytes().startswith(b"%PDF"))
            self.assertEqual(len(preview), 1)

    def test_batch_request_keeps_unavailable_single_point_measurement_as_na(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "manual-single-point.xlsx"
            save_test_records(
                source,
                [self._record(20.0)],
                session=self._session(mode="manual"),
            )
            inspection = inspect_shipping_workbook(source)
            template = self._request(ShippingReportType.SPECTRUM)
            payload = self._request_payload(
                replace(template, operating_current_a=20.0)
            )
            fields = payload["fields"]
            assert isinstance(fields, dict)
            fields["wavelengthCurrentCoefficient"] = {
                "include": True,
                "value": "0.01",
            }

            request = build_batch_shipping_report_request(inspection, payload)

            self.assertTrue(request.fields["wavelengthCurrentCoefficient"].include)
            self.assertEqual(
                request.fields["wavelengthCurrentCoefficient"].value,
                "N/A",
            )
            validate_shipping_report_request(inspection, request)

    def test_batch_scan_recurses_uses_excel_sn_and_selects_latest_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            older = root / "folder-name-is-not-sn" / "station-a" / "older.xlsx"
            newer = root / "another-folder" / "deep" / "newer.xlsx"
            missing_sn = root / "missing-sn.xlsx"
            corrupt = root / "corrupt.xlsx"
            ignored_lock = root / "~$open.xlsx"
            for path in (older, newer, missing_sn, ignored_lock):
                path.parent.mkdir(parents=True, exist_ok=True)
                self._write(path)
            corrupt.write_bytes(b"not an xlsx workbook")
            self._set_session_value(older, "SN", "EXCEL-SN-001")
            self._set_session_value(newer, "SN", "EXCEL-SN-001")
            self._set_session_value(missing_sn, "SN", "")
            os.utime(older, ns=(1_000_000_000, 1_000_000_000))
            os.utime(newer, ns=(2_000_000_000, 2_000_000_000))

            batch = inspect_shipping_report_batch(root)

            self.assertEqual(len(batch.items), 4)
            by_name = {item.source_path.name: item for item in batch.items}
            self.assertEqual(by_name["newer.xlsx"].status, "ready")
            self.assertEqual(by_name["newer.xlsx"].sn, "EXCEL-SN-001")
            self.assertEqual(by_name["older.xlsx"].status, "ignored_duplicate")
            self.assertEqual(by_name["missing-sn.xlsx"].status, "invalid")
            self.assertIn("缺少 SN", by_name["missing-sn.xlsx"].message)
            self.assertEqual(by_name["corrupt.xlsx"].status, "invalid")
            self.assertIn("无法读取 Excel", by_name["corrupt.xlsx"].message)
            self.assertEqual(batch.union_currents, (1.0, 2.0, 3.0))
            self.assertEqual(batch.common_currents, (1.0, 2.0, 3.0))
            self.assertTrue(batch.to_payload()["allReadySupportSpectrum"])

    def test_batch_duplicate_tie_breaks_by_full_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "a" / "source.xlsx"
            second = root / "b" / "source.xlsx"
            for path in (first, second):
                path.parent.mkdir(parents=True)
                self._write(path)
                self._set_session_value(path, "SN", "TIE-SN")
                os.utime(path, ns=(5_000_000_000, 5_000_000_000))

            batch = inspect_shipping_report_batch(root)

            ready = [item for item in batch.items if item.ready]
            self.assertEqual([item.source_path for item in ready], [second.resolve()])

    def test_batch_request_replaces_sn_and_measured_values_from_each_workbook(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.xlsx"
            self._write(source)
            self._set_session_value(source, "SN", "EXCEL-SN-002")
            inspection = inspect_shipping_workbook(source)
            template = self._request(ShippingReportType.POLE)
            payload = self._request_payload(template)
            fields = payload["fields"]
            assert isinstance(fields, dict)
            fields["power"] = {"include": True, "value": "9999"}
            fields["operatingVoltage"] = {"include": True, "value": "9999"}

            request = build_batch_shipping_report_request(inspection, payload)

            self.assertEqual(request.sn, "EXCEL-SN-002")
            self.assertEqual(request.product_name, template.product_name)
            self.assertEqual(request.fields["power"].value, "60.0")
            self.assertEqual(request.fields["operatingVoltage"].value, "13.00")
            self.assertEqual(request.fields["coolingTemperature"].value, "25")

    def test_batch_output_path_adds_stable_suffixes_and_generates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.xlsx"
            output = root / "pdf"
            self._write(source)
            (output / "HEA000001_出货报告.pdf").parent.mkdir(parents=True)
            (output / "HEA000001_出货报告.pdf").write_bytes(b"existing")
            (output / "HEA000001_出货报告_2.pdf").write_bytes(b"existing")

            self.assertEqual(
                next_shipping_report_output_path(output, "HEA000001").name,
                "HEA000001_出货报告_3.pdf",
            )
            target, request = generate_shipping_report_batch_item(
                source,
                output,
                self._request_payload(self._request(ShippingReportType.POLE)),
            )

            self.assertEqual(request.sn, "HEA000001")
            self.assertEqual(target.name, "HEA000001_出货报告_3.pdf")
            self.assertTrue(target.read_bytes().startswith(b"%PDF"))

    def test_inspection_detects_renamed_sheet_shifted_header_and_shuffled_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "customer-format.xlsx"
            workbook = Workbook()
            cover = workbook.active
            cover.title = "说明"
            cover["A1"] = "客户测试记录"
            sheet = workbook.create_sheet("LIV Result")
            headers = (
                "Output Power (W)",
                "SMSR (dB)",
                "Operating Current (A)",
                "FWHM (nm)",
                "Voltage (V)",
                "E-O efficiency (%)",
                "Peak Wavelength (nm)",
                "PIB (%)",
                "Centroid Wavelength (nm)",
            )
            for column, header in enumerate(headers, start=1):
                sheet.cell(120, column, header)
            rows = (
                (20.0, 35.0, 1.0, 0.3, 11.0, 0.50, 976.1, 0.95, 976.0),
                (40.0, 36.0, 2.0, 0.4, 12.0, 0.55, 976.2, 0.96, 976.1),
            )
            for row_index, values in enumerate(rows, start=121):
                for column, value in enumerate(values, start=1):
                    sheet.cell(row_index, column, value)
            session = workbook.create_sheet("会话信息")
            session.append(("出货报告格式版本", REPORT_WORKBOOK_SCHEMA_VERSION))
            session.append(("流程状态", "completed"))
            session.append(("SN", "CUSTOM-001"))
            session.append(("产品型号", "Customer Module"))
            workbook.save(path)
            workbook.close()

            inspection = inspect_shipping_workbook(path)

            self.assertEqual(inspection.compatibility.kind, "verified_success")
            self.assertEqual(inspection.sn, "CUSTOM-001")
            self.assertEqual([point.current_a for point in inspection.points], [1.0, 2.0])
            point = inspection.point_for_current(2.0)
            self.assertEqual(point.voltage_v, 12.0)
            self.assertEqual(point.power_w, 40.0)
            self.assertEqual(point.efficiency, 0.55)
            self.assertEqual(point.peak_wavelength_nm, 976.2)
            self.assertEqual(point.centroid_nm, 976.1)
            self.assertEqual(point.fwhm_nm, 0.4)
            self.assertEqual(point.pib, 0.96)
            self.assertEqual(point.smsr_db, 36.0)

    def test_pole_summary_values_use_compact_report_precision(self) -> None:
        definitions = {definition.key: definition for definition in POLE_FIELD_DEFINITIONS}

        self.assertEqual(_format_pole_summary_value(definitions["power"], "155.7"), "155.7")
        self.assertEqual(_format_pole_summary_value(definitions["operatingCurrent"], "7"), "7")
        self.assertEqual(_format_pole_summary_value(definitions["operatingVoltage"], "78.07"), "78.07")
        self.assertEqual(_format_pole_summary_value(definitions["slopeEfficiency"], "22.247"), "22.25")
        self.assertEqual(_format_pole_summary_value(definitions["electroOpticalEfficiency"], "28.50"), "28.5")
        self.assertEqual(_format_pole_summary_value(definitions["coolingTemperature"], "25"), "25")

    def test_spectrum_count_ticks_use_compact_scientific_notation(self) -> None:
        self.assertEqual(_format_scientific_axis_tick(0.0), "0")
        self.assertEqual(_format_scientific_axis_tick(4_530.8), "4.53e3")
        self.assertEqual(_format_scientific_axis_tick(18_123.2), "1.81e4")

    def test_spectrum_legend_uses_two_columns_only_when_single_column_would_overflow(self) -> None:
        self.assertEqual(_spectrum_legend_column_count(10), 1)
        self.assertEqual(_spectrum_legend_column_count(11), 2)
        self.assertEqual(_spectrum_legend_column_count(14), 2)

    def test_spectrum_legend_aligns_units_and_keeps_swatches_in_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "legend.xlsx"
            for currents in (list(range(2, 21, 2)), [0.5, *range(2, 21, 2)]):
                save_test_records(source, [self._record(current) for current in currents], session=self._session())
                request = replace(self._request(ShippingReportType.SPECTRUM), operating_current_a=20)
                figures = _HeadlessShippingReportRenderer(inspect_shipping_workbook(source), request).build()
                try:
                    figure = figures[0]
                    legend = next(axis.get_legend() for axis in figure.axes if axis.get_xlabel() == "Wavelength (nm)")
                    self.assertEqual([text.get_text() for text in legend.get_texts()], [f"{current:g} A" for current in currents])
                    columns = _spectrum_legend_column_count(len(currents))
                    for dpi in (96, 220):
                        figure.set_dpi(dpi)
                        figure.canvas.draw()
                        renderer = figure.canvas.get_renderer()
                        start = 0
                        for column in range(columns):
                            count = len(currents) // columns + (column < len(currents) % columns)
                            texts = legend.get_texts()[start:start + count]
                            handles = legend.legend_handles[start:start + count]
                            right_edges = [text.get_window_extent(renderer).x1 for text in texts]
                            swatch_starts = [handle.get_window_extent(renderer).x0 for handle in handles]
                            self.assertLess(max(right_edges) - min(right_edges), 0.01)
                            self.assertLess(max(swatch_starts) - min(swatch_starts), 0.01)
                            start += count
                finally:
                    for figure in figures:
                        figure.clear()

    def test_pole_report_labels_use_the_requested_english_names(self) -> None:
        definitions = {definition.key: definition for definition in POLE_FIELD_DEFINITIONS}
        spectrum_definitions = {definition.key: definition for definition in SPECTRUM_FIELD_DEFINITIONS}

        self.assertEqual(definitions["electroOpticalEfficiency"].label, "E-O efficiency")
        self.assertEqual(definitions["coolingTemperature"].label, "Cooling Temperature")
        self.assertEqual(spectrum_definitions["coolingTemperature"].label, "Cooling Temperature")
        self.assertEqual(_format_performance_parameters_title(7.0), "Performance @7A")

    def test_chinese_report_localizes_builtin_labels_and_heading(self) -> None:
        definitions = {
            definition.key: definition
            for definition in _report_definitions(
                ShippingReportType.SPECTRUM,
                encircled_energy_na="0.22",
                report_language=ShippingReportLanguage.CHINESE,
            )
        }

        self.assertEqual(definitions["power"].label, "输出功率（CW）")
        self.assertEqual(definitions["electroOpticalEfficiency"].label, "电光效率")
        self.assertEqual(definitions["na017Energy"].label, "0.22 NA 能量占比")
        self.assertEqual(
            _format_performance_parameters_title(7.0, ShippingReportLanguage.CHINESE),
            "性能参数 @7A",
        )

    def test_report_language_defaults_to_english_and_accepts_chinese(self) -> None:
        english_payload = self._request_payload(self._request(ShippingReportType.POLE))
        english_payload.pop("reportLanguage")
        self.assertEqual(
            ShippingReportRequest.from_mapping(english_payload).report_language,
            ShippingReportLanguage.ENGLISH,
        )

        chinese = replace(
            self._request(ShippingReportType.POLE),
            report_language=ShippingReportLanguage.CHINESE,
        )
        self.assertEqual(
            ShippingReportRequest.from_mapping(self._request_payload(chinese)).report_language,
            ShippingReportLanguage.CHINESE,
        )

    def test_spectrum_summary_columns_are_balanced_with_extra_row_on_left(self) -> None:
        fields = [
            (ReportFieldDefinition(f"field-{index}", f"Field {index}", "", "left"), str(index))
            for index in range(9)
        ]

        left, right = _balanced_report_columns(fields)

        self.assertEqual(len(left), 5)
        self.assertEqual(len(right), 4)
        self.assertEqual(left + right, fields)
        even_left, even_right = _balanced_report_columns(fields[:8])
        self.assertEqual(len(even_left), 4)
        self.assertEqual(len(even_right), 4)

    def test_explicit_failed_workbook_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "failed.xlsx"
            self._write(path, status=SessionStatus.ABORTED_SAFELY)

            inspection = inspect_shipping_workbook(path)

            self.assertEqual(inspection.compatibility.kind, "rejected")
            with self.assertRaisesRegex(ValueError, "中止或失败"):
                validate_shipping_report_request(inspection, self._request(ShippingReportType.POLE))

    def test_unknown_nonlegacy_schema_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "future.xlsx"
            self._write(path)
            workbook = load_workbook(path)
            sheet = workbook["会话信息"]
            for row in range(1, sheet.max_row + 1):
                if sheet.cell(row, 1).value == "出货报告格式版本":
                    sheet.cell(row, 2, "999")
                    break
            workbook.save(path)
            workbook.close()

            inspection = inspect_shipping_workbook(path)

            self.assertEqual(inspection.compatibility.kind, "rejected")
            self.assertIn("不受当前软件支持", inspection.compatibility.message)

    def test_legacy_workbook_requires_manual_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "测试数据"
            sheet.cell(3, 1, 1.0)
            sheet.cell(3, 2, 12.0)
            sheet.cell(3, 3, 20.0)
            sheet.cell(3, 4, 0.5)
            workbook.save(path)
            workbook.close()
            inspection = inspect_shipping_workbook(path)
            request = self._request(ShippingReportType.POLE)

            self.assertTrue(inspection.compatibility.requires_legacy_confirmation)
            with self.assertRaisesRegex(ValueError, "确认旧版"):
                validate_shipping_report_request(inspection, request)
            confirmed = ShippingReportRequest(
                **{**request.__dict__, "operating_current_a": 1.0, "legacy_completion_confirmed": True}
            )
            validate_shipping_report_request(inspection, confirmed)

    def test_missing_one_spectrum_strictly_limits_report_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mixed.xlsx"
            self._write(path, mixed=True)

            inspection = inspect_shipping_workbook(path)

            self.assertFalse(inspection.spectrum_complete)
            self.assertEqual(inspection.allowed_report_types, ("pole",))
            with self.assertRaisesRegex(ValueError, "光谱数据不完整"):
                validate_shipping_report_request(inspection, self._request(ShippingReportType.SPECTRUM))

    def test_inspection_rejects_corrupt_empty_and_duplicate_current_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corrupt = root / "corrupt.xlsx"
            corrupt.write_bytes(b"not an xlsx workbook")
            with self.assertRaisesRegex(ValueError, "无法读取 Excel"):
                inspect_shipping_workbook(corrupt)

            empty = root / "empty.xlsx"
            Workbook().save(empty)
            with self.assertRaisesRegex(ValueError, "没有有效测试点"):
                inspect_shipping_workbook(empty)

            duplicate = root / "duplicate.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "测试数据"
            for row in (3, 4):
                sheet.cell(row, 1, 2.0)
                sheet.cell(row, 2, 12.0)
                sheet.cell(row, 3, 40.0)
                sheet.cell(row, 4, 0.5)
            workbook.save(duplicate)
            workbook.close()
            with self.assertRaisesRegex(ValueError, "重复电流点"):
                inspect_shipping_workbook(duplicate)

    def test_workpoint_switch_reprefills_measured_values_and_validation_blocks_missing_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.xlsx"
            self._write(source)
            inspection = inspect_shipping_workbook(source)

            first = suggested_field_values(inspection, ShippingReportType.POLE, 1.0)
            last = suggested_field_values(inspection, ShippingReportType.POLE, 3.0)
            self.assertEqual(first["power"], "20.0")
            self.assertEqual(last["power"], "60.0")

            request = self._request(ShippingReportType.POLE)
            missing = ShippingReportRequest(
                **{
                    **request.__dict__,
                    "fields": {"power": SelectedReportField(include=True, value="")},
                }
            )
            with self.assertRaisesRegex(ValueError, "请填写已勾选参数"):
                validate_shipping_report_request(inspection, missing)

            none_selected = ShippingReportRequest(
                **{
                    **request.__dict__,
                    "fields": {"power": SelectedReportField(include=False, value="60")},
                }
            )
            with self.assertRaisesRegex(ValueError, "至少勾选一个"):
                validate_shipping_report_request(inspection, none_selected)

    def test_spectrum_axis_validation_supports_counts_and_relative_db(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.xlsx"
            self._write(source)
            inspection = inspect_shipping_workbook(source)
            base = self._request(ShippingReportType.SPECTRUM)

            counts = ShippingReportRequest(
                **{**base.__dict__, "spectrum_axis": SpectrumAxisSettings(SpectrumAxisMode.COUNTS, 0, 1200)}
            )
            validate_shipping_report_request(inspection, counts)

            wavelength_window = ShippingReportRequest(
                **{**base.__dict__, "spectrum_axis": SpectrumAxisSettings(SpectrumAxisMode.COUNTS, 0, 1200, 975, 977)}
            )
            validate_shipping_report_request(inspection, wavelength_window)

            bad_counts = ShippingReportRequest(
                **{**base.__dict__, "spectrum_axis": SpectrumAxisSettings(SpectrumAxisMode.COUNTS, -1, 1200)}
            )
            with self.assertRaisesRegex(ValueError, "下限不能小于 0"):
                validate_shipping_report_request(inspection, bad_counts)

            bad_db = ShippingReportRequest(
                **{**base.__dict__, "spectrum_axis": SpectrumAxisSettings(SpectrumAxisMode.RELATIVE_DB, -50, 1)}
            )
            with self.assertRaisesRegex(ValueError, "上限不能大于 0 dB"):
                validate_shipping_report_request(inspection, bad_db)

            bad_wavelength = ShippingReportRequest(
                **{**base.__dict__, "spectrum_axis": SpectrumAxisSettings(SpectrumAxisMode.COUNTS, 0, 1200, 977, 975)}
            )
            with self.assertRaisesRegex(ValueError, "横轴下限必须小于上限"):
                validate_shipping_report_request(inspection, bad_wavelength)

    def test_preferences_remember_selection_and_only_manual_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = JsonSettings(Path(temp_dir) / "report.json")
            request = replace(
                self._request(ShippingReportType.SPECTRUM),
                report_language=ShippingReportLanguage.CHINESE,
            )

            save_shipping_report_preferences(settings, request)
            restored = load_shipping_report_preferences(settings)["spectrum"]

            self.assertIn("power", restored["selectedFields"])
            self.assertEqual(restored["manualValues"]["coolingTemperature"], "25")
            self.assertNotIn("power", restored["manualValues"])
            self.assertEqual(restored["reportLanguage"], "zh")
            self.assertEqual(restored["spectrumAxis"]["minimum"], -80.0)
            self.assertEqual(restored["spectrumAxis"]["wavelengthMinimum"], 974.0)
            self.assertEqual(restored["spectrumAxis"]["wavelengthMaximum"], 978.0)

    def test_encircled_energy_na_is_configurable_validated_and_remembered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.xlsx"
            self._write(source)
            inspection = inspect_shipping_workbook(source)
            request = ShippingReportRequest(
                **{**self._request(ShippingReportType.SPECTRUM).__dict__, "encircled_energy_na": "0.22"}
            )

            validate_shipping_report_request(inspection, request)
            parsed = ShippingReportRequest.from_mapping(self._request_payload(request))
            self.assertEqual(parsed.encircled_energy_na, "0.22")
            definitions = _report_definitions(
                ShippingReportType.SPECTRUM,
                encircled_energy_na=request.encircled_energy_na,
            )
            self.assertEqual(
                next(field.label for field in definitions if field.key == "na017Energy"),
                "NA 0.22 Encircled Energy",
            )

            settings = JsonSettings(Path(temp_dir) / "report.json")
            save_shipping_report_preferences(settings, request)
            self.assertEqual(
                load_shipping_report_preferences(settings)["spectrum"]["encircledEnergyNa"],
                "0.22",
            )

            invalid = ShippingReportRequest(**{**request.__dict__, "encircled_energy_na": "1.1"})
            with self.assertRaisesRegex(ValueError, "必须大于 0 且不大于 1"):
                validate_shipping_report_request(inspection, invalid)

    def test_first_use_pole_preferences_match_the_approved_six_row_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = JsonSettings(Path(temp_dir) / "report.json")

            preferences = load_shipping_report_preferences(settings)

            self.assertEqual(
                preferences["pole"]["selectedFields"],
                [definition.key for definition in POLE_FIELD_DEFINITIONS],
            )
            self.assertEqual(preferences["spectrum"]["encircledEnergyNa"], "0.17")

    def test_custom_report_fields_are_validated_rendered_and_remembered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.xlsx"
            output = Path(temp_dir) / "custom.pdf"
            self._write(source)
            inspection = inspect_shipping_workbook(source)
            custom = ReportFieldDefinition("custom-package", "封装类型", "", "single")
            request = self._request(ShippingReportType.POLE)
            request = ShippingReportRequest(
                **{
                    **request.__dict__,
                    "custom_fields": (custom,),
                    "fields": {**request.fields, custom.key: SelectedReportField(True, "蝶形")},
                }
            )

            validate_shipping_report_request(inspection, request)
            generate_shipping_report(source, output, request)
            self.assertTrue(output.read_bytes().startswith(b"%PDF"))

            settings = JsonSettings(Path(temp_dir) / "report.json")
            save_shipping_report_preferences(settings, request)
            restored = load_shipping_report_preferences(settings)["pole"]
            self.assertEqual(restored["customFields"][0]["label"], "封装类型")
            self.assertIn(custom.key, restored["selectedFields"])
            self.assertEqual(restored["manualValues"][custom.key], "蝶形")

    def test_generates_both_pdf_layouts_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.xlsx"
            self._write(source)
            for report_type in (ShippingReportType.SPECTRUM, ShippingReportType.POLE):
                output = Path(temp_dir) / f"{report_type.value}.pdf"

                result = generate_shipping_report(source, output, self._request(report_type))

                self.assertEqual(result, output.resolve())
                self.assertTrue(output.read_bytes().startswith(b"%PDF"))
                self.assertGreater(output.stat().st_size, 10_000)
                self.assertFalse((Path(temp_dir) / f".{report_type.value}.tmp.pdf").exists())

    def test_renders_live_preview_pages_from_the_export_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.xlsx"
            self._write(source)

            pages = render_shipping_report_preview(source, self._request(ShippingReportType.POLE))

            self.assertEqual(len(pages), 1)
            self.assertTrue(base64.b64decode(pages[0]).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_schema_version_is_written_to_session_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "schema.xlsx"
            self._write(path)
            workbook = load_workbook(path, data_only=True)
            values = {
                str(workbook["会话信息"].cell(row, 1).value): str(workbook["会话信息"].cell(row, 2).value)
                for row in range(1, workbook["会话信息"].max_row + 1)
            }
            workbook.close()
            self.assertEqual(values["出货报告格式版本"], REPORT_WORKBOOK_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
