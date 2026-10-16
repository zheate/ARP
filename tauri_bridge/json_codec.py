"""Encode spectrum responses without changing the existing JSON-lines bytes.

Only ordinary JSON values in the shared fixed-notation float range use the
native encoder. Everything else retains the standard-library implementation.
"""
from __future__ import annotations

import json
from typing import Any

try:
    import orjson as _orjson
except ImportError:
    _orjson = None


_MIN_BULK_SPECTRUM_SAMPLES = 1024


def _has_bulk_spectrum(response: Any) -> bool:
    if type(response) is not dict or type(response.get("result")) is not dict:
        return False
    result = response["result"]
    # The compatibility walk costs more than it saves for a PD history made
    # of many small dictionaries. Keep these mixed/full responses on stdlib.
    pd = result.get("pd")
    if type(pd) is dict:
        points = pd.get("points")
        if type(points) in (list, tuple) and points:
            return False
    measurements = result.get("measurements")
    if type(measurements) is dict:
        spectrum = measurements.get("spectrum")
        if type(spectrum) is dict:
            values = spectrum.get("intensity")
            if type(values) in (list, tuple) and len(values) >= _MIN_BULK_SPECTRUM_SAMPLES:
                return True
    spectra = result.get("spectra")
    return type(spectra) in (list, tuple) and any(
        type(spectrum) is dict
        and type(spectrum.get("intensity")) in (list, tuple)
        and len(spectrum["intensity"]) >= _MIN_BULK_SPECTRUM_SAMPLES
        for spectrum in spectra
    )


def _native_values_compatible(response: Any) -> bool:
    pending = [response]
    seen_containers: set[int] = set()
    while pending:
        value = pending.pop()
        kind = type(value)
        if kind is float:
            # Both pinned encoders use identical fixed notation here, including
            # negative zero. Scientific notation and nonfinite values fall back;
            # no value is rounded, clamped, converted or replaced with null.
            if not (value == 0.0 or 0.0001 <= abs(value) < 1e16):
                return False
        elif kind is dict:
            identity = id(value)
            if identity in seen_containers or any(type(key) is not str for key in value):
                return False
            seen_containers.add(identity)
            pending.extend(value.values())
        elif kind is list or kind is tuple:
            identity = id(value)
            if identity in seen_containers:
                return False
            seen_containers.add(identity)
            pending.extend(value)
        elif kind not in (str, int, bool, type(None)):
            return False
    return True


def encode_response(response: Any) -> bytes:
    if _orjson is not None and _has_bulk_spectrum(response) and _native_values_compatible(response):
        try:
            return _orjson.dumps(response, option=_orjson.OPT_APPEND_NEWLINE)
        except _orjson.JSONEncodeError:
            # Preserve stdlib handling of large integers, surrogate strings,
            # nesting limits and any other unsupported native input.
            pass
    return (json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
