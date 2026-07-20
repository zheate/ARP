# Agent Notes

## Combined Test App

- Run from this workspace with the Python environment that can load the scripts runner compiled modules:
  `conda activate sth_eb314`
- Start the combined tester with:
  `python main.py`
- `OceanDirect.dll` is copied into this project at `assets\libs\ocean_direct\OceanDirect.dll`.
- Keep the current working directory at this project root when loading OceanDirect. The compiled OceanDirect wrapper resolves `assets\libs\ocean_direct\OceanDirect.dll` from `os.getcwd()`.

### Code Layout

- `main.py` is the stable operator entry point.
- `combined_test/window.py` owns Qt layout, signal wiring, and operator interactions.
- `combined_test/devices.py` owns hardware loading, device selection helpers, and acquisition threads.
- `combined_test/device_interfaces.py` defines the stable `PowerSupply`, `PowerMeter`, and `SpectrumMeter` ports plus the legacy/TDK power adapter.
- `combined_test/automation.py` owns automatic-test planning and device-independent state transitions.
- `combined_test/automatic_controller.py` owns the Qt-timed automatic-test lifecycle and coordinates the device/store ports.
- `combined_test/record_store.py` defines the `RecordStore` port and owns session/pending/saved record state.
- `combined_test/spectrum.py` owns saturation detection and peak annotation analysis.
- `combined_test/plots.py` owns realtime chart history, scaling, annotations, and render throttling.
- `combined_test/persistence.py` owns background Excel and CSV writes.
- `combined_test/models.py` contains the immutable data passed between those modules.
- `tools/` contains standalone device diagnostics and the legacy CH341 controller.
- `tests/` contains the full test suite.

## UI Card Alignment Requirements

- UI cards must be strictly aligned on a shared layout grid. Cards in the same row must share the same top edge, and cards intended to form a row or column must have consistent widths, heights, gaps, and outer margins.
- Card content must use consistent internal padding. Headings, labels, inputs, dropdowns, buttons, status rows, and chart areas must align to the same left and right content columns; do not use arbitrary offsets or one-off spacing to compensate for misalignment.
- Keep equivalent controls in equivalent positions across device cards. When a card contains a selector and an action button, use the same order, alignment, and spacing everywhere.
- Alignment must remain correct when cards resize, when text or status messages change, and at the supported window sizes and display scaling factors. No card may clip, overlap, or create unintended horizontal overflow.
- Before considering a UI change complete, verify the rendered/live interface visually at the relevant window sizes and check the card edges and control baselines, not only the code or automated tests.

## Tauri Device Settings Dialog Interaction

- The three automatic-test device cards open separate settings dialogs: power supply, power meter, and spectrometer. Keep each dialog tied to the existing shared configuration and Python-owned device commands.
- Locking page scroll while a dialog is open must not change the surrounding layout. Use a stable scrollbar gutter and preserve/restore the body's original overflow and padding; verify that the card and dialog positions are unchanged before and after opening.
- Device resource selectors inside these dialogs must be clickable in the Tauri/WebView runtime. Prefer a local in-dialog dropdown layer anchored to the trigger over a portal-based selector that only receives focus without opening in the target runtime. The dropdown must not change the dialog height when it opens.
- Escape should close an open dropdown first, then close the dialog. Clicking an option must update the shared configuration and close the dropdown without closing the dialog.
- Buttons must not visibly jump when clicked. Do not add a generic active-state translate/press offset such as `translate-y-px`; retain color and focus feedback instead. In particular, the `识别`/device-refresh controls must keep a stable position and width while the refresh command is pending.
- Dialog footer buttons must keep fixed widths and must not reuse the global device-command pending state as a save-progress label or disabled visual state. Only a locally initiated save may change `保存设置` to `保存中…`; clicking `识别` must not change the footer labels, colors, dimensions, or positions. Block concurrent saves in the handler and with accessibility state without visually restyling the footer during an unrelated device command.
- Device commands inside a settings dialog must use an internal command lock to prevent duplicate or concurrent requests without toggling every visible control into a disabled color state. Clicking `识别` must not make the acquisition, zeroing, save, or close controls flash between enabled and disabled styles.
- After changing these interactions, verify all three dialog selectors and at least one `识别` action in the live preview/runtime, including layout stability and Escape/close behavior.

The local spectrometer wrapper is loaded lazily and cached after its first successful import. Device detection and acquisition therefore share one loaded wrapper instead of re-executing the GUI-heavy `spectrometer_mvp.py` module.

Run standalone diagnostics from the repository root with `python -m tools.power_meter_mvp` or `python -m tools.spectrometer_mvp`.

## Device Detection

- The power-supply controller can be switched between the legacy `CH341 I²C`
  path and `TDK (RS-232)` in the Power group.
- TDK-Lambda control follows scripts_runner's serial driver: PyVISA enumerates
  `ASRL...::INSTR` resources and opens the serial link at 9600 baud. It sends
  `ADR 6`, `RMT 1`, `PV`/`PC`, `OUT`, and reads `MV?` / `MC?`.
- Connecting a TDK supply never turns its output on automatically. The operator
  must explicitly enable the output before starting an automatic current test.
- Disconnecting, switching controllers, or closing the app turns an enabled TDK
  output off first. If the close-time command fails, the operator can retry,
  cancel, or explicitly force exit after checking the supply's physical panel;
  the app never reports an unconfirmed output as safely turned off.
- The legacy input-voltage and temperature buttons are disabled in TDK mode:
  input voltage duplicates TDK output voltage, while temperature is not a
  portable query across the supported TDK-Lambda families.

- Power meters are discovered from VISA serial resources whose names start with `ASRL`.
- Supported power-meter probing tries Caihuang first (`$TES`, then optionally `$VER`),
  then LaserPoint (`*SERNU:` at 38400 baud) on ports that did not match Caihuang.
- Detected power meters are shown as either:
  `Caihuang CHLP-P | ASRLx::INSTR | OK...` or
  `LaserPoint | ASRLx::INSTR | SN ...`.
- If multiple power meters are detected, choose the target meter from the Power Meter device combo box before starting the test.
- Spectrometers are discovered through OceanDirect `find_usb_devices()` and `get_device_ids()`.
- The exposed OceanDirect wrapper does not provide model or serial-number methods, so spectrometers are shown as:
  `Ocean Insight | device id <id>`
- If multiple spectrometers are detected, choose the target device id from the Spectrometer device combo box before starting the test.

## Recording

- After setting output current, the app samples power and spectrum data.
- `Start Acquisition` requires an SN and starts a new recording session.
- The session workbook is named `<SN>_YYYY_MM_DD_HH_MM_SS_ffffff.xlsx` in the selected Excel output folder.
- Once power is stable and Vout is read, the point is queued with its current spectrum. `Save Excel` writes all queued points to the workbook's left-side `LIV` area.
- Full spectrum curves are stored in the same worksheet's right-side `Spectra` area, with one wavelength/intensity column pair per current point.
- Both LIV rows and Spectra column pairs are rewritten in ascending-current order on every save.
- `Save Excel` snapshots all queued points, rebuilds the workbook once on an `ExcelSaveThread`, and atomically replaces the target file so the GUI remains responsive during large saves.
- PIB uses trapezoidal spectral-power integration: 974.5-977.5 nm divided by the fixed 956-996 nm pump-laser analysis band.
- SMSR uses `10 * log10(main peak / highest resolved side-mode peak)` within 956-996 nm; saturated or unresolved spectra report no SMSR value.
- Spectrum saturation is flagged when at least 3 consecutive pixels are at or above 16000 counts and within 99.5% of the frame maximum; saturated points show a red warning and are not queued for Excel.
