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
- Once the power is stable for the configured window and tolerance, the app records output current, output voltage, power, wavelength statistics, and a path to the full spectrum CSV.
- Full spectrum curves are saved as `wavelength_nm,intensity` CSV files in a sibling `<main_csv_name>_spectra` directory.
