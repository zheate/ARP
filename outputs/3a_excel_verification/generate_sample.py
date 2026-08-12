from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from combined_test.automatic_samples import AutomaticSampleRecorder, AutomaticSecondSample
from combined_test.excel_export import ExcelTestRecord, save_test_records


output_dir = Path(__file__).resolve().parent
sample_path = output_dir / "sample_1s_samples.csv"
workbook_path = output_dir / "sample_three_tables.xlsx"

recorder = AutomaticSampleRecorder(sample_path)
for point_index, current_a in ((4, 3.0), (7, 5.0)):
    for second in range(1, 4):
        recorder.write(
            AutomaticSecondSample(
                point_index=point_index,
                point_elapsed_s=float(second),
                target_current_a=current_a,
                output_current_a=current_a,
                output_voltage_raw_v=20.0 + current_a / 10.0 + second / 10.0,
                output_voltage_v=20.0 + current_a / 10.0 + second / 10.0,
                optical_power_w=current_a * 2.0 + second / 10.0,
                peak_wavelength_nm=975.0 + current_a / 10.0 + second / 100.0,
                centroid_nm=974.9 + current_a / 10.0 + second / 100.0,
                fwhm_nm=0.28 + second / 1000.0,
                spectrum_wavelengths_nm=(974.0, 975.0, 976.0),
                spectrum_intensities=(
                    10.0 + current_a + second,
                    100.0 + current_a + second,
                    20.0 + current_a + second,
                ),
            )
        )
recorder.close()

records = [
    ExcelTestRecord(
        current_a=current_a,
        voltage_v=20.0 + current_a / 10.0,
        power_w=current_a * 2.0,
        efficiency=(current_a * 2.0) / current_a / (20.0 + current_a / 10.0),
        peak_wavelength_nm=975.0 + current_a / 10.0,
        centroid_nm=974.9 + current_a / 10.0,
        fwhm_nm=0.28,
        pib=0.99,
        smsr_db=32.0,
        wavelength=(974.0, 975.0, 976.0),
        intensity=(13.0, 103.0, 23.0),
    )
    for current_a in (3.0, 5.0)
]
save_test_records(workbook_path, records, automatic_samples_path=sample_path)
print(workbook_path)
