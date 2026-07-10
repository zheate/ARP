# Agent Notes

## Combined Test App

- Run from this workspace with the Python environment that can load the scripts runner compiled modules:
  `conda activate sth_eb314`
- Start the combined tester with:
  `python combined_test_mvp.py`
- The scripts runner root is expected at:
  `E:\scripts_runner - 副本`
- The combined tester adds the scripts runner root to `sys.path[0]` before loading OceanDirect modules because `application/__init__.py` depends on that layout.
- `OceanDirect.dll` is copied into this project at `assets\libs\ocean_direct\OceanDirect.dll`.
- Keep the current working directory at this project root when loading OceanDirect. The compiled OceanDirect wrapper resolves `assets\libs\ocean_direct\OceanDirect.dll` from `os.getcwd()`.

## Device Detection

- Power meters are discovered from VISA serial resources whose names start with `ASRL`.
- Supported power-meter probing currently uses the Caihuang protocol: send `$TES`, then optionally `$VER`.
- Detected power meters are shown as:
  `Caihuang CHLP-P | ASRLx::INSTR | OK...`
- If multiple power meters are detected, choose the target meter from the Power Meter device combo box before starting the test.
- Spectrometers are discovered through OceanDirect `find_usb_devices()` and `get_device_ids()`.
- The exposed OceanDirect wrapper does not provide model or serial-number methods, so spectrometers are shown as:
  `Ocean Insight | device id <id>`
- If multiple spectrometers are detected, choose the target device id from the Spectrometer device combo box before starting the test.

## Recording

- After setting output current, the app samples power and spectrum data.
- `Start Acquisition` requires an SN and starts a new recording session.
- The session workbook is named `<SN>_YYYY_MM_DD_HH_MM_SS.xlsx` in the selected Excel output folder.
- Once power is stable and Vout is read, the point is queued with its current spectrum. `Save Excel` writes all queued points to the workbook's left-side `LIV` area.
- Full spectrum curves are stored in the same worksheet's right-side `Spectra` area, with one wavelength/intensity column pair per current point.
- Both LIV rows and Spectra column pairs are rewritten in ascending-current order on every save.
- `Save Excel` snapshots all queued points, rebuilds the workbook once on an `ExcelSaveThread`, and atomically replaces the target file so the GUI remains responsive during large saves.
- PIB uses `scipy.signal.medfilt(intensity)` and the default 974.5-977.5 nm band (976.0 +/- 1.5 nm).
- Spectrum saturation is flagged when at least 3 consecutive pixels are at or above 16000 counts and within 99.5% of the frame maximum; saturated points show a red warning and are not queued for Excel.
