"""Run the local bridge as a newline-delimited JSON process."""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Iterator

from combined_test.runtime_logging import configure_runtime_logging, get_runtime_logger

from .service import BridgeService


_protocol_stdout: object | None = None
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
    payload = (json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8")
    protocol_stdout = _protocol_stdout or sys.stdout
    output = getattr(protocol_stdout, "buffer", None)
    if output is not None:
        output.write(payload)
        output.flush()
        return
    protocol_stdout.write(payload.decode("utf-8"))
    protocol_stdout.flush()


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
    from .legacy_backend import LegacyWindowBackend

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    # The Tauri frontend owns all visible charts.  The hidden Qt compatibility
    # window only needs the controller's widget-backed state model.
    window = MainWindow(headless=True)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    backend = LegacyWindowBackend(window)
    service = BridgeService(backend)

    class Dispatcher(QObject):
        request_received = Signal(str)
        input_closed = Signal()

        def __init__(self) -> None:
            super().__init__()
            self.request_received.connect(self.handle, Qt.ConnectionType.QueuedConnection)
            self.input_closed.connect(app.quit, Qt.ConnectionType.QueuedConnection)

        @Slot(str)
        def handle(self, line: str) -> None:
            _write_response(service.handle_line(line))

    dispatcher = Dispatcher()

    def read_requests() -> None:
        try:
            for line in _read_request_lines():
                if line.strip():
                    dispatcher.request_received.emit(line)
        finally:
            dispatcher.input_closed.emit()

    threading.Thread(target=read_requests, name="tauri-stdin", daemon=True).start()
    _LOGGER.info("Tauri Python 硬件后端已就绪")
    return int(app.exec())


def main() -> int:
    configure_runtime_logging(process_role="tauri-python-backend")
    _reserve_protocol_stdout()
    try:
        return _run_qt_backend()
    except (ImportError, ModuleNotFoundError) as exc:
        _LOGGER.exception("活动后端依赖加载失败，切换到只读模式")
        sys.stderr.write(f"Active backend unavailable; using read-only mode: {exc}\n")
        sys.stderr.flush()
        return _run_read_only()


if __name__ == "__main__":
    raise SystemExit(main())
