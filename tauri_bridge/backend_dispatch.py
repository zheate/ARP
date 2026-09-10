"""Request routing and application configuration for the headless backend."""

from __future__ import annotations

import math
from typing import Any, Callable

from .service import BACKGROUND_FILE_TASK_METHODS
from .station_support import (
    MANUAL_CONTROL_METHODS,
    finite as _finite,
    normalize_device_serial as _normalize_device_serial,
)


class BackendDispatcher:
    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def dispatch(self, method: str, params: dict[str, Any], *, command_generation: int | None = None) -> dict[str, Any]:
        self._backend.validate_runtime_target(params)
        st = self._backend.runtime
        guarded = method in {
            "app.configure", "powerSupply.connect", "powerSupply.setCurrent", "powerSupply.setVoltage",
            "automatic.start", "automatic.resume", "automatic.retryPoint", "automatic.recoverPowerMeter",
            "automatic.recoverSpectrometer", "automatic.adjustSpectrum", "automatic.retryPersist",
        } or (method == "powerSupply.setOutput" and params.get("enabled", False) is not False)
        if guarded:
            with st.command_safety.command(command_generation):
                return self._dispatch(method, params)
        return self._dispatch(method, params)

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        backend = self._backend
        st = backend.runtime

        if method in MANUAL_CONTROL_METHODS and not st.manual_controls_allowed():
            raise RuntimeError(
                "硬件控制权由自动测试持有；请先结束并安全下电"
            )

        actions: dict[str, Callable[[dict[str, Any]], Any]] = {
            "app.configure": lambda p: self.configure(p),
            "device.refresh": backend.discovery.refresh_device,
            "powerSupply.connect": lambda _p: st.connect_power_supply(),
            "powerSupply.disconnect": lambda _p: st.disconnect_power_supply(),
            "powerSupply.setCurrent": lambda p: st.set_current(p),
            "powerSupply.setVoltage": lambda p: st.set_voltage(p),
            "powerSupply.setOutput": lambda p: st.set_output(p),
            "powerSupply.read": lambda p: st.read_power_supply(p),
            "powerMeter.start": lambda _p: st.start_power_meter(),
            "powerMeter.stop": lambda _p: st.stop_power_meter(),
            "powerMeter.setRelativeZero": lambda p: st.set_relative_zero(p),
            "manual.saveExcel": lambda _p: st.save_manual_records(),
            "spectrometer.start": lambda _p: st.start_spectrometer(),
            "spectrometer.stop": lambda _p: st.stop_spectrometer(),
            "spectrometer.saveCsv": lambda p: st.save_spectrum_csv(p),
            "automatic.start": lambda p: st.start_automatic(p),
            "automatic.pause": lambda _p: st.pause_automatic(),
            "automatic.resume": lambda p: st.resume_automatic("resume", p),
            "automatic.retryPoint": lambda p: st.resume_automatic("retry_point", p),
            "automatic.recoverPowerMeter": lambda p: st.resume_automatic("recover_power_meter", p),
            "automatic.recoverSpectrometer": lambda p: st.resume_automatic("recover_spectrometer", p),
            "automatic.adjustSpectrum": lambda p: st.resume_automatic("adjust_spectrum", p),
            "automatic.retryPersist": lambda p: st.resume_automatic("retry_persist", p),
            "automatic.end": lambda _p: st.end_automatic(),
            "automatic.nextDevice": lambda _p: st.prepare_next_device(),
            "automatic.retryExport": lambda p: st.retry_automatic_export(p),
            "shippingReport.inspect": self._inspect_shipping_report,
            "dataBrowser.list": self._list_data_records,
            "dataBrowser.inspect": self._inspect_data_record,
            "shippingReport.batchInspect": self._inspect_shipping_report_batch,
            "shippingReport.batchGenerateItem": self._generate_shipping_report_batch_item,
            "shippingReport.preview": self._preview_shipping_report,
            "shippingReport.generate": self._generate_shipping_report,
            "pd.refresh": lambda _p: backend._refresh_pd(st),
            "pd.configure": lambda p: backend._configure_pd(st, p),
            "pd.start": lambda p: backend._start_pd(st, p),
            "pd.stop": lambda _p: backend._stop_pd(st),
            "charts.reset": lambda _p: st.reset_charts(),
            "app.stopAll": lambda _p: backend._stop_all(),
            "app.shutdown": lambda _p: backend._shutdown(),
        }
        action = actions.get(method)
        if action is None:
            raise KeyError(method)
        result = action(params)
        if method in {
            "app.configure", "device.refresh", "powerSupply.connect", "powerSupply.disconnect",
            "powerSupply.setCurrent", "powerSupply.setVoltage", "powerSupply.setOutput",
            "powerMeter.start", "spectrometer.start", "automatic.start", "automatic.nextDevice",
            "automatic.resume", "automatic.retryPoint", "automatic.recoverPowerMeter",
            "automatic.recoverSpectrometer", "automatic.adjustSpectrum", "pd.configure",
        }:
            backend._persist_configuration()
        return backend.snapshot(params) if result is None else result

    def dispatch_file_task(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        backend = self._backend
        actions: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "dataBrowser.list": self._list_data_records,
            "dataBrowser.inspect": self._inspect_data_record,
            "shippingReport.inspect": self._inspect_shipping_report,
            "shippingReport.batchInspect": self._inspect_shipping_report_batch,
            "shippingReport.batchGenerateItem": self._generate_shipping_report_batch_item,
            "shippingReport.preview": self._preview_shipping_report,
            "shippingReport.generate": self._generate_shipping_report,
        }
        action = actions.get(method)
        if action is not None:
            return action(params)
        raise KeyError(method)

    def _list_data_records(self, params: dict[str, Any]) -> dict[str, Any]:
        from combined_test.data_browser import list_data_records
        return list_data_records(params)

    def _inspect_data_record(self, params: dict[str, Any]) -> dict[str, Any]:
        from combined_test.data_browser import inspect_data_record
        return inspect_data_record(params)

    def configure(self, params: dict[str, Any]) -> None:
        backend = self._backend
        backend.validate_runtime_target(params)
        target_station = backend.runtime
        previous_kind = target_station.power_supply_kind
        previous_power_meter_resource = str(
            target_station.configuration.get("powerMeterResource", "")
        ).strip()
        previous_software_gain = _finite(
            target_station.configuration.get("softwareGain")
        )
        target_station.configure(params)
        if "powerMeterResource" in params:
            selected_resource = str(target_station.configuration.get("powerMeterResource", "")).strip()
            option = next(
                (item for item in backend.power_meter_options if item.resource == selected_resource),
                None,
            )
            if option is not None:
                submitted_software_gain = (
                    _finite(params.get("softwareGain"))
                    if "softwareGain" in params
                    else None
                )
                software_gain_was_edited = (
                    "softwareGain" in params
                    and (
                        previous_software_gain is None
                        or submitted_software_gain is None
                        or not math.isclose(
                            submitted_software_gain,
                            previous_software_gain,
                            rel_tol=1e-12,
                            abs_tol=1e-12,
                        )
                    )
                )
                # The React settings UI submits the complete configuration.
                # When only the resource changed, its softwareGain field still
                # contains the previous meter's value and must not suppress the
                # new meter's serial-bound profile restoration.
                restore_gain = "softwareGain" not in params or (
                    selected_resource != previous_power_meter_resource
                    and not software_gain_was_edited
                )
                backend.discovery._bind_power_meter_option(
                    target_station,
                    option,
                    restore_gain=restore_gain,
                )
            elif not selected_resource:
                target_station.configuration["powerMeterSerialNumber"] = ""
        if "softwareGain" in params:
            backend.discovery._remember_power_meter_profile(target_station)

        if "spectrometerResource" in params:
            selected_resource = str(target_station.configuration.get("spectrometerResource", ""))
            resource = next(
                (item for item in backend.spectrometer_resources if item.get("label") == selected_resource),
                None,
            )
            if resource is not None:
                target_station.configuration["spectrometerDeviceId"] = int(resource["deviceId"])
                target_station.configuration["spectrometerSerialNumber"] = _normalize_device_serial(
                    resource.get("serialNumber")
                )
            elif not selected_resource:
                target_station.configuration["spectrometerDeviceId"] = None
                target_station.configuration["spectrometerSerialNumber"] = ""
        current_kind = target_station.power_supply_kind
        if previous_kind == "ch341" and current_kind == "tdk":
            with backend._lock:
                backend.ch341_discovery_state = "not_scanned"
                backend.ch341_scan_error = ""
                backend.ch341_descriptors = []

    def _set_current(self, params: dict[str, Any]) -> None:
        backend = self._backend
        backend.validate_runtime_target(params)
        st = backend.runtime
        st.set_current(params)

    def _report_module(self) -> Any:
        backend = self._backend
        from combined_test import shipping_report
        return shipping_report

    def _inspect_shipping_report(self, params: dict[str, Any]) -> dict[str, Any]:
        backend = self._backend
        source = str(params.get("path", "")).strip()
        if not source:
            raise ValueError("请选择 Excel 测试文件")
        report = self._report_module()
        payload = report.inspect_shipping_workbook(source).to_payload()
        payload["preferences"] = report.load_shipping_report_preferences(backend.settings)
        return payload

    def _inspect_shipping_report_batch(self, params: dict[str, Any]) -> dict[str, Any]:
        backend = self._backend
        root = str(params.get("rootPath", "")).strip()
        if not root:
            raise ValueError("请选择 Excel 批量根目录")
        report = self._report_module()
        payload = report.inspect_shipping_report_batch(root).to_payload()
        payload["preferences"] = report.load_shipping_report_preferences(backend.settings)
        return payload

    def _generate_shipping_report(self, params: dict[str, Any]) -> dict[str, Any]:
        backend = self._backend
        report = self._report_module()
        request = report.ShippingReportRequest.from_mapping(params)
        target = report.generate_shipping_report(params["sourcePath"], params["outputPath"], request)
        report.save_shipping_report_preferences(backend.settings, request)
        return {"outputPath": str(target)}

    def _generate_shipping_report_batch_item(self, params: dict[str, Any]) -> dict[str, Any]:
        backend = self._backend
        report = self._report_module()
        target, request = report.generate_shipping_report_batch_item(
            params["sourcePath"], params["outputDirectory"], params
        )
        report.save_shipping_report_preferences(backend.settings, request)
        return {"outputPath": str(target), "sourcePath": str(params["sourcePath"]), "sn": request.sn}

    def _preview_shipping_report(self, params: dict[str, Any]) -> dict[str, Any]:
        backend = self._backend
        report = self._report_module()
        request = report.ShippingReportRequest.from_mapping(params)
        return {"pages": report.render_shipping_report_preview(params["sourcePath"], request)}
