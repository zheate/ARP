"""JSON request service for the Tauri frontend."""

from __future__ import annotations

import json
import platform
import time
from datetime import datetime, timezone
from typing import Any

from combined_test.native_realtime import native_realtime_status
from combined_test.runtime_logging import get_runtime_logger, runtime_log_path

from .protocol import PROTOCOL_VERSION, error_response, success_response


_LOGGER = get_runtime_logger("bridge_service")

BACKGROUND_FILE_TASK_METHODS = frozenset(
    {
        "shippingReport.inspect",
        "shippingReport.batchInspect",
        "shippingReport.preview",
        "shippingReport.generate",
        "shippingReport.batchGenerateItem",
    }
)

SAFETY_TASK_METHODS = frozenset(
    {
        "app.stopAll",
        "app.shutdown",
        "automatic.end",
    }
)


class BridgeService:
    """Handle one request; an injected backend owns all mutable state."""

    def __init__(self, backend: Any | None = None) -> None:
        self.backend = backend

    def handle_line(
        self,
        line: str,
        *,
        queued_at_monotonic: float | None = None,
        file_task: bool = False,
    ) -> dict[str, Any]:
        started_at = time.perf_counter()
        request_id = ""
        method: Any = ""
        try:
            return self._handle_line(line, file_task=file_task)
        finally:
            try:
                request = json.loads(line)
                if isinstance(request, dict):
                    request_id = str(request.get("id", ""))
                    method = request.get("method", "")
            except (json.JSONDecodeError, TypeError):
                pass
            finished_at = time.perf_counter()
            queued_ms = max(0.0, (started_at - (queued_at_monotonic or started_at)) * 1000.0)
            execution_ms = max(0.0, (finished_at - started_at) * 1000.0)
            if method != "app.snapshot" or queued_ms >= 50.0 or execution_ms >= 50.0:
                _LOGGER.info(
                    "桥接请求耗时 | id=%s | method=%s | queue_ms=%.3f | execution_ms=%.3f | worker=%s",
                    request_id,
                    method,
                    queued_ms,
                    execution_ms,
                    "file" if file_task else "main",
                )

    def _handle_line(self, line: str, *, file_task: bool = False) -> dict[str, Any]:
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            _LOGGER.warning("收到无效 JSON 请求 | payload=%r", line[:500])
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
                        "nativeRealtime": native_realtime_status(),
                    }
                )
                return success_response(request_id, result)
            return success_response(
                request_id,
                {
                    "status": "ok",
                    "protocolVersion": PROTOCOL_VERSION,
                    "mode": "read_only",
                    "nativeRealtime": native_realtime_status(),
                },
            )
        if method == "app.snapshot":
            params = request.get("params", {})
            if not isinstance(params, dict):
                return error_response(request_id, "invalid_params", "params 必须是 JSON 对象")
            if self.backend is not None:
                return success_response(request_id, self.backend.snapshot(params))
            return success_response(request_id, self.build_snapshot())

        # Read-only mode owns no hardware, so there is nothing that needs an
        # emergency ramp-down before the Tauri window may close.
        if method == "app.shutdown" and self.backend is None:
            shutdown = {
                "requested": True,
                "confirmed": True,
                "measuredCurrentA": None,
                "outputOffConfirmed": True,
                "reason": "只读后端未持有硬件输出",
            }
            return success_response(
                request_id,
                {
                    "shutdownConfirmed": True,
                    "exportsDrained": True,
                    "shutdown": shutdown,
                    "stations": {},
                },
            )

        if self.backend is not None:
            params = request.get("params", {})
            if not isinstance(params, dict):
                return error_response(request_id, "invalid_params", "params 必须是 JSON 对象")
            try:
                _LOGGER.info("前端命令开始 | id=%s | method=%s | params=%r", request_id, method, params)
                if file_task:
                    result = self.backend.dispatch_file_task(str(method), params)
                else:
                    result = self.backend.dispatch(str(method), params)
                _LOGGER.info("前端命令完成 | id=%s | method=%s", request_id, method)
                return success_response(request_id, result)
            except KeyError:
                pass
            except (TypeError, ValueError) as exc:
                _LOGGER.warning(
                    "前端命令参数错误 | id=%s | method=%s | error=%s",
                    request_id,
                    method,
                    exc,
                )
                return error_response(request_id, "invalid_params", str(exc))
            except Exception as exc:
                _LOGGER.exception("前端命令执行失败 | id=%s | method=%s", request_id, method)
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
                "runtimeLogPath": str(runtime_log_path() or ""),
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
                "detail": "只读模式不执行自动测试",
                "controlsEnabled": False,
            },
            "safety": {
                "hardwareAccess": False,
                "commandMode": "read_only",
                "detail": "当前桥接层不会扫描、连接或控制真实设备",
                "controlOwner": "none",
                "manualControlsAllowed": False,
                "shutdown": {
                    "requested": False,
                    "confirmed": True,
                    "measuredCurrentA": None,
                    "outputOffConfirmed": True,
                    "reason": "只读后端未持有硬件输出",
                },
            },
        }
