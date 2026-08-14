"""JSON-backed settings used by the headless sidecar."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


def default_settings_path() -> Path:
    override = os.environ.get("ARP_SETTINGS_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    local = os.environ.get("LOCALAPPDATA", "").strip()
    root = Path(local) if local else Path.home() / ".config"
    return root / "Changguang Huaxin" / "Pump Driver Integrated Test" / "settings.json"


class JsonSettings:
    """Minimal QSettings-compatible facade with atomic persistence."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or default_settings_path())
        self._lock = threading.RLock()
        self._values: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        if isinstance(loaded, dict):
            self._values = loaded

    def value(self, key: str, default: Any = None, type: type[Any] | None = None) -> Any:
        with self._lock:
            value = self._values.get(str(key), default)
        if type is None or value is None:
            return value
        if type is bool and isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        try:
            return type(value)
        except (TypeError, ValueError):
            return default

    def setValue(self, key: str, value: Any) -> None:
        with self._lock:
            self._values[str(key)] = value
            self.sync()

    def remove(self, key: str) -> None:
        with self._lock:
            self._values.pop(str(key), None)
            self.sync()

    def sync(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(self._values, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
