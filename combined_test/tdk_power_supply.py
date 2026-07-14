"""TDK-Lambda RS-232 power-supply adapter.

This module mirrors the command sequence used by scripts_runner's
``TdkPowerControl`` driver.  The serial port is opened through PyVISA as an
``ASRL...::INSTR`` resource, but the physical/device protocol is RS-232 rather
than USB/LAN SCPI.
"""

from __future__ import annotations

import math
import re
import time
from typing import Any, Callable

from tools.visa_session import acquire_visa_resource_manager, release_visa_resource_manager


TDK_DEFAULT_TIMEOUT_MS = 1000
# scripts_runner relies on PyVISA's ASRL default, which is 9600 baud. The
# installed GSP150-102 also acknowledges GEN commands at this rate.
TDK_DEFAULT_BAUD_RATE = 9600
TDK_DEFAULT_ADDRESS = 6
TDK_COMMAND_RESPONSE_DELAY_S = 0.05
# Legacy scripts_runner calibration: actual load voltage minus the raw MV?
# voltage was fitted against current with a first-order polynomial.
TDK_LINE_VOLTAGE_SLOPE_V_PER_A = -0.022621428571428535
TDK_LINE_VOLTAGE_INTERCEPT_V = -0.02882857142857197
TDK_NUMERIC_RESPONSE_PATTERN = re.compile(
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"
)


def compensate_tdk_output_voltage(raw_voltage_v: float, current_a: float) -> float:
    """Convert the TDK MV? reading to the calibrated load-side voltage."""
    voltage = float(raw_voltage_v)
    current = float(current_a)
    if not math.isfinite(voltage) or not math.isfinite(current):
        raise ValueError("TDK voltage compensation requires finite voltage and current")
    return (
        voltage
        + TDK_LINE_VOLTAGE_SLOPE_V_PER_A * current
        + TDK_LINE_VOLTAGE_INTERCEPT_V
    )


def list_tdk_serial_resources(resource_manager_factory: Callable[[], Any] | None = None) -> list[str]:
    """Return the VISA serial resources usable by the TDK RS-232 driver."""
    shared_manager = resource_manager_factory is None
    try:
        manager = (
            acquire_visa_resource_manager()
            if shared_manager
            else resource_manager_factory()
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少 TDK 电源依赖 pyvisa，请在 sth_eb314 环境中运行。") from exc
    try:
        # Restrict VISA discovery to serial resources. An unrestricted NI-VISA
        # scan also probes USB/LAN/GPIB and can block the Qt UI for ~10 seconds.
        return sorted(str(item) for item in manager.list_resources("ASRL?*::INSTR"))
    finally:
        if shared_manager:
            release_visa_resource_manager(manager)
        else:
            manager.close()


# Backwards-compatible import for callers from the first TDK implementation.
list_tdk_visa_resources = list_tdk_serial_resources


class TdkLambdaPowerSupply:
    """Controller compatible with scripts_runner's TDK RS-232 command set."""

    def __init__(
        self,
        resource: str,
        baud_rate: int = TDK_DEFAULT_BAUD_RATE,
        address: int = TDK_DEFAULT_ADDRESS,
        timeout_ms: int = TDK_DEFAULT_TIMEOUT_MS,
        resource_manager_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.resource = resource.strip()
        self.baud_rate = int(baud_rate)
        self.address = int(address)
        self.timeout_ms = int(timeout_ms)
        self._resource_manager_factory = resource_manager_factory
        self._resource_manager: Any | None = None
        self._resource_manager_is_shared = False
        self._instrument: Any | None = None
        self.is_connected = False
        self.identity = ""
        self.output_enabled = False
        # The legacy RS-232 command set does not expose portable MAX queries.
        self.maximum_voltage_v: float | None = None
        self.maximum_current_a: float | None = None

    def _make_resource_manager(self) -> Any:
        if self._resource_manager_factory is not None:
            self._resource_manager_is_shared = False
            return self._resource_manager_factory()
        try:
            manager = acquire_visa_resource_manager()
        except ModuleNotFoundError as exc:
            raise RuntimeError("缺少 TDK 电源依赖 pyvisa，请在 sth_eb314 环境中运行。") from exc
        self._resource_manager_is_shared = True
        return manager

    def connect_device(self, _index: int = 0) -> tuple[bool, str]:
        if not self.resource:
            return False, "请选择 TDK 电源串口"
        if not self.resource.upper().startswith("ASRL"):
            return False, f"TDK 电源需要 RS-232 串口资源（ASRL...::INSTR）：{self.resource}"
        if self.is_connected:
            return True, self.identity or self.resource

        try:
            self._resource_manager = self._make_resource_manager()
            self._instrument = self._resource_manager.open_resource(self.resource)
            self._instrument.timeout = self.timeout_ms
            self._instrument.read_termination = "\r"
            self._instrument.write_termination = "\r"
            self._instrument.baud_rate = self.baud_rate

            # Keep the scripts_runner initialization order: address first,
            # followed by remote-control mode. Each command must be acknowledged
            # so an open COM port cannot be mistaken for a connected supply.
            self._write(f"ADR {self.address}")
            self._write("RMT 1")
            self.identity = f"TDK-Lambda RS-232 | {self.resource} | {self.baud_rate} baud | ADR {self.address}"
            self.output_enabled = False
            self.is_connected = True
            return True, self.identity
        except Exception as exc:
            self.disconnect_device()
            return False, f"连接 TDK 电源失败：{exc}"

    def disconnect_device(self) -> bool:
        instrument, manager = self._instrument, self._resource_manager
        manager_is_shared = self._resource_manager_is_shared
        self._instrument = None
        self._resource_manager = None
        self._resource_manager_is_shared = False
        self.is_connected = False
        self.output_enabled = False
        if instrument is not None:
            try:
                instrument.close()
            except Exception:
                pass
        if manager is not None:
            try:
                if manager_is_shared:
                    release_visa_resource_manager(manager)
                else:
                    manager.close()
            except Exception:
                pass
        return True

    def set_i2c_speed(self, _speed: int) -> bool:
        """Compatibility no-op for the existing controller lifecycle."""
        return True

    def _require_instrument(self) -> Any:
        if self._instrument is None:
            raise RuntimeError("TDK 电源未连接")
        return self._instrument

    def _exchange(self, command: str) -> str:
        instrument = self._require_instrument()
        try:
            instrument.write(command)
            time.sleep(TDK_COMMAND_RESPONSE_DELAY_S)
            return str(instrument.read()).strip()
        except Exception as exc:
            error_text = str(exc).lower()
            if "invalid session" in error_text or "resource might be closed" in error_text:
                self.disconnect_device()
                raise RuntimeError(
                    "TDK RS-232 会话已失效，串口可能已被关闭；请断开后重新连接 TDK"
                ) from exc
            raise

    def _write(self, command: str) -> None:
        response = self._exchange(command)
        if response.upper() != "OK":
            display_response = response if response else "<空响应>"
            raise RuntimeError(f"TDK 电源未确认命令 {command!r}：{display_response!r}")

    def _query_float(self, command: str) -> float:
        response = self._exchange(command)
        # Some Genesys units return just the number, while others echo the
        # mnemonic (for example ``MV 12.34``). Match the whole response so an
        # error such as ``E01`` or ``ERROR 12`` cannot become a measurement.
        mnemonic = command.rstrip("?").strip()
        match = re.fullmatch(
            rf"(?:{re.escape(mnemonic)}\??\s*[:=]?\s*)?"
            rf"({TDK_NUMERIC_RESPONSE_PATTERN.pattern})(?:\s*[A-Za-z]+)?",
            response,
            re.IGNORECASE,
        )
        if match is None:
            raise RuntimeError(f"TDK 电源返回无效数据：{response!r}")
        value = float(match.group(1))
        if not math.isfinite(value):
            raise RuntimeError(f"TDK 电源返回非有限数值：{response!r}")
        return value

    @staticmethod
    def _finite_nonnegative(value: float, name: str) -> float:
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"{name}必须是大于或等于 0 的有限数值")
        return number

    def set_output_enabled(self, enabled: bool) -> None:
        self._write(f"OUT {1 if enabled else 0}")
        self.output_enabled = bool(enabled)

    def set_output_current(self, current_a: float) -> None:
        value = self._finite_nonnegative(current_a, "电流")
        self._write(f"PC {value:06.2f}")

    def set_output_voltage(self, voltage_v: float) -> None:
        value = self._finite_nonnegative(voltage_v, "电压")
        self._write(f"PV {value:06.2f}")

    def read_output_current(self) -> float:
        return self._query_float("MC?")

    def read_output_voltage(self) -> float:
        return self._query_float("MV?")

    def i2c_write(self, _device_address: int, write_data: list[int]) -> tuple[bool, str]:
        """Translate the ARP current frame to a TDK serial current command."""
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
        """Translate ARP voltage/current read frames to TDK serial queries."""
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
