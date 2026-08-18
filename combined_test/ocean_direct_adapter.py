from __future__ import annotations

from ctypes import CDLL, POINTER, byref, c_double, c_long, c_ulong, create_string_buffer, cdll
from pathlib import Path
import threading
from typing import Any

import numpy as np
from numpy.typing import NDArray


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OCEAN_DIRECT_DLL = PROJECT_ROOT / "assets" / "libs" / "ocean_direct" / "OceanDirect.dll"


class OceanDirectError(RuntimeError):
    pass


def _invoke_native(caller: str, function: Any, *args: Any) -> Any:
    try:
        return function(*args)
    except OSError as exc:
        winerror = getattr(exc, "winerror", None)
        code = f"WinError {winerror}" if winerror is not None else type(exc).__name__
        raise OceanDirectError(
            f"OceanDirect {caller} 触发原生 SDK 异常（{code}）：{exc}"
        ) from exc


class OceanDirectSession:
    """Shared OceanDirect SDK session managing multiple spectrometer devices."""

    def __init__(self, dll_path: str | Path | None = None, api: Any | None = None) -> None:
        self.api = api if api is not None else self._load_api(dll_path)
        self.dll_key = str(Path(dll_path).resolve()) if dll_path is not None else str(DEFAULT_OCEAN_DIRECT_DLL.resolve())
        self._lock = threading.RLock()
        # Opening a device queries its spectrum length before releasing this
        # per-device lock. That query follows the normal SDK call path and
        # acquires the same lock again, so it must be reentrant.
        self._device_locks: dict[int, threading.RLock] = {}
        self._ref_count = 0
        self._is_initialized = False
        self._configure_return_types()

    def _load_api(self, dll_path: str | Path | None) -> CDLL:
        path = (Path(dll_path) if dll_path is not None else DEFAULT_OCEAN_DIRECT_DLL).resolve()
        if not path.exists():
            raise FileNotFoundError(f"未找到 OceanDirect.dll：{path}")
        return cdll.LoadLibrary(str(path))

    def _configure_return_types(self) -> None:
        for name in (
            "odapi_get_maximum_intensity",
            "odapi_get_formatted_spectrum_length",
            "odapi_get_unformatted_spectrum_length",
        ):
            func = getattr(self.api, name, None)
            if func is not None and hasattr(func, "restype"):
                if name == "odapi_get_maximum_intensity":
                    func.restype = c_double

    def retain(self) -> None:
        with self._lock:
            if self._ref_count == 0 and not self._is_initialized:
                initialize = getattr(self.api, "odapi_initialize", None)
                if initialize is not None:
                    _invoke_native("initialize", initialize)
                self._is_initialized = True
            self._ref_count += 1

    def release(self) -> None:
        with self._lock:
            if self._ref_count > 0:
                self._ref_count -= 1
            if self._ref_count == 0 and self._is_initialized:
                try:
                    shutdown = getattr(self.api, "odapi_shutdown", None)
                    if shutdown is not None:
                        _invoke_native("shutdown", shutdown)
                finally:
                    self._is_initialized = False
                    self._device_locks.clear()

    def get_device_lock(self, device_id: int) -> threading.RLock:
        with self._lock:
            if device_id not in self._device_locks:
                self._device_locks[device_id] = threading.RLock()
            return self._device_locks[device_id]


_GLOBAL_SESSIONS_LOCK = threading.Lock()
_GLOBAL_SESSIONS: dict[Any, OceanDirectSession] = {}


def get_shared_ocean_direct_session(
    dll_path: str | Path | None = None, api: Any | None = None
) -> OceanDirectSession:
    with _GLOBAL_SESSIONS_LOCK:
        key = api if api is not None else (
            str(Path(dll_path).resolve()) if dll_path is not None else str(DEFAULT_OCEAN_DIRECT_DLL.resolve())
        )
        if key not in _GLOBAL_SESSIONS:
            _GLOBAL_SESSIONS[key] = OceanDirectSession(dll_path=dll_path, api=api)
        return _GLOBAL_SESSIONS[key]


class OceanDirectControl:
    """OceanDirect device controller leveraging a shared SDK session."""

    def __init__(self, dll_path: str | Path | None = None, api: Any | None = None) -> None:
        self._session = get_shared_ocean_direct_session(dll_path=dll_path, api=api)
        self._session.retain()
        self._api = self._session.api
        self._device_id: int | None = None
        self._pixel_count = 0
        self._shutdown = False

    def find_usb_devices(self) -> int:
        # Device discovery is SDK-global rather than device-scoped.  Keep it
        # serialized even though spectrum reads for different device IDs may
        # proceed independently under their per-device locks.
        with self._session._lock:
            result = int(_invoke_native("probe_devices", self._api.odapi_probe_devices))
        if result < 0:
            raise OceanDirectError(
                "OceanDirect 搜索 USB 光谱仪失败：odapi_probe_devices 返回 -1"
            )
        return result

    def get_number_devices(self) -> int:
        with self._session._lock:
            result = int(
                _invoke_native(
                    "get_number_of_device_ids",
                    self._api.odapi_get_number_of_device_ids,
                )
            )
        if result < 0:
            raise OceanDirectError(
                "OceanDirect 获取光谱仪数量失败：odapi_get_number_of_device_ids 返回 -1"
            )
        return result

    def get_device_ids(self) -> list[int]:
        with self._session._lock:
            count = self.get_number_devices()
            if count <= 0:
                return []
            ids = (c_long * count)()
            err = (c_long * 1)(0)
            copied = int(
                _invoke_native("get_device_ids", self._api.odapi_get_device_ids, ids, err)
            )
            self._raise_if_error(err[0], "get_device_ids")
            return [int(ids[index]) for index in range(max(0, min(copied, count)))]

    def open_device(self, device_id: int) -> int:
        dev_id = int(device_id)
        dev_lock = self._session.get_device_lock(dev_id)
        with dev_lock:
            err = (c_long * 1)(0)
            _invoke_native(
                f"open_device({dev_id})",
                self._api.odapi_open_device,
                dev_id,
                err,
            )
            self._raise_if_error(err[0], f"open_device({dev_id})")
            self._device_id = dev_id
            self._pixel_count = self._get_formatted_spectrum_length()
        return 0

    def set_integration_time(self, u_second: int) -> int:
        device_id = self._require_device_id()
        dev_lock = self._session.get_device_lock(device_id)
        with dev_lock:
            err = (c_long * 1)(0)
            _invoke_native(
                f"set_integration_time({int(u_second)} us)",
                self._api.odapi_set_integration_time_micros,
                device_id,
                err,
                c_ulong(int(u_second)),
            )
            self._raise_if_error(err[0], f"set_integration_time({int(u_second)} us)")
        return 0

    def get_integration_time(self) -> int:
        return self._call_int_with_error("odapi_get_integration_time_micros", "get_integration_time")

    def get_maximum_integration_time(self) -> int:
        return self._call_int_with_error("odapi_get_maximum_integration_time_micros", "get_maximum_integration_time")

    def get_minimum_integration_time(self) -> int:
        return self._call_int_with_error("odapi_get_minimum_integration_time_micros", "get_minimum_integration_time")

    def get_maximum_intensity(self) -> float:
        device_id = self._require_device_id()
        dev_lock = self._session.get_device_lock(device_id)
        with dev_lock:
            err = (c_long * 1)(0)
            value = float(
                _invoke_native(
                    "get_maximum_intensity",
                    self._api.odapi_get_maximum_intensity,
                    device_id,
                    err,
                )
            )
            self._raise_if_error(err[0], "get_maximum_intensity")
        return value

    def get_wavelength(self) -> NDArray[np.float64]:
        return self._read_double_array("odapi_get_wavelengths", "get_wavelength")

    def get_intensity(self) -> NDArray[np.float64]:
        return self._read_double_array("odapi_get_formatted_spectrum", "get_intensity")

    def close_device(self) -> None:
        if self._device_id is None:
            return
        device_id = self._device_id
        dev_lock = self._session.get_device_lock(device_id)
        with dev_lock:
            err = (c_long * 1)(0)
            _invoke_native(
                f"close_device({device_id})",
                self._api.odapi_close_device,
                device_id,
                err,
            )
            self._raise_if_error(err[0], "close_device")
            self._device_id = None

    def shutdown(self) -> None:
        if self._shutdown:
            return
        try:
            self.close_device()
        finally:
            self._shutdown = True
            self._session.release()

    def _get_formatted_spectrum_length(self) -> int:
        return self._call_int_with_error("odapi_get_formatted_spectrum_length", "get_formatted_spectrum_length")

    def _call_int_with_error(self, function_name: str, caller: str) -> int:
        device_id = self._require_device_id()
        dev_lock = self._session.get_device_lock(device_id)
        with dev_lock:
            err = (c_long * 1)(0)
            value = int(
                _invoke_native(caller, getattr(self._api, function_name), device_id, err)
            )
            self._raise_if_error(err[0], caller)
        return value

    def _read_double_array(self, function_name: str, caller: str) -> NDArray[np.float64]:
        device_id = self._require_device_id()
        dev_lock = self._session.get_device_lock(device_id)
        with dev_lock:
            if self._pixel_count <= 0:
                self._pixel_count = self._get_formatted_spectrum_length()
            err = (c_long * 1)(0)
            buffer = (c_double * self._pixel_count)()
            _invoke_native(
                caller,
                getattr(self._api, function_name),
                device_id,
                err,
                buffer,
                self._pixel_count,
            )
            self._raise_if_error(err[0], caller)
            return np.array(buffer, dtype=np.float64)

    def _require_device_id(self) -> int:
        if self._device_id is None:
            raise OceanDirectError("OceanDirect 光谱仪尚未打开")
        return self._device_id

    def _raise_if_error(self, errno: int, caller: str) -> None:
        if int(errno) == 0:
            return
        raise OceanDirectError(self._decode_error(int(errno), caller))

    def _decode_error(self, errno: int, caller: str) -> str:
        try:
            length = int(self._api.odapi_get_error_string_length(errno))
            buffer = create_string_buffer(b"\000" * max(length, 1))
            self._api.odapi_get_error_string(errno, buffer, length)
            message = buffer.value.decode(errors="replace")
        except Exception:
            message = "未知 SDK 错误"
        return f"OceanDirect {caller} 失败，错误码 {errno}：{message}"
