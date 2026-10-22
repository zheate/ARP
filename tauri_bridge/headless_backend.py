"""Single-station coordinator and JSON bridge dispatch backend."""

from __future__ import annotations

import threading
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable, Literal

from combined_test.automation import AutomaticTestOrchestrator
from combined_test.devices import (
    PowerMeterDetectThread,
    PowerMeterReaderThread,
    SpectrometerReaderThread,
)
from combined_test.ch341_ports import Ch341DeviceDescriptor
from combined_test.models import (
    PowerMeterOption,
    SpectrumFrame,
)
from combined_test.runtime_logging import get_runtime_logger

from .backend_dispatch import BackendDispatcher
from .command_safety import finite_number
from .device_discovery import DeviceDiscoveryService
from .export_queue import ExportQueueWorker
from .settings import JsonSettings
from .power_meter_profiles import PowerMeterProfiles
from .snapshot_builder import SnapshotBuilder
from .station_runtime import StationRuntime
from .station_support import (
    DeviceDiscoveryThread as _DeviceDiscoveryThread,
)


_LOGGER = get_runtime_logger("headless_backend")


class HeadlessBackend:
    """Own single-station hardware, automation, and serializable application state."""

    mode = "active"

    def __init__(self, settings: JsonSettings | None = None) -> None:
        self.runtime_id = str(uuid.uuid4())
        self._lock = threading.RLock()
        self._snapshot_lock = threading.RLock()
        self.snapshot_builder = SnapshotBuilder(self)
        self.discovery = DeviceDiscoveryService(self)
        self.dispatcher = BackendDispatcher(self)
        self.settings = settings or JsonSettings()
        stored = self.settings.get("runtime/configuration", {})
        stored_dict = stored if isinstance(stored, dict) else {}
        raw_profiles = self.settings.get("runtime/deviceProfiles", {})
        self.device_profiles: dict[str, dict[str, dict[str, Any]]] = {
            "powerMeters": {},
        }
        if isinstance(raw_profiles, dict) and isinstance(raw_profiles.get("powerMeters"), dict):
            self.device_profiles["powerMeters"] = {
                str(key): dict(value)
                for key, value in raw_profiles["powerMeters"].items()
                if isinstance(value, dict)
            }

        # Background Excel export worker
        self.export_worker = ExportQueueWorker()

        # Device discovery state
        self.power_supply_resources: list[str] = []
        self.ch341_discovery_state: Literal["not_scanned", "ready", "empty", "error"] = "not_scanned"
        self.ch341_scan_error: str = ""
        self.ch341_descriptors: list[Ch341DeviceDescriptor] = []
        self.power_meter_options: list[PowerMeterOption] = []
        self.power_meter_detect_thread: PowerMeterDetectThread | None = None
        self.power_meter_detection_detail: str = ""
        self.power_supply_detect_thread: _DeviceDiscoveryThread | None = None
        self.power_supply_detection_detail: str = ""
        self.power_supply_detection_kind: Literal["ch341", "tdk", "ru"] | None = None
        self.spectrometer_detect_thread: _DeviceDiscoveryThread | None = None
        self.spectrometer_detection_detail: str = ""
        self.spectrometer_resources: list[dict[str, Any]] = []

        # Older releases stored two configurations. Adopt station 1 once;
        # historical workbooks and journals remain in their original locations.
        legacy_stations = self.settings.get("runtime/stations")
        initial_config = stored_dict
        if isinstance(legacy_stations, dict) and isinstance(legacy_stations.get("1"), dict):
            initial_config = legacy_stations["1"]
        initial_config = dict(initial_config)
        initial_config["ch341SelectedLocationPath"] = ""
        initial_config["ch341PortBindings"] = []
        self.runtime = StationRuntime(self, initial_config)
        self.power_meter_profiles = PowerMeterProfiles(self)
        profiles_migrated = self.power_meter_profiles.initialize(initial_config)
        if legacy_stations is not None or self.runtime.power_meter_configuration_migrated or profiles_migrated:
            self._persist_configuration()
        self.export_worker.start()

        self.notices: deque[dict[str, Any]] = deque(maxlen=8)
        self._notice_sequence = 0
        self.status_message = "后台已就绪"

    @staticmethod
    def validate_runtime_target(params: dict[str, Any]) -> None:
        """Reject stale second-station commands instead of controlling this device."""
        if params.get("stationId") is not None and str(params["stationId"]) != "1":
            raise ValueError("当前版本仅支持单工位，旧工位请求已拒绝")
        view = str(params.get("view", ""))
        if view in {"automatic_2", "manual_2", "pd_2"}:
            raise ValueError(f"未知视图：{view}")

    # Compatibility properties for integrations using the coordinator directly.
    @property
    def pd_state(self) -> dict[str, Any]:
        return self.runtime.pd_state

    @property
    def series_revisions(self) -> dict[str, int]:
        return self.runtime.series_revisions

    @property
    def configuration(self) -> dict[str, Any]:
        return self.runtime.configuration

    @configuration.setter
    def configuration(self, value: dict[str, Any]) -> None:
        self.runtime.configuration = value

    @property
    def power_supply_controller(self) -> Any | None:
        return self.runtime.power_supply_controller

    @power_supply_controller.setter
    def power_supply_controller(self, value: Any | None) -> None:
        self.runtime.power_supply_controller = value

    @property
    def power_supply_kind(self) -> str:
        return self.runtime.power_supply_kind

    @power_supply_kind.setter
    def power_supply_kind(self, value: str) -> None:
        self.runtime.power_supply_kind = value

    @property
    def active_current_a(self) -> float | None:
        return self.runtime.active_current_a

    @active_current_a.setter
    def active_current_a(self, value: float | None) -> None:
        self.runtime.active_current_a = value

    @property
    def latest_spectrum(self) -> SpectrumFrame | None:
        return self.runtime.latest_spectrum

    @latest_spectrum.setter
    def latest_spectrum(self, value: SpectrumFrame | None) -> None:
        self.runtime.latest_spectrum = value

    @property
    def power_points(self) -> deque[dict[str, float]]:
        return self.runtime.power_points

    @property
    def stable_power_points(self) -> dict[float, float]:
        return self.runtime.stable_power_points

    @property
    def efficiency_points(self) -> dict[float, float]:
        return self.runtime.efficiency_points

    @property
    def automatic(self) -> AutomaticTestOrchestrator:
        return self.runtime.automatic

    @property
    def automatic_detail(self) -> str:
        return self.runtime.automatic_detail

    @automatic_detail.setter
    def automatic_detail(self, value: str) -> None:
        self.runtime.automatic_detail = value

    @property
    def power_meter_reader(self) -> PowerMeterReaderThread | None:
        return self.runtime.power_meter_reader

    @power_meter_reader.setter
    def power_meter_reader(self, value: PowerMeterReaderThread | None) -> None:
        self.runtime.power_meter_reader = value

    @property
    def spectrometer_reader(self) -> SpectrometerReaderThread | None:
        return self.runtime.spectrometer_reader

    @spectrometer_reader.setter
    def spectrometer_reader(self, value: SpectrometerReaderThread | None) -> None:
        self.runtime.spectrometer_reader = value

    @property
    def connected_ch341_port(self) -> dict[str, Any] | None:
        return self.runtime.connected_ch341_port

    @connected_ch341_port.setter
    def connected_ch341_port(self, value: dict[str, Any] | None) -> None:
        self.runtime.connected_ch341_port = value

    @property
    def power_supply_identity_error(self) -> str:
        return self.runtime.power_supply_identity_error

    @power_supply_identity_error.setter
    def power_supply_identity_error(self, value: str) -> None:
        self.runtime.power_supply_identity_error = str(value)

    @property
    def _test_reference(self) -> Any | None:
        return self.runtime._test_reference

    @_test_reference.setter
    def _test_reference(self, value: Any | None) -> None:
        self.runtime._test_reference = value

    def _connect_power_supply(self) -> None:
        self.runtime.connect_power_supply()

    def _disconnect_power_supply(self) -> None:
        self.runtime.disconnect_power_supply()

    def _history_records(self, params: dict[str, Any]) -> tuple[Any, ...]:
        return self.runtime._history_records(params)

    def notify(
        self,
        level: str,
        title: str,
        message: str,
        *,
        code: str = "",
    ) -> None:
        with self._lock:
            self._notice_sequence += 1
            notice: dict[str, Any] = {
                "id": str(self._notice_sequence),
                "level": str(level),
                "title": str(title),
                "message": str(message),
            }
            if code:
                notice["code"] = str(code)
            self.notices.append(notice)
            self.status_message = str(message)
            self.runtime.status_message = str(message)

    def set_status(self, message: str) -> None:
        with self._lock:
            self.status_message = str(message)
            self.runtime.status_message = str(message)

    def clear_transient_notices(self) -> None:
        with self._lock:
            self.notices = deque(
                (
                    notice
                    for notice in self.notices
                    if notice.get("level") == "error"
                    or notice.get("code") == "power_protection"
                ),
                maxlen=8,
            )

    def start_remembered_device_discovery(self) -> None:
        self.discovery.start_remembered_device_discovery()

    def _persist_configuration(self) -> None:
        def persistent_configuration(station: StationRuntime) -> dict[str, Any]:
            configuration = dict(station.configuration)
            configuration["ch341SelectedLocationPath"] = ""
            configuration["ch341PortBindings"] = []
            return configuration

        # Capture and flush under the same lock as profile transactions. A
        # concurrent device callback must never persist a candidate that rolls back.
        with self.runtime._lock:
            self.settings.update({
                "runtime/configuration": persistent_configuration(self.runtime),
                "runtime/deviceProfiles": self.device_profiles,
            }, remove_keys=("runtime/stations",))

    def ping(self) -> dict[str, Any]:
        return {"status": "ok", "mode": self.mode}

    def capture_command_generation(self) -> int:
        return self.runtime.command_safety.capture()

    def dispatch(self, method: str, params: dict[str, Any], *, command_generation: int | None = None) -> dict[str, Any]:
        return self.dispatcher.dispatch(method, params, command_generation=command_generation)

    def dispatch_file_task(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.dispatcher.dispatch_file_task(method, params)


    def scan_ch341(self) -> None:
        self.discovery.scan_ch341()



    def _refresh_pd(self, station: StationRuntime) -> None:
        with station._pd_lock:
            if station._pd_detect_thread is not None:
                return
            station.pd_state["detecting"] = True
            station.pd_state["status"] = "正在准备 NI-DAQ 采集卡识别"

        def discover(report: Callable[[str], None]) -> tuple[str, list[Any], Callable[[Any, str], list[str]]]:
            report("正在加载 NI-DAQmx 驱动")
            from tools.pd_daq_mvp import channels_for_terminal_mode, discover_ni_daq_devices

            report("正在枚举 NI-DAQ 采集卡")
            driver, devices = discover_ni_daq_devices()
            return driver, list(devices), channels_for_terminal_mode

        thread = _DeviceDiscoveryThread(discover)
        with station._pd_lock:
            station._pd_detect_thread = thread

        def on_completed(result: tuple[str, list[Any], Callable[[Any, str], list[str]]]) -> None:
            driver, devices, channels_for_terminal_mode = result
            with station._pd_lock:
                station._pd_devices = devices
                station.pd_state["devices"] = [device.label() for device in devices]
                if devices:
                    selected = devices[0]
                    terminal = str(station.pd_state["settings"]["terminal"])
                    station.pd_state["settings"]["device"] = selected.label()
                    station.pd_state["channels"] = list(channels_for_terminal_mode(selected, terminal))
                    station.pd_state["ranges"] = [
                        {"label": f"±{value:g} V", "value": value}
                        for value in selected.voltage_ranges
                    ]
                    if station.pd_state["channels"] and not station.pd_state["settings"]["channel"]:
                        station.pd_state["settings"]["channel"] = station.pd_state["channels"][0]
                station.pd_state["status"] = f"识别完成：{driver}，发现 {len(devices)} 台采集卡"

        def on_status(message: str) -> None:
            with station._pd_lock:
                station.pd_state["status"] = str(message)

        def on_failed(message: str) -> None:
            with station._pd_lock:
                station.pd_state["status"] = f"识别失败：{message}"
            self.notify("error", "PD 采集", message)

        def on_finished() -> None:
            with station._pd_lock:
                if station._pd_detect_thread is thread:
                    station._pd_detect_thread = None
                    station.pd_state["detecting"] = False

        thread.completed.connect(on_completed)
        thread.status.connect(on_status)
        thread.failed.connect(on_failed)
        thread.finished.connect(on_finished)
        thread.start()

    def _configure_pd(self, station: StationRuntime, params: dict[str, Any]) -> None:
        with station._pd_lock:
            candidate = {
                key: params.get(key, value)
                for key, value in station.pd_state["settings"].items()
            }
            for key in ("range", "sampleRateHz", "blockSize", "scale", "offset"):
                candidate[key] = finite_number(
                    candidate[key], key, minimum=float("-inf") if key in {"scale", "offset"} else 0,
                )
            if not candidate["blockSize"].is_integer():
                raise ValueError("blockSize 必须是整数")
            candidate["blockSize"] = int(candidate["blockSize"])
            if not isinstance(candidate["save"], bool):
                raise ValueError("save 必须是 JSON 布尔值")
            from tools.pd_daq_mvp import PdDaqSettings
            selected = next((device for device in station._pd_devices if candidate["device"] in {device.name, device.label()}), None)
            PdDaqSettings(
                channel=str(candidate["channel"]) or "unselected",
                terminal_mode=str(candidate["terminal"]),
                voltage_range_v=candidate["range"], sample_rate_hz=candidate["sampleRateHz"],
                block_size=candidate["blockSize"], scale=candidate["scale"], offset=candidate["offset"],
                unit=str(candidate["unit"]),
            ).validate(None if selected is None else selected.max_single_channel_rate_hz)
            station.pd_state["settings"] = candidate

    def _start_pd(self, station: StationRuntime, params: dict[str, Any]) -> None:
        self._configure_pd(station, params)
        with station._pd_lock:
            if station._pd_reader is not None:
                return
            has_devices = bool(station._pd_devices)
        if not has_devices:
            raise RuntimeError("请先完成 NI-DAQ 采集卡识别")
        with station._pd_lock:
            devices = list(station._pd_devices)
        from tools.pd_daq_mvp import (
            PdAcquisitionThread,
            PdDaqSettings,
            default_csv_path,
        )

        with station._pd_lock:
            values = dict(station.pd_state["settings"])
        requested_device = str(values["device"])
        device = next(
            (item for item in devices if item.label() == requested_device or item.name == requested_device),
            devices[0],
        )
        channel = str(values["channel"]).strip()
        if not channel:
            channel = device.ai_channels[0]
        save_path = None
        if bool(values["save"]):
            output_dir = Path(str(values["outputDir"] or station.configuration["outputDir"])).expanduser()
            save_path = default_csv_path(output_dir, channel)
        settings = PdDaqSettings(
            channel=channel,
            terminal_mode=str(values["terminal"]),
            voltage_range_v=float(values["range"]),
            sample_rate_hz=float(values["sampleRateHz"]),
            block_size=int(values["blockSize"]),
            scale=float(values["scale"]),
            offset=float(values["offset"]),
            unit=str(values["unit"]),
            save_path=save_path,
        )
        settings.validate(device.max_single_channel_rate_hz)
        reader = PdAcquisitionThread(settings)
        with station._pd_lock:
            if station._pd_reader is not None:
                return
            station._pd_reader = reader
            reader.block_ready.connect(lambda block: self._on_pd_block(station, block))
            reader.acquisition_ready.connect(lambda value: self._on_pd_ready(station, value))
            reader.acquisition_failed.connect(lambda message: self._on_pd_failed(station, message))
            reader.recording_stopped.connect(lambda path: self._on_pd_recording_stopped(station, path))
            reader.finished.connect(lambda: self._on_pd_finished(station))
            station.pd_state["state"] = "running"
            station.pd_state["status"] = "正在启动"
        reader.start()

    def _on_pd_block(self, station: StationRuntime, block: Any) -> None:
        with station._pd_lock:
            points = station.pd_state["points"]
            points.extend(
                {"elapsedS": float(elapsed), "value": float(value)}
                for elapsed, value in zip(block.plot_times_s, block.plot_values)
            )
            if len(points) > 13_000:
                del points[: len(points) - 13_000]
            calibrated = block.calibrated
            voltage = block.voltage
            station.pd_state["currentValue"] = f"{calibrated.latest:.6g} {station.pd_state['settings']['unit']}"
            station.pd_state["voltage"] = f"{voltage.latest:.6g} V"
            station.pd_state["mean"] = f"{calibrated.mean:.6g}"
            station.pd_state["standardDeviation"] = f"{calibrated.standard_deviation:.6g}"
            station.pd_state["rangeText"] = f"{calibrated.minimum:.6g} – {calibrated.maximum:.6g}"
            station.pd_state["sampleCount"] = str(block.sample_count)
            station.series_revisions["pd"] += 1

    def _on_pd_ready(self, station: StationRuntime, channel: str) -> None:
        with station._pd_lock:
            station.pd_state["state"] = "running"
            station.pd_state["status"] = f"正在采集 {channel}"

    def _on_pd_failed(self, station: StationRuntime, message: str) -> None:
        with station._pd_lock:
            station.pd_state["state"] = "error"
            station.pd_state["status"] = str(message)
        station.notify("error", "PD 采集", message)

    def _on_pd_recording_stopped(self, station: StationRuntime, path: str) -> None:
        if path:
            with station._pd_lock:
                station.pd_state["status"] = f"记录已保存：{path}"

    def _on_pd_finished(self, station: StationRuntime) -> None:
        with station._pd_lock:
            station._pd_reader = None
            if station.pd_state["state"] != "error":
                station.pd_state["state"] = "idle"

    def _stop_pd(self, station: StationRuntime) -> None:
        with station._pd_lock:
            reader = station._pd_reader
        if reader is not None:
            reader.stop()
            reader.wait(5_000)
        with station._pd_lock:
            station.pd_state["state"] = "idle"
            station.pd_state["status"] = "已停止"

    def _stop_all(self) -> None:
        with self.runtime.command_safety.stopping():
            self._stop_all_impl()

    def _stop_all_impl(self) -> None:
        self.runtime.request_emergency_stop("急停")
        errors: list[str] = []
        try:
            self.runtime.stop_all()
        except Exception as exc:
            errors.append(str(exc))
        try:
            self._stop_pd(self.runtime)
        except Exception as exc:
            errors.append(f"PD：{exc}")
        if errors:
            raise RuntimeError("停止未完全确认：" + "；".join(errors))

    def _shutdown(self) -> dict[str, Any]:
        stop_error = ""
        try:
            self._stop_all()
        except Exception as exc:
            stop_error = str(exc)
        finally:
            exports_drained = self.export_worker.graceful_stop()

        shutdown = self.runtime.shutdown_snapshot()
        if stop_error:
            shutdown["reason"] = "；".join(filter(None, (stop_error, shutdown["reason"])))
        return {
            "shutdownConfirmed": shutdown["confirmed"],
            "exportsDrained": exports_drained,
            "shutdown": shutdown,
        }

    def snapshot(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.snapshot_builder.build(params)
