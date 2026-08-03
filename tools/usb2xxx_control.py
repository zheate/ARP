"""USB2XXX I2C controller used by the pump-drive power supply.

The target at address 0x41 needs two STOP-separated transactions.  In
particular, do not replace the write/sleep/read sequence with
``IIC_WriteReadBytes``: that combined transaction can leave this target's bus
busy.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence, TextIO

_LOGGER = logging.getLogger("pump_driver.usb2xxx")
_LOGGER.addHandler(logging.NullHandler())


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
    DEFAULT_POWER_ON_SETTLE_S = 0.5
    DEFAULT_RECOVERY_DELAY_S = 0.1
    # Both USB transport failures and transient target-side I2C failures can
    # occur while the pump driver settles after the final 0 A command.  The
    # adapter may still look connected even though its I2C engine or the target
    # is no longer answering.  Re-initialize first, then power-cycle/reopen the
    # adapter before declaring the link unusable.
    RECOVERABLE_TRANSFER_STATUSES = frozenset((-2, -3, -4, 2, 3, 4, 5))
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
        return f"{operation}失败：{detail}（USB2XXX 状态码 {status}）"

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
                # A safe disconnect removes the adapter's 3.3 V interface
                # supply.  Let the target-side I2C logic and pull-ups settle
                # after it is restored before the first I2C initialization or
                # transfer; otherwise reconnect can succeed while every
                # following read sees a target that is still resetting.
                self._sleep(self.DEFAULT_POWER_ON_SETTLE_S)
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
            if self.device_handle is None or self.usb2xxx_dll is None:
                self.device_handle = None
                self.is_connected = False
                return True

            handle = self.device_handle
            power_off = bool(
                self.DEV_SetPowerLevel(
                    handle,
                    self.POWER_LEVEL_NONE,
                )
            )
            closed = bool(self.USB_CloseDevice(handle))
            if closed:
                self.device_handle = None
                self.is_connected = False
            else:
                # Keep the live handle so a failed close can be retried.  Do
                # not publish a disconnected state that the DLL did not
                # confirm.
                self.device_handle = handle
                self.is_connected = True
            return closed and power_off

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
        last_detail = ""
        recovery_details: list[str] = []
        for attempt in range(3):
            success, last_detail, status = self._write_once_unlocked(
                device_addr_7bit,
                values,
            )
            if success:
                if recovery_details:
                    return True, f"{last_detail}；恢复记录：{'；'.join(recovery_details)}"
                return True, last_detail
            if status not in self.RECOVERABLE_TRANSFER_STATUSES or attempt >= 2:
                break
            recovered, recovery_detail = self._recover_after_failure_unlocked(attempt)
            if not recovered:
                return False, f"{last_detail}；自动恢复失败：{recovery_detail}"
            recovery_details.append(recovery_detail)
        return False, f"{last_detail}；已自动恢复并重试 2 次仍失败"

    def _write_once_unlocked(
        self,
        device_addr_7bit: int,
        values: list[int],
    ) -> tuple[bool, str, int]:
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
            return False, self._error_message("I²C 写入", status), status
        return True, "写入成功", status

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
        success, response, _status = self._read_once_unlocked(
            device_addr_7bit,
            read_length,
        )
        return success, response

    def _read_once_unlocked(
        self,
        device_addr_7bit: int,
        read_length: int,
    ) -> tuple[bool, Any, int]:
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
            return False, self._error_message("I²C 读取", status), status
        return True, [int(buffer[index]) for index in range(read_length)], status

    def _recover_after_failure_unlocked(self, attempt: int) -> tuple[bool, str]:
        """Escalate from I²C re-init to a full USB rescan when needed."""
        if attempt == 0:
            initialized, init_detail = self._initialize_i2c()
            if initialized:
                return True, init_detail
            recovered, recovery_detail = self._recover_transfer_unlocked()
            if recovered:
                return True, f"{init_detail}；{recovery_detail}"
            return False, f"{init_detail}；{recovery_detail}"
        return self._recover_transfer_unlocked()

    def _recover_transfer_unlocked(self) -> tuple[bool, str]:
        """Close, rescan, reopen, power and initialize the USB2XXX adapter."""
        if self.device_handle is None:
            return False, "USB2XXX 设备未打开"
        previous_handle = self.device_handle
        try:
            self.DEV_SetPowerLevel(previous_handle, self.POWER_LEVEL_NONE)
            if not bool(self.USB_CloseDevice(previous_handle)):
                self.DEV_SetPowerLevel(previous_handle, self.POWER_LEVEL_3V3)
                return False, "关闭 USB2XXX 设备失败"
            self.device_handle = None
            self.is_connected = False
            self._sleep(self.DEFAULT_RECOVERY_DELAY_S)

            handles = (ctypes.c_int * self.DEVICE_BUFFER_SIZE)()
            count = int(self.USB_ScanDevice(handles))
            if count <= 0:
                return False, "重新扫描 USB2XXX 失败：未找到适配器"
            available_handles = [
                int(handles[index]) for index in range(min(count, self.DEVICE_BUFFER_SIZE))
            ]
            if previous_handle in available_handles:
                handle = previous_handle
                self.device_index = available_handles.index(previous_handle)
            elif self.device_index < len(available_handles):
                handle = available_handles[self.device_index]
            else:
                handle = available_handles[0]
                self.device_index = 0
            if not bool(self.USB_OpenDevice(handle)):
                return False, "重新打开 USB2XXX 设备失败"
            self.device_handle = handle
            if not bool(self.DEV_SetPowerLevel(handle, self.POWER_LEVEL_3V3)):
                self.USB_CloseDevice(handle)
                self.device_handle = None
                return False, "恢复 USB2XXX 3.3 V 接口电压失败"
            self._sleep(self.DEFAULT_POWER_ON_SETTLE_S)
            initialized, detail = self._initialize_i2c()
            if not initialized:
                self.DEV_SetPowerLevel(handle, self.POWER_LEVEL_NONE)
                self.USB_CloseDevice(handle)
                self.device_handle = None
                return False, detail
            self.is_connected = True
            return True, "USB2XXX 已重新扫描、打开并初始化 I²C"
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
            return False, f"重新打开 USB2XXX 时出错：{exc}"

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
                valid, detail = self._validate_transfer(device_addr_7bit, len(values))
                if not valid:
                    return False, detail
                valid, detail = self._validate_transfer(device_addr_7bit, requested)
                if not valid:
                    return False, detail

                last_detail: Any = "I²C 分步写读失败"
                for attempt in range(3):
                    success, last_detail, status = self._write_once_unlocked(
                        device_addr_7bit,
                        values,
                    )
                    if success:
                        self._sleep(self.read_delay_s)
                        success, last_detail, status = self._read_once_unlocked(
                            device_addr_7bit,
                            requested,
                        )
                    if success:
                        return self._validate_target_response(last_detail, requested)
                    if status not in self.RECOVERABLE_TRANSFER_STATUSES or attempt >= 2:
                        break
                    recovered, recovery_detail = self._recover_after_failure_unlocked(attempt)
                    if not recovered:
                        return False, f"{last_detail}；自动恢复失败：{recovery_detail}"
                return False, f"{last_detail}；已自动恢复并重试 2 次仍失败"
            except Exception as exc:
                return False, f"I²C 分步写读操作出错：{exc}"


class USB2XXXDriverProcessError(RuntimeError):
    """The isolated USB2XXX worker could not complete a request safely."""


class USB2XXXDriverProcessTimeout(USB2XXXDriverProcessError):
    """The isolated worker exceeded the parent-side hard deadline."""


class USB2XXXProcessController:
    """Run every USB2XXX DLL call in a disposable child process.

    ``TimeOutMs`` in the vendor API only covers the adapter's I2C operation. A
    USB transport or DLL deadlock can outlive it. This proxy supplies a hard
    parent-side deadline and terminates the complete worker when it expires.
    """

    DEFAULT_CALL_TIMEOUT_S = 12.0
    DEFAULT_START_TIMEOUT_S = 5.0
    PROTOCOL_VERSION = 1

    def __init__(
        self,
        *,
        call_timeout_s: float = DEFAULT_CALL_TIMEOUT_S,
        start_timeout_s: float = DEFAULT_START_TIMEOUT_S,
        worker_command: Sequence[str] | None = None,
        popen_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
    ) -> None:
        if call_timeout_s <= 0.0 or start_timeout_s <= 0.0:
            raise ValueError("USB2XXX 子进程超时时间必须大于 0 秒")
        self.call_timeout_s = float(call_timeout_s)
        self.start_timeout_s = float(start_timeout_s)
        self._configured_speed = USB2XXXI2CController.DEFAULT_CLOCK_HZ
        self._worker_command_override = list(worker_command) if worker_command else None
        self._popen_factory = popen_factory
        self._process: subprocess.Popen[str] | None = None
        self._process_access_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self._next_request_id = 1
        self._connection_detail: dict[str, Any] | None = None
        self.is_connected = False
        self.output_enabled = True
        self.fault_reason: str | None = None
        self.last_operation_detail = ""
        self._abort_reason: str | None = None

    @staticmethod
    def _default_worker_command() -> list[str]:
        if bool(getattr(sys, "frozen", False)):
            return [sys.executable, "--usb2xxx-worker"]
        return [sys.executable, "-u", str(Path(__file__).resolve()), "--worker"]

    def _worker_command(self) -> list[str]:
        if self._worker_command_override is not None:
            return list(self._worker_command_override)
        return self._default_worker_command()

    @staticmethod
    def _hidden_window_flags() -> int:
        if os.name == "nt":
            return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return 0

    def _read_responses(
        self,
        stream: TextIO,
        responses: queue.Queue[dict[str, Any]],
    ) -> None:
        try:
            for line in stream:
                try:
                    response = json.loads(line)
                except json.JSONDecodeError:
                    responses.put(
                        {"transport_error": f"USB2XXX 子进程返回了无效数据：{line.strip()}"}
                    )
                    continue
                if isinstance(response, dict):
                    responses.put(response)
        except Exception as exc:
            responses.put({"transport_error": f"读取 USB2XXX 子进程失败：{exc}"})
        finally:
            responses.put({"transport_error": "USB2XXX 子进程已退出"})

    def _read_stderr(self, stream: TextIO) -> None:
        try:
            for line in stream:
                value = line.strip()
                if value:
                    self._stderr_tail.append(value)
        except Exception:
            pass

    def _start_worker_locked(self) -> None:
        with self._process_access_lock:
            process = self._process
        if process is not None and process.poll() is None:
            return
        self._stop_worker_locked()
        self._responses = queue.Queue()
        responses = self._responses
        self._stderr_tail.clear()
        try:
            process = self._popen_factory(
                self._worker_command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=self._hidden_window_flags(),
            )
        except Exception as exc:
            raise USB2XXXDriverProcessError(f"无法启动 USB2XXX 驱动子进程：{exc}") from exc
        if process.stdin is None or process.stdout is None or process.stderr is None:
            process.kill()
            raise USB2XXXDriverProcessError("USB2XXX 驱动子进程管道创建失败")
        with self._process_access_lock:
            self._process = process
        threading.Thread(
            target=self._read_responses,
            args=(process.stdout, responses),
            name="usb2xxx-worker-stdout",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_stderr,
            args=(process.stderr,),
            name="usb2xxx-worker-stderr",
            daemon=True,
        ).start()
        response = self._send_and_wait_locked("ping", {}, self.start_timeout_s)
        if not isinstance(response, dict) or response.get("protocol") != self.PROTOCOL_VERSION:
            self._stop_worker_locked()
            raise USB2XXXDriverProcessError("USB2XXX 驱动子进程协议不兼容")

    def _stop_worker_locked(self) -> None:
        with self._process_access_lock:
            process = self._process
            self._process = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except Exception:
            pass
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=1.0)
            except Exception:
                try:
                    process.kill()
                    process.wait(timeout=1.0)
                except Exception:
                    pass
        for stream in (process.stdout, process.stderr):
            if stream is None:
                continue
            try:
                stream.close()
            except Exception:
                pass

    def _transport_detail(self, message: str) -> str:
        if not self._stderr_tail:
            return message
        return f"{message}；子进程日志：{self._stderr_tail[-1]}"

    def _invalidate_worker_locked(self, reason: str) -> None:
        _LOGGER.error("USB2XXX 驱动子进程被标记为失联 | reason=%s", reason)
        self.fault_reason = reason
        self.is_connected = False
        self._connection_detail = None
        self._stop_worker_locked()

    def _send_and_wait_locked(
        self,
        method: str,
        params: dict[str, Any],
        timeout_s: float,
    ) -> Any:
        started = time.monotonic()
        with self._process_access_lock:
            process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise USB2XXXDriverProcessError("USB2XXX 驱动子进程未运行")
        request_id = self._next_request_id
        self._next_request_id += 1
        request = {"v": self.PROTOCOL_VERSION, "id": request_id, "method": method, "params": params}
        _LOGGER.debug(
            "USB2XXX 调用开始 | request_id=%s | worker_pid=%s | method=%s | timeout_s=%s | params=%r",
            request_id,
            process.pid,
            method,
            timeout_s,
            params,
        )
        try:
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except Exception as exc:
            raise USB2XXXDriverProcessError(
                self._transport_detail(f"无法向 USB2XXX 子进程发送命令：{exc}")
            ) from exc

        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            timeout_message = (
                f"USB2XXX {method} 调用超过 {timeout_s:g} 秒，"
                "已终止失去响应的驱动子进程"
            )
            if remaining <= 0.0:
                raise USB2XXXDriverProcessTimeout(timeout_message)
            try:
                response = self._responses.get(timeout=remaining)
            except queue.Empty as exc:
                raise USB2XXXDriverProcessTimeout(timeout_message) from exc
            if "transport_error" in response:
                raise USB2XXXDriverProcessError(
                    self._transport_detail(str(response["transport_error"]))
                )
            if response.get("id") != request_id:
                continue
            state = response.get("state")
            if isinstance(state, dict):
                self.is_connected = bool(state.get("is_connected", self.is_connected))
                self.output_enabled = bool(state.get("output_enabled", self.output_enabled))
            if not bool(response.get("ok")):
                raise USB2XXXDriverProcessError(
                    str(response.get("error") or "USB2XXX 子进程调用失败")
                )
            result = response.get("result")
            elapsed_s = time.monotonic() - started
            if isinstance(result, list) and result and result[0] is False:
                _LOGGER.warning(
                    "USB2XXX 调用返回失败 | request_id=%s | method=%s | elapsed_s=%.3f | result=%r",
                    request_id,
                    method,
                    elapsed_s,
                    result,
                )
            else:
                _LOGGER.debug(
                    "USB2XXX 调用完成 | request_id=%s | method=%s | elapsed_s=%.3f | result=%r",
                    request_id,
                    method,
                    elapsed_s,
                    result,
                )
            return result

    def _request(self, method: str, params: dict[str, Any], timeout_s: float | None = None) -> Any:
        # Never queue a UI safety command behind a known in-flight DLL call.
        if not self._request_lock.acquire(timeout=0.1):
            raise USB2XXXDriverProcessError("USB2XXX 上一条命令仍在执行，拒绝并发访问同一适配器")
        try:
            with self._lifecycle_lock:
                try:
                    self._start_worker_locked()
                    return self._send_and_wait_locked(
                        method,
                        params,
                        self.call_timeout_s if timeout_s is None else float(timeout_s),
                    )
                except USB2XXXDriverProcessError as exc:
                    _LOGGER.exception(
                        "USB2XXX 调用异常 | method=%s | params=%r",
                        method,
                        params,
                    )
                    reason = self._abort_reason or str(exc)
                    self._abort_reason = None
                    self._invalidate_worker_locked(reason)
                    if reason == str(exc):
                        raise
                    raise USB2XXXDriverProcessError(reason) from exc
        finally:
            self._request_lock.release()

    def abort_in_flight(self, reason: str = "已中止 USB2XXX 当前调用") -> bool:
        """Terminate the worker immediately so a blocked DLL call can unwind."""

        detail = str(reason).strip() or "已中止 USB2XXX 当前调用"
        self._abort_reason = detail
        self.fault_reason = detail
        self.is_connected = False
        self._connection_detail = None
        # Do not take the request/lifecycle locks here: the blocked reader owns
        # them until terminating this disposable process wakes its request.
        with self._process_access_lock:
            process = self._process
            if process is None or process.poll() is not None:
                return False
            try:
                process.terminate()
            except Exception:
                try:
                    process.kill()
                except Exception:
                    return False
        return True

    def set_i2c_speed(self, speed: int) -> bool:
        speed_value = int(speed)
        clock_hz = USB2XXXI2CController.SPEED_MAP_HZ.get(speed_value, speed_value)
        if clock_hz <= 0:
            return False
        self._configured_speed = clock_hz
        if not self.is_connected:
            return True
        try:
            return bool(self._request("set_i2c_speed", {"speed": speed_value}))
        except USB2XXXDriverProcessError:
            return False

    def connect_device(self, index: int = 0) -> tuple[bool, Any]:
        if self.is_connected and self._connection_detail is not None:
            return True, dict(self._connection_detail)
        try:
            self._request("set_i2c_speed", {"speed": self._configured_speed})
            result = self._request("connect_device", {"index": int(index)})
        except USB2XXXDriverProcessError as exc:
            return False, str(exc)
        if not isinstance(result, list) or len(result) != 2:
            with self._lifecycle_lock:
                self._invalidate_worker_locked("USB2XXX 连接响应格式异常")
            return False, "USB2XXX 连接响应格式异常"
        connected, detail = bool(result[0]), result[1]
        self.is_connected = connected
        if connected and isinstance(detail, dict):
            self._connection_detail = dict(detail)
            self._connection_detail["isolation"] = "subprocess"
            self.fault_reason = None
            return True, dict(self._connection_detail)
        with self._lifecycle_lock:
            self._invalidate_worker_locked(str(detail))
        return False, detail

    def disconnect_device(self) -> bool:
        if self._process is None:
            self.is_connected = False
            return True
        try:
            result = bool(self._request("disconnect_device", {}))
        except USB2XXXDriverProcessError:
            return False
        if not result:
            return False
        self.is_connected = False
        self._connection_detail = None
        with self._lifecycle_lock:
            self._stop_worker_locked()
        return True

    def i2c_write(self, device_addr_7bit: int, write_data: Iterable[int]) -> tuple[bool, str]:
        try:
            result = self._request(
                "i2c_write",
                {"address": int(device_addr_7bit), "data": [int(item) for item in write_data]},
            )
        except (TypeError, ValueError, USB2XXXDriverProcessError) as exc:
            self.last_operation_detail = str(exc)
            return False, self.last_operation_detail
        if not isinstance(result, list) or len(result) != 2:
            self.last_operation_detail = "USB2XXX 写入响应格式异常"
            return False, self.last_operation_detail
        self.last_operation_detail = str(result[1])
        return bool(result[0]), self.last_operation_detail

    def i2c_write_read(
        self,
        device_addr_7bit: int,
        write_data: Iterable[int],
        read_length: int,
    ) -> tuple[bool, Any]:
        try:
            result = self._request(
                "i2c_write_read",
                {
                    "address": int(device_addr_7bit),
                    "data": [int(item) for item in write_data],
                    "read_length": int(read_length),
                },
            )
        except (TypeError, ValueError, USB2XXXDriverProcessError) as exc:
            self.last_operation_detail = str(exc)
            return False, self.last_operation_detail
        if not isinstance(result, list) or len(result) != 2:
            self.last_operation_detail = "USB2XXX 读取响应格式异常"
            return False, self.last_operation_detail
        self.last_operation_detail = "读取成功" if bool(result[0]) else str(result[1])
        return bool(result[0]), result[1]

    def close_worker(self) -> None:
        """Release only the helper process; callers must zero the supply first."""
        with self._lifecycle_lock:
            self.is_connected = False
            self._connection_detail = None
            self._stop_worker_locked()

    def __del__(self) -> None:
        try:
            self.close_worker()
        except Exception:
            pass


_WORKER_METHODS = frozenset(
    {"set_i2c_speed", "connect_device", "disconnect_device", "i2c_write", "i2c_write_read"}
)


def _write_worker_response(response: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def usb2xxx_worker_main() -> int:
    """Own the vendor DLL and serve a deliberately small JSON-lines API."""
    controller = USB2XXXI2CController()
    for line in sys.stdin:
        request_id: Any = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("请求必须是 JSON 对象")
            request_id = request.get("id")
            if request.get("v") != USB2XXXProcessController.PROTOCOL_VERSION:
                raise ValueError("协议版本不兼容")
            method = str(request.get("method") or "")
            params = request.get("params") or {}
            if not isinstance(params, dict):
                raise ValueError("params 必须是对象")
            if method == "ping":
                result: Any = {
                    "protocol": USB2XXXProcessController.PROTOCOL_VERSION,
                    "pid": os.getpid(),
                }
            elif method == "shutdown":
                _write_worker_response(
                    {
                        "id": request_id,
                        "ok": True,
                        "result": True,
                        "state": {
                            "is_connected": controller.is_connected,
                            "output_enabled": controller.output_enabled,
                        },
                    }
                )
                return 0
            elif method not in _WORKER_METHODS:
                raise ValueError(f"不允许的 USB2XXX 子进程方法：{method}")
            elif method == "set_i2c_speed":
                result = controller.set_i2c_speed(int(params["speed"]))
            elif method == "connect_device":
                result = controller.connect_device(int(params.get("index", 0)))
            elif method == "disconnect_device":
                result = controller.disconnect_device()
            elif method == "i2c_write":
                result = controller.i2c_write(int(params["address"]), params["data"])
            else:
                result = controller.i2c_write_read(
                    int(params["address"]),
                    params["data"],
                    int(params["read_length"]),
                )
            _write_worker_response(
                {
                    "id": request_id,
                    "ok": True,
                    "result": result,
                    "state": {
                        "is_connected": controller.is_connected,
                        "output_enabled": controller.output_enabled,
                    },
                }
            )
        except Exception as exc:
            _write_worker_response(
                {
                    "id": request_id,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "state": {
                        "is_connected": controller.is_connected,
                        "output_enabled": controller.output_enabled,
                    },
                }
            )
    return 0


__all__ = [
    "IICConfig",
    "USB2XXXDriverProcessError",
    "USB2XXXDriverProcessTimeout",
    "USB2XXXI2CController",
    "USB2XXXProcessController",
    "usb2xxx_worker_main",
]


if __name__ == "__main__" and "--worker" in sys.argv:
    raise SystemExit(usb2xxx_worker_main())
