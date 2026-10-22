"""Request routing and application configuration for the headless backend."""

from __future__ import annotations

import copy
from typing import Any, Callable

from .errors import CommandRejected
from .service import BACKGROUND_FILE_TASK_METHODS
from .station_support import (
    MANUAL_CONTROL_METHODS,
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
        } or method.startswith("powerMeter.profile.") or (method == "powerSupply.setOutput" and params.get("enabled", False) is not False)
        if guarded:
            with st.command_safety.command(command_generation):
                with st.workflow_access.command(method, params):
                    return self._dispatch(method, params)
        with st.workflow_access.command(method, params):
            return self._dispatch(method, params)

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        backend = self._backend
        st = backend.runtime

        if st.power_stability.job and (
            method == "automatic.start" or method.startswith("powerMeter.profile.")
            or method in {"powerSupply.setCurrent", "powerSupply.setVoltage", "powerMeter.setRelativeZero"}
            or (method == "powerSupply.setOutput" and params.get("enabled") is True)
        ):
            raise CommandRejected("control_conflict", "本底任务进行中，请先完成或取消")

        if method in MANUAL_CONTROL_METHODS and not st.manual_controls_allowed():
            raise CommandRejected(
                "control_conflict", "硬件控制权由自动测试持有；请先结束并安全下电"
            )

        actions: dict[str, Callable[[dict[str, Any]], Any]] = {
            "app.configure": lambda p: self.configure(p),
            "device.refresh": backend.discovery.refresh_device,
            "powerSupply.connect": lambda _p: st.connect_power_supply(),
            "powerSupply.disconnect": lambda _p: st.disconnect_power_supply(),
            "powerSupply.safeRampDown": lambda _p: st.safe_manual_power_down(),
            "powerSupply.setCurrent": lambda p: st.set_manual_current(p),
            "powerSupply.setVoltage": lambda p: st.set_voltage(p),
            "powerSupply.setOutput": lambda p: st.set_output(p),
            "powerSupply.read": lambda p: st.read_power_supply(p),
            "powerMeter.start": lambda _p: st.start_power_meter(),
            "powerMeter.stop": lambda _p: st.stop_power_meter(),
            "powerMeter.setRelativeZero": lambda p: st.set_relative_zero(p),
            "powerMeter.background.learn": lambda p: st.power_stability.start_background(p),
            "powerMeter.background.verify": lambda p: st.power_stability.start_background(p, verify=True),
            "powerMeter.background.cancel": lambda _p: st.power_stability.cancel_background(),
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
            "dataBrowser.import": self._import_data_records,
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
        if method.startswith("powerMeter.profile."):
            result = backend.power_meter_profiles.command(method.rsplit(".", 1)[1], params)
            return backend.snapshot(params) if result is None else result
        action = actions.get(method)
        if action is None:
            raise KeyError(method)
        result = action(params)
        if method in {
            "device.refresh", "powerSupply.connect", "powerSupply.disconnect",
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
            "dataBrowser.import": self._import_data_records,
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
        if params.get("source") != "database":
            return list_data_records(params)
        library = self._data_library()
        warnings = []
        for journal_root in sorted(library.path.parent.glob("station-*")):
            warnings.extend(library.recover(journal_root))
        result = library.list(params)
        result["warnings"] = warnings
        return result

    def _inspect_data_record(self, params: dict[str, Any]) -> dict[str, Any]:
        from combined_test.data_browser import inspect_data_record
        if params.get("recordId"):
            return self._data_library().inspect(str(params["recordId"]), include_spectra=bool(params.get("includeSpectra", False)))
        return inspect_data_record(params)

    def _data_library(self):
        from combined_test.data_library import DataLibrary
        return DataLibrary(self._backend.settings.path.parent / "test-records" / "library.sqlite3")

    def _import_data_records(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._data_library().import_excel(params)

    def configure(self, params: dict[str, Any]) -> None:
        backend = self._backend
        with backend.runtime._lock:
            old_config = dict(backend.runtime.configuration)
            old_profiles = copy.deepcopy(backend.device_profiles)
            try:
                self._configure(params)
                backend._persist_configuration()
            except Exception:
                backend.runtime.configuration = old_config
                backend.runtime.power_supply_kind = str(old_config["powerSupplyKind"])
                backend.device_profiles = old_profiles
                raise

    def _configure(self, params: dict[str, Any]) -> None:
        backend = self._backend
        backend.validate_runtime_target(params)
        target_station = backend.runtime
        previous_kind = target_station.power_supply_kind
        target_station.configure(params)

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
        if previous_kind == "ch341" and current_kind in {"tdk", "ru"}:
            with backend._lock:
                backend.ch341_discovery_state = "not_scanned"
                backend.ch341_scan_error = ""
                backend.ch341_descriptors = []
        if previous_kind != current_kind and "ru" in {previous_kind, current_kind}:
            with backend._lock:
                backend.power_supply_resources = []
                backend.power_supply_detection_detail = ""
                backend.power_supply_detection_kind = None

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
