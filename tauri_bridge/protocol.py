"""Versioned JSON-lines protocol used by the local Tauri/Python bridge."""

from __future__ import annotations

from typing import Any


PROTOCOL_VERSION = 2


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
