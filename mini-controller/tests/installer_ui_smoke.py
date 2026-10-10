"""Check the NSIS directory page only; never click Install or run a driver."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
api = ctypes.WinDLL("user32", use_last_error=True)
callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
api.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
api.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
api.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
api.IsWindowVisible.argtypes = [wintypes.HWND]
api.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
api.SendMessageW.restype = wintypes.LPARAM
api.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
api.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
api.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
api.GetDlgItem.restype = wintypes.HWND
api.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
ctypes.windll.shcore.SetProcessDpiAwareness(1)


def text(window):
    value = ctypes.create_unicode_buffer(api.SendMessageW(window, 0x000E, 0, 0) + 1)
    api.SendMessageW(window, 0x000D, len(value), ctypes.cast(value, ctypes.c_void_p).value)
    return value.value


def children(window):
    found = []
    def visit(handle, _):
        if api.IsWindowVisible(handle):
            name = ctypes.create_unicode_buffer(80)
            api.GetClassNameW(handle, name, len(name))
            found.append((handle, name.value, text(handle)))
        return True
    api.EnumChildWindows(window, callback_type(visit), 0)
    return found


def windows(pid):
    found = []
    def visit(handle, _):
        owner = wintypes.DWORD()
        api.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value == pid and api.IsWindowVisible(handle):
            found.append(handle)
        return True
    api.EnumWindows(callback_type(visit), 0)
    return found


def wait_for(function):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        value = function()
        if value:
            return value
        time.sleep(0.05)
    raise RuntimeError("Installer directory-page check timed out")


def main():
    # Run with reduced privileges for a UI-only preview; installation is never invoked.
    environment = dict(os.environ, __COMPAT_LAYER="RunAsInvoker")
    process = subprocess.Popen([str(ROOT / "release/ch341-mini/CH341Mini_0.1.0_x64-setup.exe")], env=environment)
    try:
        window = wait_for(lambda: next((handle for handle in windows(process.pid) if any(kind == "Edit" for _, kind, _ in children(handle))), None))
        controls = children(window)
        edit = next(handle for handle, kind, _ in controls if kind == "Edit")
        browse = next(handle for handle, kind, value in controls if kind == "Button" and "浏览" in value)
        default = text(edit)
        assert default.endswith("CH341 Mini Controller"), default
        chosen = str(Path(os.environ["TEMP"]) / "CH341 Mini 自定义目录")
        value = ctypes.create_unicode_buffer(chosen)
        api.SendMessageW(edit, 0x000C, 0, ctypes.cast(value, ctypes.c_void_p).value)
        assert text(edit) == chosen
        api.SetWindowPos(window, wintypes.HWND(-1), 80, 80, 0, 0, 0x0001 | 0x0040)
        time.sleep(0.15)
        screenshot = Path(os.environ["TEMP"]) / "codex-shot-mini-installer-directory.png"
        helper = Path.home() / ".codex/skills/screenshot/scripts/take_screenshot.ps1"
        rectangle = wintypes.RECT()
        assert api.GetWindowRect(window, ctypes.byref(rectangle))
        region = f"{rectangle.left},{rectangle.top},{rectangle.right - rectangle.left},{rectangle.bottom - rectangle.top}"
        subprocess.run([shutil.which("pwsh"), "-NoProfile", "-File", str(helper), "-Path", str(screenshot), "-Region", region], capture_output=True, check=True, timeout=30)
        # The browse control opens a folder selector; cancel it without changing files.
        api.PostMessageW(browse, 0x00F5, 0, 0)
        dialog = wait_for(lambda: next((handle for handle in windows(process.pid) if handle != window and "文件夹" in text(handle)), None))
        cancel = api.GetDlgItem(dialog, 2)
        assert cancel
        api.PostMessageW(cancel, 0x00F5, 0, 0)
        wait_for(lambda: dialog not in windows(process.pid))
        assert text(edit) == chosen
        report = {"ok": True, "installationInvoked": False, "defaultDirectory": default, "customDirectory": chosen, "browseOpenedAndCancelled": True, "screenshot": str(screenshot)}
        (ROOT / "build/ch341-mini/installer-ui-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        # This process has never left the directory page or performed installation.
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    main()
