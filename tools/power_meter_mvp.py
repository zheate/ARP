"""Power-meter adapters and standalone command-line diagnostics."""

from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import pyvisa
from pyvisa import constants

from combined_test.concurrency import Signal, WorkerThread
from tools.visa_session import acquire_visa_resource_manager, release_visa_resource_manager


MAX_SAMPLES = 10_000
PLOT_HISTORY_S = 30.0
PLOT_REFRESH_S = 0.2
DEFAULT_RESOURCE = "ASRL3::INSTR"
OPHIR_COM_PROGID = "OphirLMMeasurement.CoLMMeasurement"
OPHIR_RESOURCE_PREFIX = "OPHIR::USB::"
OPHIR_CHANNEL = 0
OPHIR_SAMPLE_TIMEOUT_S = 2.0
LASERPOINT_RANGE_SYNC_ATTEMPTS = 4


@dataclass(frozen=True)
class ProbeResult:
    resource: str
    device_type: str
    detail: str
    driver_kind: str = "caihuang"
    serial_number: str = ""


def normalize_resource(name: str) -> str:
    value = name.strip().upper()
    if value.startswith("COM") and value[3:].isdigit():
        return f"ASRL{value[3:]}::INSTR"
    return value


def parse_caihuang_hin(raw: str) -> tuple[str, str]:
    """Return the normalized $HIN response and its probe SN when identifiable."""
    value = " ".join(str(raw).replace("\x00", " ").strip().split())
    value = re.sub(r"^\$?HIN\s*[:=]?\s*", "", value, flags=re.IGNORECASE)
    normalized = value.strip().upper()
    if normalized in {"", "OK", "ERROR", "ERR", "FAIL", "UNSUPPORTED", "SNPN"}:
        return "", ""
    probe_patterns = (
        r"(?:探头|PROBE|SENSOR)\s*(?:SN)?\s*[:=]\s*([^|,;\s]+)",
        r"(?:PSN|PN|SNPN)\s*[:=]\s*([^|,;\s]+)",
    )
    for pattern in probe_patterns:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match is not None:
            return normalized, match.group(1).strip().upper()
    # Unknown vendor formats remain safe by using the full response. This may
    # miss reuse across a head swap, but it cannot apply one probe's gain to a
    # different head/probe combination.
    return normalized, normalized


def normalize_ophir_resource(name: str) -> str:
    """Return the stable resource identifier used for an Ophir USB device."""
    value = str(name).strip()
    if value.upper().startswith(OPHIR_RESOURCE_PREFIX):
        value = value[len(OPHIR_RESOURCE_PREFIX) :].strip()
    if not value:
        return ""
    return f"{OPHIR_RESOURCE_PREFIX}{value.upper()}"


def ophir_serial_from_resource(name: str) -> str:
    """Extract the Ophir USB serial from a UI/configuration resource value."""
    value = str(name).strip()
    if value.upper().startswith(OPHIR_RESOURCE_PREFIX):
        value = value[len(OPHIR_RESOURCE_PREFIX) :].strip()
    return value.upper()


def _com_first_value(value: Any) -> Any:
    """Unwrap a one-element tuple returned for a COM out parameter."""
    if isinstance(value, tuple) and len(value) == 1:
        return value[0]
    return value


def _com_sequence(value: Any) -> list[Any]:
    """Convert a COM SAFEARRAY/list result into a normal Python list."""
    value = _com_first_value(value)
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]


def _com_outputs(value: Any, count: int) -> tuple[Any, ...]:
    """Normalize dynamic-dispatch results containing several out parameters."""
    if isinstance(value, tuple):
        if len(value) == count:
            return value
        if len(value) == 1 and isinstance(value[0], (tuple, list)) and len(value[0]) == count:
            return tuple(value[0])
    return (value,) + (None,) * (count - 1)


def _create_ophir_controller() -> tuple[Any, Any | None]:
    """Create the Ophir COM controller and initialize COM for this thread."""
    try:
        import pythoncom
        import win32com.client
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Ophir 驱动依赖未安装：需要 pywin32，并且需要先安装 StarLab/Ophir COM 组件"
        ) from exc

    pythoncom.CoInitialize()
    try:
        controller = win32com.client.Dispatch(OPHIR_COM_PROGID)
    except Exception:
        pythoncom.CoUninitialize()
        raise
    return controller, pythoncom


def _release_ophir_controller(controller: Any) -> None:
    """Close any handles owned by a COM controller.

    The caller must drop its own controller reference before uninitializing
    COM. Keeping that final step outside this helper avoids blocking when the
    caller still has a local reference to the Dispatch wrapper.
    """
    try:
        close_all = getattr(controller, "CloseAll", None)
        if callable(close_all):
            close_all()
    except Exception:
        pass


def configure_caihuang(inst: pyvisa.resources.Resource, timeout_ms: int = 1000) -> None:
    inst.timeout = int(timeout_ms)
    inst.write_termination = "\r\n"
    inst.read_termination = "\r\n"
    if hasattr(inst, "baud_rate"):
        inst.baud_rate = 9600
        inst.data_bits = 8
        inst.parity = constants.Parity.none
        inst.stop_bits = constants.StopBits.one


def configure_coherent(inst: pyvisa.resources.Resource, timeout_ms: int = 1000) -> None:
    """Configure a Coherent PowerMax-USB/RS serial session."""
    inst.timeout = int(timeout_ms)
    # PowerMax commands must end in CR. SCPI replies end in CR/LF.
    inst.write_termination = "\r"
    inst.read_termination = "\r\n"
    if hasattr(inst, "baud_rate"):
        inst.baud_rate = 9600
        inst.data_bits = 8
        inst.parity = constants.Parity.none
        inst.stop_bits = constants.StopBits.one
        if hasattr(inst, "flow_control"):
            inst.flow_control = constants.ControlFlow.none


def configure_laserpoint(inst: pyvisa.resources.Resource, timeout_ms: int = 1000) -> None:
    """Configure the VISA serial session used by scripts_runner's LaserPoint driver."""
    inst.timeout = int(timeout_ms)
    inst.write_termination = ":"
    inst.read_termination = ";"
    if hasattr(inst, "baud_rate"):
        inst.baud_rate = 38400
        inst.data_bits = 8
        inst.parity = constants.Parity.none
        inst.stop_bits = constants.StopBits.one
        if hasattr(inst, "flow_control"):
            inst.flow_control = constants.ControlFlow.none


def parse_power_w(raw: str) -> float:
    text = raw.strip()
    if text.upper() == "OVERFLOW":
        raise RuntimeError("power meter overflow")
    value = float(text)
    if not math.isfinite(value):
        raise RuntimeError(f"invalid power value: {text}")
    return value


def parse_coherent_power_w(raw: str) -> float:
    """Parse a PowerMax ``READ?`` record and reject unsafe readings."""
    fields = [field.strip() for field in str(raw).strip().split(",")]
    if not fields or not fields[0]:
        raise RuntimeError("Coherent 功率计未返回测量数据")
    power_w = parse_power_w(fields[0])
    if len(fields) >= 3:
        flags = fields[-2].upper()
        if "R" in flags:
            raise RuntimeError("Coherent 功率计量程溢出")
        if "T" in flags:
            raise RuntimeError("Coherent 功率计过温")
    return power_w


def _unquote_scpi_text(raw: str) -> str:
    text = str(raw).strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].strip()
    return text


def format_wavelength_nm(wavelength_nm: float) -> str:
    value = float(wavelength_nm)
    if not math.isfinite(value):
        raise RuntimeError(f"invalid wavelength value: {wavelength_nm}")
    return f"{value:.6f}".rstrip("0").rstrip(".")


def format_laserpoint_wavelength_nm(wavelength_nm: float) -> str:
    """Return LaserPoint's five-digit integer wavelength field."""
    value = float(wavelength_nm)
    if not math.isfinite(value) or not value.is_integer():
        raise RuntimeError("LaserPoint 波长必须是整数 nm")
    integer_value = int(value)
    if integer_value < 0 or integer_value > 99999:
        raise RuntimeError("LaserPoint 波长必须在 0 至 99999 nm 范围内")
    return f"{integer_value:05d}"


def parse_laserpoint_serial(raw: str) -> str:
    """Extract the six-digit serial returned by the LaserPoint SERNU command."""
    text = raw.strip().rstrip(";").strip()
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    if match is None:
        raise RuntimeError(f"LaserPoint 序列号响应无效：{text or '<empty>'}")
    return match.group(1)


def query_laserpoint_serial(inst: pyvisa.resources.Resource) -> str:
    """Read the serial number, recovering from one stale response frame."""
    raw = inst.query("*SERNU")
    try:
        return parse_laserpoint_serial(raw)
    except RuntimeError:
        # Auto-detection tries the Caihuang protocol first.  A PCPlug V3 can
        # leave that protocol error in its input buffer, so the first SERNU
        # query consumes the stale frame and the second returns the serial.
        return parse_laserpoint_serial(inst.query("*SERNU"))


def parse_laserpoint_power_w(raw: str) -> float:
    """Parse the numeric OUTPM response, tolerating a short textual prefix."""
    text = raw.strip().rstrip(";").strip()
    if not text or text.startswith("?"):
        raise RuntimeError(f"LaserPoint 功率响应无效：{text or '<empty>'}")
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", text)
    if match is None:
        raise RuntimeError(f"LaserPoint 功率响应无效：{text}")
    value = float(match.group(0))
    if not math.isfinite(value):
        raise RuntimeError(f"LaserPoint 功率响应无效：{text}")
    return value


def parse_laserpoint_gain_index(raw: str) -> int:
    """Map LaserPoint's fixed/automatic X1D mode to its active gain index."""
    text = raw.strip().rstrip(";").strip()
    match = re.search(r"(?<!\d)([0-5])(?!\d)", text)
    if match is None:
        raise RuntimeError(f"LaserPoint 当前量程响应无效：{text or '<empty>'}")
    mode = int(match.group(1))
    # X1D 0-2 are fixed x1/x10/x100; 3-5 are the same active gains while
    # automatic gain selection is enabled.
    return mode if mode <= 2 else mode - 3


def parse_laserpoint_full_scale_power_to_w(raw: str) -> tuple[float, float]:
    """Parse an FSWX1 response and return (full scale W, unit scale to W)."""
    text = raw.strip().rstrip(";").strip().lstrip("#").strip()
    match = re.fullmatch(
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*_\s*([A-Za-z\u00b5\u03bc]+)",
        text,
    )
    if match is None:
        raise RuntimeError(f"LaserPoint 功率量程响应无效：{text or '<empty>'}")
    value = float(match.group(1))
    if not math.isfinite(value) or value <= 0.0:
        raise RuntimeError(f"LaserPoint 功率量程响应无效：{text}")
    unit = match.group(2)
    normalized_unit = unit.replace("\u00b5", "u").replace("\u03bc", "u")
    scales_to_w = {
        "nW": 1e-9,
        "nw": 1e-9,
        "uW": 1e-6,
        "uw": 1e-6,
        "mW": 1e-3,
        "mw": 1e-3,
        "W": 1.0,
        "w": 1.0,
        "kW": 1e3,
        "kw": 1e3,
    }
    scale_to_w = scales_to_w.get(normalized_unit)
    if scale_to_w is None:
        raise RuntimeError(f"LaserPoint 功率量程单位不受支持：{unit}")
    return value * scale_to_w, scale_to_w


class CaihuangPowerMeter:
    device_type = "Caihuang CHLP-P"
    driver_kind = "caihuang"

    def __init__(self, resource: str) -> None:
        self.resource = normalize_resource(resource)
        self.probe_serial_number = ""
        self.rm = acquire_visa_resource_manager()
        self.inst = None
        try:
            self.inst = self.rm.open_resource(self.resource)
            configure_caihuang(self.inst)
        except Exception:
            if self.inst is not None:
                try:
                    self.inst.close()
                except Exception:
                    pass
                self.inst = None
            release_visa_resource_manager(self.rm)
            self.rm = None
            raise

    @staticmethod
    def probe(resource: str, timeout_ms: int = 1000) -> ProbeResult | None:
        rm = acquire_visa_resource_manager()
        inst = None
        try:
            inst = rm.open_resource(normalize_resource(resource))
            configure_caihuang(inst, timeout_ms=timeout_ms)
            reply = inst.query("$TES").strip()
            if reply == "OK":
                detail = "OK"
                try:
                    version = inst.query("$VER").strip()
                    if version:
                        detail = f"OK, version {version}"
                except Exception:
                    pass
                hardware_identity = ""
                try:
                    hin_response, hardware_identity = parse_caihuang_hin(inst.query("$HIN"))
                    if hardware_identity:
                        detail = f"{detail} | HIN {hin_response} | 增益身份：探头 SN {hardware_identity}"
                except Exception:
                    pass
                return ProbeResult(
                    normalize_resource(resource),
                    CaihuangPowerMeter.device_type,
                    detail,
                    CaihuangPowerMeter.driver_kind,
                    hardware_identity,
                )
        except Exception:
            return None
        finally:
            if inst is not None:
                try:
                    inst.close()
                except Exception:
                    pass
            release_visa_resource_manager(rm)
        return None

    def test(self) -> str:
        reply = self.inst.query("$TES").strip()
        if reply == "OK":
            try:
                _hin_response, self.probe_serial_number = parse_caihuang_hin(
                    self.inst.query("$HIN")
                )
            except Exception:
                self.probe_serial_number = ""
        return reply

    def set_wavelength(self, wavelength_nm: float) -> None:
        reply = self.inst.query(f"$WAV={format_wavelength_nm(wavelength_nm)}").strip()
        if reply != "SUCCEED":
            raise RuntimeError(f"set wavelength failed: {reply}")

    def read_power_w(self) -> float:
        return parse_power_w(self.inst.query("$POW"))

    def set_relative_zero(self, enabled: bool) -> None:
        reply = self.inst.query(f"$REL={1 if enabled else 0}").strip()
        if reply != "SUCCEED":
            raise RuntimeError(f"set relative zero failed: {reply}")

    def close(self) -> None:
        inst, rm = self.inst, self.rm
        self.inst = None
        self.rm = None
        try:
            if inst is not None:
                inst.close()
        finally:
            if rm is not None:
                release_visa_resource_manager(rm)


class CoherentPowerMeter:
    """Coherent PowerMax-USB/RS adapter using the documented SCPI host port."""

    device_type = "Coherent PowerMax"
    driver_kind = "coherent"

    def __init__(self, resource: str) -> None:
        self.resource = normalize_resource(resource)
        self.rm = acquire_visa_resource_manager()
        self.inst = None
        self.model_name = ""
        self.instrument_serial_number = ""
        self.probe_serial_number = ""
        try:
            self.inst = self.rm.open_resource(self.resource)
            configure_coherent(self.inst)
        except Exception:
            if self.inst is not None:
                try:
                    self.inst.close()
                except Exception:
                    pass
                self.inst = None
            release_visa_resource_manager(self.rm)
            self.rm = None
            raise

    @staticmethod
    def _is_powermax_identity(raw: str) -> bool:
        identity = str(raw).strip().upper()
        return "COHERENT" in identity and "POWERMAX" in identity

    @staticmethod
    def probe(resource: str, timeout_ms: int = 1000) -> ProbeResult | None:
        rm = acquire_visa_resource_manager()
        inst = None
        try:
            normalized_resource = normalize_resource(resource)
            inst = rm.open_resource(normalized_resource)
            configure_coherent(inst, timeout_ms=timeout_ms)
            identity = inst.query("*IDN?").strip()
            if not CoherentPowerMeter._is_powermax_identity(identity):
                return None

            model_name = ""
            instrument_serial_number = ""
            probe_serial_number = ""
            try:
                model_name = _unquote_scpi_text(inst.query("SYSTem:INFormation:MODel?"))
            except Exception:
                pass
            try:
                instrument_serial_number = _unquote_scpi_text(
                    inst.query("SYSTem:INFormation:INSTrument:SNUMber?")
                )
            except Exception:
                pass
            try:
                probe_serial_number = _unquote_scpi_text(
                    inst.query("SYSTem:INFormation:PROBe:SNUMber?")
                )
            except Exception:
                pass

            detail_parts = []
            if model_name:
                detail_parts.append(model_name)
            if instrument_serial_number:
                detail_parts.append(f"表头 SN {instrument_serial_number}")
            if probe_serial_number:
                detail_parts.append(f"探头 SN {probe_serial_number}")
            else:
                detail_parts.append("未读取到探头 SN（无法按探头记忆增益）")
            return ProbeResult(
                normalized_resource,
                CoherentPowerMeter.device_type,
                " | ".join(detail_parts) or identity,
                CoherentPowerMeter.driver_kind,
                probe_serial_number,
            )
        except Exception:
            return None
        finally:
            if inst is not None:
                try:
                    inst.close()
                except Exception:
                    pass
            release_visa_resource_manager(rm)

    def test(self) -> str:
        identity = self.inst.query("*IDN?").strip()
        if not self._is_powermax_identity(identity):
            raise RuntimeError(f"不是受支持的 Coherent PowerMax 设备：{identity or '<empty>'}")
        try:
            self.model_name = _unquote_scpi_text(
                self.inst.query("SYSTem:INFormation:MODel?")
            )
            if self.model_name:
                self.device_type = f"Coherent {self.model_name}"
        except Exception:
            self.model_name = ""
        try:
            self.instrument_serial_number = _unquote_scpi_text(
                self.inst.query("SYSTem:INFormation:INSTrument:SNUMber?")
            )
        except Exception:
            self.instrument_serial_number = ""
        try:
            self.probe_serial_number = _unquote_scpi_text(
                self.inst.query("SYSTem:INFormation:PROBe:SNUMber?")
            )
        except Exception:
            self.probe_serial_number = ""
        return "OK"

    def set_power_mode(self) -> None:
        self.inst.write("CONFigure:MEASure W")
        mode = self.inst.query("CONFigure:MEASure?").strip().upper()
        if mode != "W":
            raise RuntimeError(f"Coherent 功率计未进入功率测量模式：{mode or '<empty>'}")

    def set_wavelength(self, wavelength_nm: float) -> None:
        requested = float(wavelength_nm)
        command_value = format_wavelength_nm(requested)
        self.inst.write(f"CONFigure:WAVElength {command_value}")
        granted_text = self.inst.query("CONFigure:WAVElength?").strip()
        granted = float(granted_text)
        if not math.isfinite(granted) or not math.isclose(granted, requested, abs_tol=0.01):
            raise RuntimeError(
                f"Coherent 功率计不支持波长 {command_value} nm，设备设置为 {granted_text} nm"
            )

    def read_power_w(self) -> float:
        return parse_coherent_power_w(self.inst.query("READ?"))

    def set_relative_zero(self, enabled: bool) -> None:
        # PowerMax ZERO is a one-shot baseline capture. *RST restores the
        # documented power-on zero state, which is the closest equivalent to
        # the application's "cancel relative zero" action.
        self.inst.write("CONFigure:ZERO" if enabled else "*RST")

    def close(self) -> None:
        inst, rm = self.inst, self.rm
        self.inst = None
        self.rm = None
        try:
            if inst is not None:
                inst.close()
        finally:
            if rm is not None:
                release_visa_resource_manager(rm)


class LaserPointPowerMeter:
    """PyVISA adapter matching the LaserPoint protocol used by scripts_runner."""

    device_type = "LaserPoint"
    driver_kind = "laserpoint"

    def __init__(self, resource: str) -> None:
        self.resource = normalize_resource(resource)
        self.rm = acquire_visa_resource_manager()
        self.inst = None
        self.probe_serial_number = ""
        self._power_ranges_to_w: dict[int, tuple[float, float]] = {}
        try:
            self.inst = self.rm.open_resource(self.resource)
            configure_laserpoint(self.inst)
        except Exception:
            if self.inst is not None:
                try:
                    self.inst.close()
                except Exception:
                    pass
                self.inst = None
            release_visa_resource_manager(self.rm)
            self.rm = None
            raise

    @staticmethod
    def probe(resource: str, timeout_ms: int = 1000) -> ProbeResult | None:
        rm = acquire_visa_resource_manager()
        inst = None
        try:
            normalized_resource = normalize_resource(resource)
            inst = rm.open_resource(normalized_resource)
            configure_laserpoint(inst, timeout_ms=timeout_ms)
            serial_number = query_laserpoint_serial(inst)
            return ProbeResult(
                normalized_resource,
                LaserPointPowerMeter.device_type,
                f"探头 SN {serial_number}",
                LaserPointPowerMeter.driver_kind,
                serial_number,
            )
        except Exception:
            return None
        finally:
            if inst is not None:
                try:
                    inst.close()
                except Exception:
                    pass
            release_visa_resource_manager(rm)

    def test(self) -> str:
        self.probe_serial_number = query_laserpoint_serial(self.inst)
        return "OK"

    def _setting_command(self, command: str) -> None:
        self.inst.write(command)
        # PCPlug V3 acknowledges setters (for example ``#ok;`` and
        # ``#LAMBDA00940;``).  Consume that acknowledgement so it cannot be
        # mistaken for the following OUTPM response.  Some older controllers
        # are write-only, so only read when VISA reports buffered bytes.
        deadline = time.monotonic() + 0.15
        while time.monotonic() < deadline:
            try:
                buffered = int(self.inst.bytes_in_buffer)
            except (AttributeError, NotImplementedError):
                return
            if buffered > 0:
                reply = self.inst.read().strip()
                if reply.lstrip("#").startswith("?"):
                    raise RuntimeError(f"LaserPoint 设置命令失败：{command} -> {reply}")
                return
            time.sleep(0.005)

    def set_power_mode(self) -> None:
        self._setting_command("*POWER")
        self._power_ranges_to_w.clear()

    def set_gain_mode(self, mode: int) -> None:
        value = int(mode)
        if value < 0 or value > 3:
            raise ValueError("LaserPoint 增益模式必须在 0 至 3 之间")
        self._setting_command(f"*SETX1 {value}")
        self._power_ranges_to_w.clear()

    def set_wavelength(self, wavelength_nm: float) -> None:
        self._setting_command(f"*SETLAM{format_laserpoint_wavelength_nm(wavelength_nm)}")
        self._power_ranges_to_w.clear()

    def read_power_w(self) -> float:
        for _attempt in range(LASERPOINT_RANGE_SYNC_ATTEMPTS):
            gain_before = parse_laserpoint_gain_index(self.inst.query("*X1D"))
            power = parse_laserpoint_power_w(self.inst.query("*OUTPM"))
            gain_after = parse_laserpoint_gain_index(self.inst.query("*X1D"))
            if gain_before != gain_after:
                continue

            power_range = self._power_ranges_to_w.get(gain_after)
            if power_range is None:
                power_range = parse_laserpoint_full_scale_power_to_w(
                    self.inst.query(f"*FSWX1 {gain_after}")
                )
                self._power_ranges_to_w[gain_after] = power_range
            full_scale_w, scale_to_w = power_range
            power_w = power * scale_to_w
            # A unit/gain race produces impossible values such as 420,000 W
            # on a 3,000 W scale. Retry instead of publishing that sample.
            if abs(power_w) > full_scale_w * 1.05:
                continue
            return power_w

        raise RuntimeError("LaserPoint 自动量程切换期间未获得一致功率读数")

    def close(self) -> None:
        inst, rm = self.inst, self.rm
        self.inst = None
        self.rm = None
        try:
            if inst is not None:
                inst.close()
        finally:
            if rm is not None:
                release_visa_resource_manager(rm)


class OphirPowerMeter:
    """Ophir USB power-meter adapter backed by the StarLab COM interface.

    Ophir meters do not expose an ``ASRL...::INSTR`` VISA resource.  StarLab
    registers ``OphirLMMeasurement.CoLMMeasurement`` and provides USB scan,
    stream, and data APIs through that COM object instead.
    """

    device_type = "Ophir USB"
    driver_kind = "ophir"

    _STATUS_NAMES = {
        0: "OK",
        1: "overrange",
        2: "way overrange",
        3: "missing",
        4: "energy reset",
        5: "energy waiting",
        6: "energy summing",
        7: "energy timeout",
        8: "energy peak over",
        9: "energy over",
    }

    def __init__(self, resource: str = "") -> None:
        self.resource = normalize_ophir_resource(resource)
        self.meter_serial_number = ophir_serial_from_resource(self.resource)
        self.probe_serial_number = ""
        self.channel = OPHIR_CHANNEL
        self.controller: Any | None = None
        self._pythoncom: Any | None = None
        self.handle: int | None = None
        self._stream_started = False
        try:
            self.controller, self._pythoncom = _create_ophir_controller()
            serial_numbers = self._scan_serial_numbers(self.controller)
            if self.meter_serial_number:
                if not serial_numbers:
                    raise RuntimeError(
                        f"未检测到指定的 Ophir USB 设备：{self.meter_serial_number}"
                    )
                matching = next(
                    (
                        item
                        for item in serial_numbers
                        if item.upper() == self.meter_serial_number.upper()
                    ),
                    self.meter_serial_number,
                )
                if all(
                    item.upper() != self.meter_serial_number.upper()
                    for item in serial_numbers
                ):
                    raise RuntimeError(
                        f"未检测到指定的 Ophir USB 设备：{self.meter_serial_number}"
                    )
                self.meter_serial_number = matching
            elif serial_numbers:
                self.meter_serial_number = serial_numbers[0]
                self.resource = normalize_ophir_resource(self.meter_serial_number)
            else:
                raise RuntimeError("未检测到 Ophir USB 功率计，请先连接设备并在 StarLab 中确认可见")

            opened = self.controller.OpenUSBDevice(self.meter_serial_number)
            self.handle = int(_com_first_value(opened))
            if self.handle <= 0:
                raise RuntimeError(f"Ophir USB 设备打开失败：句柄 {self.handle}")
            self.probe_serial_number, _sensor_type, _sensor_name = self._sensor_info(
                self.controller,
                self.handle,
                self.channel,
            )
        except Exception:
            self.close()
            raise

    @staticmethod
    def _scan_serial_numbers(controller: Any) -> list[str]:
        result = controller.ScanUSB()
        serial_numbers: list[str] = []
        for value in _com_sequence(result):
            text = str(value).strip()
            if text and text.upper() not in serial_numbers:
                serial_numbers.append(text.upper())
        return serial_numbers

    @staticmethod
    def _sensor_info(controller: Any, handle: int, channel: int) -> tuple[str, str, str]:
        """Return probe serial, sensor type, and sensor name for one Ophir channel."""
        serial_number, sensor_type, sensor_name = _com_outputs(
            controller.GetSensorInfo(handle, channel),
            3,
        )
        return (
            str(serial_number or "").strip().upper(),
            str(sensor_type or "").strip(),
            str(sensor_name or "").strip(),
        )

    @staticmethod
    def probe_all(resource: str = "", timeout_ms: int = 1000) -> list[ProbeResult]:
        """Return all visible Ophir USB devices without opening a measurement stream."""
        del timeout_ms  # The StarLab COM API does not expose a probe timeout.
        requested_serial = ophir_serial_from_resource(resource) if resource else ""
        controller: Any | None = None
        pythoncom: Any | None = None
        try:
            controller, pythoncom = _create_ophir_controller()
            serial_numbers = OphirPowerMeter._scan_serial_numbers(controller)
            if requested_serial:
                serial_numbers = [
                    item for item in serial_numbers if item.upper() == requested_serial.upper()
                ]
            results: list[ProbeResult] = []
            for meter_serial_number in serial_numbers:
                handle: int | None = None
                probe_serial_number = ""
                sensor_type = ""
                sensor_name = ""
                try:
                    handle = int(
                        _com_first_value(controller.OpenUSBDevice(meter_serial_number))
                    )
                    if handle <= 0:
                        raise RuntimeError(f"Ophir USB 设备打开失败：句柄 {handle}")
                    probe_serial_number, sensor_type, sensor_name = OphirPowerMeter._sensor_info(
                        controller,
                        handle,
                        OPHIR_CHANNEL,
                    )
                except Exception:
                    probe_serial_number = ""
                finally:
                    if handle is not None and handle > 0:
                        try:
                            controller.Close(handle)
                        except Exception:
                            pass

                detail_parts = [f"USB 表头 SN {meter_serial_number}"]
                if probe_serial_number:
                    detail_parts.append(f"探头 SN {probe_serial_number}")
                    sensor_description = " ".join(
                        value for value in (sensor_name, sensor_type) if value
                    )
                    if sensor_description:
                        detail_parts.append(sensor_description)
                else:
                    detail_parts.append("未读取到探头 SN（无法按探头记忆增益）")
                results.append(
                    ProbeResult(
                        normalize_ophir_resource(meter_serial_number),
                        OphirPowerMeter.device_type,
                        " | ".join(detail_parts),
                        OphirPowerMeter.driver_kind,
                        probe_serial_number,
                    )
                )
            return results
        except Exception:
            # Ophir is an optional USB device. A missing StarLab/COM install or
            # an unplugged meter must not prevent VISA meters from being found.
            return []
        finally:
            if controller is not None:
                _release_ophir_controller(controller)
                controller = None
            if pythoncom is not None:
                pythoncom.CoUninitialize()

    @staticmethod
    def probe(resource: str = "", timeout_ms: int = 1000) -> ProbeResult | None:
        results = OphirPowerMeter.probe_all(resource, timeout_ms=timeout_ms)
        return results[0] if results else None

    def test(self) -> str:
        if self.controller is None or self.handle is None:
            raise RuntimeError("Ophir USB 功率计尚未打开")
        is_sensor_exists = getattr(self.controller, "IsSensorExists", None)
        if callable(is_sensor_exists):
            result = _com_first_value(is_sensor_exists(self.handle, self.channel))
            if result is False or (isinstance(result, (int, float)) and int(result) == 0):
                raise RuntimeError("Ophir 功率计未检测到测量探头")
        return "OK"

    def set_wavelength(self, wavelength_nm: float) -> None:
        if self.controller is None or self.handle is None:
            raise RuntimeError("Ophir USB 功率计尚未打开")
        getter = getattr(self.controller, "GetWavelengths", None)
        setter = getattr(self.controller, "SetWavelength", None)
        if not callable(getter) or not callable(setter):
            raise RuntimeError("当前 Ophir COM 组件不支持设置校准波长")

        current_index, options = _com_outputs(
            getter(self.handle, self.channel),
            2,
        )
        option_values = _com_sequence(options)
        if not option_values:
            raise RuntimeError("Ophir 功率计未返回可用波长列表")

        target = float(wavelength_nm)
        numeric_options: list[tuple[int, float]] = []
        for index, option in enumerate(option_values):
            match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", str(option))
            if match is None:
                continue
            try:
                numeric_options.append((index, float(match.group(0))))
            except ValueError:
                continue

        if numeric_options:
            selected_index = min(numeric_options, key=lambda item: abs(item[1] - target))[0]
        else:
            try:
                selected_index = int(_com_first_value(current_index))
            except (TypeError, ValueError):
                selected_index = 0
        setter(self.handle, self.channel, int(selected_index))

    def _ensure_stream(self) -> None:
        if self._stream_started:
            return
        if self.controller is None or self.handle is None:
            raise RuntimeError("Ophir USB 功率计尚未打开")
        self.controller.StartStream(self.handle, self.channel)
        self._stream_started = True

    def read_power_w(self) -> float:
        if self.controller is None or self.handle is None:
            raise RuntimeError("Ophir USB 功率计尚未打开")
        self._ensure_stream()
        # Every returned value becomes a new stability sample. Give the meter
        # the same bounded warm-up budget between samples, but never manufacture
        # fresh data by returning the previous value when its stream is empty.
        deadline = time.monotonic() + OPHIR_SAMPLE_TIMEOUT_S
        while True:
            data_result = self.controller.GetData(self.handle, self.channel)
            values, _timestamps, statuses = _com_outputs(data_result, 3)
            value_array = _com_sequence(values)
            status_array = _com_sequence(statuses)
            for index in range(len(value_array) - 1, -1, -1):
                try:
                    value = float(value_array[index])
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(value):
                    continue
                status = 0
                if index < len(status_array):
                    try:
                        status = int(status_array[index])
                    except (TypeError, ValueError):
                        status = 0
                if status != 0:
                    status_name = self._STATUS_NAMES.get(status, f"status {status}")
                    raise RuntimeError(f"Ophir 功率计读数无效：{status_name}")
                return value

            if time.monotonic() >= deadline:
                raise RuntimeError("Ophir 功率计超时未返回新的有效测量数据")
            time.sleep(0.02)

    def set_relative_zero(self, _enabled: bool) -> None:
        raise RuntimeError("Ophir COM 接口不支持当前的相对调零操作")

    def close(self) -> None:
        controller, pythoncom = self.controller, self._pythoncom
        handle = self.handle
        stream_started = self._stream_started
        self.controller = None
        self._pythoncom = None
        self.handle = None
        self._stream_started = False
        try:
            if controller is not None and handle is not None:
                if stream_started:
                    try:
                        controller.StopStream(handle, self.channel)
                    except Exception:
                        pass
                try:
                    controller.Close(handle)
                except Exception:
                    pass
        finally:
            if controller is not None:
                _release_ophir_controller(controller)
                controller = None
            if pythoncom is not None:
                pythoncom.CoUninitialize()


class PowerReaderThread(WorkerThread):
    """Toolkit-free compatibility reader for standalone diagnostics."""

    sample = Signal(float, float)
    status = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        resource: str,
        wavelength_nm: float,
        software_gain: float,
        interval_ms: int,
        driver_kind: str = "caihuang",
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)
        self.resource = resource
        self.wavelength_nm = wavelength_nm
        self.software_gain = software_gain
        self.interval_ms = interval_ms
        self.driver_kind = driver_kind
        self._running = False

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        meter: Any | None = None
        try:
            meter_classes = {
                "caihuang": CaihuangPowerMeter,
                "coherent": CoherentPowerMeter,
                "laserpoint": LaserPointPowerMeter,
                "ophir": OphirPowerMeter,
            }
            meter_class = meter_classes.get(self.driver_kind)
            if meter_class is None:
                raise RuntimeError(f"Unsupported diagnostic driver: {self.driver_kind}")
            meter = meter_class(self.resource)
            if meter.test() != "OK":
                raise RuntimeError("device test did not return OK")
            if self.driver_kind == "coherent" and hasattr(meter, "set_power_mode"):
                meter.set_power_mode()
            meter.set_wavelength(self.wavelength_nm)
            self.status.emit(
                f"Connected: {meter.device_type} on {normalize_resource(self.resource)}"
            )
            start = time.monotonic()
            self._running = True
            while self._running:
                power = round(meter.read_power_w() * self.software_gain, 2)
                self.sample.emit(time.monotonic() - start, power)
                self.msleep(self.interval_ms)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if meter is not None:
                try:
                    meter.close()
                except Exception:
                    pass


def discover_power_meters(resources: Iterable[str] | None = None) -> list[ProbeResult]:
    """Probe every supported VISA and Ophir meter without a GUI toolkit."""

    resource_manager = None
    try:
        if resources is None:
            resource_manager = acquire_visa_resource_manager()
            candidates = [
                str(item)
                for item in resource_manager.list_resources()
                if str(item).upper().startswith("ASRL")
            ]
        else:
            candidates = [normalize_resource(item) for item in resources if str(item).strip()]

        detected: list[ProbeResult] = []
        for resource in candidates:
            for meter_class in (CaihuangPowerMeter, CoherentPowerMeter, LaserPointPowerMeter):
                result = meter_class.probe(resource)
                if result is not None:
                    detected.append(result)
                    break
        detected.extend(OphirPowerMeter.probe_all())
        return detected
    finally:
        if resource_manager is not None:
            release_visa_resource_manager(resource_manager)


def _open_diagnostic_meter(driver_kind: str, resource: str) -> Any:
    meter_classes = {
        "caihuang": CaihuangPowerMeter,
        "coherent": CoherentPowerMeter,
        "laserpoint": LaserPointPowerMeter,
        "ophir": OphirPowerMeter,
    }
    if driver_kind == "auto":
        matches = discover_power_meters([resource] if resource else None)
        if not matches:
            raise RuntimeError("未检测到受支持的功率计")
        match = matches[0]
        driver_kind = match.driver_kind
        resource = match.resource
    meter_class = meter_classes[driver_kind]
    return meter_class(resource)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="无界面功率计诊断工具")
    parser.add_argument("--list", action="store_true", help="识别并列出所有受支持的功率计")
    parser.add_argument("--resource", default="", help="VISA 或 OPHIR::USB 资源")
    parser.add_argument(
        "--driver",
        choices=("auto", "caihuang", "coherent", "laserpoint", "ophir"),
        default="auto",
    )
    parser.add_argument("--wavelength", type=float, default=976.0, help="校准波长，单位 nm")
    parser.add_argument("--samples", type=int, default=0, help="读取样本数量")
    parser.add_argument("--interval-ms", type=int, default=300, help="样本间隔，单位 ms")
    parser.add_argument("--zero", choices=("on", "off"), help="切换支持设备的相对调零")
    args = parser.parse_args(argv)

    if args.list:
        print(json.dumps([result.__dict__ for result in discover_power_meters()], ensure_ascii=False, indent=2))
    if args.samples < 0:
        parser.error("--samples 必须大于或等于 0")
    if args.interval_ms < 0:
        parser.error("--interval-ms 必须大于或等于 0")
    if not args.list and args.samples == 0 and args.zero is None:
        parser.print_help()
        return 0

    if args.samples or args.zero is not None:
        meter = _open_diagnostic_meter(args.driver, args.resource)
        try:
            if meter.test() != "OK":
                raise RuntimeError("设备自检未返回 OK")
            if args.zero is not None:
                meter.set_relative_zero(args.zero == "on")
            if args.samples:
                if getattr(meter, "driver_kind", "") == "coherent" and hasattr(meter, "set_power_mode"):
                    meter.set_power_mode()
                meter.set_wavelength(args.wavelength)
                for index in range(args.samples):
                    print(json.dumps({"sample": index + 1, "power_w": meter.read_power_w()}, ensure_ascii=False))
                    if index + 1 < args.samples:
                        time.sleep(args.interval_ms / 1000.0)
        finally:
            meter.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
