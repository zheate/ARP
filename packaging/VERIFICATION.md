# Verification for the offline installer source change

## Windows follow-up — 2026-09-15

The second field report, serial transport adaptation, frozen NI dependency fix,
47 focused passing checks and rebuilt artifacts are recorded in
`FIELD_COMPATIBILITY_2026-09-15.md`. The actual frozen EXE passed on the build
host. The spare-PC report received on 2026-09-16 also confirms that the frozen
program loaded DAQmx 9.7.0 and the embedded serial backend. Actual acquisition,
old LabVIEW operation and installed application startup remain unverified.

The Linux results below describe the original source change. The subsequent
Windows work is recorded in `WINDOWS_FOLLOWUP_2026-09-15.md`:

- Windows build preflight and the actual Rust/PyInstaller/Tauri/MSI build passed.
- Python packaging regressions after the field-target/probe changes: 33 passed,
  1 skipped (symlink privilege).
- PowerShell parser/boundary tests and new real Windows Installer COM tests passed.
- Runtime compatibility mocks passed: reuse sufficient installed versions,
  install missing/old versions, verify results and refuse unreviewed downgrades.
- Default target floor adjusted to Windows build 18363. The user subsequently
  required preservation of existing LabVIEW/NI applications. A new preflight
  blocks all deployment before vendor changes when the existing NI stack has
  no qualified reuse path. Qualified NI bypasses both runtime and NIPM setup.
- NI preservation, registered COM version/activation, Driver Store probes and
  static PE export inventory tests passed in native Windows PowerShell 5.1.
- WebView2 embedded runtime version 151.0.4129.86 was identified. NI-VISA's
  dedicated x64 registry version and DAQmx numeric file version were checked.
- StarLab 3.80 x64 COM 10.4.0.0 and VC++ 14.22.27821.0 were extracted and their
  signatures checked. The documented redistributable adapter passed PlanOnly;
  no COM registration, driver installation or acquisition was performed.
- Ophir's bundled license.rtf contains an InstallShield template, not vendor
  terms; it is excluded from the working license inventory.
- The fresh MSI passed the actual INSTALLDIR/main-executable/sidecar contract
  after fixing COM void output leaking null records from `Read-MsiRows`.
- Local vendor binary/signature and NI feed static checks completed; this is
  not a clean-machine installation result.
- NI adapter simulation on this already configured host returned "Packages are
  already installed". Clean disconnected installation remains unverified.
- No full offline NSIS release EXE, installed-path acceptance, real hardware
  acceptance or signed release has been produced by this follow-up.

The working local inventory is `offline-deployment.review.json`, deliberately
unapproved. It is not the production `offline-deployment.lock.json`.

## Executed

Environment: Linux x86_64, Python 3.13. No vendor installer was executed.

```
python -m unittest discover -s packaging/tests -p 'test_*.py' -v
Ran 30 tests
OK

python -m py_compile packaging/offline_manifest.py packaging/tests/test_offline_manifest.py
Completed successfully

python packaging/offline_manifest.py --lock packaging/offline-deployment.example.json
Exit 1, as required: Deployment lock is NOT approved
```

Tests cover schema/order/completeness, reviewed vendor metadata, mandatory version
and Ophir COM probes, invalid paths, duplicate names/JSON keys, bootstrapper
substitution, exit-code classification, corrupt/missing payloads, symlink escapes,
ZIP traversal/unreviewed members/member hashes, allowlist staging and network
command lint. Fixtures contain dummy bytes, not actual vendor binaries.

## Added, but NOT executed in this environment

- Windows PowerShell parser and boundary-mocked tests in `Test-OfflineScripts.ps1`.
- The full Windows DryRun with a real, reviewed complete payload.
- Windows packaging preflight and Tauri/MSI/NSIS/PyInstaller release builds.
- Frontend and Rust test suites; those application sources were not changed.
- Real installation to default/alternate-drive/Chinese paths, repair and upgrade.
- Disconnected Windows 10/11 installation, restart and trust-chain behavior.
- Actual NI/Ophir/Coherent silent-install validation and all hardware acceptance.

Windows PowerShell, Conda, Rust, NSIS and the private local vendor media are not
available in the execution environment. The PowerShell checks are wired into the
full Windows build; their presence in source does not mean they have passed.

## Release blockers

The repo intentionally excludes `local_drivers/`. Complete the reviewed lock,
media inventory, licenses, exact NI feed/installer procedure, Ophir redistributable
or StarLab silent recipe, Coherent INF/runtime recipe, and vendor verification
reports on the controlled build machine. Do not change the example approval flag
merely to bypass a gate. Then execute the Windows acceptance matrix in
`OFFLINE_DEPLOYMENT.md` and sign the final EXE.

No final installer EXE was generated during this source change. No final EXE size,
SHA-256 or clean-target installation success is asserted here.
