"""Optional Rust acceleration for high-frequency acquisition calculations.

The native library is loaded lazily inside the existing Python device process.
Missing or incompatible builds therefore never prevent the application from
starting; callers can transparently retain their Python implementation.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import numpy as np


NATIVE_ABI_VERSION = 2
NATIVE_DLL_NAME = "arp_realtime_core.dll" if sys.platform == "win32" else "libarp_realtime_core.so"
DISABLE_ENVIRONMENT_VARIABLE = "ARP_DISABLE_NATIVE_REALTIME"
DLL_ENVIRONMENT_VARIABLE = "ARP_REALTIME_CORE_DLL"


class NativeRealtimeUnavailable(RuntimeError):
    """Raised when the optional native implementation cannot be used."""


class _NativeSpectrumStats(ctypes.Structure):
    _fields_ = (
        ("peak_wavelength_nm", ctypes.c_double),
        ("peak_intensity", ctypes.c_double),
        ("centroid_nm", ctypes.c_double),
        ("fwhm_nm", ctypes.c_double),
    )


class _NativeSampleSummary(ctypes.Structure):
    _fields_ = (
        ("latest", ctypes.c_double),
        ("mean", ctypes.c_double),
        ("minimum", ctypes.c_double),
        ("maximum", ctypes.c_double),
        ("standard_deviation", ctypes.c_double),
        ("rms", ctypes.c_double),
    )


@dataclass(frozen=True)
class NativeSpectrumStats:
    peak_wavelength_nm: float
    peak_intensity: float
    centroid_nm: float
    fwhm_nm: float


@dataclass(frozen=True)
class NativeSampleSummary:
    latest: float
    mean: float
    minimum: float
    maximum: float
    standard_deviation: float
    rms: float


def _truthy_environment_value(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _candidate_paths() -> tuple[Path, ...]:
    candidates: list[Path] = []
    configured = os.environ.get(DLL_ENVIRONMENT_VARIABLE, "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / NATIVE_DLL_NAME)

    candidates.append(Path(sys.executable).resolve().parent / NATIVE_DLL_NAME)
    project_root = Path(__file__).resolve().parent.parent
    candidates.extend(
        (
            project_root / "assets" / "libs" / "realtime_core" / NATIVE_DLL_NAME,
            project_root / "rust-realtime-core" / "target" / "release" / NATIVE_DLL_NAME,
            project_root / "rust-realtime-core" / "target" / "debug" / NATIVE_DLL_NAME,
        )
    )
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return tuple(unique)


class NativeRealtimeCore:
    """Thread-safe wrapper around the allocation-light Rust C ABI."""

    def __init__(self, library: ctypes.CDLL, path: Path) -> None:
        self.path = path
        self._library = library
        self._call_lock = threading.RLock()
        double_pointer = ctypes.POINTER(ctypes.c_double)

        library.arp_realtime_core_abi_version.argtypes = []
        library.arp_realtime_core_abi_version.restype = ctypes.c_uint32
        version = int(library.arp_realtime_core_abi_version())
        if version != NATIVE_ABI_VERSION:
            raise NativeRealtimeUnavailable(
                f"Rust realtime ABI mismatch: expected {NATIVE_ABI_VERSION}, received {version}"
            )

        library.arp_calculate_spectrum_stats.argtypes = (
            double_pointer,
            double_pointer,
            ctypes.c_size_t,
            ctypes.POINTER(_NativeSpectrumStats),
        )
        library.arp_calculate_spectrum_stats.restype = ctypes.c_int32
        library.arp_calculate_pib.argtypes = (
            double_pointer,
            double_pointer,
            ctypes.c_size_t,
            ctypes.c_double,
            ctypes.c_double,
            double_pointer,
        )
        library.arp_calculate_pib.restype = ctypes.c_int32
        library.arp_summarize_samples.argtypes = (
            double_pointer,
            ctypes.c_size_t,
            ctypes.POINTER(_NativeSampleSummary),
        )
        library.arp_summarize_samples.restype = ctypes.c_int32

    @staticmethod
    def _array(values: Iterable[float]) -> np.ndarray:
        array = np.ascontiguousarray(values, dtype=np.float64)
        if array.ndim != 1 or array.size == 0:
            raise ValueError("native realtime calculation requires a non-empty 1D array")
        return array

    @staticmethod
    def _pointer(array: np.ndarray) -> ctypes.POINTER(ctypes.c_double):
        return array.ctypes.data_as(ctypes.POINTER(ctypes.c_double))

    def calculate_spectrum_stats(
        self,
        wavelength: Iterable[float],
        intensity: Iterable[float],
    ) -> NativeSpectrumStats:
        x_array = self._array(wavelength)
        y_array = self._array(intensity)
        if x_array.size != y_array.size:
            raise ValueError("wavelength and intensity lengths differ")
        output = _NativeSpectrumStats()
        with self._call_lock:
            status = self._library.arp_calculate_spectrum_stats(
                self._pointer(x_array), self._pointer(y_array), x_array.size, ctypes.byref(output)
            )
        if status != 0:
            raise NativeRealtimeUnavailable(f"Rust spectrum calculation failed: {status}")
        return NativeSpectrumStats(
            peak_wavelength_nm=float(output.peak_wavelength_nm),
            peak_intensity=float(output.peak_intensity),
            centroid_nm=float(output.centroid_nm),
            fwhm_nm=float(output.fwhm_nm),
        )

    def calculate_pib(
        self,
        wavelength: Iterable[float],
        intensity: Iterable[float],
        center_nm: float,
        half_range_nm: float,
    ) -> float:
        x_array = self._array(wavelength)
        y_array = self._array(intensity)
        if x_array.size != y_array.size:
            raise ValueError("wavelength and intensity lengths differ")
        output = ctypes.c_double()
        with self._call_lock:
            status = self._library.arp_calculate_pib(
                self._pointer(x_array),
                self._pointer(y_array),
                x_array.size,
                float(center_nm),
                float(half_range_nm),
                ctypes.byref(output),
            )
        if status != 0:
            raise NativeRealtimeUnavailable(f"Rust PIB calculation failed: {status}")
        return float(output.value)

    def summarize_samples(self, values: Iterable[float]) -> NativeSampleSummary:
        array = self._array(values)
        output = _NativeSampleSummary()
        with self._call_lock:
            status = self._library.arp_summarize_samples(
                self._pointer(array), array.size, ctypes.byref(output)
            )
        if status != 0:
            raise NativeRealtimeUnavailable(f"Rust sample summary failed: {status}")
        return NativeSampleSummary(
            latest=float(output.latest),
            mean=float(output.mean),
            minimum=float(output.minimum),
            maximum=float(output.maximum),
            standard_deviation=float(output.standard_deviation),
            rms=float(output.rms),
        )


@lru_cache(maxsize=1)
def get_native_realtime_core() -> NativeRealtimeCore | None:
    """Return the compatible native core, or ``None`` for safe Python fallback."""

    if _truthy_environment_value(DISABLE_ENVIRONMENT_VARIABLE):
        return None
    for path in _candidate_paths():
        if not path.is_file():
            continue
        try:
            return NativeRealtimeCore(ctypes.CDLL(str(path)), path)
        except (AttributeError, NativeRealtimeUnavailable, OSError):
            continue
    return None


def native_realtime_status() -> dict[str, object]:
    core = get_native_realtime_core()
    return {
        "available": core is not None,
        "abiVersion": NATIVE_ABI_VERSION,
        "library": core.path.name if core is not None else "",
        "disabled": _truthy_environment_value(DISABLE_ENVIRONMENT_VARIABLE),
    }
