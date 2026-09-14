import base64
import sys
from dataclasses import replace
from pathlib import Path
from tests.test_shipping_report import ShippingReportTests
from combined_test.shipping_report import (ShippingReportType, ShippingReportLanguage, SpectrumAxisMode, SpectrumAxisSettings, SelectedReportField, SPECTRUM_FIELD_DEFINITIONS, suggested_field_values, inspect_shipping_workbook, generate_shipping_report, render_shipping_report_preview)
out = Path('output/pdf/label-spacing-validation')
source = Path('output/pdf/current-limit-validation/synthetic-2-to-20A.xlsx')
inspection = inspect_shipping_workbook(source)
request = replace(ShippingReportTests()._request(ShippingReportType.SPECTRUM), operating_current_a=20, report_language=ShippingReportLanguage.ENGLISH, encircled_energy_na="0.175", product_name='300W 976nm Fiber Coupling Module (Synthetic Data)', spectrum_axis=SpectrumAxisSettings(SpectrumAxisMode.COUNTS, 0, 30000, 965, 985))
values = suggested_field_values(inspection, request.report_type, 20)
values.update(coolingTemperature='25', powerFluctuation='3', encircledEnergy='95.5', fiberCoreDiameter='135', fiberNa='0.22')
request = replace(request, fields={field.key:SelectedReportField(include=field.key not in ('wavelengthCurrentCoefficient','thresholdCurrent'),value=values.get(field.key,'95.5')) for field in SPECTRUM_FIELD_DEFINITIONS})
name = sys.argv[1]
generate_shipping_report(source, out / (name + '.pdf'), request)
pages = render_shipping_report_preview(source, request, page_width_px=1440)
(out / (name + '-preview.png')).write_bytes(base64.b64decode(pages[0]))
print(name)
