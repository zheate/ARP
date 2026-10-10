"""Inspect the onefile archive without executing hardware operations."""
import argparse
import hashlib
import json
from pathlib import Path
from PyInstaller.archive.readers import CArchiveReader, pkg_archive_contents

parser = argparse.ArgumentParser()
parser.add_argument("exe", type=Path)
parser.add_argument("dll", type=Path)
parser.add_argument("report", type=Path)
args = parser.parse_args()
archive = CArchiveReader(str(args.exe))
names = pkg_archive_contents(str(args.exe))
forbidden = ("pil", "numpy", "scipy", "pandas", "pyvisa", "nidaqmx", "pyside", "pyqt", "matplotlib", "win32com", "combined_test", "scripts_runner", "tauri_bridge", "oceandirect", "ophir", "webview", "tdk")
unexpected = [name for name in names if name.lower().startswith(forbidden) or any(word in name.lower() for word in ("oceandirect", "webview2", "ophirlm"))]
assert not unexpected, unexpected
for required in ("CH341DLLA64.DLL", "backend", "tkinter", "tkinter.ttk", "controller.ico"):
    assert required in names, required
assert "brand.json" not in names, "Company logo resource was bundled"
assert archive.extract("controller.ico") == args.dll.with_name("controller.ico").read_bytes()
assert archive.extract("CH341DLLA64.DLL") == args.dll.read_bytes()
assert not any(name == "CH341DLL.DLL" for name in names), "32-bit runtime DLL was bundled"
report = {"ok": True, "hardwareOpened": False, "archiveEntries": len(names), "forbiddenModulesFound": unexpected, "dllSha256": hashlib.sha256(archive.extract("CH341DLLA64.DLL")).hexdigest(), "binaryFiles": [name for name in archive.toc if name.lower().endswith((".dll", ".pyd", ".exe"))]}
args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Frozen archive verified: {len(names)} entries, no instrument or WebView2 modules")
