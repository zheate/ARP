"""Run the local bridge as a newline-delimited JSON process."""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

from combined_test.runtime_logging import configure_runtime_logging, get_runtime_logger

from .service import BridgeService


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


def _run_qt_backend() -> int:
    from PySide6.QtCore import QObject, Qt, Signal, Slot
    from PySide6.QtWidgets import QApplication

    from combined_test.window import MainWindow
    from .legacy_backend import BACKGROUND_FILE_TASK_METHODS, LegacyWindowBackend

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    # The Tauri frontend owns all visible charts.  The hidden Qt compatibility
    # window only needs the controller's widget-backed state model.
    window = MainWindow(headless=True)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    backend = LegacyWindowBackend(window)
    service = BridgeService(backend)

    class ResponseWriter:
        """Serialize and write responses on a worker thread.

        The Qt main thread only builds the response dict (widget reads and
        cached series payloads); json.dumps of large snapshots runs here so
        device acquisition events are never delayed by serialization. A
        FIFO queue serializes stdout writes. Rust routes responses by request
        ID, so background file tasks may safely finish out of order.
        """

        def __init__(self) -> None:
            self._queue: queue.Queue[dict[str, object] | None] = queue.Queue()
            self._thread = threading.Thread(
                target=self._run,
                name="tauri-stdout",
                daemon=True,
            )
            self._thread.start()

        def submit(self, response: dict[str, object]) -> None:
            self._queue.put(response)

        def stop(self) -> None:
            self._queue.put(None)

        def _run(self) -> None:
            while True:
                response = self._queue.get()
                if response is None:
                    break
                _write_response(response)

    writer = ResponseWriter()
    file_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="excel-scan")

    class Dispatcher(QObject):
        request_received = Signal(str, float)
        input_closed = Signal()

        def __init__(self) -> None:
            super().__init__()
            self.request_received.connect(self.handle, Qt.ConnectionType.QueuedConnection)
            self.input_closed.connect(app.quit, Qt.ConnectionType.QueuedConnection)

        @Slot(str, float)
        def handle(self, line: str, queued_at_monotonic: float) -> None:
            writer.submit(
                service.handle_line(line, queued_at_monotonic=queued_at_monotonic)
            )

    dispatcher = Dispatcher()

    def read_requests() -> None:
        try:
            for line in _read_request_lines():
                if line.strip():
                    queued_at_monotonic = time.perf_counter()
                    if _request_method(line) in BACKGROUND_FILE_TASK_METHODS:
                        file_executor.submit(
                            lambda request_line=line, queued_at=queued_at_monotonic: writer.submit(
                                service.handle_line(
                                    request_line,
                                    queued_at_monotonic=queued_at,
                                    file_task=True,
                                )
                            )
                        )
                    else:
                        dispatcher.request_received.emit(line, queued_at_monotonic)
        finally:
            dispatcher.input_closed.emit()

    threading.Thread(target=read_requests, name="tauri-stdin", daemon=True).start()
    _LOGGER.info("Tauri Python 硬件后端已就绪")
    exit_code = int(app.exec())
    file_executor.shutdown(wait=False, cancel_futures=True)
    writer.stop()
    return exit_code


def _run_headless_backend() -> int:
    """Serve requests without a GUI event loop or Qt dependency."""

    from .headless_backend import HeadlessBackend
    from .legacy_backend import BACKGROUND_FILE_TASK_METHODS

    service = BridgeService(HeadlessBackend())
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
    file_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="excel-scan")
    _LOGGER.info("Tauri headless Python hardware backend is ready")
    try:
        for line in _read_request_lines():
            if not line.strip():
                continue
            queued_at = time.perf_counter()
            if _request_method(line) in BACKGROUND_FILE_TASK_METHODS:
                file_executor.submit(
                    lambda request_line=line, queued=queued_at: writer_queue.put(
                        service.handle_line(
                            request_line,
                            queued_at_monotonic=queued,
                            file_task=True,
                        )
                    )
                )
            else:
                writer_queue.put(
                    service.handle_line(line, queued_at_monotonic=queued_at)
                )
    except KeyboardInterrupt:
        pass
    finally:
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
