"""Hardware discovery, identity binding, and remembered-device selection."""

from __future__ import annotations

from typing import Any, Callable, Sequence

from combined_test.ch341_ports import Ch341DeviceDescriptor
from combined_test.devices import (
    PowerMeterDetectThread,
    extract_power_resource_name,
    load_spectrometer_components,
    scan_ch341_devices,
)
from combined_test.models import PowerMeterOption
from combined_test.tdk_power_supply import (
    list_tdk_serial_resources,
    probe_tdk_serial_resource,
)

from .station_runtime import StationRuntime
from .station_support import (
    DeviceDiscoveryThread as _DeviceDiscoveryThread,
    normalize_device_serial as _normalize_device_serial,
    power_meter_serial_number as _power_meter_serial_number,
    spectrometer_label as _spectrometer_label,
)


class DeviceDiscoveryService:
    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def _power_meter_profile_gain(self, option: PowerMeterOption) -> float:
        service = self._backend.power_meter_profiles
        revision = service.selected(service.library(option.driver_kind, _power_meter_serial_number(option)))
        return revision["softwareGain"] if revision else 1.0

    def _bind_power_meter_option(self, station: StationRuntime, option: PowerMeterOption) -> None:
        service = self._backend.power_meter_profiles
        serial_number = _power_meter_serial_number(option)
        with station._lock:
            if (service.key(option.driver_kind, serial_number) != service.current_key()
                    or option.resource != station.configuration["powerMeterResource"]):
                service.assert_editable()
            key = service.key(option.driver_kind, serial_number)
            is_new = key not in self._backend.device_profiles["powerMeters"]
            service.ensure(option.driver_kind, serial_number, wavelength=976.0 if is_new else None)
            station.configuration["powerMeterResource"] = option.resource
            station.configuration["powerMeterDriverKind"] = option.driver_kind
            station.configuration["powerMeterSerialNumber"] = serial_number
            service.apply_current()

    def _rebind_remembered_power_meters(self, options: Sequence[PowerMeterOption]) -> None:
        backend = self._backend
        station = backend.runtime
        remembered_serial = _normalize_device_serial(
            station.configuration.get("powerMeterSerialNumber")
        )
        remembered_driver = str(
            station.configuration.get("powerMeterDriverKind", "caihuang")
        ).lower()
        matches = [
            option
            for option in options
            if _power_meter_serial_number(option) == remembered_serial
            and option.driver_kind.lower() == remembered_driver
        ] if remembered_serial else []
        if len(matches) == 1:
            self._bind_power_meter_option(station, matches[0])
            return

        resource = str(station.configuration.get("powerMeterResource", "")).strip()
        resource_match = next((option for option in options if option.resource == resource), None)
        if not remembered_serial and resource_match is not None:
            self._bind_power_meter_option(station, resource_match)

    def _select_power_meter_after_scan(
        self,
        station: StationRuntime,
        options: Sequence[PowerMeterOption],
    ) -> None:
        """Keep the remembered meter when found, otherwise select the first result."""
        selected_resource = str(
            station.configuration.get("powerMeterResource", "")
        ).strip()
        selected_option = next(
            (option for option in options if option.resource == selected_resource),
            None,
        )
        if selected_option is not None:
            current_serial = _normalize_device_serial(
                station.configuration.get("powerMeterSerialNumber")
            )
            if (
                _power_meter_serial_number(selected_option) != current_serial
                or selected_option.driver_kind.lower()
                != str(station.configuration.get("powerMeterDriverKind", "")).lower()
            ):
                self._bind_power_meter_option(station, selected_option)
            return
        choice = next(iter(options), None)
        if choice is not None:
            self._bind_power_meter_option(station, choice)

    def _rebind_remembered_spectrometers(self, resources: Sequence[dict[str, Any]]) -> None:
        backend = self._backend
        station = backend.runtime
        remembered_serial = _normalize_device_serial(
            station.configuration.get("spectrometerSerialNumber")
        )
        matches = [
            resource
            for resource in resources
            if _normalize_device_serial(resource.get("serialNumber")) == remembered_serial
        ] if remembered_serial else []
        if len(matches) == 1:
            resource = matches[0]
            station.configuration["spectrometerDeviceId"] = int(resource["deviceId"])
            station.configuration["spectrometerSerialNumber"] = remembered_serial
            station.configuration["spectrometerResource"] = str(resource["label"])

    def _select_spectrometer_after_scan(
        self,
        station: StationRuntime,
        resources: Sequence[dict[str, Any]],
    ) -> None:
        """Keep the remembered spectrometer when found, otherwise select the first result."""
        selected_serial = _normalize_device_serial(
            station.configuration.get("spectrometerSerialNumber")
        )
        selected_id = station.configuration.get("spectrometerDeviceId")
        if any(
            (
                selected_serial
                and _normalize_device_serial(resource.get("serialNumber")) == selected_serial
            )
            or (
                not selected_serial
                and selected_id not in {None, ""}
                and int(resource["deviceId"]) == int(selected_id)
            )
            for resource in resources
        ):
            return
        choice = next(iter(resources), None)
        if choice is not None:
            station.configuration["spectrometerDeviceId"] = int(choice["deviceId"])
            station.configuration["spectrometerSerialNumber"] = _normalize_device_serial(
                choice.get("serialNumber")
            )
            station.configuration["spectrometerResource"] = str(choice["label"])

    def _select_ch341_after_scan(
        self,
        station: StationRuntime,
        descriptors: Sequence[Ch341DeviceDescriptor],
    ) -> None:
        """Select the first available physical CH341 port after discovery."""
        selected_location = str(
            station.configuration.get("ch341SelectedLocationPath", "")
        ).strip()
        if any(descriptor.location_path == selected_location for descriptor in descriptors):
            return
        choice = next(iter(descriptors), None)
        if choice is not None:
            station.configuration["ch341SelectedLocationPath"] = choice.location_path

    def start_remembered_device_discovery(self) -> None:
        """Restore persisted choices and enumerate spectrometers at startup."""
        backend = self._backend
        power_meter_types = {
            "caihuang": "Caihuang CHLP-P",
            "coherent": "Coherent PowerMax",
            "laserpoint": "LaserPoint",
            "ophir": "Ophir USB",
        }
        power_meter_options: list[PowerMeterOption] = []
        spectrometer_resources: list[dict[str, Any]] = []
        station = backend.runtime
        power_serial = _normalize_device_serial(
            station.configuration.get("powerMeterSerialNumber")
        )
        power_resource = str(
            station.configuration.get("powerMeterResource", "")
        ).strip()
        driver_kind = str(
            station.configuration.get("powerMeterDriverKind", "caihuang")
        ).lower()
        if power_serial and power_resource:
            option = PowerMeterOption(
                resource=power_resource,
                device_type=power_meter_types.get(driver_kind, driver_kind),
                detail=f"已记住 | 探头 SN {power_serial} | 连接时校验",
                driver_kind=driver_kind,
                serial_number=power_serial,
            )
            if option not in power_meter_options:
                power_meter_options.append(option)

        spectrum_serial = _normalize_device_serial(
            station.configuration.get("spectrometerSerialNumber")
        )
        raw_device_id = station.configuration.get("spectrometerDeviceId")
        if spectrum_serial and raw_device_id not in {None, ""}:
            device_id = int(raw_device_id)
            resource = {
                "label": _spectrometer_label(device_id, spectrum_serial),
                "deviceId": device_id,
                "serialNumber": spectrum_serial,
            }
            if resource not in spectrometer_resources:
                spectrometer_resources.append(resource)

        with backend._lock:
            backend.power_meter_options = power_meter_options
            backend.spectrometer_resources = spectrometer_resources
            if power_meter_options:
                backend.power_meter_detection_detail = (
                    f"识别完成：已恢复 {len(power_meter_options)} 台功率计，连接时校验探头 SN"
                )
            if spectrometer_resources:
                backend.spectrometer_detection_detail = (
                    f"识别完成：已恢复 {len(spectrometer_resources)} 台光谱仪，连接时校验 SN"
                )

        # USB spectrometers are safe to enumerate without starting acquisition.
        # Do this once at launch so the normal single-device setup is ready to
        # use without requiring an initial click on the Identify button.
        self._refresh_spectrometer()

    def scan_ch341(self) -> None:
        backend = self._backend
        try:
            descriptors = scan_ch341_devices()
            with backend._lock:
                backend.ch341_descriptors = descriptors
                backend.ch341_discovery_state = "ready" if descriptors else "empty"
                backend.ch341_scan_error = ""
        except Exception as exc:
            with backend._lock:
                backend.ch341_discovery_state = "error"
                backend.ch341_scan_error = str(exc)
                backend.ch341_descriptors = []
            backend.notify("error", "CH341 扫描失败", str(exc))

    def refresh_device(self, params: dict[str, Any]) -> None:
        backend = self._backend
        device = str(params.get("device", "all"))
        backend.validate_runtime_target(params)
        target_station = backend.runtime
        if device in {"all", "powerSupply"}:
            self._refresh_power_supply(target_station)

        if device in {"all", "powerMeter"}:
            self._refresh_power_meter(params)

        if device in {"all", "spectrometer"}:
            self._refresh_spectrometer()

    def _refresh_power_supply(self, station: StationRuntime) -> None:
        backend = self._backend
        with backend._lock:
            if backend.power_supply_detect_thread is not None:
                return
            controller_kind = station.power_supply_kind
            backend.power_supply_detection_detail = "正在准备电源识别"
            backend.power_supply_detection_kind = controller_kind

        def discover(report: Callable[[str], None]) -> tuple[str, list[Any]]:
            if controller_kind == "tdk":
                report("正在枚举 TDK RS232 串口")
                candidates = list_tdk_serial_resources()
                confirmed_power_meter_resources = {
                    extract_power_resource_name(option.resource).upper()
                    for option in backend.power_meter_options
                    if extract_power_resource_name(option.resource).upper().startswith("ASRL")
                }
                configured_meter_resource = str(backend.runtime.configuration.get("powerMeterResource", "")).strip()
                if configured_meter_resource:
                    confirmed_power_meter_resources.add(extract_power_resource_name(configured_meter_resource).upper())
                remaining = [
                    resource
                    for resource in candidates
                    if str(resource).strip().upper() not in confirmed_power_meter_resources
                ]
                verified: list[str] = []
                for index, resource in enumerate(remaining, start=1):
                    report(f"正在验证 TDK 串口 {index}/{len(remaining)}：{resource}")
                    if probe_tdk_serial_resource(resource):
                        verified.append(str(resource))
                return controller_kind, verified
            report("正在扫描 CH341 设备")
            return controller_kind, scan_ch341_devices()

        thread = _DeviceDiscoveryThread(discover)
        with backend._lock:
            backend.power_supply_detect_thread = thread

        def on_completed(result: tuple[str, list[Any]]) -> None:
            kind, devices = result
            with backend._lock:
                if kind == "tdk":
                    backend.power_supply_resources = [str(resource) for resource in devices]
                    backend.power_supply_detection_detail = f"识别完成：发现 {len(devices)} 个 TDK 串口"
                else:
                    backend.ch341_descriptors = list(devices)
                    backend.ch341_discovery_state = "ready" if devices else "empty"
                    backend.ch341_scan_error = ""
                    self._select_ch341_after_scan(station, devices)
                    backend.power_supply_detection_detail = f"识别完成：发现 {len(devices)} 个 CH341 设备"
            if kind == "ch341":
                backend.notify(
                    "info",
                    "CH341 识别",
                    backend.power_supply_detection_detail,
                )

        def on_status(message: str) -> None:
            with backend._lock:
                backend.power_supply_detection_detail = str(message)
            backend.set_status(str(message))

        def on_failed(message: str) -> None:
            with backend._lock:
                if controller_kind == "ch341":
                    backend.ch341_discovery_state = "error"
                    backend.ch341_scan_error = str(message)
                    backend.ch341_descriptors = []
                backend.power_supply_detection_detail = f"识别失败：{message}"
            backend.notify("error", "CH341 扫描失败" if controller_kind == "ch341" else "TDK 串口识别", message)

        def on_finished() -> None:
            with backend._lock:
                if backend.power_supply_detect_thread is thread:
                    backend.power_supply_detect_thread = None

        thread.completed.connect(on_completed)
        thread.status.connect(on_status)
        thread.failed.connect(on_failed)
        thread.finished.connect(on_finished)
        thread.start()

    def _refresh_power_meter(self, params: dict[str, Any]) -> None:
        backend = self._backend
        with backend._lock:
            if backend.power_meter_detect_thread is not None:
                return
        backend.validate_runtime_target(params)
        backend.power_meter_profiles.assert_editable()
        thread = PowerMeterDetectThread(preferred_resource="")
        with backend._lock:
            backend.power_meter_detect_thread = thread
            backend.power_meter_detection_detail = "正在准备功率计识别"

        def on_detected(options: list[PowerMeterOption]) -> None:
            # Never nest the shared backend lock around the station lock.
            with backend._lock:
                backend.power_meter_options = list(options)
            with backend.runtime._lock:
                import copy
                old_config = dict(backend.runtime.configuration)
                old_profiles = copy.deepcopy(backend.device_profiles)
                try:
                    for option in options:
                        key = backend.power_meter_profiles.key(option.driver_kind, _power_meter_serial_number(option))
                        backend.power_meter_profiles.ensure(
                            option.driver_kind, _power_meter_serial_number(option),
                            wavelength=976.0 if key not in backend.device_profiles["powerMeters"] else None,
                        )
                    self._rebind_remembered_power_meters(options)
                    self._select_power_meter_after_scan(backend.runtime, options)
                    backend._persist_configuration()
                except Exception as exc:
                    backend.runtime.configuration = old_config
                    backend.device_profiles = old_profiles
                    on_failed(str(exc))
                    return
            with backend._lock:
                backend.power_meter_detection_detail = (
                    f"识别完成：发现 {len(options)} 台兼容功率计"
                    if options
                    else "识别完成：未找到兼容功率计，请检查 USB/串口连接与驱动"
                )
        def on_finished() -> None:
            with backend._lock:
                backend.power_meter_detect_thread = None

        def on_status(message: str) -> None:
            with backend._lock:
                backend.power_meter_detection_detail = str(message)
            backend.set_status(str(message))

        def on_failed(message: str) -> None:
            with backend._lock:
                backend.power_meter_detection_detail = f"识别失败：{message}"
            backend.notify("error", "功率计扫描", message)

        thread.detected.connect(on_detected)
        thread.status.connect(on_status)
        thread.failed.connect(on_failed)
        thread.finished.connect(on_finished)
        thread.start()

    def _refresh_spectrometer(self) -> None:
        backend = self._backend
        with backend._lock:
            if backend.spectrometer_detect_thread is not None:
                return
            backend.spectrometer_detection_detail = "正在准备光谱仪识别"

        def discover(report: Callable[[str], None]) -> list[dict[str, Any]]:
            report("正在加载 Ocean Insight 驱动")
            spectrometer_class, _ = load_spectrometer_components(None)
            report("正在扫描 Ocean Insight USB 设备")
            return [dict(value) for value in spectrometer_class.detect_devices()]

        thread = _DeviceDiscoveryThread(discover)
        with backend._lock:
            backend.spectrometer_detect_thread = thread

        def on_completed(devices: list[dict[str, Any]]) -> None:
            resources = [
                {
                    "label": _spectrometer_label(
                        int(device["deviceId"]),
                        str(device.get("serialNumber", "")),
                    ),
                    "deviceId": int(device["deviceId"]),
                    "serialNumber": _normalize_device_serial(device.get("serialNumber")),
                }
                for device in devices
            ]
            with backend._lock:
                backend.spectrometer_resources = resources
                backend.spectrometer_detection_detail = f"识别完成：发现 {len(devices)} 台光谱仪"
            with backend.runtime._lock:
                self._rebind_remembered_spectrometers(resources)
                self._select_spectrometer_after_scan(
                    backend.runtime,
                    resources,
                )
                backend._persist_configuration()

        def on_status(message: str) -> None:
            with backend._lock:
                backend.spectrometer_detection_detail = str(message)
            backend.set_status(str(message))

        def on_failed(message: str) -> None:
            with backend._lock:
                backend.spectrometer_detection_detail = f"识别失败：{message}"
            backend.notify("error", "光谱仪识别", message)

        def on_finished() -> None:
            with backend._lock:
                if backend.spectrometer_detect_thread is thread:
                    backend.spectrometer_detect_thread = None

        thread.completed.connect(on_completed)
        thread.status.connect(on_status)
        thread.failed.connect(on_failed)
        thread.finished.connect(on_finished)
        thread.start()
