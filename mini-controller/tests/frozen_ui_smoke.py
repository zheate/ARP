"""Open the packaged window and close it without scanning/connecting hardware."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
ctypes.windll.shcore.SetProcessDpiAwareness(1)
assert not user32.FindWindowW(None, "CH341 Mini Controller"), "Close the existing Mini before verification"
process = subprocess.Popen([str(ROOT / "release/ch341-mini/CH341Mini.exe")])
window = None
try:
    deadline = time.monotonic() + 15
    while not window:
        window = user32.FindWindowW(None, "CH341 Mini Controller")
        assert process.poll() is None, "Packaged app exited before showing its window"
        if time.monotonic() > deadline:
            raise RuntimeError("Packaged app window timed out")
        time.sleep(0.1)
    user32.SetWindowPos(window, wintypes.HWND(-1), 40, 40, 0, 0, 0x0001 | 0x0040)
    time.sleep(0.25)
    rectangle, point = wintypes.RECT(), wintypes.POINT()
    assert user32.GetClientRect(window, ctypes.byref(rectangle))
    assert user32.ClientToScreen(window, ctypes.byref(point))
    screenshot = Path(os.environ["TEMP"]) / "codex-shot-mini-packaged.png"
    helper = Path.home() / ".codex/skills/screenshot/scripts/take_screenshot.ps1"
    region = f"{point.x},{point.y},{rectangle.right},{rectangle.bottom}"
    subprocess.run([shutil.which("pwsh"), "-NoProfile", "-File", str(helper), "-Path", str(screenshot), "-Region", region], check=True, capture_output=True, timeout=30)
    user32.PostMessageW(window, 0x0010, 0, 0)
    assert process.wait(timeout=10) == 0
    report = {"ok": True, "hardwareActions": "No scan, connection or output command", "screenshot": str(screenshot), "clientSize": [rectangle.right, rectangle.bottom]}
    (ROOT / "build/ch341-mini/frozen-ui-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
finally:
    if process.poll() is None:
        if window:
            user32.PostMessageW(window, 0x0010, 0, 0)
        process.wait(timeout=10)
