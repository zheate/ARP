"""Small Tkinter operator application for manual CH341 current control."""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime
import json
import math
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from backend import CH341Driver, ManualController, VERSION, dll_path


class App:
    def __init__(self, root: tk.Tk, controller: ManualController | None = None):
        self.root = root
        root.withdraw()
        self.controller = controller or ManualController()
        self.commands = queue.Queue()
        self.results = queue.SimpleQueue()
        self.worker = threading.Thread(target=self.run_worker, name="ch341", daemon=True)
        self.worker.start()
        self.pending = False
        self.closing = False
        self.close_dialog = None
        self.devices: list[int] = []
        self.log_count = 0
        self.readings = {}
        self.scale = root.winfo_fpixels("1i") / 96
        root.title("CH341 Mini Controller")
        root.geometry(f"{round(900 * self.scale)}x{round(800 * self.scale)}")
        root.minsize(round(700 * self.scale), round(800 * self.scale))
        root.configure(bg="#f3f5f9")
        self.resources = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent / "resources"))
        root.iconbitmap(str(self.resources / "controller.ico"))
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.build_ui()
        self.render()
        root.deiconify()
        root.state("normal")
        self.poll_token = root.after(50, self.poll)

    def build_ui(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        p = lambda value: round(value * self.scale)
        font = ("Microsoft YaHei UI", 10)
        style.configure("TFrame", background="#f3f5f9")
        style.configure("TLabel", background="#f3f5f9", foreground="#23324d", font=font)
        style.configure("Muted.TLabel", foreground="#718096", font=("Microsoft YaHei UI", 9))
        style.configure("Heading.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("TButton", padding=(p(12), p(9)), font=font, background="#edf2fa", foreground="#244a9e", borderwidth=0, focusthickness=1, focuscolor="#244a9e", relief="flat")
        style.map("TButton", background=[("disabled", "#f1f3f7"), ("active", "#dfe8f7"), ("pressed", "#d2dff3")], foreground=[("disabled", "#8793a7")])
        style.configure("Primary.TButton", background="#244a9e", foreground="white", focuscolor="#ffffff")
        style.map("Primary.TButton", background=[("disabled", "#dbe3f1"), ("pressed", "#183574"), ("active", "#1d408d")], foreground=[("disabled", "#71819d"), ("!disabled", "white")])
        style.configure("Stop.TButton", background="#fff1f1", foreground="#b33b43", focuscolor="#b33b43")
        style.map("Stop.TButton", background=[("disabled", "#f7f0f1"), ("active", "#fbe3e5")], foreground=[("disabled", "#a99094")])
        style.configure("Read.TButton", padding=(p(8), p(3)), font=("Microsoft YaHei UI", 9))
        style.configure("Link.TButton", background="white", padding=(p(9), p(4)), font=("Microsoft YaHei UI", 9))
        style.configure("TCombobox", padding=p(8), font=font, borderwidth=1, bordercolor="#dce3ed", arrowcolor="#244a9e", fieldbackground="#ffffff", background="#ffffff")
        style.map("TCombobox", fieldbackground=[("readonly", "#ffffff"), ("disabled", "#f5f7fa")], foreground=[("readonly", "#23324d"), ("disabled", "#718096")])
        style.configure("White.TFrame", background="#ffffff")
        style.configure("White.TLabel", background="#ffffff")
        style.configure("WhiteHeading.TLabel", background="#ffffff", foreground="#182b50", font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("WhiteMuted.TLabel", background="#ffffff", foreground="#718096", font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", background="#ffffff", foreground="#182b50", font=("Segoe UI", 19, "bold"))
        style.configure("Well.TFrame", background="#f5f8fe")
        style.configure("Well.TLabel", background="#f5f8fe", foreground="#718096", font=("Microsoft YaHei UI", 9))
        style.configure("Unit.TLabel", background="#f5f8fe", foreground="#244a9e", font=("Segoe UI", 18))
        style.configure("Metric.TLabel", background="#f5f8fe", foreground="#182b50", font=("Segoe UI", 22, "bold"))

        tk.Frame(self.root, bg="#244a9e", height=p(4)).pack(fill="x")
        header = ttk.Frame(self.root, style="White.TFrame", padding=(p(28), p(12)))
        header.pack(fill="x")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="CH341 Mini Controller", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="手动电流控制器", style="WhiteMuted.TLabel").grid(row=1, column=0, sticky="w", pady=(p(4), 0))
        ttk.Label(header, text="I²C / USB", style="WhiteMuted.TLabel").grid(row=0, column=1, rowspan=2, sticky="e")

        body = ttk.Frame(self.root, padding=(p(28), p(22)))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)

        connection = ttk.Frame(body, style="White.TFrame", padding=p(16))
        connection.grid(row=0, column=0, sticky="ew", pady=(0, p(18)))
        connection.columnconfigure(1, weight=1)
        self.connection_text = tk.StringVar(value="未连接")
        ttk.Label(connection, text="设备连接", style="WhiteHeading.TLabel").grid(row=0, column=0, sticky="w")
        self.connection_label = ttk.Label(connection, textvariable=self.connection_text, style="WhiteMuted.TLabel")
        self.connection_label.grid(row=0, column=1, columnspan=3, sticky="e")
        self.device = ttk.Combobox(connection, state="readonly", values=[], width=18, font=font)
        self.device.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(0, p(10)), pady=(p(12), 0))
        self.device.set("未识别设备")
        self.scan_button = ttk.Button(connection, text="识别设备", command=lambda: self.submit("scan", self.controller.scan))
        self.scan_button.grid(row=1, column=2, sticky="ew", padx=(0, p(10)), pady=(p(12), 0))
        self.connect_button = ttk.Button(connection, text="连接设备", width=9, style="Primary.TButton", command=self.toggle_connection)
        self.connect_button.grid(row=1, column=3, sticky="ew", pady=(p(12), 0))

        workspace = ttk.Frame(body)
        workspace.grid(row=1, column=0, sticky="ew")
        workspace.columnconfigure((0, 1), weight=1, uniform="workspace")
        manual = self.manual_panel = ttk.Frame(workspace, style="White.TFrame", padding=p(16))
        manual.grid(row=0, column=0, sticky="nsew", padx=(0, p(9)))
        manual.columnconfigure(0, weight=1)
        ttk.Label(manual, text="手动电流控制", style="WhiteHeading.TLabel").grid(row=0, column=0, sticky="w", pady=(0, p(12)))
        target = ttk.Frame(manual, style="Well.TFrame", padding=(p(16), p(12)))
        target.grid(row=2, column=0, sticky="ew")
        target.columnconfigure(0, weight=1)
        ttk.Label(target, text="目标电流", style="Well.TLabel").grid(row=0, column=0, sticky="w")
        self.current = tk.Entry(target, font=("Segoe UI", 36, "bold"), width=6, background="#f5f8fe", foreground="#182b50", disabledbackground="#f5f8fe", disabledforeground="#9ba8bd", insertbackground="#244a9e", relief="flat", borderwidth=0, highlightthickness=1, highlightbackground="#f5f8fe", highlightcolor="#244a9e")
        self.current.insert(0, "0.00")
        self.current.grid(row=1, column=0, sticky="ew", pady=(p(2), 0))
        ttk.Label(target, text="A", style="Unit.TLabel").grid(row=1, column=1, sticky="se", padx=(p(8), 0), pady=(0, p(12)))
        self.current.bind("<Return>", lambda _: self.apply_current())
        ttk.Label(manual, text="范围 0–20 A  /  最小分辨率 0.01 A", style="WhiteMuted.TLabel").grid(row=3, column=0, sticky="w", pady=(p(8), p(12)))
        actions = ttk.Frame(manual, style="White.TFrame")
        actions.grid(row=4, column=0, sticky="ew")
        actions.columnconfigure((0, 1), weight=1, uniform="actions")
        self.apply_button = ttk.Button(actions, text="下发电流", style="Primary.TButton", command=self.apply_current)
        self.apply_button.grid(row=0, column=0, sticky="ew", padx=(0, p(8)))
        self.stop_button = ttk.Button(actions, text="下电至 0 A", style="Stop.TButton", command=lambda: self.submit("stop", self.controller.stop))
        self.stop_button.grid(row=0, column=1, sticky="ew")
        self.output_text = tk.StringVar()
        self.output_label = ttk.Label(manual, textvariable=self.output_text, style="WhiteMuted.TLabel", wraplength=p(300))
        self.output_label.grid(row=5, column=0, sticky="nw", pady=(p(12), 0))
        manual.bind("<Configure>", lambda event: self.output_label.configure(wraplength=max(1, event.width - p(32))))

        readback = self.readback_panel = ttk.Frame(workspace, style="White.TFrame", padding=p(16))
        readback.grid(row=0, column=1, sticky="nsew", padx=(p(9), 0))
        readback.columnconfigure((0, 1), weight=1, uniform="metrics")
        ttk.Label(readback, text="状态回读", style="WhiteHeading.TLabel").grid(row=0, column=0, sticky="w", pady=(0, p(12)))
        self.read_time = tk.StringVar(value="未读取")
        ttk.Label(readback, textvariable=self.read_time, style="WhiteMuted.TLabel").grid(row=0, column=1, sticky="e", pady=(0, p(12)))
        self.read_buttons = {}
        self.read_times = {}
        self.metrics = {}
        for number, (key, title, unit) in enumerate((("output_current", "输出电流", "A"), ("output_voltage", "输出电压", "V"), ("input_voltage", "输入电压", "V"), ("temperature", "模块温度", "°C"))):
            column, row = number % 2, 2 + number // 2
            well = ttk.Frame(readback, style="Well.TFrame", padding=(p(10), p(8)))
            well.grid(row=row, column=column, sticky="nsew", padx=(0, p(8)) if column == 0 else 0, pady=(0, p(8)))
            well.columnconfigure(0, weight=1)
            ttk.Label(well, text=title, style="Well.TLabel").grid(row=0, column=0, sticky="w")
            variable = tk.StringVar(value=f"— {unit}")
            self.metrics[key] = (variable, unit)
            number_text = tk.StringVar(value="—")
            variable.trace_add("write", lambda *_, source=variable, target=number_text: target.set(source.get().rsplit(" ", 1)[0]))
            ttk.Label(well, text=unit, style="Well.TLabel").grid(row=0, column=1, sticky="e", padx=(p(4), 0))
            ttk.Label(well, textvariable=number_text, style="Metric.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(p(2), p(6)))
            button = ttk.Button(well, text="读取", style="Read.TButton", command=lambda key=key: self.submit(f"read:{key}", lambda: self.controller.read_status(key)))
            button.grid(row=2, column=0, columnspan=2, sticky="ew")
            self.read_buttons[key] = button
        self.message = tk.StringVar(value="就绪 · 设备未连接")
        self.message_label = ttk.Label(body, textvariable=self.message, wraplength=p(800), style="Muted.TLabel")
        self.message_label.grid(row=2, column=0, sticky="ew", pady=(p(14), p(14)))
        records = ttk.Frame(body, style="White.TFrame", padding=(p(16), p(12)))
        records.grid(row=3, column=0, sticky="nsew")
        records.columnconfigure(0, weight=1)
        records.rowconfigure(1, weight=1)
        log_head = ttk.Frame(records, style="White.TFrame")
        log_head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, p(8)))
        log_head.columnconfigure(0, weight=1)
        ttk.Label(log_head, text="操作记录", style="WhiteHeading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(log_head, text="清空记录", style="Link.TButton", command=self.clear_log).grid(row=0, column=1, sticky="e")
        self.log = tk.Text(records, height=3, background="#ffffff", foreground="#64748b", relief="flat", borderwidth=0, wrap="word", state="disabled", font=("Microsoft YaHei UI", 9), spacing1=p(3), spacing3=p(3))
        self.log.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(records, orient="vertical", command=self.log.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", padx=(p(8), 0))
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.tag_configure("error", foreground="#b42332")
        footer = ttk.Frame(body)
        footer.grid(row=4, column=0, sticky="ew", pady=(p(14), 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, text="CH341 Mini Controller", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(footer, text=f"v{VERSION}", style="Muted.TLabel").grid(row=0, column=1, sticky="e")
        body.bind("<Configure>", lambda event: self.message_label.configure(wraplength=max(1, event.width - p(56))))

    def render(self):
        status = self.controller.status
        locked = self.pending or self.closing
        self.connection_text.set(f"已连接 · Device {status.index}" if status.connected else "未连接")
        self.connection_label.configure(foreground="#244a9e" if status.connected else "#718096")
        for control in (self.scan_button,):
            control.configure(state="disabled" if locked or status.connected else "normal")
        for control in (self.device,):
            control.configure(state="disabled" if locked or status.connected else "readonly")
        self.connect_button.configure(text="断开设备" if status.connected else "连接设备", state="disabled" if locked or (not status.connected and not self.devices) else "normal")
        for control in (self.current, self.apply_button, self.stop_button, *self.read_buttons.values()):
            control.configure(state="normal" if status.connected and not locked else "disabled")
        self.output_text.set("设备未连接" if not status.connected else "设定指令：0.00 A" if status.zero_sent else "未下发电流指令" if status.set_current is None else f"设定指令：{status.set_current:.2f} A")
        for key, (variable, unit) in self.metrics.items():
            variable.set(f"{self.readings[key]:.2f} {unit}" if key in self.readings else f"— {unit}")
        if not self.readings:
            self.read_time.set("未读取")

    def report(self, text: str, *, error=False):
        self.message.set(text)
        self.message_label.configure(foreground="#b42332" if error else "#526073")
        self.log.configure(state="normal")
        self.log.insert("1.0", f"{datetime.now():%H:%M:%S}  {text}\n", "error" if error else ())
        self.log_count += 1
        if self.log_count > 150:
            self.log.delete("151.0", "end")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.log_count = 0

    def submit(self, action, function):
        if self.pending or (self.closing and action != "close"):
            return
        self.pending = True
        self.render()
        self.commands.put((action, function))

    def run_worker(self):
        while True:
            task = self.commands.get()
            if task is None:
                return
            action, function = task
            try:
                self.results.put((action, function(), None))
            except Exception as error:
                self.results.put((action, None, str(error)))

    def toggle_connection(self):
        if self.controller.status.connected:
            self.submit("disconnect", self.controller.disconnect)
            return
        try:
            selected = self.device.current()
            if not 0 <= selected < len(self.devices):
                raise ValueError("未选择 CH341 设备")
            index = self.devices[selected]
            self.submit("connect", lambda: self.controller.connect(index))
        except ValueError as error:
            self.report(str(error), error=True)

    def apply_current(self):
        if self.pending or self.closing or not self.controller.status.connected:
            return
        try:
            try:
                value = float(self.current.get())
            except ValueError as error:
                raise ValueError("电流格式无效；要求数值") from error
            if not math.isfinite(value) or not 0 <= value <= 20:
                raise ValueError("电流超出范围；允许 0–20 A")
            if abs(value * 100 - round(value * 100)) > 1e-7:
                raise ValueError("电流最小分辨率：0.01 A；最多两位小数")
            self.submit("set", lambda: self.controller.set_current(value))
        except ValueError as error:
            self.report(str(error), error=True)

    def poll(self):
        try:
            action, result, error = self.results.get_nowait()
        except queue.Empty:
            self.poll_token = self.root.after(50, self.poll)
            return
        self.pending = False
        if action not in ("scan",) and not action.startswith("read:"):
            self.readings = {}
        if error:
            if action.startswith("read:"):
                self.readings.pop(action.split(":", 1)[1], None)
            self.report(error, error=True)
            if action == "close":
                self.closing = False
                self.show_close_failure(error)
        else:
            messages = {"connect": "设备已连接 · 未下发电流指令", "disconnect": "设备已断开", "set": f"电流指令已下发：{self.controller.status.set_current or 0:.2f} A", "stop": "下电指令完成：0.00 A"}
            if action == "close":
                self.destroy()
                return
            if action == "scan":
                self.devices = result
                self.device.configure(values=[f"CH341 · Device {index}" for index in result])
                if result:
                    self.device.current(0)
                else:
                    self.device.set("未发现设备")
                self.report(f"识别结果：{len(result)} 个 CH341 设备" if result else "未识别到 CH341 设备；检查 USB 连接及驱动")
            else:
                if action.startswith("read:"):
                    self.readings.update(result)
                    self.read_time.set(f"回读 {datetime.now():%H:%M:%S}")
                    labels = {"input_voltage": "输入电压", "output_voltage": "输出电压", "output_current": "输出电流", "temperature": "模块温度"}
                    name = action.split(":", 1)[1]
                    self.report(f"{labels[name]}：{result[name]:.2f} {self.metrics[name][1]}")
                else:
                    self.report(messages[action])
        self.render()
        if self.closing:
            self.submit("close", self.controller.disconnect)
        self.poll_token = self.root.after(50, self.poll)

    def on_close(self):
        if self.close_dialog or self.closing:
            return
        self.closing = True
        if self.pending:
            self.report("关闭中：等待通信完成并下电…")
            self.render()
        else:
            self.submit("close", self.controller.disconnect)

    def show_close_failure(self, error):
        dialog = self.close_dialog = tk.Toplevel(self.root)
        dialog.title("下电失败")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="下电失败", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(frame, text=error, wraplength=490).pack(anchor="w", pady=(12, 12))
        ttk.Label(frame, text="实际输出状态未确认。强制退出前须关闭物理电源输出。", wraplength=490).pack(anchor="w")
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(20, 0))
        def cancel():
            self.close_dialog = None
            dialog.destroy()
        def retry():
            cancel()
            self.on_close()
        def force():
            if messagebox.askyesno("确认断电", "确认物理电源输出已关闭？\n强制退出仅关闭软件，不验证实际输出。", parent=dialog):
                try:
                    self.controller.force_disconnect()
                except Exception as error:
                    self.report(str(error), error=True)
                self.destroy()
        for title, command, button_style in (("取消关闭", cancel, "TButton"), ("重试下电", retry, "Primary.TButton"), ("断电后强制退出", force, "Stop.TButton")):
            ttk.Button(actions, text=title, command=command, style=button_style).pack(side="left", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.grab_set()

    def destroy(self):
        self.commands.put(None)
        self.root.after_cancel(self.poll_token)
        self.root.destroy()


def self_check(output: Path):
    """Load frozen dependencies only. No scan, open, I2C, or power command."""
    report = {"version": VERSION, "python": sys.version, "tk": tk.TkVersion, "frozen": bool(getattr(sys, "frozen", False)), "hardware_opened": False}
    try:
        interpreter = tk.Tk()
        interpreter.withdraw()
        report["tcl_patchlevel"] = interpreter.eval("info patchlevel")
        report["tk_patchlevel"] = interpreter.eval("package require Tk")
        resources = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent / "resources"))
        interpreter.iconbitmap(str(resources / "controller.ico"))
        report["application_icon_loaded"] = True
        interpreter.destroy()
        driver = CH341Driver()
        report["dll_version"] = driver.dll.CH341GetVersion()
        report["dll_path"] = str(dll_path())
        report["ok"] = True
        code = 0
    except Exception as error:
        report["ok"], report["error"] = False, str(error)
        code = 1
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return code


def main():
    parser = argparse.ArgumentParser(description="CH341 Mini Controller")
    parser.add_argument("--self-check", type=Path, metavar="REPORT_JSON")
    args = parser.parse_args()
    if args.self_check:
        return self_check(args.self_check)
    mutex = None
    if sys.platform == "win32":
        # Keep two copies of the Mini app from controlling one adapter concurrently.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        mutex = kernel.CreateMutexW(None, False, "Local\\CGHX.CH341MiniController")
        if not mutex:
            raise RuntimeError("无法创建控制器实例锁")
        if ctypes.get_last_error() == 183:
            kernel.CloseHandle(mutex)
            messagebox.showinfo("CH341 Mini Controller", "程序已运行。")
            return 0
    try:
        if sys.platform == "win32":
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except (AttributeError, OSError):
                pass
        root = tk.Tk()
        App(root)
        root.mainloop()
    finally:
        if mutex:
            kernel.CloseHandle(mutex)
    return 0


if __name__ == "__main__":
    sys.exit(main())
