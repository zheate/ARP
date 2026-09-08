"""Dual-station coordinator and JSON bridge dispatch backend."""

from __future__ import annotations

import threading
import uuid
from contextlib import ExitStack
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from .snapshot_builder import SnapshotBuilder
from .station_runtime import StationRuntime
from .station_support import (
    DeviceDiscoveryThread as _DeviceDiscoveryThread,
    normalize_device_serial as _normalize_device_serial,
)


_LOGGER = get_runtime_logger("headless_backend")


class HeadlessBackend:
    """Own dual-station hardware, automation, and serializable application state."""

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

        # Shared export queue worker
        self.export_worker = ExportQueueWorker()
        self.export_worker.start()

        # Shared device discovery state
        self.power_supply_resources: list[str] = []
        self.ch341_discovery_state: Literal["not_scanned", "ready", "empty", "error"] = "not_scanned"
        self.ch341_scan_error: str = ""
        self.ch341_descriptors: list[Ch341DeviceDescriptor] = []
        self.power_meter_options: list[PowerMeterOption] = []
        self.power_meter_detect_thread: PowerMeterDetectThread | None = None
        self.power_meter_detection_detail: str = ""
        self.power_meter_detection_station_id: str = ""
        self.power_supply_detect_thread: _DeviceDiscoveryThread | None = None
        self.power_supply_detection_detail: str = ""
        self.power_supply_detection_kind: Literal["ch341", "tdk"] | None = None
        self.power_supply_detection_station_id: str = ""
        self.spectrometer_detect_thread: _DeviceDiscoveryThread | None = None
        self.spectrometer_detection_detail: str = ""
        self.spectrometer_detection_station_id: str = ""
        self.spectrometer_resources: list[dict[str, Any]] = []

        # Initialize Stations
        station_configs = self.settings.get("runtime/stations", {})
        raw_st1_config = station_configs.get("1", stored_dict) if isinstance(station_configs, dict) else stored_dict
        raw_st2_config = station_configs.get("2", {}) if isinstance(station_configs, dict) else {}
        st1_config = dict(raw_st1_config) if isinstance(raw_st1_config, dict) else {}
        st2_config = dict(raw_st2_config) if isinstance(raw_st2_config, dict) else {}

        for config in (st1_config, st2_config):
            config["ch341SelectedLocationPath"] = ""
            config["ch341PortBindings"] = []

        for key in (
            "tdkResource",
            "powerMeterResource",
            "powerMeterSerialNumber",
            "spectrometerSerialNumber",
        ):
            station_one_resource = st1_config.get(key)
            if station_one_resource not in {None, ""} and st2_config.get(key) == station_one_resource:
                st2_config[key] = ""

        self.stations: dict[str, StationRuntime] = {
            "1": StationRuntime("1", self, st1_config),
            "2": StationRuntime("2", self, st2_config),
        }
        if any(station.power_meter_configuration_migrated for station in self.stations.values()):
            self._persist_configuration()

        self.notices: deque[dict[str, Any]] = deque(maxlen=8)
        self._notice_sequence = 0
        self.status_message = "后台已就绪"

    def _station(self, station_id: str | None = None) -> StationRuntime:
        key = str(station_id) if station_id is not None else "1"
        return self.stations.get(key, self.stations["1"])

    def _station_id_from_params(self, params: dict[str, Any]) -> str:
        """Resolve routing without confusing the human-readable station label."""
        value = params.get("stationId")
        if value is None:
            legacy_value = params.get("station")
            value = legacy_value if str(legacy_value) in self.stations else "1"
        station_id = str(value)
        if station_id not in self.stations:
            raise ValueError(f"未知工位 ID：{station_id}")
        return station_id

    # Forwarding properties for Station 1 backwards compatibility.  Older
    # tests/integrations used these names before PD became station-scoped.
    @property
    def pd_state(self) -> dict[str, Any]:
        return self.stations["1"].pd_state

    @property
    def series_revisions(self) -> dict[str, int]:
        return self.stations["1"].series_revisions

    # Forwarding properties for Station 1 backwards compatibility
    @property
    def configuration(self) -> dict[str, Any]:
        return self.stations["1"].configuration

    @configuration.setter
    def configuration(self, value: dict[str, Any]) -> None:
        self.stations["1"].configuration = value

    @property
    def power_supply_controller(self) -> Any | None:
        return self.stations["1"].power_supply_controller

    @power_supply_controller.setter
    def power_supply_controller(self, value: Any | None) -> None:
        self.stations["1"].power_supply_controller = value

    @property
    def power_supply_kind(self) -> str:
        return self.stations["1"].power_supply_kind

    @power_supply_kind.setter
    def power_supply_kind(self, value: str) -> None:
        self.stations["1"].power_supply_kind = value

    @property
    def active_current_a(self) -> float | None:
        return self.stations["1"].active_current_a

    @active_current_a.setter
    def active_current_a(self, value: float | None) -> None:
        self.stations["1"].active_current_a = value

    @property
    def latest_spectrum(self) -> SpectrumFrame | None:
        return self.stations["1"].latest_spectrum

    @latest_spectrum.setter
    def latest_spectrum(self, value: SpectrumFrame | None) -> None:
        self.stations["1"].latest_spectrum = value

    @property
    def power_points(self) -> deque[dict[str, float]]:
        return self.stations["1"].power_points

    @property
    def stable_power_points(self) -> dict[float, float]:
        return self.stations["1"].stable_power_points

    @property
    def efficiency_points(self) -> dict[float, float]:
        return self.stations["1"].efficiency_points

    @property
    def automatic(self) -> AutomaticTestOrchestrator:
        return self.stations["1"].automatic

    @property
    def automatic_detail(self) -> str:
        return self.stations["1"].automatic_detail

    @automatic_detail.setter
    def automatic_detail(self, value: str) -> None:
        self.stations["1"].automatic_detail = value

    @property
    def power_meter_reader(self) -> PowerMeterReaderThread | None:
        return self.stations["1"].power_meter_reader

    @power_meter_reader.setter
    def power_meter_reader(self, value: PowerMeterReaderThread | None) -> None:
        self.stations["1"].power_meter_reader = value

    @property
    def spectrometer_reader(self) -> SpectrometerReaderThread | None:
        return self.stations["1"].spectrometer_reader

    @spectrometer_reader.setter
    def spectrometer_reader(self, value: SpectrometerReaderThread | None) -> None:
        self.stations["1"].spectrometer_reader = value

    @property
    def connected_ch341_port(self) -> dict[str, Any] | None:
        return self.stations["1"].connected_ch341_port

    @connected_ch341_port.setter
    def connected_ch341_port(self, value: dict[str, Any] | None) -> None:
        self.stations["1"].connected_ch341_port = value

    @property
    def power_supply_identity_error(self) -> str:
        return self.stations["1"].power_supply_identity_error

    @power_supply_identity_error.setter
    def power_supply_identity_error(self, value: str) -> None:
        self.stations["1"].power_supply_identity_error = str(value)

    @property
    def _test_reference(self) -> Any | None:
        return self.stations["1"]._test_reference

    @_test_reference.setter
    def _test_reference(self, value: Any | None) -> None:
        self.stations["1"]._test_reference = value

    def _connect_power_supply(self) -> None:
        self.stations["1"].connect_power_supply()

    def _disconnect_power_supply(self) -> None:
        self.stations["1"].disconnect_power_supply()

    def _history_records(self, params: dict[str, Any]) -> tuple[Any, ...]:
        return self.stations["1"]._history_records(params)

    def check_device_conflicts(self, target_station_id: str, device_type: str, resource_identifier: Any) -> None:
        """Prevent Station 1 and Station 2 from binding to the same physical hardware."""
        for other_id, other_st in self.stations.items():
            if other_id == target_station_id:
                continue
            if device_type == "ch341":
                other_loc = other_st.configuration.get("ch341SelectedLocationPath")
                if other_st.power_supply_controller is not None and other_loc == resource_identifier:
                    raise RuntimeError(f"CH341 端口 {resource_identifier} 正在被工位 {other_id} 使用")
            elif device_type == "tdk":
                other_res = other_st.configuration.get("tdkResource")
                if other_st.power_supply_controller is not None and other_res == resource_identifier:
                    raise RuntimeError(f"TDK 电源 {resource_identifier} 正在被工位 {other_id} 使用")
            elif device_type == "powerMeter":
                other_res = other_st.configuration.get("powerMeterResource")
                if other_st.power_meter_reader is not None and other_res == resource_identifier:
                    raise RuntimeError(f"功率计 {resource_identifier} 正在被工位 {other_id} 使用")
            elif device_type == "spectrometer":
                other_dev = other_st.configuration.get("spectrometerDeviceId")
                if other_st.spectrometer_reader is not None and other_dev == resource_identifier:
                    raise RuntimeError(f"光谱仪设备 ID {resource_identifier} 正在被工位 {other_id} 使用")
            elif device_type == "spectrometerSerial":
                other_serial = _normalize_device_serial(
                    other_st.configuration.get("spectrometerSerialNumber")
                )
                if other_st.spectrometer_reader is not None and other_serial == resource_identifier:
                    raise RuntimeError(f"光谱仪 SN {resource_identifier} 正在被工位 {other_id} 使用")

    def check_workbook_path_available(self, target_station_id: str, workbook_path: Path) -> None:
        """Prevent two live station sessions from exporting to the same workbook."""
        target = Path(workbook_path).resolve(strict=False)
        with self._lock:
            for other_id, other_st in self.stations.items():
                if other_id == str(target_station_id):
                    continue
                other_path = other_st.record_store.workbook_path
                if other_path is not None and Path(other_path).resolve(strict=False) == target:
                    raise RuntimeError(
                        f"工位 {target_station_id} 与工位 {other_id} 的测试文件路径相同：{target}；"
                        "请修改 SN、测试站别或输出目录"
                    )

    def notify(
        self,
        level: str,
        title: str,
        message: str,
        *,
        station_id: str = "",
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
            if station_id:
                notice["stationId"] = str(station_id)
            if code:
                notice["code"] = str(code)
            self.notices.append(notice)
            self.status_message = str(message)
            if station_id and str(station_id) in self.stations:
                self.stations[str(station_id)].status_message = f"[工位 {station_id}] {message}"

    def set_status(self, message: str, *, station_id: str | None = None) -> None:
        with self._lock:
            self.status_message = str(message)
            if station_id is not None and str(station_id) in self.stations:
                self.stations[str(station_id)].status_message = str(message)

    def clear_station_transient_notices(self, station_id: str) -> None:
        with self._lock:
            self.notices = deque(
                (
                    notice
                    for notice in self.notices
                    if str(notice.get("stationId", "")) != str(station_id)
                    or notice.get("level") == "error"
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

        self.settings.update({
            "runtime/configuration": persistent_configuration(self.stations["1"]),
            "runtime/stations": {sid: persistent_configuration(st) for sid, st in self.stations.items()},
            "runtime/deviceProfiles": self.device_profiles,
        })

    def ping(self) -> dict[str, Any]:
        return {"status": "ok", "mode": self.mode}

    def capture_command_generations(self) -> dict[str, int]:
        return {sid: st.command_safety.capture() for sid, st in self.stations.items()}

    def dispatch(self, method: str, params: dict[str, Any], *, command_generations: dict[str, int] | None = None) -> dict[str, Any]:
        return self.dispatcher.dispatch(method, params, command_generations=command_generations)

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
        with ExitStack() as guards:
            for station in self.stations.values():
                guards.enter_context(station.command_safety.stopping())
            self._stop_all_impl()

    def _stop_all_impl(self) -> None:
        errors: list[str] = []
        stations = tuple(self.stations.values())
        for st in stations:
            st.request_emergency_stop("全部工位急停")

        def stop_station(station: StationRuntime) -> list[str]:
            station_errors: list[str] = []
            try:
                station.stop_all()
            except Exception as exc:
                station_errors.append(f"工位 {station.station_id}：{exc}")
            try:
                self._stop_pd(station)
            except Exception as exc:
                station_errors.append(f"工位 {station.station_id} PD：{exc}")
            return station_errors

        with ThreadPoolExecutor(
            max_workers=len(stations),
            thread_name_prefix="station-emergency-stop",
        ) as executor:
            futures = [executor.submit(stop_station, st) for st in stations]
            for future in as_completed(futures):
                errors.extend(future.result())
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

        station_results = {
            station_id: st.shutdown_snapshot()
            for station_id, st in self.stations.items()
        }
        confirmed = all(result["confirmed"] for result in station_results.values())
        reasons = [
            f"工位 {station_id}：{result['reason']}"
            for station_id, result in station_results.items()
            if not result["confirmed"] and result["reason"]
        ]
        if stop_error:
            reasons.insert(0, stop_error)
        return {
            "shutdownConfirmed": confirmed,
            "exportsDrained": exports_drained,
            "shutdown": {
                "requested": True,
                "confirmed": confirmed,
                "measuredCurrentA": None,
                "outputOffConfirmed": all(
                    result["outputOffConfirmed"] for result in station_results.values()
                ),
                "reason": "；".join(reasons) if reasons else "全部工位已确认安全下电",
            },
            "stations": station_results,
        }


    def snapshot(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.snapshot_builder.build(params)
