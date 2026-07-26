"""USB2XXX I2C controller used by the pump-drive power supply.

The target at address 0x41 needs two STOP-separated transactions.  In
particular, do not replace the write/sleep/read sequence with
``IIC_WriteReadBytes``: that combined transaction can leave this target's bus
busy.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable


class IICConfig(ctypes.Structure):
    _fields_ = [
        ("ClockSpeedHz", ctypes.c_uint),
        ("OwnAddr", ctypes.c_ushort),
        ("Master", ctypes.c_ubyte),
        ("AddrBits", ctypes.c_ubyte),
        ("EnablePu", ctypes.c_ubyte),
        ("ByteIntervalUs", ctypes.c_ubyte),
    ]


IIC_ERROR_MESSAGES = {
    -1: "适配器不支持该 I²C 操作",
    -2: "USB 写入失败",
    -3: "USB 读取失败",
    -4: "适配器命令执行失败",
    -5: "I²C 参数错误",
    1: "当前适配器不支持 I²C 通道 0",
    2: "I²C 总线忙（请检查 SDA/SCL 是否被拉低，必要时断电重启被测板）",
    3: "I²C 启动失败（请检查 SDA/SCL 接线和上拉）",
    4: "I²C 操作超时",
    5: "I²C 从机无应答（地址应为 7 位 0x41）",
    6: "I²C PEC 校验失败",
}


class USB2XXXI2CController:
    """Compatibility controller matching ``CH341I2CController``'s API."""

    DEVICE_BUFFER_SIZE = 20
    IIC_INDEX = 0
    DEFAULT_TARGET_ADDRESS = 0x41
    DEFAULT_CLOCK_HZ = 10_000
    DEFAULT_TIMEOUT_MS = 3_000
    DEFAULT_READ_DELAY_S = 0.5
    POWER_LEVEL_NONE = 0
    POWER_LEVEL_3V3 = 3
    SPEED_MAP_HZ = {0: 10_000, 1: 100_000, 2: 400_000, 3: 750_000}

    def __init__(
        self,
        *,
        dll: Any | None = None,
        dll_dir: str | os.PathLike[str] | None = None,
        target_address: int = DEFAULT_TARGET_ADDRESS,
        read_delay_s: float = DEFAULT_READ_DELAY_S,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.device_index = 0
        self.device_handle: int | None = None
        self.is_connected = False
        self.output_enabled = True
        self.i2c_speed_hz = self.DEFAULT_CLOCK_HZ
        self.target_address = int(target_address)
        self.read_delay_s = float(read_delay_s)
        self.timeout_ms = int(timeout_ms)
        self._sleep = sleep_fn
        self._lock = threading.RLock()
        self._dll_directory_handles: list[Any] = []
        self.dll_path: Path | None = None
        self.load_error: str | None = None
        self.usb2xxx_dll = dll

        if not 0 <= self.target_address <= 0x7F:
            raise ValueError("USB2XXX 目标地址必须在 0x00–0x7F 范围内")
        if self.read_delay_s < 0:
            raise ValueError("USB2XXX 读等待时间不能小于 0 秒")
        if self.timeout_ms <= 0:
            raise ValueError("USB2XXX 超时时间必须大于 0 毫秒")

        if self.usb2xxx_dll is None:
            try:
                self.usb2xxx_dll = self._load_dll(dll_dir)
            except Exception as exc:  # Keep construction compatible with CH341.
                self.load_error = str(exc)
        if self.usb2xxx_dll is not None:
            try:
                self._bind_api()
            except Exception as exc:
                self.load_error = f"USB2XXX.dll 接口不完整：{exc}"
                self.usb2xxx_dll = None

    @staticmethod
    def _project_root() -> Path:
        return Path(__file__).resolve().parent.parent

    @classmethod
    def _candidate_dll_directories(
        cls,
        explicit_dir: str | os.PathLike[str] | None,
    ) -> Iterable[Path]:
        candidates: list[Path] = []
        if explicit_dir:
            candidates.append(Path(explicit_dir))
        env_dir = os.environ.get("USB2XXX_DLL_DIR", "").strip()
        if env_dir:
            candidates.append(Path(env_dir))
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            candidates.append(Path(bundle_root))
        project_root = cls._project_root()
        candidates.extend(
            (
                project_root / "local_drivers" / "usb2xxx",
                project_root / "assets" / "libs" / "usb2xxx",
            )
        )
        seen: set[str] = set()
        for directory in candidates:
            key = os.path.normcase(str(directory.resolve(strict=False)))
            if key not in seen:
                seen.add(key)
                yield directory

    def _load_dll(self, explicit_dir: str | os.PathLike[str] | None) -> Any:
        if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
            raise RuntimeError("USB2XXX 目前仅支持 Windows")

        searched: list[str] = []
        last_error: BaseException | None = None
        for directory in self._candidate_dll_directories(explicit_dir):
            dll_path = directory / "USB2XXX.dll"
            searched.append(str(dll_path))
            if not dll_path.is_file():
                continue
            try:
                if hasattr(os, "add_dll_directory"):
                    self._dll_directory_handles.append(os.add_dll_directory(str(directory)))
                self.dll_path = dll_path
                return ctypes.WinDLL(str(dll_path))
            except OSError as exc:
                last_error = exc

        detail = f"；最后错误：{last_error}" if last_error is not None else ""
        locations = "\n".join(f"- {item}" for item in searched)
        raise RuntimeError(
            "未找到或无法加载 USB2XXX.dll。请设置 USB2XXX_DLL_DIR，"
            "或将 USB2XXX.dll 与 libusb-1.0.dll 放入 local_drivers/usb2xxx。"
            f"{detail}\n已检查：\n{locations}"
        )

    @staticmethod
    def _configure_function(function: Any, argtypes: list[Any], restype: Any) -> None:
        function.argtypes = argtypes
        function.restype = restype

    def _bind_api(self) -> None:
        assert self.usb2xxx_dll is not None
        self.USB_ScanDevice = self.usb2xxx_dll.USB_ScanDevice
        self.USB_OpenDevice = self.usb2xxx_dll.USB_OpenDevice
        self.USB_CloseDevice = self.usb2xxx_dll.USB_CloseDevice
        self.DEV_SetPowerLevel = self.usb2xxx_dll.DEV_SetPowerLevel
        self.IIC_Init = self.usb2xxx_dll.IIC_Init
        self.IIC_WriteBytes = self.usb2xxx_dll.IIC_WriteBytes
        self.IIC_ReadBytes = self.usb2xxx_dll.IIC_ReadBytes

        self._configure_function(
            self.USB_ScanDevice,
            [ctypes.POINTER(ctypes.c_int)],
            ctypes.c_int,
        )
        self._configure_function(self.USB_OpenDevice, [ctypes.c_int], ctypes.c_ubyte)
        self._configure_function(self.USB_CloseDevice, [ctypes.c_int], ctypes.c_ubyte)
        self._configure_function(
            self.DEV_SetPowerLevel,
            [ctypes.c_int, ctypes.c_ubyte],
            ctypes.c_ubyte,
        )
        self._configure_function(
            self.IIC_Init,
            [ctypes.c_int, ctypes.c_int, ctypes.POINTER(IICConfig)],
            ctypes.c_int,
        )
        transfer_args = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_short,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._configure_function(self.IIC_WriteBytes, transfer_args, ctypes.c_int)
        self._configure_function(self.IIC_ReadBytes, transfer_args, ctypes.c_int)

    @staticmethod
    def _error_message(operation: str, status: int) -> str:
        detail = IIC_ERROR_MESSAGES.get(status, f"未知错误码 {status}")
        return f"{operation}失败：{detail}"

    def _config(self) -> IICConfig:
        return IICConfig(
            ClockSpeedHz=self.i2c_speed_hz,
            OwnAddr=0,
            Master=1,
            AddrBits=7,
            EnablePu=1,
            ByteIntervalUs=0,
        )

    def _initialize_i2c(self) -> tuple[bool, str]:
        if self.device_handle is None:
            return False, "USB2XXX 设备未打开"
        config = self._config()
        status = int(self.IIC_Init(self.device_handle, self.IIC_INDEX, ctypes.byref(config)))
        if status != 0:
            return False, self._error_message("初始化 I²C", status)
        return True, "I²C 初始化成功"

    def connect_device(self, index: int = 0) -> tuple[bool, Any]:
        with self._lock:
            if self.is_connected and self.device_handle is not None:
                return True, self._connection_detail()
            if self.usb2xxx_dll is None:
                return False, self.load_error or "未加载 USB2XXX.dll"

            handles = (ctypes.c_int * self.DEVICE_BUFFER_SIZE)()
            try:
                count = int(self.USB_ScanDevice(handles))
            except Exception as exc:
                return False, f"扫描 USB2XXX 适配器失败：{exc}"
            if count <= 0:
                return False, "未找到 USB2XXX 适配器，请检查 USB 连接和驱动"
            if index < 0 or index >= min(count, self.DEVICE_BUFFER_SIZE):
                return False, f"USB2XXX 设备索引 {index} 无效，共检测到 {count} 台"

            handle = int(handles[index])
            try:
                if not bool(self.USB_OpenDevice(handle)):
                    return False, f"打开 USB2XXX 设备 {handle:08X} 失败"
                self.device_index = int(index)
                self.device_handle = handle
                if not bool(self.DEV_SetPowerLevel(handle, self.POWER_LEVEL_3V3)):
                    self.USB_CloseDevice(handle)
                    self.device_handle = None
                    return False, "设置 USB2XXX 接口电压为 3.3 V 失败"
                initialized, detail = self._initialize_i2c()
                if not initialized:
                    self.DEV_SetPowerLevel(handle, self.POWER_LEVEL_NONE)
                    self.USB_CloseDevice(handle)
                    self.device_handle = None
                    return False, detail
                self.is_connected = True
                return True, self._connection_detail()
            except Exception as exc:
                if self.device_handle is not None:
                    try:
                        self.DEV_SetPowerLevel(
                            self.device_handle,
                            self.POWER_LEVEL_NONE,
                        )
                        self.USB_CloseDevice(self.device_handle)
                    except Exception:
                        pass
                self.device_handle = None
                self.is_connected = False
                return False, f"连接 USB2XXX 时出错：{exc}"

    def _connection_detail(self) -> dict[str, Any]:
        handle = int(self.device_handle or 0)
        return {
            "controller": "USB2XXX",
            "serial": f"{handle:08X}",
            "channel": self.IIC_INDEX,
            "address": f"0x{self.target_address:02X}",
            "clock_hz": self.i2c_speed_hz,
            "interface_voltage_v": 3.3,
            "internal_pullup": True,
        }

    def disconnect_device(self) -> bool:
        with self._lock:
            success = True
            if self.device_handle is not None and self.usb2xxx_dll is not None:
                try:
                    power_off = bool(
                        self.DEV_SetPowerLevel(
                            self.device_handle,
                            self.POWER_LEVEL_NONE,
                        )
                    )
                    success = bool(self.USB_CloseDevice(self.device_handle)) and power_off
                finally:
                    self.device_handle = None
                    self.is_connected = False
            else:
                self.device_handle = None
                self.is_connected = False
            return success

    def set_i2c_speed(self, speed: int) -> bool:
        speed_value = int(speed)
        clock_hz = self.SPEED_MAP_HZ.get(speed_value, speed_value)
        if clock_hz <= 0:
            return False
        with self._lock:
            previous = self.i2c_speed_hz
            self.i2c_speed_hz = clock_hz
            if not self.is_connected:
                return True
            initialized, _detail = self._initialize_i2c()
            if initialized:
                return True
            self.i2c_speed_hz = previous
            self._initialize_i2c()
            return False

    def _validate_transfer(self, device_addr_7bit: int, length: int) -> tuple[bool, str]:
        if not self.is_connected or self.device_handle is None or self.usb2xxx_dll is None:
            return False, "USB2XXX 设备未连接"
        address = int(device_addr_7bit)
        if address != self.target_address:
            return (
                False,
                f"为保护当前硬件，USB2XXX 仅允许访问固定 7 位地址 "
                f"0x{self.target_address:02X}，拒绝 0x{address:02X}",
            )
        if length <= 0:
            return False, "I²C 数据长度必须大于 0"
        return True, ""

    @staticmethod
    def _byte_values(data: Iterable[int]) -> list[int]:
        values = [int(item) for item in data]
        if not values:
            raise ValueError("I²C 写入数据不能为空")
        if any(item < 0 or item > 0xFF for item in values):
            raise ValueError("I²C 数据必须是 0x00–0xFF 范围内的字节")
        return values

    def _write_unlocked(self, device_addr_7bit: int, values: list[int]) -> tuple[bool, str]:
        valid, detail = self._validate_transfer(device_addr_7bit, len(values))
        if not valid:
            return False, detail
        assert self.device_handle is not None
        buffer = (ctypes.c_ubyte * len(values))(*values)
        status = int(
            self.IIC_WriteBytes(
                self.device_handle,
                self.IIC_INDEX,
                int(device_addr_7bit),
                buffer,
                len(values),
                self.timeout_ms,
            )
        )
        if status != 0:
            return False, self._error_message("I²C 写入", status)
        return True, "写入成功"

    def i2c_write(self, device_addr_7bit: int, write_data: Iterable[int]) -> tuple[bool, str]:
        try:
            values = self._byte_values(write_data)
        except (TypeError, ValueError) as exc:
            return False, str(exc)
        with self._lock:
            try:
                return self._write_unlocked(device_addr_7bit, values)
            except Exception as exc:
                return False, f"I²C 写操作出错：{exc}"

    def _read_unlocked(self, device_addr_7bit: int, read_length: int) -> tuple[bool, Any]:
        valid, detail = self._validate_transfer(device_addr_7bit, read_length)
        if not valid:
            return False, detail
        assert self.device_handle is not None
        buffer = (ctypes.c_ubyte * read_length)()
        status = int(
            self.IIC_ReadBytes(
                self.device_handle,
                self.IIC_INDEX,
                int(device_addr_7bit),
                buffer,
                read_length,
                self.timeout_ms,
            )
        )
        if status != 0:
            return False, self._error_message("I²C 读取", status)
        return True, [int(buffer[index]) for index in range(read_length)]

    @staticmethod
    def _validate_target_response(data: Any, expected_length: int) -> tuple[bool, Any]:
        if not isinstance(data, list) or len(data) != expected_length:
            return False, f"I²C 响应长度异常：期望 {expected_length} 字节，实际为 {data!r}"
        if expected_length != 4:
            return True, data
        raw = " ".join(f"{item:02X}" for item in data)
        if data[0] != 0x5A:
            return (
                False,
                f"I²C 响应帧错位：{raw}；已拒绝使用该数值，请断电重启被测板",
            )
        if data[3] > 99:
            return (
                False,
                f"I²C 响应小数位异常：{raw}；已拒绝使用该数值，请断电重启被测板",
            )
        return True, data

    def i2c_write_read(
        self,
        device_addr_7bit: int,
        write_data: Iterable[int],
        read_length: int,
    ) -> tuple[bool, Any]:
        """Write, issue STOP, wait for the target, then perform a fresh read."""
        try:
            values = self._byte_values(write_data)
            requested = int(read_length)
        except (TypeError, ValueError) as exc:
            return False, str(exc)
        with self._lock:
            try:
                success, detail = self._write_unlocked(device_addr_7bit, values)
                if not success:
                    return False, detail
                self._sleep(self.read_delay_s)
                success, response = self._read_unlocked(device_addr_7bit, requested)
                if not success:
                    return False, response
                return self._validate_target_response(response, requested)
            except Exception as exc:
                return False, f"I²C 分步写读操作出错：{exc}"


__all__ = ["IICConfig", "USB2XXXI2CController"]
