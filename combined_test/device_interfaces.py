"""Stable device ports used by the application and automatic test runner.

The concrete Windows drivers remain behind these protocols.  This keeps the
workflow independent from CH341, TDK/VISA, and Qt thread implementations.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .core import build_set_current_command
from .devices import read_power_status_value


DEFAULT_POWER_SUPPLY_ADDRESS = 0x41


@runtime_checkable
class PowerSupply(Protocol):
    @property
    def connected(self) -> bool: ...

    @property
    def output_enabled(self) -> bool: ...

    def set_current(self, current_a: float) -> None: ...

    def set_voltage(self, voltage_v: float) -> None: ...

    def set_output_enabled(self, enabled: bool) -> None: ...

    def read_output_voltage(self) -> float: ...

    def read_output_current(self) -> float: ...

    def disconnect(self) -> None: ...


@runtime_checkable
class PowerMeter(Protocol):
    is_ready: bool
    reading: Any
    status: Any
    ready: Any
    failed: Any
    finished: Any

    def start(self) -> None: ...

    def isRunning(self) -> bool: ...

    def wait(self, timeout_ms: int = ...) -> bool: ...

    def reset_stability_window(self) -> int: ...

    def update_stability_settings(self, window_s: float, tolerance_w: float) -> None: ...

    def stop(self) -> None: ...


@runtime_checkable
class SpectrumMeter(Protocol):
    is_ready: bool
    reading: Any
    integration_time_changed: Any
    status: Any
    ready: Any
    failed: Any
    finished: Any

    def start(self) -> None: ...

    def isRunning(self) -> bool: ...

    def wait(self, timeout_ms: int = ...) -> bool: ...

    def take_latest_spectrum(self) -> tuple[Any, Any] | None: ...

    def stop(self) -> None: ...


class ControllerPowerSupply:
    """Semantic adapter for both legacy CH341 and TDK compatibility controllers."""

    def __init__(self, controller: Any, address: int = DEFAULT_POWER_SUPPLY_ADDRESS) -> None:
        self.controller = controller
        self.address = int(address)

    @property
    def connected(self) -> bool:
        return bool(getattr(self.controller, "is_connected", False))

    @property
    def output_enabled(self) -> bool:
        return bool(getattr(self.controller, "output_enabled", True))

    def set_current(self, current_a: float) -> None:
        setter = getattr(self.controller, "set_output_current", None)
        if callable(setter):
            setter(float(current_a))
            return
        success, detail = self.controller.i2c_write(
            self.address,
            build_set_current_command(float(current_a)),
        )
        if not success:
            raise RuntimeError(str(detail))

    def set_voltage(self, voltage_v: float) -> None:
        setter = getattr(self.controller, "set_output_voltage", None)
        if not callable(setter):
            raise RuntimeError("当前电源不支持设置输出电压")
        setter(float(voltage_v))

    def set_output_enabled(self, enabled: bool) -> None:
        setter = getattr(self.controller, "set_output_enabled", None)
        if not callable(setter):
            raise RuntimeError("当前电源不支持切换输出")
        setter(bool(enabled))

    def read_output_voltage(self) -> float:
        reader = getattr(self.controller, "read_output_voltage", None)
        if callable(reader):
            return float(reader())
        return read_power_status_value(
            self.controller,
            self.address,
            [0xB4, 0x8B, 0x00, 0x00],
        )

    def read_output_current(self) -> float:
        reader = getattr(self.controller, "read_output_current", None)
        if callable(reader):
            return float(reader())
        return read_power_status_value(
            self.controller,
            self.address,
            [0xB4, 0x8C, 0x00, 0x00],
        )

    def disconnect(self) -> None:
        self.controller.disconnect_device()
