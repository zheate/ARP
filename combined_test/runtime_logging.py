"""Persistent runtime logging for the desktop application and device backend."""

from __future__ import annotations

import atexit
import logging
import os
import platform
import re
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Mapping


LOGGER_NAME = "pump_driver"
LOG_DIRECTORY_ENV = "ARP_RUNTIME_LOG_DIR"
_HANDLER_MARKER = "_pump_driver_runtime_handler"
_CONFIGURE_LOCK = threading.RLock()
_runtime_log_path: Path | None = None
_previous_sys_excepthook = None
_previous_threading_excepthook = None
_exception_hooks_installed = False
_atexit_registered = False

# Library imports and unit tests may exercise device code without launching an
# application entry point. Suppress logging's stderr fallback in that case;
# ``configure_runtime_logging`` adds the real file handler at process startup.
logging.getLogger(LOGGER_NAME).addHandler(logging.NullHandler())


def default_runtime_log_directory(environment: Mapping[str, str] | None = None) -> Path:
    """Return a writable-by-design per-user directory for application logs."""

    values = os.environ if environment is None else environment
    configured = str(values.get(LOG_DIRECTORY_ENV, "")).strip()
    if configured:
        return Path(configured).expanduser()
    local_app_data = str(values.get("LOCALAPPDATA", "")).strip()
    if local_app_data:
        return (
            Path(local_app_data)
            / "Changguang Huaxin"
            / "Pump Driver Integrated Test"
            / "logs"
        )
    xdg_state_home = str(values.get("XDG_STATE_HOME", "")).strip()
    if xdg_state_home:
        return Path(xdg_state_home) / "pump-driver-integrated-test" / "logs"
    return Path.home() / ".local" / "state" / "pump-driver-integrated-test" / "logs"


def get_runtime_logger(component: str | None = None) -> logging.Logger:
    """Return a component logger without implicitly creating files during imports."""

    suffix = str(component or "").strip(".")
    return logging.getLogger(f"{LOGGER_NAME}.{suffix}" if suffix else LOGGER_NAME)


def runtime_log_path() -> Path | None:
    """Return the active process log path, if logging has been configured."""

    return _runtime_log_path


def _safe_role_name(process_role: str) -> str:
    role = re.sub(r"[^A-Za-z0-9_-]+", "-", str(process_role).strip()).strip("-")
    return role or "backend"


def _install_exception_hooks() -> None:
    global _exception_hooks_installed
    global _previous_sys_excepthook
    global _previous_threading_excepthook

    if _exception_hooks_installed:
        return
    logger = get_runtime_logger("exceptions")
    _previous_sys_excepthook = sys.excepthook

    def log_unhandled_exception(
        exc_type: type[BaseException],
        exc_value: BaseException,
        traceback: TracebackType | None,
    ) -> None:
        logger.critical(
            "主线程出现未处理异常",
            exc_info=(exc_type, exc_value, traceback),
        )
        previous = _previous_sys_excepthook
        if callable(previous):
            previous(exc_type, exc_value, traceback)

    sys.excepthook = log_unhandled_exception

    if hasattr(threading, "excepthook"):
        _previous_threading_excepthook = threading.excepthook

        def log_thread_exception(args: threading.ExceptHookArgs) -> None:
            logger.critical(
                "后台线程出现未处理异常 | thread=%s",
                getattr(args.thread, "name", "unknown"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
            previous = _previous_threading_excepthook
            if callable(previous):
                previous(args)

        threading.excepthook = log_thread_exception

    _exception_hooks_installed = True


def _restore_exception_hooks() -> None:
    global _exception_hooks_installed

    if not _exception_hooks_installed:
        return
    if callable(_previous_sys_excepthook):
        sys.excepthook = _previous_sys_excepthook
    if hasattr(threading, "excepthook") and callable(_previous_threading_excepthook):
        threading.excepthook = _previous_threading_excepthook
    _exception_hooks_installed = False


def _create_file_handler(directory: Path, process_role: str) -> tuple[logging.FileHandler, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"{_safe_role_name(process_role)}_{timestamp}_pid{os.getpid()}.log"
    path = directory / filename
    handler = logging.FileHandler(path, mode="a", encoding="utf-8", delay=False)
    setattr(handler, _HANDLER_MARKER, True)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s.%(msecs)03d | %(levelname)-8s | pid=%(process)d | "
            "thread=%(threadName)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    return handler, path


def configure_runtime_logging(
    log_directory: str | os.PathLike[str] | None = None,
    *,
    process_role: str = "backend",
) -> Path | None:
    """Create one UTF-8 log file for this process and install crash hooks.

    Logging failures never prevent the equipment application from starting. If
    the normal per-user directory is unavailable, a temp-directory fallback is
    attempted before returning ``None``.
    """

    global _runtime_log_path
    global _atexit_registered

    with _CONFIGURE_LOCK:
        if _runtime_log_path is not None:
            return _runtime_log_path

        logger = get_runtime_logger()
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        candidates = [
            Path(log_directory).expanduser()
            if log_directory is not None
            else default_runtime_log_directory()
        ]
        fallback = Path(tempfile.gettempdir()) / "pump-driver-integrated-test" / "logs"
        if fallback not in candidates:
            candidates.append(fallback)

        handler: logging.FileHandler | None = None
        path: Path | None = None
        for directory in candidates:
            try:
                handler, path = _create_file_handler(directory, process_role)
                break
            except OSError:
                continue
        if handler is None or path is None:
            return None

        logger.addHandler(handler)
        _runtime_log_path = path
        _install_exception_hooks()
        if not _atexit_registered:
            atexit.register(shutdown_runtime_logging)
            _atexit_registered = True

        logger.info(
            "运行日志已启动 | role=%s | path=%s | python=%s | platform=%s | executable=%s | cwd=%s",
            process_role,
            path,
            platform.python_version(),
            platform.platform(),
            sys.executable,
            Path.cwd(),
        )
        logger.debug("启动参数 | argv=%r", sys.argv)
        return path


def shutdown_runtime_logging() -> None:
    """Flush and close handlers; primarily useful for orderly process exit and tests."""

    global _runtime_log_path

    with _CONFIGURE_LOCK:
        logger = get_runtime_logger()
        handlers = [
            handler
            for handler in logger.handlers
            if bool(getattr(handler, _HANDLER_MARKER, False))
        ]
        if handlers:
            logger.info("运行日志正常关闭")
        for handler in handlers:
            logger.removeHandler(handler)
            handler.flush()
            handler.close()
        _runtime_log_path = None
        _restore_exception_hooks()


__all__ = [
    "LOG_DIRECTORY_ENV",
    "configure_runtime_logging",
    "default_runtime_log_directory",
    "get_runtime_logger",
    "runtime_log_path",
    "shutdown_runtime_logging",
]
