# Power Test fully offline deployment

## Status: implementation present; production release still gated

This change adds a manifest-driven offline installer, not a claim that the vendor
media or Windows installation has passed acceptance. The repository excludes
`local_drivers/`; it does not contain the NI offline script/feed, actual Ophir or
Coherent media, or a reviewed deployment lock. Do not invent silent switches,
hashes, package versions, or evidence to get past the gate.

The release engineer prepares the assets on the controlled Windows build machine.
The operator receives only `Power Test_<version>_x64_offline_setup.exe`. The operator
does not prepare a manifest, browse a driver directory, or install vendor packages.

## Architecture

`build_full_windows.ps1` retains the four existing curated media hash pins and
signature checks, calls the existing Windows application build, verifies the
actual MSI Directory/Component/File tables, stages an allowlisted payload, checks
its hashes and signatures, and compiles the outer NSIS installer. Python is used
on the build machine only; it is not an installer prerequisite on the target.

The elevated, 32-bit NSIS wrapper invokes native x64 Windows PowerShell through
`Sysnative`, passing the selected application path as environment data (not
interpolated command-line switches). Windows PowerShell 5.1 runs these modules:

| Module | Responsibility |
|---|---|
| `offline/Common.ps1` | Native argument quoting, synchronous process execution, timeouts, JSONL events, version checks |
| `offline/Payload.ps1` | Manifest/hash verification, protected persistent cache, unresolved-install guard, build-only signature checks |
| `offline/Components.ps1` | WebView2, individual PnP INF staging, reviewed vendor recipes and runtime probes |
| `offline/NiPreservation.ps1` | Preserve installed NI/LabVIEW applications; reuse qualified runtimes without running NIPM |
| `offline/Application.ps1` | MSI contract inspection, path preflight, repair and actual installed component path/hash verification |
| `install_full_bundle.ps1` | Environment/preflight, dependency order, application install, aggregated restart and failure status |

The component order is fixed: `webview2`, `ch341-i2c`, `ch341-serial`,
`ch343-serial`, `ocean-hr4`, `laserpoint-ftdi`, `ni-package-manager`, `ni-runtime`,
`ophir`, `coherent`, followed by the application MSI. There is no operator device
selection. PnP packages are staged even when hardware is disconnected.

## Preparing the reviewed lock — release engineers only

Start with `offline-deployment.example.json`. It is intentionally `approved:false`
and cannot build. Store the completed lock at:

```
local_drivers/current_project/offline-deployment.lock.json
```

`offline_manifest.py` is the authoritative schema validator. All fields below
must describe the ACTUAL vendor assets, not placeholder values.

- `schemaVersion: 1`, `architecture: "x64"`, `minimumWindowsBuild` (integer >=17763),
  `approved: true` and a named `reviewer`. The default target floor is **18363**
  (Windows 10 1909 x64), matching the field computers. Existing NI software is
  allowed; a clean machine is not an installation prerequisite. This target
  definition is not a claim that the actual media has passed Windows 1909 tests.
- `files`: one entry per redistributed file, `{path, sha256, signatureSubject}`.
  Paths are local, relative, case-unique Windows-safe paths. SHA-256 values must
  come from approved source media. `signatureSubject` is a publisher regular
  expression; require a meaningful constraint on every launched vendor EXE.
  Catalog signatures are checked separately. Never auto-approve fresh hashes.
- `archives`: optional `{path, destination}` for ZIPs. Both the archive and EVERY
  extracted file are separately pinned. Unreviewed members, traversal, symlinks,
  case collisions, mismatched hashes and excessive expansion are rejected.
- `licenses`: paths of pinned UTF-8 vendor license texts, concatenated into the
  NSIS consent page. Independently confirm redistribution rights. Consent text
  and a valid Authenticode signature do not grant redistribution rights.
- `components`: exactly the ten IDs above, in that order; no optional omissions.

### WebView2 recipe

`kind: "webview2"`, `path` pointing to the actual
`MicrosoftEdgeWebView2RuntimeInstallerX64.exe`, tested `minimumVersion`,
`timeoutSeconds`, `successExitCodes` and `rebootExitCodes`. The adapter uses the
official `/silent /install` invocation and verifies a machine-wide EdgeUpdate
runtime version. An elevated administrator's per-user runtime does not count as
satisfying another operator account. The Tauri `webviewInstallMode: skip` stays in
place: the outer offline installer owns provisioning.

### PnP recipe

`kind: "pnp"`, nonempty `inf` array containing pinned relative INF paths. Each INF
is passed separately to `System32/pnputil.exe /add-driver <inf> /install`, never
with `/reboot`. Exit 259 is accepted ONLY if the expected INF hash is subsequently
found via structured `Get-WindowsDriver -Online -All` output. Here `-Online`
means the running Windows image, NOT an Internet operation. No hardware scan or
instrument command is used. Windows selects the matching driver according to its
normal ranking; staging alone does not prove a higher-ranked installed driver is
compatible with ARP. Include that case in target acceptance.

### Vendor recipe (NI, Ophir, or Coherent runtime)

Required fields:

```
id, kind: "vendor", path, launcher: "exe" | "powershell64",
arguments: [literal arguments], timeoutSeconds,
successExitCodes, rebootExitCodes,
unattendedVerified: true, offlineVerified: true,
rebootSuppressionVerified: true,
evidence: <pinned local report path>, detect: [probes]
```

Only `{PayloadRoot}` and `{LogRoot}` are deployment argument substitutions. The
application install directory is not a vendor runtime destination. An approved
PowerShell adapter must wait for all child installers and propagate their actual
exit/restart codes. It must not detach work or start a GUI and return success.
The lock must NOT accept 1641 (reboot already initiated) or generalize PnP 259 to
an arbitrary EXE. There is no universal vendor `/S` or `/quiet` fallback.

Supported probes:

- `file-version`: `path` starting with `{System32}/`, `{ProgramFiles}/`,
  `{ProgramFilesX86}/` or `{CommonProgramFiles}/`, `minimumVersion`, optional
  `maximumVersion`. Numeric file-version resource fields are used.
- `registry-version`: HKLM `key` starting `SOFTWARE\`, explicit `view: 32|64`,
  registry `value`, numeric `minimumVersion`, optional `maximumVersion`.
- `com`: `progId: "OphirLMMeasurement.CoLMMeasurement"`. Activates/releases the
  64-bit COM object without calling discovery, acquisition or output methods.
  Requires a local machine-wide registered server. Optional `minimumVersion`
  and `maximumVersion` check that actual server DLL, not an unrelated copied
  library. A COM probe with `minimumVersion` counts as a versioned probe.
- `driver-store`: a pinned relative `inf` path. Requires the matching INF hash
  in Driver Store, also for vendor recipes such as Ophir. Its catalog is checked
  at build time just like a standalone PnP component.

Every vendor recipe needs a versioned probe; Ophir additionally needs the COM
probe. All probes must pass after installation. A return code or file's existence
alone is not proof. Versions above a reviewed maximum are rejected rather than
automatically downgraded. A reboot flag does not waive failed postconditions.

For NI, inspect the actual `06_ni_runtime/install_ni_runtime.ps1` and use a pinned,
reviewed local-only adapter. The review report must establish the complete feed
and transitive dependency closure, exact installed runtime versions, offline NIPM
media, disabled/isolated online sources without destructively deleting another
application's feeds, EULA behavior, no forced restart, and a synchronous exit.
Do not guess feed registration/isolation flags from another NIPM release.

For Ophir, use a legally redistributable runtime/USB package or a verified fully
unattended StarLab installation. Do not assume COM registration plus one DLL is
sufficient; inspect x64 bitness and all native prerequisites. For Coherent, use
verified INF/CAT/SYS or an approved synchronous installer. Neither vendor is
silently skipped just because this checkout lacks its assets.

The pinned evidence report should include media origin/version/hash, vendor
silent-command documentation, clean VM OS/build, network-disconnected install
and reboot tests, process/exit logs, detection results and redistribution review.
An approval flag is an engineer attestation; the validator cannot independently
prove an opaque vendor installer never attempts network IO.

## Existing input layout retained

The build stages `local_drivers/current_project/` into a unique scratch directory.
It extracts the five existing CH341 SFX members to `01_ch341_i2c/driver/`, adds the
unique local `StarLab_Setup.exe` as `07_ophir_starlab/StarLab_Setup.exe`, and adds the
existing Coherent ZIP as `08_coherent/coherent-meter-connection-setup.zip`.
Review required FTDI/Coherent ZIP contents and list any expansion in `archives`.
No member names or installer types are inferred without inspection. Additional
approved redistributables/adapters/evidence/license texts go into the local
curated bundle and inventory. Unlisted files are never shipped to the operator.
The existing curated `verify_bundle.ps1` remains a build-only prerequisite.

## Build and verification commands

On the controlled Windows x64 build machine with the existing `sth_eb314`
environment, all local driver media and NSIS available:

```powershell
npm run check:windows
npm run build:windows:full
```

The full build runs Python packaging regressions and the PowerShell parser /
boundary-mocked tests before compiling. The normal Tauri/realtime/sidecar build
stays unchanged. To use an already generated MSI, specify it explicitly:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build_full_windows.ps1 `
  -SkipApplicationBuild -AppMsiPath 'C:\build\Power Test_0.1.2_x64_en-US.msi'
```

The MSI version must match `tauri-ui/package.json`; the compiled MSI component
layout and installed executable hashes are checked. Reusing an MSI still requires
its corresponding built application/sidecar binaries, never arbitrary old files.
The build does not silently choose the latest stale MSI. Source version remains
0.1.2 until the normal versioning process changes it.

Focused tests:

```powershell
python -m unittest discover -s packaging/tests -p 'test_*.py' -v
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/tests/Test-OfflineScripts.ps1
```

For a staged reviewed payload, `install_full_bundle.ps1 -DryRun` prints the plan
without creating a cache/log or executing component installers. Supply the staged
`-PayloadRoot`, `-AppMsiPath`, `-AppInstallDir`, and the independently verified
`-ExpectedManifestSha256`. DryRun does not establish installation acceptance.

## Path, reboot, upgrade and failure semantics

The application supports local fixed NTFS application subdirectories, including
spaces and Chinese names; drive roots, network shares, reparse points, Windows,
ProgramData and Program Files itself are rejected. Limits are explicit rather
than silently redirecting an unsupported path. System components and user data do
not follow the application path.

The build reads real MSI tables; after install it checks Windows Installer's
registered component paths AND executable hashes against the chosen directory.
MSI remains responsible for application upgrade/uninstall. Same-version repair
at the existing directory is supported; same-version relocation is rejected
before driver installation. To relocate, uninstall only Power Test first, keep
user data/shared dependencies, then reinstall. Cross-version relocation and
rollback still need clean-VM validation; do not advertise them as proven.

The wrapper requests elevation once and uses a native elevated child process.
It does not automatically launch the application as administrator. All recognized
restart codes aggregate into one final 3010/finish-page decision. If a vendor
requires an intermediate restart, that recipe is NOT acceptable under the
one-final-restart contract until a verified alternative or resume design exists.
No component is allowed to force a restart.

`InstallerLogs/<run>/deployment.jsonl` contains commands, versions, timestamps,
exit codes, reboot state and verification. Captured component stdout/stderr and
MSI logs are adjacent. A mutex rejects concurrent Power Test setup runs. Never
kill a running Power Test or instrument-control process for an upgrade.

The verified installer cache is persistent and protected for Administrators and
SYSTEM. On timeout/interruption, `process-state.json` marks unresolved work and a
subsequent setup refuses to overlap it. Killing an MSI client is not a rollback.
Maintenance must inspect the named process, Windows Installer state and logs
before clearing the marker; do not remove a cache an active process may use.
Shared drivers/runtimes are not automatically uninstalled on failure or app
uninstall. Successful components are detected on a normal retry.

## Offline boundary and release acceptance

Build machines may obtain/review assets online. Targets never run Python/npm/pip/
cargo/Conda or download missing prerequisites. The outer executable contains all
payload bytes; an embedded manifest hash anchors inventory and MSI verification.
Authenticode chain/revocation checks run on the build machine; Windows still
performs its normal PnP catalog validation on the target. Do not disable driver
signing, revocation policy or security controls to force installation through.

Sign the final outer EXE using the organization's trusted signing process. The
printed sidecar SHA-256 file is an integrity aid, not a substitute for trusted
code signing. Build output is a candidate, not automatically approved production
media. The single-EXE size budget is <2 GiB; oversized payloads must be reviewed,
not silently changed into a multi-file or online installer.

"Works fully disconnected" is not the same as "the entire operating system
attempted zero network requests." Evergreen WebView2, Windows certificate checks
and opaque vendor services can have their own behavior. Static URL/command lint
cannot prove their behavior. Do not claim a packet-capture result without one.

Release acceptance must cover the field baseline: disconnected Windows 10 1909
(build 18363) or later x64, including existing NI software, with a standard
operator account. Do not require operators to reinstall Windows or remove NI
merely to make a target eligible. Test both an existing-software installation
and a missing-prerequisites baseline; the latter establishes that the offline
payload is complete, not an operator prerequisite.

The user requires existing LabVIEW/NI applications to remain usable. Before any
component installation, `Assert-NiPreservation` checks the existing NI stack.
When the reviewed NI runtime probes pass, both NI and NIPM setup are bypassed.
When existing NI has no qualified reuse path, the entire deployment stops before
vendor changes. Do not automatically upgrade, remove, downgrade or declare the
legacy stack compatible just to proceed. The standalone NI candidate also refuses
changes when it detects an existing NI runtime/application; simulation stays
read-only. NI 23.5 DAQmx remains a candidate for machines without an existing NI stack.

The field report collected at `2026-09-15T09:37:36.7571006Z` confirms an x64
`nicaiu.dll` with all 14 selected exports; its file version `2.8.0f0` is a C API
component version, not evidence that the installed DAQmx product is version 2.8.
The report did not find `System32/visa64.dll`, a native NI-VISA version key or
machine-wide WebView2 registration. It is still not an actual runtime load test.

Serial communication now has a bundled ASRL implementation: PyVISA-py 0.8.1 and
PySerial 3.5. Available native VISA and explicit `PYVISA_LIBRARY` selections retain
their original paths. Only default native library discovery/loading failures
select the local serial implementation; live native I/O failures are propagated.
The bundled implementation enumerates local COM ports only and rejects network,
USB and GPIB resource opens. CH341/CH343/FTDI/Coherent USB serial drivers remain
required. The NI candidate no longer installs NI-VISA just for ASRL communication.

`arp-python ... --check-runtime --output report.json` loads the actual packaged
Python dependencies and queries only the installed DAQmx version API. It never
starts HeadlessBackend, enumerates devices, creates a DAQ task or opens ports.
The optional `--imports-only` mode skips loading the system DAQmx DLL; the Windows
build runs that check against its actual frozen sidecar before creating the MSI.
Metadata for `nidaqmx` and `nitypes` is explicitly included, because these packages
read their installed distribution versions during import.

NI-DAQmx/VISA 23.5 list Windows 10 support, but their published LabVIEW support
starts at 2020/2020 SP1, not 2011. The bundled Python nidaqmx is 1.4.1. The supplied
DAQmx 9.7 machine still requires runtime/ABI validation and actual ARP plus
legacy-application acceptance before a reuse profile can be approved.
`Collect-OfflineTarget.ps1` now records x64 NI DLL versions, hashes and selected
export availability without loading them. Export names alone do not establish
supported ABI, successful runtime loading or acquisition compatibility.

The manifest approval gate remains in force for all target classes:

1. Install to default, alternate-drive, spaces and Chinese paths. Verify actual
   MSI component paths, shortcuts, unprivileged startup and offline activation.
2. Preinstall a different/newer runtime, repeat/repair, upgrade, inject a failure,
   verify restart aggregation and that no unrelated application loses its feeds
   or shared drivers. Exercise outstanding restart/installer states.
   Include the supplied Windows 18363.592 + DAQmx 9.7/LabVIEW 2011 baseline.
   Preserve a recoverable copy of that test environment and verify its existing
   measurement programs after deployment without changing its NI stack. Do not declare that legacy driver
   sufficient for ARP without the required runtime and acquisition checks.
3. Install without instruments, reboot, then connect each supported device and
   perform separately authorized hardware acceptance. Driver staging and mocks
   are not evidence of real acquisition or safe device shutdown.
4. Corrupt/remove each required payload, catalog or NI dependency. Installation
   must stop with a named component/error and never fall back to a download.
5. Observe network attempts in an isolated controlled network in addition to
   disconnected success, validate trust-chain behavior and keep logs as evidence.

Until the actual media/recipes and these Windows checks pass, the project must
report the missing components and must NOT call the offline release complete.

## Primary technical references

- Microsoft WebView2 distribution: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution
- Microsoft PnPUtil return values: https://learn.microsoft.com/en-us/windows-hardware/drivers/devtest/pnputil-return-values
- Windows Installer component-path verification: https://learn.microsoft.com/en-us/windows/win32/msi/installer-componentpath
- NI-DAQmx 2023 Q3 OS/application support: https://www.ni.com/pdf/manuals/ni-daqmx-2023-q3.html
- NI-VISA 2023 Q3 OS/application support: https://www.ni.com/pdf/manuals/ni-visa-windows-2023-q3.html

These explain platform mechanisms, not the silent-install behavior of the local
NI/Ophir/Coherent package versions. That evidence must come from their actual
reviewed media and target-machine tests.
