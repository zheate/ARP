"""Shared configuration and helpers for station runtimes."""

from __future__ import annotations

import math
from typing import Any, Callable, Sequence

from combined_test.concurrency import Signal, WorkerThread
from combined_test.devices import extract_power_resource_name
from combined_test.models import PowerMeterOption
from combined_test.record_store import AttemptValidity
from combined_test.runtime_logging import get_runtime_logger


_LOGGER = get_runtime_logger("station_support")

MANUAL_CONTROL_METHODS = frozenset(
    {
        "app.configure",
        "device.refresh",
        "powerSupply.connect",
        "powerSupply.disconnect",
        "powerSupply.safeRampDown",
        "powerSupply.setCurrent",
        "powerSupply.setVoltage",
        "powerSupply.setOutput",
        "powerSupply.read",
        "powerMeter.start",
        "powerMeter.stop",
        "powerMeter.setRelativeZero",
        "manual.saveExcel",
        "spectrometer.start",
        "spectrometer.stop",
        "spectrometer.saveCsv",
    }
)
MIN_SPECTRUM_PEAK_INTENSITY = 500.0
AGING_TEST_SCRIPT_ID = "aging"
AGING_START_CURRENT_A = 2.0
AGING_TARGET_CURRENT_A = 20.0
AGING_CURRENT_STEP_A = 2.0
AGING_RAMP_INTERVAL_S = 1.0
AGING_HOLD_DURATION_S = 2.0 * 60.0 * 60.0
AGING_POWER_SAMPLE_INTERVAL_S = 5.0 * 60.0
AUTOMATIC_PAUSE_ACTIONS: dict[str, tuple[str, ...]] = {
    "operator": ("resume", "safe_end"),
    "spectrum_saturated": ("adjust_spectrum", "retry_point", "safe_end"),
    "power_meter_disconnected": ("recover_power_meter", "retry_point", "safe_end"),
    "spectrometer_disconnected": ("recover_spectrometer", "retry_point", "safe_end"),
    "record_persistence_failed": ("retry_persist", "safe_end"),
    "measurement_timeout": ("retry_point", "safe_end"),
    "device_error": ("retry_point", "safe_end"),
}


class DeviceDiscoveryThread(WorkerThread):
    """Run one blocking hardware discovery task without blocking snapshots."""

    completed = Signal(object)
    status = Signal(str)
    failed = Signal(str)

    def __init__(self, discover: Callable[[Callable[[str], None]], object]) -> None:
        super().__init__()
        self._discover = discover

    def run(self) -> None:
        try:
            self.completed.emit(self._discover(self.status.emit))
        except Exception as exc:
            _LOGGER.exception("设备识别线程失败")
            self.failed.emit(str(exc))


class AutomaticPointRetry(RuntimeError):
    """Internal control-flow signal used to restart the active current point."""


class AutomaticPointQualityError(RuntimeError):
    def __init__(self, validity: AttemptValidity, message: str, *, hold_current: bool = False) -> None:
        super().__init__(message)
        self.validity = validity
        self.hold_current = bool(hold_current)


DEFAULT_CONFIGURATION: dict[str, Any] = {
    "sn": "",
    "productModel": "",
    "batch": "",
    "station": "测试",
    "testTemperatureC": "",
    "outputDir": "test_records",
    "powerSupplyKind": "ch341",
    "ch341SelectedLocationPath": "",
    "ch341PortBindings": [],
    "tdkResource": "",
    "setCurrentA": 0.0,
    "tdkVoltageV": 12.0,
    "powerMeterResource": "",
    "powerMeterDriverKind": "caihuang",
    "powerMeterSerialNumber": "",
    "powerMeterWavelengthNm": 976.0,
    "softwareGain": 1.0,
    "powerMeterProfileId": "",
    "powerMeterProfileName": "未绑定方案",
    "powerMeterProfileVersion": 0,
    "powerMeterProfileNotes": "",
    "powerMeterProfileWavelengthPending": False,
    "powerMeterIntervalMs": 100,
    "powerSmoothing": True,
    "spectrometerResource": "",
    "spectrometerDeviceId": None,
    "spectrometerSerialNumber": "",
    "integrationTimeUs": 10_000,
    "autoIntegration": False,
    "spectrometerIntervalMs": 100,
    "spectrumAverageFrames": 1,
    "spectrumSmoothing": True,
    "pibCenterWavelengthNm": 976.0,
    "pibHalfRangeNm": 1.5,
    "centerWavelengthSpecNm": 976.0,
    "centerWavelengthToleranceNm": 1.5,
    "fwhmMaximumNm": 0.6,
    "pibMinimumPercent": 95.0,
    "stableWindowS": 3.0,
    "stabilityLimitPercent": 0.2,
    "initialCurrentA": 1.0,
    "targetCurrentA": 20.0,
    "currentStepA": 3.0,
    "selectedScriptId": "standard",
    "selectedScriptPresetId": "",
    "testScriptPresets": [],
    "currentPlanMode": "range",
    "customCurrentPoints": [],
    "pointTimeoutS": 120.0,
    "rampDownStepA": 5.0,
    "rampDownIntervalS": 1.1,
    "pauseRampDownTimeoutS": 30.0,
}

BUILTIN_TEST_SCRIPTS: tuple[dict[str, Any], ...] = (
    {"id": "standard", "name": "标准测试", "builtin": True, "currentPlanMode": "range"},
    {"id": "hold-points", "name": "增加停留点", "builtin": True, "currentPlanMode": "custom"},
    {
        "id": AGING_TEST_SCRIPT_ID,
        "name": "AU 老化测试",
        "builtin": True,
        "currentPlanMode": "custom",
        "powerSupplyKinds": ["ch341"],
    },
)


def apply_aging_script_configuration(configuration: dict[str, Any]) -> None:
    configuration.update(
        {
            "initialCurrentA": AGING_START_CURRENT_A,
            "targetCurrentA": AGING_TARGET_CURRENT_A,
            "currentStepA": AGING_CURRENT_STEP_A,
            "currentPlanMode": "custom",
            "customCurrentPoints": [
                {"currentA": AGING_TARGET_CURRENT_A, "dwellS": AGING_HOLD_DURATION_S}
            ],
        }
    )


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def device_state(connected: bool, running: bool, error: str) -> str:
    if error:
        return "error"
    if connected or running:
        return "connected"
    return "disconnected"


def default_pd_state() -> dict[str, Any]:
    """Create an isolated PD acquisition state for one station."""
    return {
        "state": "idle",
        "status": "未启动",
        "detecting": False,
        "devices": [],
        "channels": [],
        "ranges": [],
        "settings": {
            "device": "",
            "channel": "",
            "terminal": "DIFF",
            "range": 10.0,
            "sampleRateHz": 1_000.0,
            "blockSize": 100,
            "scale": 1.0,
            "offset": 0.0,
            "unit": "V",
            "save": True,
            "outputDir": "",
        },
        "points": [],
        "currentValue": "--",
        "voltage": "--",
        "mean": "--",
        "standardDeviation": "--",
        "rangeText": "--",
        "sampleCount": "0",
    }


def normalize_power_meter_configuration(
    configuration: dict[str, Any],
    options: Sequence[PowerMeterOption] = (),
) -> bool:
    before = (
        configuration.get("powerMeterResource"),
        configuration.get("powerMeterDriverKind"),
    )
    resource = extract_power_resource_name(str(before[0] or ""))
    configuration["powerMeterResource"] = resource
    if resource.startswith("OPHIR::USB::"):
        configuration["powerMeterDriverKind"] = "ophir"
    else:
        matched = next((item for item in options if item.resource == resource), None)
        if matched is not None:
            configuration["powerMeterDriverKind"] = matched.driver_kind
        elif str(configuration.get("powerMeterDriverKind", "")).lower() == "ophir":
            configuration["powerMeterDriverKind"] = "caihuang"
    after = (
        configuration.get("powerMeterResource"),
        configuration.get("powerMeterDriverKind"),
    )
    return after != before


def normalize_device_serial(value: Any) -> str:
    return str(value or "").strip().upper()


def power_meter_serial_number(option: PowerMeterOption) -> str:
    return normalize_device_serial(option.serial_number)


def spectrometer_label(device_id: int, serial_number: str) -> str:
    serial = normalize_device_serial(serial_number)
    suffix = f" | SN {serial}" if serial else ""
    return f"Ocean Insight | 设备 ID {int(device_id)}{suffix}"
