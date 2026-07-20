"""Run the local bridge as a newline-delimited JSON process."""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Iterator

from .service import BridgeService


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
    output = getattr(sys.stdout, "buffer", None)
    if output is not None:
        output.write(payload)
        output.flush()
        return
    sys.stdout.write(payload.decode("utf-8"))
    sys.stdout.flush()


def _run_read_only() -> int:
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
    window = MainWindow()
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
    return int(app.exec())


def main() -> int:
    try:
        return _run_qt_backend()
    except (ImportError, ModuleNotFoundError) as exc:
        sys.stderr.write(f"Active backend unavailable; using read-only mode: {exc}\n")
        sys.stderr.flush()
        return _run_read_only()


if __name__ == "__main__":
    raise SystemExit(main())
