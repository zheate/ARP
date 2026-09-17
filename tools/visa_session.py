"""Process-wide lifetime management for PyVISA's shared ResourceManager.

PyVISA returns the same default ResourceManager object to every caller. Closing
one apparent instance therefore closes resources opened by all other callers.
This module keeps that shared manager alive until the last active user releases
it.
"""

from __future__ import annotations

from contextlib import contextmanager
from threading import RLock
from typing import Any, Callable, Iterator


def _create_default_resource_manager() -> Any:
    import os
    import pyvisa

    if os.environ.get("PYVISA_LIBRARY"):
        # An explicitly selected library must not be silently substituted.
        return pyvisa.ResourceManager()
    try:
        library = pyvisa.highlevel.open_visa_library("@ivi")
    except (OSError, ValueError):
        # Only library discovery/loading failures select the bundled serial
        # implementation. Runtime I/O failures retain the original error path.
        from tools.serial_visa import create_serial_resource_manager

        return create_serial_resource_manager()
    return pyvisa.ResourceManager(library)


class VisaResourceManagerPool:
    """Reference-counted owner of one VISA ResourceManager."""

    def __init__(self, factory: Callable[[], Any] = _create_default_resource_manager) -> None:
        self._factory = factory
        self._lock = RLock()
        self._manager: Any | None = None
        self._lease_count = 0

    def acquire(self) -> Any:
        with self._lock:
            if self._manager is None:
                self._manager = self._factory()
            self._lease_count += 1
            return self._manager

    def release(self, manager: Any) -> None:
        with self._lock:
            if manager is not self._manager or self._lease_count <= 0:
                return
            self._lease_count -= 1
            if self._lease_count != 0:
                return
            try:
                manager.close()
            finally:
                self._manager = None

    @contextmanager
    def lease(self) -> Iterator[Any]:
        manager = self.acquire()
        try:
            yield manager
        finally:
            self.release(manager)


_default_pool = VisaResourceManagerPool()


def acquire_visa_resource_manager() -> Any:
    return _default_pool.acquire()


def release_visa_resource_manager(manager: Any) -> None:
    _default_pool.release(manager)


@contextmanager
def visa_resource_manager() -> Iterator[Any]:
    with _default_pool.lease() as manager:
        yield manager
