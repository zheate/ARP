"""Small JSON-lines protocol used by the local Tauri/Python bridge.

The bridge intentionally exposes read-only methods first.  Hardware discovery,
connection, and output commands are outside the phase-1 contract.
"""

from __future__ import annotations

from typing import Any


PROTOCOL_VERSION = 1


def success_response(request_id: str, result: Any) -> dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "id": request_id,
        "ok": True,
        "result": result,
    }


def error_response(
    request_id: str,
    code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "id": request_id,
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
    }
