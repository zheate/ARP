# Windows offline packaging follow-up — 2026-09-15

## Result

The v3 target report has now been received. See
`FIELD_COMPATIBILITY_2026-09-15.md` for the resulting serial transport change,
actual frozen dependency fix and newly built runtime qualification kit. Its
next action supersedes the earlier request below to run the static v3 collector.

### Latest constraint: existing NI applications must remain usable

The user explicitly confirmed that LabVIEW 2011 / DAQmx 9.7 must continue to
work. This supersedes the earlier candidate upgrade approach below. Deployment
now preflights NI before any vendor action: qualified installed NI is reused
without NIPM; an unqualified existing stack is never changed automatically.
NI 23.5 is only a candidate for machines with no NI runtime/application stack.
The actual DAQmx 9.7 reuse profile is not yet qualified.

Further preparation completed:

- WebView2 runtime `151.0.4129.86` identified inside the signed offline EXE's
  LZMA/Omaha manifest; updater version `1.3.255.5` is not the runtime version.
- NI-VISA uses native HKLM `SOFTWARE\National Instruments\NI-VISA`,
  `CurrentVersion=23.5.0`; DAQmx numeric DLL version is `23.5.0.49381`.
- StarLab 3.80 InstallScript media extracted without running its installer.
  A locally built unshield from the author's source extracted 205 CAB members.
  The packaged x64 Redistributable contains signed COM `10.4.0.0`, matching
  the installed-application COM DLL exactly, plus Boost 1.72, driver catalogs
  and firmware. Bundled VC++ x64 `14.22.27821.0` has a valid Microsoft signature.
- COM manual revision 13 documents the independent x64 redistribution path,
  `regsvr32 /s` and `swapInf StarlabDrivers --silent`. `StarlabDrivers` is a mode
  token; the executable contains the `ophdrivers` directory reference. Do not
  reinterpret it as a missing folder. The manual requires USB disconnection.
- `vendor/Install-OphirRuntime.ps1` implements the candidate sequence, runtime
  version reuse, local persistent copies, COM registration and Driver Store
  postconditions. PlanOnly passed on real media; actual installation is untested.
- The extracted license.rtf is an InstallShield authoring placeholder. It is
  excluded from the draft license inventory; real vendor terms are still needed.
- Python manifest checks: 34 discovered, 33 passed, 1 skipped (symlink privilege).
  Windows PowerShell 5.1 parser, MSI COM, runtime reuse/probe, NI preservation,
  real PE inventory and malformed-file tests passed.

Next evidence: run `release/qualification/Offline-Target-Check-v3.zip` on the
spare computer and return its JSON. This records the real x64 NI DLL versions,
hashes and selected exports absent from the first report. It never loads those
DLLs, opens instruments or changes NI. Presence of exports does not prove
runtime/acquisition compatibility; those remain separate acceptance steps.

The application MSI now builds on this Windows x64 host. A real COM interop
failure in the MSI inspection stage was fixed. A complete offline deployment
EXE is still blocked by vendor qualification and the approved lock.

No device scan, acquisition, vendor installation, system feed change or reboot
was performed. The user has a spare Windows computer for later qualification;
its baseline was subsequently inspected below.

## Field-target adjustment after the report

The user clarified that Windows 18363 with existing NI software is the normal
deployment environment. The default example and the local working inventory now
use `minimumWindowsBuild: 18363`. The earlier 19045 rejection below is historical,
not the current target policy. Existing NI/LabVIEW entries do not disqualify a
computer or require clearing its software before installation.

The candidate NI adapter now uses the documented `--install-also-upgrades`
option and resolves a `--simulate` transaction before actual installation.
Neither downgrades, forced uninstalls nor ignored compatibility errors are
enabled. Existing runtimes meeting all reviewed probes continue to be reused.
New PowerShell regressions cover reuse of equal/newer versions, installation of
missing/old versions, failed postconditions and refusal of unreviewed downgrades.
Python packaging regressions now discover 31 tests: 30 passed and one skipped
for symlink privilege. These checks do not validate an actual NI 9.7 upgrade.

Acceptance now explicitly includes the supplied legacy field environment and
missing-prerequisite installation. The former is the normal target; the latter
checks payload completeness. NI's published DAQmx/VISA 23.5 Windows 10 support
does not establish compatibility with LabVIEW 2011. Old application workflows,
the packaged WebView2 runtime, ARP startup and hardware operations still need
target tests. The manifest remains unapproved and no full offline EXE exists.

## Checks and artifacts

| Check | Result |
| --- | --- |
| `npm run check:windows` | Passed, `sth_eb314` environment |
| `build_tauri_windows.ps1 -BundleTargets msi` | Rust realtime DLL, Python sidecar, frontend, Rust desktop and MSI built |
| Python packaging tests | 30 discovered, 29 passed, 1 skipped because symlink creation is not permitted |
| `Test-OfflineScripts.ps1` | Passed under Windows PowerShell 5.1 |
| `Test-MsiContract.ps1` | Real COM database queries, empty results, accepted layout, rejected version and wrong sidecar directory passed; no installation |
| Fresh MSI contract | Both `tauri-ui.exe` and `arp-python.exe` are component key paths directly under INSTALLDIR |
| Curated `verify_bundle.ps1` | Passed, 62 NI packages and valid WCH/NI installer signatures |
| NI index audit | 62 package sizes and MD5 values match index; all declared Depends/Pre-Depends/Eula names are present |
| NI candidate adapter | Custom config lists only the local feed; simulate returns already installed/no operation on this host |

The MSI is copied to:

`release/qualification/Power Test_0.1.2_APPLICATION_ONLY.msi`

- Bytes: `83668992`
- SHA-256: `C2BA5E685E203DEF45D24F18ED011C4303482E000494155532F8D0E11EF3688D`
- This MSI contains the application only. It does not satisfy the single offline
  EXE requirement and is not approved for distribution.
- Build warnings include missing optional hidden import `scipy.special._cdflib`
  and a frontend chunk size warning. Application startup and report workflows
  were not exercised in this follow-up; build success is not runtime acceptance.

Local detailed evidence is under `build/offline-audit/`: application-build.log,
powershell-tests.log, vendor-binaries.json, ni-feed-audit.json,
coherent-contents.log, coherent-catalogs.json, ni-adapter-simulation.log and
build-host-readiness.json. These ignored local files are not GitHub assets.

## Source fix

Windows Installer's `View.Execute()` and `View.Close()` emitted null pipeline
objects. `Read-MsiRows` therefore returned nulls alongside its row objects,
causing strict-mode `Values` property failures in `Get-ApplicationContract`.
Explicit `[void]` suppression fixes both nonempty and empty queries. The new
regression creates a temporary real MSI database and never installs it.

## Actual vendor media

| Media | Observed result | Remaining boundary |
| --- | --- | --- |
| NIPackageManager24.0.0.exe | 24.0.0f168; valid NI signature; original pinned hash matches | Silent clean installation, child-process completion and restart behavior |
| StarLab_Setup.exe | 3.80.0000; valid Ophir signature; original pinned hash matches; InstallShield container | Exact unattended recipe, prerequisites and version probe |
| Coherent CMC ZIP | Original pinned archive matches; inner EXE 1.4.2.3 has valid Coherent signature | Redistribution review and target driver acceptance |
| Coherent contents | Inno Setup 5.6.0; bundled .NET 4.8 installer and device drivers found | Extracted PowerMax USB/Pro INF/CAT/SYS candidates are in current_project/08_coherent/driver; CAT signatures valid; no PnP installation attempted |
| WebView2 | Valid Microsoft signature; original pinned installer hash matches | Pin embedded WebView runtime version; EXE version 1.3.255.5 is updater metadata |
| Older Ophir redistribution tree | x64 COM DLL, dependencies, registration script and driver INF present in StarLab 3.31 tree | Not the same release as StarLab 3.80; do not silently substitute it |

The working inventory is:

`local_drivers/current_project/offline-deployment.review.json`

It records 165 observed files and their SHA-256 values. `approved` remains false,
the reviewer is empty, vendor verified flags remain false, and unresolved
version/evidence fields are explicit. The validator rejects it as expected.
Hashes record observed bytes; they do not establish redistribution permission
or clean-machine acceptance. WCH package variants still need selection review.

## NI adapter candidate

`packaging/vendor/Install-NiRuntime.ps1` uses the inspected NIPM 24.0 CLI:

- Writes a new per-run config under the caller's log directory.
- Uses `--config` on every CLI invocation, a local feed only, and
  `enable-NI-connectivity=false`; does not remove/edit existing machine feeds.
- Pins DAQmx core 23.5.0.49391-0+f239 and VISA runtime 23.5.0.49318-0+f166.
- Waits for CLI exit and maps candidate NI reboot-required status -125071 to
  3010. Windows PowerShell native stderr is classified by the exit code.
- Provides `-Simulate`; this host's installed packages make simulation a no-op.
- Remains unapproved until clean-target installation and reboot tests pass.

The legacy local NI script was preserved; the candidate copy is
`06_ni_runtime/install_ni_runtime_offline.ps1`.

Do not use `visa64.dll` version 7.2.7619.0 as proof of NI-VISA Runtime 23.5: it is
a shared VISA API binary on this host. The NI-VISA-specific registry probe was
subsequently identified as recorded above. Package-name presence in the feed is not a full dependency
solver, installer, trust-chain or disconnected-network test.

## Next target-machine action

### Spare computer report received — 2026-09-15

The report collected at `2026-09-15T08:36:19.1474543Z` was reviewed:

- Windows 10 Pro x64, build `18363.592`.
- At the time of this initial assessment the manifest required build `19045`;
  the target would have been rejected. The subsequent user-directed target
  adjustment above changed the default floor to `18363`.
- 275 relevant uninstall entries, including 252 NI-prefixed entries. These are
  registry entries, not 252 separate user-facing applications. Examples include
  NI-DAQmx 9.7.0 and LabVIEW 2011. This is not a clean vendor-runtime baseline.
- Fixed NTFS volumes C: and D: have approximately 45.1 GiB and 803.7 GiB free.
- No pending-restart indicators or inventory errors were reported by the
  collector. This inventory is not an exhaustive installer-state test.
- `machineWebView2Version` was an empty object, not a valid version string;
  runtime availability cannot be established from that field.
- Physical disconnection and successful offline installation are unverified.

Result: this computer is an existing-software field-installation/upgrade target,
not evidence of a fresh installation. Preserve a recoverable baseline and use
it for the legacy NI compatibility matrix; no OS reinstall or blanket NI cleanup
is required. No installation or OS changes have been made on the spare computer.

Copy `release/qualification/Collect-OfflineTarget.ps1` to the spare computer and
run it with Windows PowerShell. It writes `offline-target-readiness.json` next
to itself, recording Windows build/architecture, relevant installed components,
fixed NTFS drive availability and pending restart indicators. It does not
install anything, activate instrument COM objects or scan instruments. It does
not infer that the machine is clean or disconnected from this inventory alone.

After the baseline is known, qualify the real vendor installation recipes and
complete the reviewed lock. Then build the full NSIS candidate and execute the
path/repair/upgrade/restart/offline/hardware matrix in OFFLINE_DEPLOYMENT.md.
Do not claim final delivery until those results are recorded.

## Primary references inspected

- NI offline feed preparation and dependency download:
  https://knowledge.ni.com/KnowledgeArticleDetails?id=kA03q000000YGejCAG
- NI unattended NIPM installation and reboot-required status:
  https://knowledge.ni.com/KnowledgeArticleDetails?id=kA00Z000000g18jSAA&l=en-MY
- NIPM 24.0 local `help install`, `help config-set`, `help get-dependencies`,
  `config-get` and per-run `feed-list` outputs are saved in the local audit folder.
- InstallShield command options vary by project type; generic switches do not
  prove StarLab prerequisites are unattended:
  https://docs.revenera.com/installshield/helplibrary/IHelpSetup_EXECmdLine.htm
- The Coherent extraction tool was obtained from its author's site and its
  published archive MD5 checked: https://constexpr.org/innoextract/
  The tool was used only on the build machine and is not part of the payload.
