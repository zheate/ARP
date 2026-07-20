"""JSON request service for the Tauri frontend."""

from __future__ import annotations

import json
import platform
from datetime import datetime, timezone
from typing import Any

from .protocol import PROTOCOL_VERSION, error_response, success_response


class BridgeService:
    """Handle one request; an injected backend owns all mutable state."""

    def __init__(self, backend: Any | None = None) -> None:
        self.backend = backend

    def handle_line(self, line: str) -> dict[str, Any]:
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            return error_response("", "invalid_json", "请求不是有效的 JSON")

        if not isinstance(request, dict):
            return error_response("", "invalid_request", "请求必须是 JSON 对象")

        request_id = str(request.get("id", ""))
        if request.get("v") != PROTOCOL_VERSION:
            return error_response(
                request_id,
                "unsupported_version",
                f"仅支持协议版本 {PROTOCOL_VERSION}",
            )

        method = request.get("method")
        if method == "system.ping":
            if self.backend is not None:
                result = dict(self.backend.ping())
                result.update(
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                    }
                )
                return success_response(request_id, result)
            return success_response(
                request_id,
                {
                    "status": "ok",
                    "protocolVersion": PROTOCOL_VERSION,
                    "mode": "read_only",
                },
            )
        if method == "app.snapshot":
            params = request.get("params", {})
            if not isinstance(params, dict):
                return error_response(request_id, "invalid_params", "params 必须是 JSON 对象")
            if self.backend is not None:
                return success_response(request_id, self.backend.snapshot(params))
            return success_response(request_id, self.build_snapshot())

        if self.backend is not None:
            params = request.get("params", {})
            if not isinstance(params, dict):
                return error_response(request_id, "invalid_params", "params 必须是 JSON 对象")
            try:
                return success_response(request_id, self.backend.dispatch(str(method), params))
            except KeyError:
                pass
            except (TypeError, ValueError) as exc:
                return error_response(request_id, "invalid_params", str(exc))
            except Exception as exc:
                return error_response(request_id, "action_failed", str(exc))

        return error_response(
            request_id,
            "method_not_found",
            f"未知方法：{method}",
        )

    def build_snapshot(self) -> dict[str, Any]:
        """Return honest state without probing or opening any device."""

        unavailable_reason = "只读桥接阶段尚未启用硬件访问"
        return {
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "backend": {
                "connected": True,
                "mode": "read_only",
                "protocolVersion": PROTOCOL_VERSION,
                "pythonVersion": platform.python_version(),
            },
            "devices": {
                "powerSupply": {
                    "state": "disconnected",
                    "label": "CH341 / TDK",
                    "detail": unavailable_reason,
                },
                "powerMeter": {
                    "state": "disconnected",
                    "label": "VISA 串口",
                    "detail": unavailable_reason,
                },
                "spectrometer": {
                    "state": "disconnected",
                    "label": "Ocean Insight",
                    "detail": unavailable_reason,
                },
            },
            "automaticTest": {
                "state": "idle",
                "detail": "等待后续接入现有 Python 自动测试控制器",
                "controlsEnabled": False,
            },
            "safety": {
                "hardwareAccess": False,
                "commandMode": "read_only",
                "detail": "当前桥接层不会扫描、连接或控制真实设备",
            },
        }
