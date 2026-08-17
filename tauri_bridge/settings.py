"""JSON-backed settings used by the headless sidecar."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


_LEGACY_REPORT_ROOT = r"Software\Changguang Huaxin\Pump Driver Integrated Test\shipping_report"
_LEGACY_REPORT_VALUE_NAMES = (
    "selected_fields",
    "manual_values",
    "custom_fields",
    "axis_mode",
    "axis_minimum",
    "axis_maximum",
    "wavelength_minimum",
    "wavelength_maximum",
)


def _legacy_shipping_report_settings() -> dict[str, Any]:
    """Read the previous Qt/QSettings report preferences on Windows."""
    if os.name != "nt":
        return {}
    try:
        import winreg
    except ImportError:
        return {}

    migrated: dict[str, Any] = {}
    for report_type in ("pole", "spectrum"):
        try:
            report_key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                rf"{_LEGACY_REPORT_ROOT}\{report_type}",
            )
        except OSError:
            continue
        with report_key:
            for value_name in _LEGACY_REPORT_VALUE_NAMES:
                try:
                    value, _value_type = winreg.QueryValueEx(report_key, value_name)
                except OSError:
                    continue
                migrated[f"shipping_report/{report_type}/{value_name}"] = value
    return migrated


def default_settings_path() -> Path:
    override = os.environ.get("ARP_SETTINGS_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    local = os.environ.get("LOCALAPPDATA", "").strip()
    root = Path(local) if local else Path.home() / ".config"
    return root / "Changguang Huaxin" / "Pump Driver Integrated Test" / "settings.json"


class JsonSettings:
    """Small JSON settings store with atomic persistence."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or default_settings_path())
        self._lock = threading.RLock()
        self._values: dict[str, Any] = {}
        self._load()
        if path is None and not os.environ.get("ARP_SETTINGS_PATH", "").strip():
            self._migrate_legacy_shipping_report_settings()

    def _load(self) -> None:
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        if isinstance(loaded, dict):
            self._values = loaded

    def _migrate_legacy_shipping_report_settings(self) -> None:
        legacy_values = _legacy_shipping_report_settings()
        missing_values = {
            key: value
            for key, value in legacy_values.items()
            if key not in self._values
        }
        if not missing_values:
            return
        self._values.update(missing_values)
        self.flush()

    def get(self, key: str, default: Any = None, value_type: type[Any] | None = None) -> Any:
        with self._lock:
            value = self._values.get(str(key), default)
        if value_type is None or value is None:
            return value
        if value_type is bool and isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        try:
            return value_type(value)
        except (TypeError, ValueError):
            return default

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._values[str(key)] = value
            self.flush()

    def delete(self, key: str) -> None:
        with self._lock:
            self._values.pop(str(key), None)
            self.flush()

    def flush(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(self._values, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
