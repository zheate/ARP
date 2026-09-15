# Verification for the offline installer source change

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
