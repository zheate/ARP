"""CH341-only manual control. No measurement, VISA, Qt, or automation imports."""
from __future__ import annotations

import ctypes
import math
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

VERSION = "0.1.0"
DEFAULT_ADDRESS = 0x41
DEFAULT_SPEED = 0  # Product connection path uses CH341 mode 0: 20 kHz.
MIN_COMMAND_INTERVAL_S = 1.1
RAMP_DOWN_STEP_A = 5.0


def current_command(current: float) -> bytes:
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        raise ValueError("电流格式无效；要求数值")
    if not math.isfinite(current) or not 0 <= current <= 20:
        raise ValueError("电流超出范围；允许 0–20 A")
    integer, fraction = divmod(round(current * 100), 100)
    return bytes([0xB4, 0xFF, integer, fraction])


def decode_value(data: bytes) -> float:
    if len(data) != 4 or data[3] >= 100:
        raise RuntimeError("I²C 返回数据无效；检查设备连接和模块供电")
    return data[2] + data[3] / 100


def dll_path() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent / "resources"))
    return root / "CH341DLLA64.DLL"


class CH341Driver:
    def __init__(self, path: Path | None = None):
        if sys.platform != "win32" or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise RuntimeError("系统要求：Windows 10/11（64 位）")
        path = (path or dll_path()).resolve()
        try:
            self.dll = ctypes.WinDLL(str(path), winmode=0x100 | 0x800)
        except OSError as exc:
            raise RuntimeError("CH341 运行库加载失败；重新安装软件") from exc
        # WCH API uses Windows BOOL (32 bit) and pointer-sized device handles.
        signatures = {
            "CH341OpenDevice": ([ctypes.c_ulong], ctypes.c_void_p),
            "CH341CloseDevice": ([ctypes.c_ulong], None),
            "CH341SetExclusive": ([ctypes.c_ulong, ctypes.c_ulong], ctypes.c_int),
            "CH341SetStream": ([ctypes.c_ulong, ctypes.c_ulong], ctypes.c_int),
            "CH341FlushBuffer": ([ctypes.c_ulong], ctypes.c_int),
            "CH341StreamI2C": ([ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p], ctypes.c_int),
            "CH341GetVersion": ([], ctypes.c_ulong),
        }
        for name, (argtypes, restype) in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = argtypes, restype

    def scan(self) -> list[int]:
        found = []
        for index in range(16):
            handle = self.dll.CH341OpenDevice(index)
            if handle and handle != ctypes.c_void_p(-1).value:
                found.append(index)
                self.dll.CH341CloseDevice(index)
        return found

    def open(self, index: int, speed: int):
        handle = self.dll.CH341OpenDevice(index)
        if not handle or handle == ctypes.c_void_p(-1).value:
            raise RuntimeError("CH341 连接失败；检查 USB 连接、驱动及设备占用")
        try:
            if not self.dll.CH341SetExclusive(index, 1):
                raise RuntimeError("CH341 设备占用失败")
            if not self.dll.CH341SetStream(index, speed):
                raise RuntimeError("I²C 通信初始化失败")
        except Exception:
            self.dll.CH341CloseDevice(index)
            raise

    def close(self, index: int):
        self.dll.CH341CloseDevice(index)

    def write(self, index: int, speed: int, address: int, data: bytes):
        if not self.dll.CH341SetStream(index, speed):
            raise RuntimeError("I²C 通信初始化失败")
        if not self.dll.CH341FlushBuffer(index):
            raise RuntimeError("清空 CH341 通信缓冲区失败")
        buffer = (ctypes.c_ubyte * (len(data) + 1))(address << 1, *data)
        if not self.dll.CH341StreamI2C(index, len(buffer), buffer, 0, None):
            raise RuntimeError("I²C 写入失败；检查 USB 连接及模块供电")

    def read(self, index: int, speed: int, address: int, register: int) -> bytes:
        # Match the established write / 1 ms delay / separate read transaction.
        self.write(index, speed, address, bytes([0xB4, register, 0, 0]))
        time.sleep(0.001)
        read_address = (ctypes.c_ubyte * 1)((address << 1) | 1)
        response = (ctypes.c_ubyte * 4)()
        if not self.dll.CH341StreamI2C(index, 1, read_address, 4, response):
            raise RuntimeError("I²C 回读失败；检查 USB 连接及模块供电")
        return bytes(response)


@dataclass(frozen=True)
class Status:
    connected: bool = False
    index: int | None = None
    set_current: float | None = None
    zero_sent: bool = False


class ManualController:
    def __init__(self, driver=None, *, sleeper=time.sleep, clock=time.monotonic):
        self._driver = driver
        self._sleep = sleeper
        self._clock = clock
        self._last_command_at = None
        self._active_current = 0.0
        self._lock = threading.RLock()
        self.status = Status()
        self.address, self.speed = DEFAULT_ADDRESS, DEFAULT_SPEED

    @property
    def driver(self):
        if self._driver is None:
            self._driver = CH341Driver()
        return self._driver

    def scan(self) -> list[int]:
        with self._lock:
            if self.status.connected:
                raise RuntimeError("识别前需断开当前设备")
            return self.driver.scan()

    def connect(self, index: int, address: int = DEFAULT_ADDRESS, speed: int = DEFAULT_SPEED):
        with self._lock:
            if self.status.connected:
                raise RuntimeError("连接前需断开当前设备")
            if any(type(value) is not int for value in (index, address, speed)) or not (0 <= index < 16 and 8 <= address <= 0x77 and 0 <= speed <= 3):
                raise ValueError("CH341 连接参数无效")
            self.driver.open(index, speed)
            self.address, self.speed = address, speed
            # Never restore a current value or issue any output command on connection.
            self.status = Status(True, index)
            self._last_command_at = None
            self._active_current = 0.0

    def _index(self) -> int:
        if not self.status.connected or self.status.index is None:
            raise RuntimeError("CH341 设备未连接")
        return self.status.index

    def _reserve_command(self, *, wait: bool):
        now = self._clock()
        remaining = 0.0 if self._last_command_at is None else MIN_COMMAND_INTERVAL_S - (now - self._last_command_at)
        if remaining > 1e-9:
            if not wait:
                raise RuntimeError(f"电源命令过快；{remaining:.1f} 秒后重试")
            self._sleep(remaining)
        self._last_command_at = self._clock()

    def set_current(self, value: float, *, wait_for_slot: bool = False):
        with self._lock:
            command = current_command(value)
            index = self._index()
            self._reserve_command(wait=wait_for_slot)
            # A failed USB write may have reached the board; invalidate the safe state first.
            self.status = Status(True, index)
            self.driver.write(index, self.speed, self.address, command)
            self.status = Status(True, index, command[2] + command[3] / 100)
            self._active_current = self.status.set_current

    def read_status(self, name: str) -> dict[str, float]:
        with self._lock:
            index = self._index()
            registers = {"input_voltage": 0x88, "output_voltage": 0x8B, "output_current": 0x8C, "temperature": 0x8D}
            if name not in registers:
                raise ValueError("未知读取项")
            self._reserve_command(wait=False)
            value = decode_value(self.driver.read(index, self.speed, self.address, registers[name]))
            return {name: value}

    def stop(self):
        with self._lock:
            self._index()
            # Match StationAutomaticMixin.safe_ramp_down: 5 A decrements,
            # 1.1 s between commands, no CH341 read command during shutdown.
            start = max(0.0, self._active_current)
            count = max(1, math.ceil(round(start * 100) / round(RAMP_DOWN_STEP_A * 100)))
            for step in range(1, count + 1):
                current = max(0.0, round(start * 100) - step * round(RAMP_DOWN_STEP_A * 100)) / 100
                self.set_current(current, wait_for_slot=True)
                if current > 0:
                    self._sleep(MIN_COMMAND_INTERVAL_S)
            self.status = Status(True, self._index(), 0.0, True)

    def disconnect(self):
        with self._lock:
            if not self.status.connected:
                return
            # Connected-at-zero follows the original disconnect path without another write.
            if abs(self._active_current) > 0.01 or self.status.set_current is None:
                self.stop()
            self.driver.close(self._index())
            self.status = Status()
            self._last_command_at = None
            self._active_current = 0.0

    def force_disconnect(self):
        """Only called after the operator explicitly confirms physical power is off."""
        with self._lock:
            if self.status.connected:
                self.driver.close(self._index())
            self.status = Status()
            self._last_command_at = None
            self._active_current = 0.0
