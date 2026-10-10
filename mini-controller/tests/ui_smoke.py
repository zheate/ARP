"""Developer GUI check with an injected fake driver; never uses CH341 hardware."""
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tkinter as tk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import App
from backend import ManualController
from test_backend import FakeClock, FakeDriver


class PreviewDriver(FakeDriver):
    def write(self, index, speed, address, data):
        super().write(index, speed, address, data)
        if data[1] == 0xFF:
            self.current = bytes([0xB4, 0x8C, data[2], data[3]])

    def read(self, index, speed, address, register):
        response = super().read(index, speed, address, register)
        return {0x88: bytes([0, 0, 24, 12]), 0x8B: bytes([0, 0, 2, 85]), 0x8D: bytes([0, 0, 26, 50])}.get(register, response)


def pump(root, app, timeout=5):
    deadline = time.monotonic() + timeout
    while app.pending:
        root.update()
        if time.monotonic() > deadline:
            raise RuntimeError("GUI task timed out")
        time.sleep(0.01)
    root.update()


def screenshot(root):
    root.attributes("-topmost", True)
    root.lift()
    root.update()
    time.sleep(0.15)
    root.update()
    region = f"{root.winfo_rootx()},{root.winfo_rooty()},{root.winfo_width()},{root.winfo_height()}"
    helper = Path.home() / ".codex/skills/screenshot/scripts/take_screenshot.ps1"
    path = Path(os.environ['TEMP']) / f"codex-shot-mini-{root.winfo_width()}x{root.winfo_height()}.png"
    result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-File", str(helper), "-Path", str(path), "-Region", region], capture_output=True, text=True, check=True, timeout=30)
    return result.stdout.strip().splitlines()[-1]


def main(requested_scale):
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    root = tk.Tk()
    root.tk.call("tk", "scaling", requested_scale * 96 / 72)
    driver = PreviewDriver()
    clock = FakeClock()
    app = App(root, ManualController(driver, sleeper=clock.sleep, clock=clock.clock))
    root.title("CH341 Mini Controller — 模拟界面检查")
    root.state("normal")
    root.resizable(False, False)
    root.geometry(f"{round(900 * app.scale)}x{round(800 * app.scale)}+40+40")
    root.update()
    assert driver.calls == []
    app.scan_button.invoke(); pump(root, app)
    app.device.current(1)
    app.connect_button.invoke(); pump(root, app)
    assert driver.calls == [("scan",), ("open", 2, 0)]
    assert not hasattr(app, "address") and not hasattr(app, "speed")
    assert not app.controller.status.zero_sent
    app.current.delete(0, "end"); app.current.insert(0, "5.25")
    app.apply_button.invoke(); pump(root, app)
    for button in app.read_buttons.values():
        clock.sleep(1.1)
        button.invoke(); pump(root, app)
    assert app.metrics["output_current"][0].get() == "5.25 A"
    assert app.current.get() == "5.25"
    for widget in [app.apply_button, app.stop_button, *app.read_buttons.values()]:
        bounds = [widget.winfo_x(), widget.winfo_y(), widget.winfo_width(), widget.winfo_height()]
        widget.event_generate("<Enter>"); root.update()
        assert bounds == [widget.winfo_x(), widget.winfo_y(), widget.winfo_width(), widget.winfo_height()]
        widget.event_generate("<Leave>")
    images = [screenshot(root)]
    root.update()
    actual_sizes = [[root.winfo_width(), root.winfo_height()]]
    for width, height in [(700, 800), (1100, 820)]:
        root.state("normal")
        width, height = round(width * app.scale), round(height * app.scale)
        root.geometry(f"{width}x{height}+40+40"); root.update()
        for widget in [app.scan_button, app.connect_button, app.current, app.apply_button, app.stop_button, *app.read_buttons.values(), app.message_label, app.log]:
            assert widget.winfo_rootx() >= root.winfo_rootx()
            assert widget.winfo_rootx() + widget.winfo_width() <= root.winfo_rootx() + root.winfo_width(), (width, widget)
            assert widget.winfo_rooty() + widget.winfo_height() <= root.winfo_rooty() + root.winfo_height(), (height, widget)
        assert app.apply_button.winfo_rooty() == app.stop_button.winfo_rooty()
        assert app.log.winfo_height() >= round(40 * app.scale), ("Operation records were compressed", app.log.winfo_height(), app.scale)
        assert app.manual_panel.winfo_y() == app.readback_panel.winfo_y()
        assert abs(app.manual_panel.winfo_width() - app.readback_panel.winfo_width()) <= 1
        assert app.manual_panel.winfo_height() == app.readback_panel.winfo_height()
        images.append(screenshot(root))
        root.update()
        assert [root.winfo_width(), root.winfo_height()] == [width, height]
        actual_sizes.append([root.winfo_width(), root.winfo_height()])
    app.stop_button.invoke(); pump(root, app)
    assert app.controller.status.zero_sent
    clock.sleep(1.1)
    app.apply_button.invoke(); pump(root, app)
    driver.fail_write = True
    app.on_close(); pump(root, app)
    assert app.close_dialog is not None
    assert app.controller.status.connected and not app.controller.status.zero_sent
    app.close_dialog.event_generate("<Escape>")
    # Cancel close without running a physical device or forced-exit path.
    app.close_dialog.grab_release(); app.close_dialog.destroy(); app.close_dialog = None
    driver.fail_write = False
    app.connect_button.invoke(); pump(root, app)
    assert not app.controller.status.connected
    assert driver.calls[-1] == ("close", 2)
    result = {"ok": True, "hardwareOpened": False, "dpiScale": app.scale, "sizes": actual_sizes, "screenshots": images, "driverCalls": len(driver.calls)}
    path = ROOT.parent / "build/ch341-mini/ui-smoke.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    app.destroy()
    return result


if __name__ == "__main__":
    results = [main(scale) for scale in (1.0, 1.25, 1.5)]
    (ROOT.parent / "build/ch341-mini/ui-smoke.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
