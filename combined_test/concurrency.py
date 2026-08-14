"""Thread and callback primitives for the headless runtime."""

from __future__ import annotations

import threading
import time
import weakref
from collections.abc import Callable
from typing import Any


class BoundSignal:
    """Thread-safe callback collection attached to one object instance."""

    def __init__(self) -> None:
        self._callbacks: list[Callable[..., Any]] = []
        self._lock = threading.RLock()

    def connect(self, callback: Callable[..., Any], *_args: Any, **_kwargs: Any) -> None:
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    def disconnect(self, callback: Callable[..., Any] | None = None) -> None:
        with self._lock:
            if callback is None:
                self._callbacks.clear()
            elif callback in self._callbacks:
                self._callbacks.remove(callback)

    def emit(self, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            callback(*args, **kwargs)


class Signal:
    """Descriptor compatible with class-level ``Signal(...)`` declarations."""

    def __init__(self, *_types: type[Any]) -> None:
        self._instances: weakref.WeakKeyDictionary[object, BoundSignal] = weakref.WeakKeyDictionary()
        self._lock = threading.Lock()

    def __get__(self, instance: object | None, _owner: type[Any]) -> Signal | BoundSignal:
        if instance is None:
            return self
        with self._lock:
            signal = self._instances.get(instance)
            if signal is None:
                signal = BoundSignal()
                self._instances[instance] = signal
            return signal


class WorkerThread:
    """Worker wrapper exposing the small QThread API used by this project."""

    finished = Signal()

    def __init__(self, _parent: object | None = None) -> None:
        self._worker_thread: threading.Thread | None = None
        self._worker_lock = threading.Lock()

    def run(self) -> None:  # pragma: no cover - implemented by concrete workers
        raise NotImplementedError

    def _execute(self) -> None:
        try:
            self.run()
        finally:
            self.finished.emit()

    def start(self) -> None:
        with self._worker_lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                raise RuntimeError("worker is already running")
            self._worker_thread = threading.Thread(
                target=self._execute,
                name=type(self).__name__,
                daemon=True,
            )
            self._worker_thread.start()

    def isRunning(self) -> bool:
        thread = self._worker_thread
        return bool(thread is not None and thread.is_alive())

    def wait(self, timeout_ms: int | None = None) -> bool:
        thread = self._worker_thread
        if thread is None:
            return True
        timeout = None if timeout_ms is None else max(0.0, timeout_ms / 1000.0)
        thread.join(timeout)
        return not thread.is_alive()

    def deleteLater(self) -> None:
        """Compatibility no-op; standard threads own no GUI resources."""

        return

    @staticmethod
    def msleep(milliseconds: int) -> None:
        time.sleep(max(0.0, milliseconds / 1000.0))


class ManagedTimer:
    """Cancelable timer supporting one-shot and repeating callbacks."""

    timeout = Signal()

    def __init__(self, _parent: object | None = None) -> None:
        self._interval_ms = 0
        self._single_shot = False
        self._timer: threading.Timer | None = None
        self._deadline = 0.0
        self._lock = threading.RLock()

    def setInterval(self, milliseconds: int) -> None:
        self._interval_ms = max(0, int(milliseconds))

    def setSingleShot(self, enabled: bool) -> None:
        self._single_shot = bool(enabled)

    def setTimerType(self, _timer_type: object) -> None:
        return

    def start(self, milliseconds: int | None = None) -> None:
        if milliseconds is not None:
            self.setInterval(milliseconds)
        with self._lock:
            self.stop()
            delay_s = self._interval_ms / 1000.0
            self._deadline = time.monotonic() + delay_s
            self._timer = threading.Timer(delay_s, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            self._timer = None
        self.timeout.emit()
        with self._lock:
            if not self._single_shot and self._timer is None:
                self.start()

    def stop(self) -> None:
        timer = self._timer
        self._timer = None
        self._deadline = 0.0
        if timer is not None:
            timer.cancel()

    def isActive(self) -> bool:
        timer = self._timer
        return bool(timer is not None and timer.is_alive())

    def remainingTime(self) -> int:
        if not self.isActive():
            return -1
        return max(0, round((self._deadline - time.monotonic()) * 1000.0))

    @staticmethod
    def singleShot(milliseconds: int, callback: Callable[[], Any]) -> None:
        timer = threading.Timer(max(0.0, milliseconds / 1000.0), callback)
        timer.daemon = True
        timer.start()
