"""TDK-Lambda programmable power-supply adapter.

The Z+ and Genesys+ families expose a VISA resource and accept SCPI commands.
This adapter also implements the small ``i2c_write``/``i2c_write_read`` surface
used by the existing ARP window so the automatic current-test state machine can
use either CH341 or TDK hardware without duplicating its safety logic.
"""

from __future__ import annotations

import math
from typing import Any, Callable


TDK_DEFAULT_TIMEOUT_MS = 2000


def list_tdk_visa_resources(resource_manager_factory: Callable[[], Any] | None = None) -> list[str]:
    """Return serial, USB, TCPIP and GPIB VISA resources in stable order."""
    if resource_manager_factory is None:
        try:
            import pyvisa
        except ModuleNotFoundError as exc:
            raise RuntimeError("缺少 TDK 电源依赖 pyvisa，请在 sth_eb314 环境中运行。") from exc
        resource_manager_factory = pyvisa.ResourceManager

    manager = resource_manager_factory()
    try:
        prefixes = ("ASRL", "USB", "TCPIP", "GPIB")
        return sorted(str(item) for item in manager.list_resources() if str(item).upper().startswith(prefixes))
    finally:
        manager.close()


class TdkLambdaPowerSupply:
    """SCPI controller for one TDK-Lambda VISA resource."""

    def __init__(
        self,
        resource: str,
        timeout_ms: int = TDK_DEFAULT_TIMEOUT_MS,
        resource_manager_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.resource = resource.strip()
        self.timeout_ms = int(timeout_ms)
        self._resource_manager_factory = resource_manager_factory
        self._resource_manager: Any | None = None
        self._instrument: Any | None = None
        self.is_connected = False
        self.identity = ""
        self.output_enabled = False
        self.maximum_voltage_v: float | None = None
        self.maximum_current_a: float | None = None

    def _make_resource_manager(self) -> Any:
        if self._resource_manager_factory is not None:
            return self._resource_manager_factory()
        try:
            import pyvisa
        except ModuleNotFoundError as exc:
            raise RuntimeError("缺少 TDK 电源依赖 pyvisa，请在 sth_eb314 环境中运行。") from exc
        return pyvisa.ResourceManager()

    def connect_device(self, _index: int = 0) -> tuple[bool, str]:
        if not self.resource:
            return False, "请选择 TDK 电源 VISA 资源"
        if self.is_connected:
            return True, self.identity or self.resource
        try:
            self._resource_manager = self._make_resource_manager()
            self._instrument = self._resource_manager.open_resource(self.resource)
            self._instrument.timeout = self.timeout_ms
            # These terminators are accepted by TDK-Lambda Z+/Genesys+ serial,
            # USB and LAN VISA sessions. Backends that do not expose the
            # properties simply ignore the assignment.
            try:
                self._instrument.write_termination = "\n"
                self._instrument.read_termination = "\n"
            except Exception:
                pass
            self.identity = self._query_identity()
            identity_upper = self.identity.upper()
            if "TDK" not in identity_upper and "LAMBDA" not in identity_upper:
                raise RuntimeError(f"所选资源不是 TDK-Lambda 电源：{self.identity}")
            self.output_enabled = self._query_output_state()
            self.maximum_voltage_v = self._query_optional_float("VOLT? MAX")
            self.maximum_current_a = self._query_optional_float("CURR? MAX")
            self.is_connected = True
            return True, f"{self.identity} | {self.resource}"
        except Exception as exc:
            self.disconnect_device()
            return False, f"连接 TDK 电源失败：{exc}"

    def disconnect_device(self) -> bool:
        instrument, manager = self._instrument, self._resource_manager
        self._instrument = None
        self._resource_manager = None
        self.is_connected = False
        self.output_enabled = False
        self.maximum_voltage_v = None
        self.maximum_current_a = None
        if instrument is not None:
            try:
                instrument.close()
            except Exception:
                pass
        if manager is not None:
            try:
                manager.close()
            except Exception:
                pass
        return True

    def set_i2c_speed(self, _speed: int) -> bool:
        """Compatibility no-op for the existing controller lifecycle."""
        return True

    def _require_instrument(self) -> Any:
        if not self.is_connected or self._instrument is None:
            raise RuntimeError("TDK 电源未连接")
        return self._instrument

    def _query_identity(self) -> str:
        instrument = self._instrument
        if instrument is None:
            raise RuntimeError("TDK 电源会话未建立")
        errors: list[str] = []
        for command in ("*IDN?", "IDN?"):
            try:
                value = str(instrument.query(command)).strip()
                if value:
                    return value
            except Exception as exc:
                errors.append(str(exc))
        raise RuntimeError("设备未响应 *IDN? / IDN?" + (f"：{errors[-1]}" if errors else ""))

    def _query_output_state(self) -> bool:
        instrument = self._instrument
        if instrument is None:
            return False
        for command in ("OUTP?", "OUTP:STAT?"):
            try:
                value = str(instrument.query(command)).strip().upper()
                return value in {"1", "ON", "TRUE"}
            except Exception:
                continue
        return False

    def _query_optional_float(self, command: str) -> float | None:
        instrument = self._instrument
        if instrument is None:
            return None
        try:
            value = float(str(instrument.query(command)).strip())
        except Exception:
            return None
        return value if math.isfinite(value) and value >= 0.0 else None

    @staticmethod
    def _finite_nonnegative(value: float, name: str) -> float:
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"{name}必须是大于或等于 0 的有限数值")
        return number

    def set_output_enabled(self, enabled: bool) -> None:
        self._require_instrument().write(f"OUTP {'ON' if enabled else 'OFF'}")
        self.output_enabled = bool(enabled)

    def set_output_current(self, current_a: float) -> None:
        value = self._finite_nonnegative(current_a, "电流")
        self._require_instrument().write(f"CURR {value:.3f}")

    def set_output_voltage(self, voltage_v: float) -> None:
        value = self._finite_nonnegative(voltage_v, "电压")
        self._require_instrument().write(f"VOLT {value:.3f}")

    def read_output_current(self) -> float:
        return float(str(self._require_instrument().query("MEAS:CURR?")).strip())

    def read_output_voltage(self) -> float:
        return float(str(self._require_instrument().query("MEAS:VOLT?")).strip())

    def i2c_write(self, _device_address: int, write_data: list[int]) -> tuple[bool, str]:
        """Translate the ARP current frame to a TDK SCPI current command."""
        try:
            if len(write_data) != 4 or write_data[:2] != [0xB4, 0xFF]:
                raise ValueError("TDK 控制器不支持该 I2C 写命令")
            current_a = float(write_data[2]) + float(write_data[3]) / 100.0
            self.set_output_current(current_a)
            return True, "写入成功"
        except Exception as exc:
            return False, str(exc)

    def i2c_write_read(
        self,
        _device_address: int,
        write_data: list[int],
        read_length: int,
    ) -> tuple[bool, list[int] | str]:
        """Translate ARP voltage/current read frames to TDK SCPI queries."""
        try:
            if read_length != 4 or len(write_data) < 2 or write_data[0] != 0xB4:
                raise ValueError("TDK 控制器不支持该读取命令")
            if write_data[1] in (0x88, 0x8B):
                value = self.read_output_voltage()
            elif write_data[1] == 0x8C:
                value = self.read_output_current()
            else:
                raise ValueError("TDK 电源不支持温度读取")
            integer_part = int(value)
            decimal_part = round((value - integer_part) * 100)
            if decimal_part == 100:
                integer_part += 1
                decimal_part = 0
            if not (0 <= integer_part <= 255):
                raise ValueError("TDK 电源读数超出当前 4 字节显示范围")
            return True, [0, 0, integer_part, decimal_part]
        except Exception as exc:
            return False, str(exc)
