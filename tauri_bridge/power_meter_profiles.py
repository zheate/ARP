"""Probe-bound, append-only coefficient revisions owned by the Python backend."""

from __future__ import annotations

import copy
import math
import uuid
from datetime import datetime, timezone
from typing import Any

from combined_test.automation import AutomaticTestState
from .station_support import normalize_device_serial


PROFILE_CONFIGURATION_KEYS = (
    "powerMeterProfileId", "powerMeterProfileName", "powerMeterProfileVersion",
    "powerMeterProfileNotes", "powerMeterProfileWavelengthPending",
)
MEASUREMENT_CONFIGURATION_KEYS = (
    "powerMeterDriverKind", "powerMeterSerialNumber", "softwareGain",
    "powerMeterWavelengthNm", *PROFILE_CONFIGURATION_KEYS,
)


def positive(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}必须是大于零的有限数值") from None
    if isinstance(value, bool) or not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label}必须是大于零的有限数值")
    return number


class PowerMeterProfiles:
    def __init__(self, backend: Any) -> None:
        self.backend = backend

    @staticmethod
    def key(driver: str, serial: str) -> str:
        return f"{driver.strip().lower()}:{normalize_device_serial(serial)}"

    def current_key(self) -> str:
        config = self.backend.runtime.configuration
        return self.key(config["powerMeterDriverKind"], config["powerMeterSerialNumber"])

    @staticmethod
    def revision(values: dict[str, Any], version: int) -> dict[str, Any]:
        name = str(values.get("name", "")).strip()
        if not name:
            raise ValueError("请填写方案名称")
        return {
            "version": version, "name": name,
            "softwareGain": positive(values.get("softwareGain"), "功率系数"),
            "wavelengthNm": positive(values.get("wavelengthNm"), "校准波长"),
            "notes": str(values.get("notes", "")).strip(),
            "operator": str(values.get("operator", "")).strip(),
            "reason": str(values.get("reason", "")).strip(),
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }

    def ensure(self, driver: str, serial: str, *, wavelength: float | None = None) -> dict[str, Any] | None:
        serial = normalize_device_serial(serial)
        if not serial:
            return None
        key = self.key(driver, serial)
        entries = self.backend.device_profiles["powerMeters"]
        entry = entries.get(key)
        if entry is not None and "profiles" in entry:
            return entry
        legacy = entry is not None
        gain = (entry or {}).get("softwareGain", 1.0)
        known_wave = (entry or {}).get("wavelengthNm", wavelength)
        if known_wave is not None:
            known_wave = positive(known_wave, "原有校准波长")
        revision = self.revision({
            "name": "原有系数" if legacy else "默认方案",
            "softwareGain": gain, "wavelengthNm": known_wave or 1,
            "reason": "旧系数迁移" if legacy else "首次建立探头方案",
        }, 1)
        revision["wavelengthNm"] = known_wave
        profile_id = str(uuid.uuid4())
        entry = {
            "serialNumber": serial, "driverKind": driver.lower(),
            "selectedProfileId": profile_id, "selectedVersion": 1,
            "profiles": [{"id": profile_id, "versions": [revision]}],
        }
        entries[key] = entry
        return entry

    def initialize(self, saved_configuration: dict[str, Any]) -> bool:
        """Only the previously selected probe can inherit the legacy global wavelength."""
        before = copy.deepcopy(self.backend.device_profiles)
        config = self.backend.runtime.configuration
        current_key = self.current_key()
        known_wave = saved_configuration.get("powerMeterWavelengthNm")
        if known_wave is not None:
            known_wave = positive(known_wave, "原有校准波长")
        for key, entry in list(self.backend.device_profiles["powerMeters"].items()):
            self.ensure(entry.get("driverKind", key.split(":", 1)[0]), entry.get("serialNumber", ""),
                        wavelength=known_wave if key == current_key else None)
        if config["powerMeterSerialNumber"]:
            if current_key not in self.backend.device_profiles["powerMeters"]:
                self.backend.device_profiles["powerMeters"][current_key] = {
                    "driverKind": config["powerMeterDriverKind"],
                    "serialNumber": config["powerMeterSerialNumber"],
                    "softwareGain": config["softwareGain"],
                }
            self.ensure(config["powerMeterDriverKind"], config["powerMeterSerialNumber"],
                        wavelength=known_wave)
        self.apply_current()
        return before != self.backend.device_profiles

    def library(self, driver: str | None = None, serial: str | None = None) -> dict[str, Any] | None:
        key = self.current_key() if driver is None else self.key(driver, serial or "")
        return copy.deepcopy(self.backend.device_profiles["powerMeters"].get(key))

    @staticmethod
    def selected(entry: dict[str, Any] | None) -> dict[str, Any] | None:
        if entry is None:
            return None
        for profile in entry.get("profiles", []):
            if profile["id"] == entry["selectedProfileId"]:
                return next((r for r in profile["versions"] if r["version"] == entry["selectedVersion"]), None)
        return None

    def apply_current(self, config: dict[str, Any] | None = None) -> None:
        config = self.backend.runtime.configuration if config is None else config
        entry = self.library(config["powerMeterDriverKind"], config["powerMeterSerialNumber"])
        revision = self.selected(entry)
        config.update({
            "powerMeterProfileId": entry["selectedProfileId"] if revision else "",
            "powerMeterProfileName": revision["name"] if revision else "未绑定方案",
            "powerMeterProfileVersion": revision["version"] if revision else 0,
            "powerMeterProfileNotes": revision["notes"] if revision else "",
            "powerMeterProfileWavelengthPending": bool(revision and revision["wavelengthNm"] is None),
            "softwareGain": revision["softwareGain"] if revision else 1.0,
            # Keep a valid numeric configuration even when a migrated wavelength is unknown.
            "powerMeterWavelengthNm": (revision["wavelengthNm"] or 976.0) if revision else 976.0,
        })

    def resolve_configuration(self, candidate: dict[str, Any], previous: dict[str, Any]) -> None:
        from .station_support import power_meter_serial_number
        resource = candidate["powerMeterResource"]
        option = next((item for item in self.backend.power_meter_options if item.resource == resource), None)
        if option is not None:
            candidate["powerMeterDriverKind"] = option.driver_kind
            candidate["powerMeterSerialNumber"] = power_meter_serial_number(option)
        elif not resource or resource != previous["powerMeterResource"]:
            candidate["powerMeterSerialNumber"] = ""
        key = self.key(candidate["powerMeterDriverKind"], candidate["powerMeterSerialNumber"])
        if key != self.key(previous["powerMeterDriverKind"], previous["powerMeterSerialNumber"]):
            self.assert_editable()
        if option is not None:
            is_new = key not in self.backend.device_profiles["powerMeters"]
            self.ensure(option.driver_kind, candidate["powerMeterSerialNumber"], wavelength=976.0 if is_new else None)
        self.apply_current(candidate)

    def assert_editable(self) -> None:
        station = self.backend.runtime
        if station.power_meter_reader is not None or station.automatic.state not in {
            AutomaticTestState.IDLE, AutomaticTestState.COMPLETED,
        } or (station._automatic_thread is not None and station._automatic_thread.is_alive()):
            raise ValueError("请先停止采集并结束自动测试，再切换或修改系数方案")
        session = station.record_store.current_session
        if station.record_store.unsaved_records() and (session is None or session.mode == "manual"):
            raise ValueError("请先保存未保存的手动测试点，再切换或修改系数方案")

    def require_ready(self) -> None:
        config = self.backend.runtime.configuration
        if self.backend.power_meter_detect_thread is not None:
            raise ValueError("正在识别功率计，请等待识别完成后开始采集")
        revision = self.selected(self.library())
        if config["powerMeterSerialNumber"] and revision is None:
            raise ValueError("请先为当前探头建立系数方案")
        if revision and revision["wavelengthNm"] is None:
            raise ValueError("当前系数方案的校准波长待补充，请先编辑并保存方案")

    def protect_configuration(self, params: dict[str, Any]) -> dict[str, Any]:
        """Ordinary configuration payloads cannot write measurement provenance."""
        config = self.backend.runtime.configuration
        from combined_test.devices import extract_power_resource_name
        resource = extract_power_resource_name(str(params.get("powerMeterResource", config["powerMeterResource"])))
        resource_changed = resource != config["powerMeterResource"]
        changed_values = any(k in params and params[k] != config[k] for k in ("softwareGain", "powerMeterWavelengthNm"))
        if resource_changed or changed_values:
            self.assert_editable()
            if self.backend.power_meter_detect_thread is not None:
                raise ValueError("正在识别功率计，请等待识别完成")
        if changed_values and not resource_changed:
            raise ValueError("请在系数方案中保存系数和校准波长，普通设置不会覆盖方案")
        return {k: v for k, v in params.items() if k not in MEASUREMENT_CONFIGURATION_KEYS}

    def command(self, action: str, params: dict[str, Any]) -> Any:
        backend = self.backend
        with backend.runtime._lock:
            if self.key(str(params.get("driverKind", "")), str(params.get("serialNumber", ""))) != self.current_key():
                raise ValueError("探头已切换，请重新打开系数方案")
            entry = self.library()
            if not entry or not backend.runtime.configuration["powerMeterSerialNumber"]:
                raise ValueError("请先识别并选择具有探头 SN 的功率计")
            profiles = entry["profiles"]
            profile = next((p for p in profiles if p["id"] == params.get("profileId")), None)
            if action == "history":
                if profile is None:
                    raise ValueError("方案不存在")
                return copy.deepcopy(profile)
            self.assert_editable()
            if backend.power_meter_detect_thread is not None:
                raise ValueError("正在识别功率计，请等待识别完成")
            if action == "create":
                revision = self.revision(params, 1)
                profile = {"id": str(uuid.uuid4()), "versions": [revision]}
                profiles.append(profile)
            else:
                if profile is None:
                    raise ValueError("方案不存在")
                latest = profile["versions"][-1]
                if params.get("expectedVersion") != latest["version"]:
                    raise ValueError("方案已被修改，请刷新后查看最新版本；本次编辑未覆盖已有修改")
                if action == "select":
                    revision = latest
                elif action == "delete":
                    profiles.remove(profile)
                    if entry["selectedProfileId"] == profile["id"]:
                        replacement = profiles[0] if profiles else None
                        entry["selectedProfileId"] = replacement["id"] if replacement else ""
                        entry["selectedVersion"] = replacement["versions"][-1]["version"] if replacement else 0
                elif action in {"save", "restore"}:
                    values = params
                    if action == "restore":
                        target = next((r for r in profile["versions"] if r["version"] == params.get("restoreVersion")), None)
                        if target is None:
                            raise ValueError("历史版本不存在")
                        values = {**target, "operator": params.get("operator", ""),
                                  "reason": params.get("reason") or f"恢复 v{target['version']}"}
                    revision = self.revision(values, latest["version"] + 1)
                    profile["versions"].append(revision)
                else:
                    raise ValueError("未知系数方案操作")
            if action != "delete":
                if any(p["id"] != profile["id"] and p["versions"][-1]["name"] == revision["name"] for p in profiles):
                    raise ValueError("同一探头已有同名方案，请使用不同名称")
                entry["selectedProfileId"] = profile["id"]
                entry["selectedVersion"] = revision["version"]
            previous_profiles = backend.device_profiles
            previous_config = dict(backend.runtime.configuration)
            candidate = copy.deepcopy(previous_profiles)
            candidate["powerMeters"][self.current_key()] = entry
            backend.device_profiles = candidate
            try:
                self.apply_current()
                backend._persist_configuration()
            except Exception:
                backend.device_profiles = previous_profiles
                backend.runtime.configuration = previous_config
                raise
            return None
