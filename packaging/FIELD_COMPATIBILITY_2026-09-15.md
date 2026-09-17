# Existing NI field compatibility — 2026-09-15

## Packaged runtime result received — 2026-09-16

The returned `runtime-compatibility.json` reports Python 3.14.6, AMD64,
64-bit pointers, `frozen=true` and `driverQueryRequested=true`. The actual
packaged program successfully queried DAQmx **9.7.0** and initialized
`SerialVisaLibrary`. No ports were opened and no discovery was performed.
The companion log contains the report path and no exception.

Received file SHA-256 values:

- JSON: `28A1CAAD9FF14A3E00862C94926A54B248FDF82A9D09D6BCBA1C70E89B201A0D`
- Log: `5E74F6F19509221A3683C01FF92E0709A4088244FEF020F6EBC91E6973D86988`

This closes the pending packaged-load check for the reported spare computer.
The report does not contain OS identity or an executable hash, so its association
with the prior Windows 18363 inventory and supplied kit comes from the user's
test workflow, not an independently recorded identity match.
The result does not establish acquisition or old-application compatibility:
both acceptance fields are explicitly false (not tested, not reported failures).
The user is unsure whether a DAQ device and the old application are available.

Source inspection confirms that PD acquisition additionally creates a task,
configures voltage channels and sample-clock timing, starts acquisition and reads
samples (`tools/pd_daq_mvp.py`). The version-only check exercises none of those
operations. Keep the existing NI preservation gate and modern-runtime version
probe unchanged; do not approve legacy reuse solely from this report.

Next: application/WebView2 installation and startup qualification using
`LEGACY_APP_ACCEPTANCE.md`. No additional inventory collector is required.
Device acquisition and legacy LabVIEW acceptance remain pending availability.

Prepared `release/qualification/Legacy-App-Acceptance-2026-09-16.zip`,
296,603,097 bytes (about 283 MiB), SHA-256
`62CB1DB231FA5620E4383FD3D37A302C08F1E918D1414DA5AB48E7327CBAAC12`.
It contains the existing serial-compatible application MSI, the full offline
WebView2 installer, instructions and input hashes. Both installer hashes match
the previously pinned artifacts; ZIP integrity and member hashes passed.
WebView2's Microsoft Authenticode signature is valid on this build host.
No installer was executed during assembly. No NI installer is included.
The release lock remains absent and the working inventory remains unapproved.

## Report received

The v3 inventory collected at `2026-09-15T09:37:36.7571006Z` describes Windows
10 Pro x64 `18363.592`, with existing LabVIEW 2011 / DAQmx 9.7 entries:

- `System32/nicaiu.dll` exists, PE machine `0x8664`, all 14 selected exports
  present. File version `2.8.0f0`; SHA-256
  `951643650B02CB53F5B6DCF3E9B8D65F5A85ED85642A422E4EF2BA5897454332`.
  This is a component file version, not the actual DAQmx product/API version.
- `System32/visa64.dll` and native NI-VISA version registration were not found.
  The inventory is not an exhaustive search for every possible VISA library.
- Machine-wide WebView2 registration was not found. Per-user/runtime API
  availability was not tested by this report.
- No inventory errors or pending-restart indicators were reported.

No actual DLL loading or hardware operation occurred during that inventory.
The user requires the old applications to keep working, so automatic NI
upgrades/removal remain prohibited. Do not lower the modern NI file-version
probe to 2.8 or treat matching export names as runtime qualification.

## Implementation

`tools/visa_session.py` preserves explicit VISA configuration and available
native VISA. When the default native library cannot be discovered/loaded, it
uses the bundled `tools/serial_visa.py` implementation. Native runtime I/O errors
are propagated instead of silently changing transports.

The bundled implementation uses PyVISA-py 0.8.1 / PySerial 3.5, restricts resource
enumeration to local serial ports and only opens `ASRL<number>::INSTR` names.
It never dispatches network/USB/GPIB discovery. Existing command framing,
timeouts and the shared resource-manager lifetime are retained. The NI candidate
now installs DAQmx only on an absent-NI baseline; NI-VISA is unnecessary merely
to supply serial transport. A qualified legacy DAQmx reuse profile is pending.

The real sidecar has a `--check-runtime --output <json>` diagnostic entry,
handled before importing the live backend. It imports its bundled dependencies,
initializes the embedded serial manager without enumerating/opening ports and
queries only the installed DAQmx version. It never creates a DAQ task.
`--imports-only` skips the installed driver query for build hosts without NI.

An actual frozen-program test caught missing `nitypes` package metadata, even
though source imports and MSI building succeeded. Explicit `nitypes` and
`nidaqmx` metadata inclusion fixes that failure. The build now checks frozen
imports before creating an MSI.

## Verification

- 47 focused Python checks passed: transport selection/error handling, virtual
  PySerial loopback with Caihuang/Coherent/LaserPoint framing and timeout/close
  behavior, existing TDK/Coherent/reconnect tests, diagnostic entry isolation.
- Native Windows PowerShell 5.1 packaging parser and boundary checks passed.
- Actual PyInstaller, frontend, Rust/Tauri and MSI rebuild succeeded.
- Frozen imports-only check passed; actual frozen DAQmx query on this build host
  returned `23.5.0`. Embedded serial manager loaded without opening ports.
- Fresh MSI COM inspection confirms application and sidecar under INSTALLDIR.
- NI DAQ-only simulation was a no-op on the already configured build host.
- ZIP integrity and equality with the exact rebuilt sidecar were verified.

The build-host checks above were not Windows 18363/DAQmx 9.7 load results; the
subsequent returned load report is recorded at the top of this document.
Neither set of checks establishes hardware acceptance, installed
application path/repair tests or proof that old LabVIEW applications still work.
The deployment review inventory remains unapproved; no full release EXE exists.

## Artifacts

`release/qualification/Legacy-Runtime-Check.zip` (80,243,284 bytes):
SHA-256 `1A3881FEA1ED5A81BD41924A8F0586EDA86C890BAF1A71FF58348F13CF16809E`.
Contains the actual sidecar, `Check-InstalledRuntime.cmd` and instructions.
The spare-PC CMD run and returned reports were received on 2026-09-16;
see the result above. No repeat run is needed for the same artifact/environment.

Rebuilt application-only MSI (83,931,136 bytes):
`release/qualification/Power Test_0.1.2_SERIAL_COMPAT_APPLICATION_ONLY.msi`.
SHA-256 `BEF2C1E6F93A9A1A2EE3AF98D2CDE6366E6232023BF13D84CD0DEC46AE492B95`.
This does not contain the complete offline prerequisite/driver bundle.

The previous application-only MSI remains a historical qualification artifact;
it predates the serial transport and NI Python metadata fixes.

Primary transport dependency reference:
https://pyvisa.readthedocs.io/projects/pyvisa-py/en/latest/installation.html
