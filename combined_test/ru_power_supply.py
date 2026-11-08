"""VARIED RU RS-232 Modbus RTU driver (RU manual A1.5, sections 5/6).

Only documented input registers, setpoints and the output coil are used.
Discovery and connection are read-only; no broadcast, write retries or polarity writes.
"""
from __future__ import annotations

from decimal import Decimal
import re
import threading
import time
from typing import Any, Callable

from combined_test.runtime_logging import get_runtime_logger
from tools.visa_session import acquire_visa_resource_manager, release_visa_resource_manager

RU_BAUD_RATES = (1200, 2400, 4800, 9600, 14400, 19200, 38400, 43000, 57600, 115200)
RU_OUTPUT_CONFIRM_TIMEOUT_S = 1.0
RU_OUTPUT_CONFIRM_INTERVAL_S = 0.05
_LOGGER = get_runtime_logger("ru_power_supply")


def rtu_frame(payload: bytes) -> bytes:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return payload + crc.to_bytes(2, "little")


class RuPowerSupply:
    def __init__(self, resource: str, baud_rate: int = 9600, address: int = 1,
                 timeout_ms: int = 1000,
                 resource_manager_factory: Callable[[], Any] | None = None) -> None:
        self.resource = str(resource).strip()
        if not re.fullmatch(r"ASRL\d+::INSTR", self.resource, re.IGNORECASE):
            raise ValueError("RU 电源需要 RS-232 串口资源（ASRL...::INSTR）")
        if isinstance(address, bool) or int(address) != address or not 1 <= address <= 255:
            raise ValueError("RU 地址必须为 1–255，禁止广播地址 0")
        if baud_rate not in RU_BAUD_RATES:
            raise ValueError("RU 波特率不在手册支持范围内")
        self.baud_rate, self.address = int(baud_rate), int(address)
        self.timeout_ms = int(timeout_ms)
        self._factory = resource_manager_factory
        self._manager: Any = None
        self._instrument: Any = None
        self._lock = threading.RLock()
        self._last_exchange = 0.0
        self.is_connected = False
        self.output_enabled = False
        self.identity = ""
        self.maximum_voltage_v: float | None = None
        self.maximum_current_a: float | None = None
        self._voltage_decimals: int | None = None
        self._current_decimals: int | None = None

    def connect_device(self, _index: int = 0) -> tuple[bool, str]:
        with self._lock:
            if self.is_connected:
                return True, self.identity
            try:
                self._manager = self._factory() if self._factory else acquire_visa_resource_manager()
                self._instrument = self._manager.open_resource(self.resource)
                from pyvisa.constants import Parity, StopBits, SerialTermination
                port = self._instrument
                port.timeout = self.timeout_ms
                port.baud_rate, port.data_bits = self.baud_rate, 8
                port.parity, port.stop_bits = Parity.none, StopBits.one
                port.read_termination = port.write_termination = None
                port.end_input = port.end_output = SerialTermination.none
                decimals = self._read_registers(106, 2)
                # A 16-bit nonnegative setpoint has at most four fractional digits.
                if any(n > 4 for n in decimals):
                    raise RuntimeError(f"RU 返回无效小数位数：{decimals}")
                self._voltage_decimals, self._current_decimals = decimals
                rated = self._read_registers(103, 2)
                if any(n <= 0 for n in rated):
                    raise RuntimeError("RU 返回无效额定量程")
                self.maximum_voltage_v = rated[0] / 10 ** decimals[0]
                self.maximum_current_a = rated[1] / 10 ** decimals[1]
                self.output_enabled = self.read_output_enabled()
                self.identity = (f"VARIED RU | {self.resource} | {self.baud_rate} baud | ADR {self.address}"
                                 f" | {self.maximum_voltage_v:g} V / {self.maximum_current_a:g} A")
                self.is_connected = True
                return True, self.identity
            except Exception as exc:
                self.disconnect_device()
                return False, f"连接 RU 电源失败：{exc}"

    def disconnect_device(self) -> bool:
        """Release handles; the station must confirm safe output before calling."""
        with self._lock:
            port, manager = self._instrument, self._manager
            self._instrument = self._manager = None
            self.is_connected = False
            try:
                if port is not None:
                    port.close()
            finally:
                if manager is not None:
                    if self._factory:
                        manager.close()
                    else:
                        release_visa_resource_manager(manager)
            return True

    def _exchange(self, function: int, register: int, value: int) -> bytes:
        request = rtu_frame(bytes([self.address, function])
                            + register.to_bytes(2, "big") + value.to_bytes(2, "big"))
        with self._lock:
            if self._instrument is None:
                raise RuntimeError("RU 电源未连接")
            port = self._instrument
            # 3.5 silent 8N1 character times between complete transactions.
            delay = max(0.004, 35 / self.baud_rate) - (time.monotonic() - self._last_exchange)
            if delay > 0:
                time.sleep(delay)
            response = b""
            try:
                from pyvisa.constants import BufferOperation
                port.flush(BufferOperation.discard_read_buffer)
                port.write_raw(request)
                header = bytes(port.read_bytes(2))
                response = header
                if len(header) != 2 or header[0] != self.address:
                    raise RuntimeError("RU 响应地址或长度不匹配")
                if header[1] == function | 0x80:
                    response = header + bytes(port.read_bytes(3))
                elif header[1] != function:
                    raise RuntimeError("RU 响应功能码不匹配")
                elif function in (5, 6):
                    response = header + bytes(port.read_bytes(6))
                else:
                    count = bytes(port.read_bytes(1))
                    expected = 1 if function == 1 else value * 2
                    if count != bytes([expected]):
                        raise RuntimeError("RU 响应字节数不匹配")
                    response = header + count + bytes(port.read_bytes(expected + 2))
                if len(response) < 5 or rtu_frame(response[:-2]) != response:
                    raise RuntimeError("RU 响应 CRC 或长度校验失败")
                if response[1] & 0x80:
                    raise RuntimeError(f"RU Modbus 异常响应：0x{response[2]:02X}")
                if function in (5, 6) and response != request:
                    raise RuntimeError("RU 未确认写入：响应回显不匹配")
                if function in (1, 5, 6):
                    _LOGGER.debug("RU RTU | resource=%s | tx=%s | rx=%s",
                                  self.resource, request.hex(" "), response.hex(" "))
                return response[2:-2]
            except Exception:
                _LOGGER.exception("RU RTU 失败 | resource=%s | tx=%s | rx=%s",
                                  self.resource, request.hex(" "), response.hex(" "))
                raise
            finally:
                self._last_exchange = time.monotonic()

    def _read_registers(self, register: int, count: int = 1) -> list[int]:
        payload = self._exchange(4, register, count)[1:]
        return [int.from_bytes(payload[i:i+2], "big") for i in range(0, len(payload), 2)]

    def read_output_enabled(self) -> bool:
        payload = self._exchange(1, 133, 1)
        if payload[1] not in (0, 1):
            raise RuntimeError("RU 返回无效输出状态")
        return bool(payload[1])

    def set_output_enabled(self, enabled: bool) -> None:
        with self._lock:
            # A lost enable acknowledgement must not be mistaken for output off.
            if enabled:
                self.output_enabled = True
            self._exchange(5, 133, 0xFF00 if enabled else 0)
            # A command echo acknowledges receipt, not completion of the output
            # transition. Poll only the documented coil; never replay the write.
            deadline = time.monotonic() + RU_OUTPUT_CONFIRM_TIMEOUT_S
            port = self._instrument
            previous_timeout = port.timeout
            actual: bool | None = None
            reads = 0
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    port.timeout = max(1, min(self.timeout_ms, int(remaining * 1000)))
                    # Protocol failures remain failures; only a valid opposite
                    # state is eligible for another read within the deadline.
                    actual = self.read_output_enabled()
                    reads += 1
                    if actual == bool(enabled):
                        self.output_enabled = actual
                        return
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    time.sleep(min(RU_OUTPUT_CONFIRM_INTERVAL_S, remaining))
            finally:
                port.timeout = previous_timeout
            detail = (f"RU 输出状态回读与设定不一致：请求{'开启' if enabled else '关闭'}，"
                      f"回读{'开启' if actual else '关闭'}；"
                      f"{RU_OUTPUT_CONFIRM_TIMEOUT_S:g} 秒内读取 {reads} 次（{self.resource}，地址 {self.address}）")
            _LOGGER.error(detail)
            raise RuntimeError(detail)

    def _encode_setpoint(self, value: float, decimals: int | None,
                         maximum: float | None, name: str) -> int:
        number = Decimal(str(value))
        if decimals is None or maximum is None or not self.is_connected:
            raise RuntimeError("RU 尚未读取小数位数和额定量程")
        if not number.is_finite() or number < 0 or number > Decimal(str(maximum)):
            raise ValueError(f"RU {name}必须在 0–{maximum:g} 范围内")
        scaled = number * 10 ** decimals
        if scaled != scaled.to_integral_value():
            raise ValueError(f"RU {name}分辨率为 {10 ** -decimals:g}，请调整设定值")
        raw = int(scaled)
        if not 0 <= raw <= 65535:
            raise ValueError("RU 设定值超出 16 位寄存器范围")
        return raw

    def validate_current(self, value: float) -> None:
        self._encode_setpoint(value, self._current_decimals, self.maximum_current_a, "电流")

    def set_output_voltage(self, voltage_v: float) -> None:
        raw = self._encode_setpoint(voltage_v, self._voltage_decimals, self.maximum_voltage_v, "电压")
        self._exchange(6, 149, raw)

    def set_output_current(self, current_a: float) -> None:
        raw = self._encode_setpoint(current_a, self._current_decimals, self.maximum_current_a, "电流")
        self._exchange(6, 150, raw)

    def read_output_voltage(self) -> float:
        if self._voltage_decimals is None:
            raise RuntimeError("RU 电压小数位数未知")
        return self._read_registers(100)[0] / 10 ** self._voltage_decimals

    def read_output_current(self) -> float:
        if self._current_decimals is None:
            raise RuntimeError("RU 电流小数位数未知")
        return self._read_registers(101)[0] / 10 ** self._current_decimals


def probe_ru_serial_resource(resource: str, *, baud_rate: int = 9600, address: int = 1,
                             resource_manager_factory: Callable[[], Any] | None = None) -> bool:
    supply = RuPowerSupply(resource, baud_rate, address, timeout_ms=300,
                           resource_manager_factory=resource_manager_factory)
    try:
        return supply.connect_device()[0]
    finally:
        supply.disconnect_device()
