"""Windows USB LocationPaths resolver for CH341 physical port binding."""

from __future__ import annotations

import ctypes
import platform
from dataclasses import dataclass


@dataclass(frozen=True)
class Ch341DeviceDescriptor:
    """Describe one online CH341 adapter and its current DLL index."""

    device_index: int
    interface_path: str
    location_path: str


DWORD = ctypes.c_ulong
ULONG_PTR = ctypes.c_size_t
BOOL = ctypes.c_int
HDEVINFO = ctypes.c_void_p

ERROR_INSUFFICIENT_BUFFER = 122
DEVPROP_TYPE_STRING_LIST = 0x00002012
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", DWORD),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", DWORD)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", DWORD),
        ("Reserved", ULONG_PTR),
    ]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", DWORD),
        ("ClassGuid", GUID),
        ("DevInst", DWORD),
        ("Reserved", ULONG_PTR),
    ]


DEVPKEY_DEVICE_LOCATION_PATHS = DEVPROPKEY(
    GUID(
        0xA45C254E,
        0xDF1C,
        0x4EFD,
        (ctypes.c_ubyte * 8)(0x80, 0x20, 0x67, 0xD1, 0x46, 0xA8, 0x50, 0xE0),
    ),
    35,
)


def _last_error() -> int:
    getter = getattr(ctypes, "get_last_error", None)
    return int(getter()) if callable(getter) else 0


def _decode_location_paths(raw: bytes) -> list[str]:
    try:
        value = raw.decode("utf-16-le")
    except UnicodeDecodeError as exc:
        raise RuntimeError("Windows 返回了无效的 CH341 LocationPaths 数据") from exc
    return [part.strip() for part in value.split("\x00") if part.strip()]


def _load_setupapi():
    try:
        setupapi = ctypes.WinDLL("setupapi.dll", use_last_error=True)
    except Exception as exc:
        raise RuntimeError(f"无法加载 Windows SetupAPI：{exc}") from exc

    setupapi.SetupDiCreateDeviceInfoList.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p]
    setupapi.SetupDiCreateDeviceInfoList.restype = HDEVINFO
    setupapi.SetupDiOpenDeviceInterfaceW.argtypes = [
        HDEVINFO,
        ctypes.c_wchar_p,
        DWORD,
        ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ]
    setupapi.SetupDiOpenDeviceInterfaceW.restype = BOOL
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        HDEVINFO,
        ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
        ctypes.c_void_p,
        DWORD,
        ctypes.POINTER(DWORD),
        ctypes.POINTER(SP_DEVINFO_DATA),
    ]
    setupapi.SetupDiGetDeviceInterfaceDetailW.restype = BOOL
    setupapi.SetupDiGetDevicePropertyW.argtypes = [
        HDEVINFO,
        ctypes.POINTER(SP_DEVINFO_DATA),
        ctypes.POINTER(DEVPROPKEY),
        ctypes.POINTER(DWORD),
        ctypes.c_void_p,
        DWORD,
        ctypes.POINTER(DWORD),
        DWORD,
    ]
    setupapi.SetupDiGetDevicePropertyW.restype = BOOL
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [HDEVINFO]
    setupapi.SetupDiDestroyDeviceInfoList.restype = BOOL
    return setupapi


def get_location_path_from_interface_path(interface_path: str) -> str:
    """Resolve a CH341 interface path to its physical USB LocationPath.

    Device interface paths and physical device properties belong to different
    Windows property scopes. SetupAPI first resolves the interface to its
    SP_DEVINFO_DATA, then reads DEVPKEY_Device_LocationPaths from that device
    instance. Failure is explicit because an interface-path fallback would not
    satisfy fixed-port binding.
    """
    cleaned_path = str(interface_path).strip()
    if not cleaned_path:
        raise ValueError("设备接口路径不能为空")
    if platform.system() != "Windows":
        raise RuntimeError("CH341 固定端口识别仅支持 Windows")

    setupapi = _load_setupapi()
    device_info_set = setupapi.SetupDiCreateDeviceInfoList(None, None)
    if not device_info_set or device_info_set == INVALID_HANDLE_VALUE:
        raise RuntimeError(f"创建设备信息集失败（Windows 错误 {_last_error()}）")

    try:
        interface_data = SP_DEVICE_INTERFACE_DATA()
        interface_data.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
        if not setupapi.SetupDiOpenDeviceInterfaceW(
            device_info_set,
            cleaned_path,
            0,
            ctypes.byref(interface_data),
        ):
            raise RuntimeError(f"无法打开 CH341 设备接口（Windows 错误 {_last_error()}）")

        device_info = SP_DEVINFO_DATA()
        device_info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
        detail_size = DWORD(0)
        detail_ok = setupapi.SetupDiGetDeviceInterfaceDetailW(
            device_info_set,
            ctypes.byref(interface_data),
            None,
            0,
            ctypes.byref(detail_size),
            ctypes.byref(device_info),
        )
        detail_error = _last_error()
        if detail_ok or detail_error != ERROR_INSUFFICIENT_BUFFER or detail_size.value == 0:
            raise RuntimeError(f"无法定位 CH341 设备实例（Windows 错误 {detail_error}）")

        property_type = DWORD(0)
        property_size = DWORD(0)
        first_ok = setupapi.SetupDiGetDevicePropertyW(
            device_info_set,
            ctypes.byref(device_info),
            ctypes.byref(DEVPKEY_DEVICE_LOCATION_PATHS),
            ctypes.byref(property_type),
            None,
            0,
            ctypes.byref(property_size),
            0,
        )
        property_error = _last_error()
        if first_ok or property_error != ERROR_INSUFFICIENT_BUFFER or property_size.value == 0:
            raise RuntimeError(f"读取 CH341 LocationPaths 长度失败（Windows 错误 {property_error}）")

        buffer = (ctypes.c_ubyte * property_size.value)()
        if not setupapi.SetupDiGetDevicePropertyW(
            device_info_set,
            ctypes.byref(device_info),
            ctypes.byref(DEVPKEY_DEVICE_LOCATION_PATHS),
            ctypes.byref(property_type),
            ctypes.byref(buffer),
            property_size.value,
            ctypes.byref(property_size),
            0,
        ):
            raise RuntimeError(f"读取 CH341 LocationPaths 失败（Windows 错误 {_last_error()}）")
        if property_type.value != DEVPROP_TYPE_STRING_LIST:
            raise RuntimeError(f"CH341 LocationPaths 类型异常：0x{property_type.value:08X}")

        paths = _decode_location_paths(bytes(buffer[: property_size.value]))
        if not paths:
            raise RuntimeError("Windows 未返回 CH341 的物理 LocationPaths")
        return paths[0]
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(device_info_set)
