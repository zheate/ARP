"""Run the local bridge as a newline-delimited JSON process."""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

from combined_test.runtime_logging import configure_runtime_logging, get_runtime_logger

from .service import BACKGROUND_FILE_TASK_METHODS, SAFETY_TASK_METHODS, BridgeService


_protocol_stdout: object | None = None
_protocol_stdout_lock = threading.Lock()
_LOGGER = get_runtime_logger("tauri_bridge")


def _reserve_protocol_stdout() -> None:
    """Keep protocol output isolated from device-library diagnostics."""

    global _protocol_stdout
    if _protocol_stdout is None:
        _protocol_stdout = sys.stdout
    sys.stdout = sys.stderr


def _read_request_lines() -> Iterator[str]:
    """Decode the Rust bridge pipe as UTF-8, independent of Windows locale."""
    input_buffer = getattr(sys.stdin, "buffer", None)
    if input_buffer is not None:
        for raw_line in input_buffer:
            yield raw_line.decode("utf-8")
        return
    for line in sys.stdin:
        yield line


def _write_response(response: dict[str, object]) -> None:
    payload = (
        json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    with _protocol_stdout_lock:
        protocol_stdout = _protocol_stdout or sys.stdout
        output = getattr(protocol_stdout, "buffer", None)
        if output is not None:
            output.write(payload)
            output.flush()
            return
        protocol_stdout.write(payload.decode("utf-8"))
        protocol_stdout.flush()


def _request_method(line: str) -> str:
    try:
        request = json.loads(line)
    except json.JSONDecodeError:
        return ""
    return str(request.get("method", "")) if isinstance(request, dict) else ""


def _run_read_only() -> int:
    _LOGGER.warning("Python 后端以只读模式启动；硬件控制不可用")
    service = BridgeService()
    try:
        for line in _read_request_lines():
            if line.strip():
                _write_response(service.handle_line(line))
    except KeyboardInterrupt:
        return 0
    return 0


def _run_headless_backend() -> int:
    """Serve requests without a GUI event loop or Qt dependency."""

    from .headless_backend import HeadlessBackend
    backend = HeadlessBackend()
    service = BridgeService(backend)
    backend.start_remembered_device_discovery()
    writer_queue: queue.Queue[dict[str, object] | None] = queue.Queue()

    def write_responses() -> None:
        while True:
            response = writer_queue.get()
            if response is None:
                return
            _write_response(response)

    writer_thread = threading.Thread(
        target=write_responses,
        name="tauri-stdout",
        daemon=True,
    )
    writer_thread.start()
    regular_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bridge-command")
    safety_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="safety-command")
    file_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="report-task")

    def submit_request(
        executor: ThreadPoolExecutor,
        request_line: str,
        queued_at: float,
        *,
        file_task: bool = False,
    ) -> None:
        command_generations = backend.capture_command_generations()
        executor.submit(
            lambda: writer_queue.put(
                service.handle_line(
                    request_line,
                    queued_at_monotonic=queued_at,
                    file_task=file_task,
                    command_generations=command_generations,
                )
            )
        )

    _LOGGER.info("Tauri headless Python hardware backend is ready")
    try:
        for line in _read_request_lines():
            if not line.strip():
                continue
            queued_at = time.perf_counter()
            method = _request_method(line)
            if method in SAFETY_TASK_METHODS:
                submit_request(safety_executor, line, queued_at)
            elif method in BACKGROUND_FILE_TASK_METHODS:
                submit_request(file_executor, line, queued_at, file_task=True)
            else:
                submit_request(regular_executor, line, queued_at)
    except KeyboardInterrupt:
        pass
    finally:
        safety_executor.shutdown(wait=True, cancel_futures=False)
        regular_executor.shutdown(wait=True, cancel_futures=False)
        file_executor.shutdown(wait=False, cancel_futures=True)
        writer_queue.put(None)
        writer_thread.join(timeout=2.0)
    return 0


def main() -> int:
    configure_runtime_logging(process_role="tauri-python-backend")
    _reserve_protocol_stdout()
    try:
        return _run_headless_backend()
    except (ImportError, ModuleNotFoundError) as exc:
        _LOGGER.exception("活动后端依赖加载失败，切换到只读模式")
        sys.stderr.write(f"Active backend unavailable; using read-only mode: {exc}\n")
        sys.stderr.flush()
        return _run_read_only()


if __name__ == "__main__":
    raise SystemExit(main())
