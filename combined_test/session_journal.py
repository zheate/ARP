"""Durable append-only JSONL journal for test sessions."""

from __future__ import annotations

import json
import logging
import math
import os
import threading
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class SessionJournalError(RuntimeError):
    """Raised when a session fact cannot be durably appended."""


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


class SessionJournal:
    """Append complete session facts and fsync every accepted event."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path: Path | None = None
        self._lock = threading.Lock()

    def begin(self, session: Any) -> Path:
        session_id = str(session.session_id)
        date_segment = str(session.started_at_utc)[:10] or "unknown-date"
        path = self.root / date_segment / f"{session_id}.jsonl"
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.path = path
            self._append_locked({"type": "session_start", "session": session})
        return path

    def append(self, event: Mapping[str, Any]) -> None:
        with self._lock:
            if self.path is None:
                raise SessionJournalError("测试会话日志尚未创建")
            self._append_locked(event)
            if event.get("type") == "session_completed":
                # Acquisition facts are already durable. A library failure must never
                # interfere with safe shutdown; browsing retries from the journal.
                try:
                    from .data_library import DataLibrary
                    DataLibrary(self.root.parent / "library.sqlite3").ingest_journal(self.path)
                except Exception:
                    logging.getLogger(__name__).exception("测试数据库同步失败，已保留本地日志，数据浏览将重试")

    def _append_locked(self, event: Mapping[str, Any]) -> None:
        path = self.path
        if path is None:
            raise SessionJournalError("测试会话日志尚未创建")
        try:
            payload = json.dumps(
                _json_value(event),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8") + b"\n"
            with path.open("ab", buffering=0) as stream:
                stream.write(payload)
                os.fsync(stream.fileno())
        except (OSError, TypeError, ValueError) as exc:
            raise SessionJournalError(f"本地测试记录写入失败：{exc}") from exc
