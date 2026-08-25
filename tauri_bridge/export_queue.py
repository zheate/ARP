"""Sequential Excel export queue shared by all stations."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from combined_test.excel_export import ExcelTestRecord, save_test_records
from combined_test.runtime_logging import get_runtime_logger


_LOGGER = get_runtime_logger("station_export")


@dataclass
class ExportTask:
    station_id: str
    target_path: Path
    records: list[ExcelTestRecord]
    callback: Callable[[str | None, Exception | None], None] | None = None
    session: Any | None = None
    attempts: tuple[Any, ...] = ()
    automatic_samples_path: Path | None = None


class ExportQueueWorker:
    """Sequential worker for heavy Excel export operations."""

    def __init__(self) -> None:
        self._queue: queue.Queue[ExportTask | None] = queue.Queue(maxsize=32)
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._accepting = False
        self._sentinel_enqueued = False

    def start(self) -> None:
        with self._state_lock:
            if self._thread is None or not self._thread.is_alive():
                self._accepting = True
                self._sentinel_enqueued = False
                self._thread = threading.Thread(
                    target=self._run,
                    name="export-queue-worker",
                    daemon=True,
                )
                self._thread.start()

    def submit(self, task: ExportTask) -> bool:
        with self._state_lock:
            if not self._accepting:
                return False
            try:
                self._queue.put_nowait(task)
                return True
            except queue.Full:
                return False

    def graceful_stop(self, timeout_s: float = 30.0) -> bool:
        """Stop accepting work, drain accepted tasks, and then stop the worker."""

        timeout = max(0.0, float(timeout_s))
        deadline = time.monotonic() + timeout
        with self._state_lock:
            self._accepting = False
            if not self._sentinel_enqueued:
                try:
                    self._queue.put(None, timeout=timeout)
                except queue.Full:
                    return False
                self._sentinel_enqueued = True
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        return thread is None or not thread.is_alive()

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            if task is None:
                self._queue.task_done()
                break
            started_at = time.perf_counter()
            out_path: str | None = None
            error: Exception | None = None
            try:
                saved_path = save_test_records(
                    task.target_path,
                    task.records,
                    session=task.session,
                    attempts=task.attempts,
                    automatic_samples_path=task.automatic_samples_path,
                )
                out_path = str(saved_path)
                duration_ms = (time.perf_counter() - started_at) * 1000.0
                _LOGGER.info(
                    "工位 %s Excel 导出完成 | path=%s | duration_ms=%.2f",
                    task.station_id,
                    out_path,
                    duration_ms,
                )
            except Exception as exc:
                error = exc
                _LOGGER.exception("工位 %s Excel 导出失败", task.station_id)
            finally:
                if task.callback is not None:
                    try:
                        task.callback(out_path, error)
                    except Exception:
                        _LOGGER.exception("工位 %s Excel 导出回调失败", task.station_id)
                self._queue.task_done()
