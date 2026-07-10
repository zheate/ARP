from __future__ import annotations

from ctypes import CDLL, POINTER, byref, c_double, c_long, c_ulong, create_string_buffer, cdll
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OCEAN_DIRECT_DLL = PROJECT_ROOT / "assets" / "libs" / "ocean_direct" / "OceanDirect.dll"


class OceanDirectError(RuntimeError):
    pass


class OceanDirectControl:
    def __init__(self, dll_path: str | Path | None = None, api: Any | None = None) -> None:
        self._api = api if api is not None else self._load_api(dll_path)
        self._device_id: int | None = None
        self._pixel_count = 0
        self._api.odapi_initialize()
        self._configure_return_types()

    def _load_api(self, dll_path: str | Path | None) -> CDLL:
        path = Path(dll_path) if dll_path is not None else DEFAULT_OCEAN_DIRECT_DLL
        if not path.exists():
            raise FileNotFoundError(f"OceanDirect.dll not found: {path}")
        return cdll.LoadLibrary(str(path))

    def _configure_return_types(self) -> None:
        for name in (
            "odapi_get_maximum_intensity",
            "odapi_get_formatted_spectrum_length",
            "odapi_get_unformatted_spectrum_length",
        ):
            func = getattr(self._api, name, None)
            if func is not None and hasattr(func, "restype"):
                if name == "odapi_get_maximum_intensity":
                    func.restype = c_double

    def find_usb_devices(self) -> int:
        return int(self._api.odapi_probe_devices())

    def get_number_devices(self) -> int:
        return int(self._api.odapi_get_number_of_device_ids())

    def get_device_ids(self) -> list[int]:
        count = self.get_number_devices()
        if count <= 0:
            return []
        ids = (c_long * count)()
        err = (c_long * 1)(0)
        copied = int(self._api.odapi_get_device_ids(ids, err))
        self._raise_if_error(err[0], "get_device_ids")
        return [int(ids[index]) for index in range(max(0, min(copied, count)))]

    def open_device(self, device_id: int) -> int:
        err = (c_long * 1)(0)
        self._api.odapi_open_device(int(device_id), err)
        if err[0] != 0:
            return -1
        self._device_id = int(device_id)
        self._pixel_count = self._get_formatted_spectrum_length()
        return 0

    def set_integration_time(self, u_second: int) -> int:
        device_id = self._require_device_id()
        err = (c_long * 1)(0)
        self._api.odapi_set_integration_time_micros(device_id, err, c_ulong(int(u_second)))
        return -1 if err[0] != 0 else 0

    def get_integration_time(self) -> int:
        return self._call_int_with_error("odapi_get_integration_time_micros", "get_integration_time")

    def get_maximum_integration_time(self) -> int:
        return self._call_int_with_error("odapi_get_maximum_integration_time_micros", "get_maximum_integration_time")

    def get_minimum_integration_time(self) -> int:
        return self._call_int_with_error("odapi_get_minimum_integration_time_micros", "get_minimum_integration_time")

    def get_maximum_intensity(self) -> float:
        device_id = self._require_device_id()
        err = (c_long * 1)(0)
        value = float(self._api.odapi_get_maximum_intensity(device_id, err))
        self._raise_if_error(err[0], "get_maximum_intensity")
        return value

    def get_wavelength(self) -> NDArray[np.float64]:
        return self._read_double_array("odapi_get_wavelengths", "get_wavelength")

    def get_intensity(self) -> NDArray[np.float64]:
        return self._read_double_array("odapi_get_formatted_spectrum", "get_intensity")

    def close_device(self) -> None:
        if self._device_id is None:
            return
        err = (c_long * 1)(0)
        self._api.odapi_close_device(self._device_id, err)
        self._raise_if_error(err[0], "close_device")
        self._device_id = None

    def shutdown(self) -> None:
        shutdown = getattr(self._api, "odapi_shutdown", None)
        if shutdown is not None:
            shutdown()

    def _get_formatted_spectrum_length(self) -> int:
        return self._call_int_with_error("odapi_get_formatted_spectrum_length", "get_formatted_spectrum_length")

    def _call_int_with_error(self, function_name: str, caller: str) -> int:
        device_id = self._require_device_id()
        err = (c_long * 1)(0)
        value = int(getattr(self._api, function_name)(device_id, err))
        self._raise_if_error(err[0], caller)
        return value

    def _read_double_array(self, function_name: str, caller: str) -> NDArray[np.float64]:
        device_id = self._require_device_id()
        if self._pixel_count <= 0:
            self._pixel_count = self._get_formatted_spectrum_length()
        err = (c_long * 1)(0)
        buffer = (c_double * self._pixel_count)()
        getattr(self._api, function_name)(device_id, err, buffer, self._pixel_count)
        self._raise_if_error(err[0], caller)
        return np.array(buffer, dtype=np.float64)

    def _require_device_id(self) -> int:
        if self._device_id is None:
            raise OceanDirectError("OceanDirect device is not open")
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
            message = "unknown error"
        return f"{caller} errcode({errno}): {message}"
