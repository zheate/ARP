"""Invalidate queued hardware commands without waiting for device I/O."""

from contextlib import contextmanager
import math
import threading


class CommandSafetyGate:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._generation = 0
        self._stops = 0
        self._local = threading.local()

    def capture(self) -> int:
        with self._lock:
            # Requests submitted during shutdown must not become valid later.
            return -1 if self._stops else self._generation

    def token(self) -> int:
        generation = getattr(self._local, "generation", None)
        return self.capture() if generation is None else generation

    @contextmanager
    def commit(self):
        """Protect only short state transitions, never hardware I/O or joins."""
        with self._lock:
            self.check()
            yield

    def invalidate(self) -> None:
        with self._lock:
            self._generation += 1

    def check(self) -> None:
        with self._lock:
            expected = getattr(self._local, "generation", self._generation)
            if self._stops or expected != self._generation:
                raise RuntimeError("命令已因安全停止失效；请等待停止完成后重新操作")

    @contextmanager
    def command(self, generation: int | None = None):
        previous = getattr(self._local, "generation", None)
        self._local.generation = (
            generation if generation is not None else previous if previous is not None else self.capture()
        )
        try:
            self.check()
            yield
        finally:
            if previous is None:
                del self._local.generation
            else:
                self._local.generation = previous

    def begin_stop(self) -> None:
        with self._lock:
            self._generation += 1
            self._stops += 1

    def end_stop(self) -> None:
        with self._lock:
            self._stops -= 1

    @contextmanager
    def stopping(self):
        self.begin_stop()
        try:
            yield
        finally:
            self.end_stop()


def finite_number(value: object, name: str, *, minimum: float = 0, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}必须是 JSON 数值")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name}超出允许范围") from exc
    if not math.isfinite(number) or number < minimum or (maximum is not None and number > maximum):
        raise ValueError(f"{name}超出允许范围或不是有限数值")
    return number
