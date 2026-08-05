"""Tauri adapter for the existing, tested Qt application controller.

The Qt window stays hidden.  Its widgets are used as the compatibility model
while the existing device threads, automatic controller, Excel export, and safety
shutdown code remain the single owner of application state.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from enum import Enum
from typing import Any, Callable

from combined_test.runtime_logging import runtime_log_path
from combined_test.spectrum_math import calculate_smsr, calculate_stats
from combined_test.spectrum import (
    SPECTRUM_CENTER_LOCK_HALF_RANGE_NM,
    find_spectrum_peak_annotations,
)
from combined_test.shipping_report import (
    ShippingReportRequest,
    generate_shipping_report,
    generate_shipping_report_batch_item,
    inspect_shipping_report_batch,
    inspect_shipping_workbook,
    load_shipping_report_preferences,
    render_shipping_report_preview,
    save_shipping_report_preferences,
)


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _paired_series(
    x_values: Any,
    y_values: Any,
    *,
    limit: int | None = 1200,
) -> tuple[list[float], list[float]]:
    """Sanitize paired samples without shifting one axis past invalid values."""

    if x_values is None or y_values is None:
        return [], []
    x_source = list(x_values)
    y_source = list(y_values)
    point_count = min(len(x_source), len(y_source))
    start = 0 if limit is None else max(0, point_count - limit)
    x_output: list[float] = []
    y_output: list[float] = []
    for index in range(start, point_count):
        x_value = _number(x_source[index])
        y_value = _number(y_source[index])
        if x_value is None or y_value is None:
            continue
        x_output.append(x_value)
        y_output.append(y_value)
    return x_output, y_output


SPECTRUM_CHART_POINT_LIMIT = 4096


def _downsample_spectrum(
    wavelength: list[float],
    intensity: list[float],
    *,
    limit: int = SPECTRUM_CHART_POINT_LIMIT,
) -> tuple[list[float], list[float]]:
    """Keep a full OceanHR4 frame while bounding unusually large payloads."""

    point_count = min(len(wavelength), len(intensity))
    if point_count <= limit:
        return wavelength[:point_count], intensity[:point_count]
    if limit < 4:
        indexes = [0, point_count - 1][:limit]
        return [wavelength[index] for index in indexes], [intensity[index] for index in indexes]

    bucket_count = max(1, (limit - 2) // 2)
    bucket_size = math.ceil((point_count - 2) / bucket_count)
    selected_indexes = [0]
    for start in range(1, point_count - 1, bucket_size):
        stop = min(point_count - 1, start + bucket_size)
        low_index = high_index = start
        for index in range(start + 1, stop):
            if intensity[index] < intensity[low_index]:
                low_index = index
            if intensity[index] > intensity[high_index]:
                high_index = index
        selected_indexes.extend(
            (low_index,) if low_index == high_index else sorted((low_index, high_index))
        )
    selected_indexes.append(point_count - 1)
    selected_indexes = selected_indexes[:limit]
    return (
        [wavelength[index] for index in selected_indexes],
        [intensity[index] for index in selected_indexes],
    )


def _downsample_pd_history(
    times: Any,
    values: Any,
    *,
    bucket_s: float = 0.1,
) -> list[dict[str, float]]:
    """Compact the bounded PD display history while retaining narrow extrema."""

    duration = max(float(bucket_s), 1e-6)
    output: list[dict[str, float]] = []
    bucket_index: int | None = None
    first: tuple[float, float] | None = None
    minimum: tuple[float, float] | None = None
    maximum: tuple[float, float] | None = None
    last: tuple[float, float] | None = None

    def flush_bucket() -> None:
        candidates = (first, minimum, maximum, last)
        unique = {
            point[0]: point
            for point in candidates
            if point is not None
        }
        output.extend(
            {"elapsedS": elapsed_s, "value": value}
            for elapsed_s, value in sorted(unique.values())
        )

    for raw_time, raw_value in zip(times, values):
        elapsed_s = _number(raw_time)
        value = _number(raw_value)
        if elapsed_s is None or value is None:
            continue
        next_bucket_index = math.floor(elapsed_s / duration)
        if bucket_index is None or next_bucket_index != bucket_index:
            if bucket_index is not None:
                flush_bucket()
            bucket_index = next_bucket_index
            first = minimum = maximum = last = (elapsed_s, value)
            continue
        point = (elapsed_s, value)
        last = point
        if minimum is None or value < minimum[1]:
            minimum = point
        if maximum is None or value > maximum[1]:
            maximum = point

    if bucket_index is not None:
        flush_bucket()
    return output


class LegacyWindowBackend:
    """Expose the current Qt application through serializable commands."""

    mode = "active"

    def __init__(self, window: Any) -> None:
        self.window = window
        self.notices: list[dict[str, str]] = []
        log_path = runtime_log_path()
        if log_path is not None:
            self.notices.append(
                {
                    "id": "runtime-log-enabled",
                    "level": "info",
                    "title": "运行日志已启用",
                    "message": f"完整运行日志保存至：{log_path}",
                }
            )
        self._notice_sequence = 0
        self.latest_spectrometer_reading: Any | None = None
        self._connected_spectrum_reader_id: int | None = None
        self._spectrum_cache_source: tuple[Any, Any, bool, float | None, int | None] | None = None
        self._spectrum_cache_payload: tuple[
            list[float], list[float], float | None, list[dict[str, Any]], Any | None
        ] = ([], [], None, [], None)
        self._power_cache_key: tuple[Any, ...] | None = None
        self._power_cache_payload: list[dict[str, float]] = []
        self._spectrum_points_cache_source: tuple[Any, Any] | None = None
        self._spectrum_points_cache_payload: list[dict[str, float]] = []
        self._stable_cache_key: tuple[Any, ...] | None = None
        self._stable_cache_payload: list[dict[str, float | None]] = []
        self._pd_cache_key: tuple[Any, ...] | None = None
        self._pd_cache_payload: list[dict[str, float]] = []
        self._series_revisions = {"power": 0, "stable": 0, "spectrum": 0, "pd": 0}
        self._install_nonblocking_dialogs()

    def _bump_series_revision(self, name: str) -> None:
        revisions = getattr(self, "_series_revisions", None)
        if revisions is None:
            revisions = {"power": 0, "stable": 0, "spectrum": 0, "pd": 0}
            self._series_revisions = revisions
        revisions[name] = int(revisions.get(name, 0)) + 1

    def _current_series_revisions(self) -> dict[str, int]:
        revisions = getattr(self, "_series_revisions", {})
        return {
            name: int(revisions.get(name, 0))
            for name in ("power", "stable", "spectrum", "pd")
        }

    def _power_snapshot(self, plots: Any) -> list[dict[str, float]]:
        """Reuse the serialized power trace when no new acquisition arrived."""

        times = getattr(plots, "power_curve_times", ())
        values = getattr(plots, "power_curve_values", ())
        revision = getattr(plots, "_power_revision", None)
        last_time = times[-1] if times else None
        cache_key = (revision, len(times), last_time)
        if cache_key == self._power_cache_key:
            return self._power_cache_payload
        elapsed_values, power_values = _paired_series(times, values)
        payload = [
            {"elapsedS": x, "powerW": y}
            for x, y in zip(elapsed_values, power_values)
        ]
        self._power_cache_key = cache_key
        self._power_cache_payload = payload
        self._bump_series_revision("power")
        return payload

    def _spectrum_points_snapshot(
        self,
        wavelength: list[float],
        intensity: list[float],
    ) -> list[dict[str, float]]:
        """Avoid rebuilding spectrum row dictionaries before JSON encoding."""

        cached_source = self._spectrum_points_cache_source
        if (
            cached_source is not None
            and cached_source[0] is wavelength
            and cached_source[1] is intensity
        ):
            return self._spectrum_points_cache_payload
        payload = [
            {"wavelengthNm": x, "intensity": y}
            for x, y in zip(wavelength, intensity)
        ]
        # Retaining the source references also prevents Python object-id reuse
        # from producing a false cache hit after the previous frame is freed.
        self._spectrum_points_cache_source = (wavelength, intensity)
        self._spectrum_points_cache_payload = payload
        return payload

    def _stable_snapshot(self, window: Any) -> list[dict[str, float | None]]:
        """Reuse stable-point rows while the automatic test is waiting."""

        power_items = tuple(sorted(window.stable_power_points.items()))
        efficiency_items = tuple(sorted(window.efficiency_points.items()))
        cache_key = (power_items, efficiency_items)
        if cache_key == self._stable_cache_key:
            return self._stable_cache_payload
        payload = [
            {
                "currentA": float(current),
                "powerW": _number(power),
                "efficiencyPercent": _number(window.efficiency_points.get(current)),
            }
            for current, power in power_items
        ]
        self._stable_cache_key = cache_key
        self._stable_cache_payload = payload
        self._bump_series_revision("stable")
        return payload

    def _spectrum_snapshot(
        self,
        wavelength_values: Any,
        intensity_values: Any,
        saturated: bool,
        locked_center_nm: float | None = None,
        frame_revision: int | None = None,
    ) -> tuple[
        list[float],
        list[float],
        float | None,
        list[dict[str, Any]],
        Any | None,
    ]:
        """Analyze a spectrum once per acquired frame, not once per UI poll."""

        cached_source = self._spectrum_cache_source
        if (
            cached_source is not None
            and cached_source[0] is wavelength_values
            and cached_source[1] is intensity_values
            and cached_source[2] == saturated
            and cached_source[3] == locked_center_nm
            and cached_source[4] == frame_revision
        ):
            return self._spectrum_cache_payload

        full_wavelength, full_intensity = _paired_series(
            wavelength_values,
            intensity_values,
            limit=None,
        )
        chart_wavelength = full_wavelength
        chart_intensity = full_intensity
        if locked_center_nm is not None:
            minimum_nm = locked_center_nm - SPECTRUM_CENTER_LOCK_HALF_RANGE_NM
            maximum_nm = locked_center_nm + SPECTRUM_CENTER_LOCK_HALF_RANGE_NM
            visible_wavelength: list[float] = []
            visible_intensity: list[float] = []
            for wavelength_nm, value in zip(full_wavelength, full_intensity):
                if minimum_nm <= wavelength_nm <= maximum_nm:
                    visible_wavelength.append(wavelength_nm)
                    visible_intensity.append(value)
            if visible_wavelength:
                chart_wavelength = visible_wavelength
                chart_intensity = visible_intensity
        # Preserve every native spectrometer sample in the locked display
        # window. The wavelength density is hardware-defined, so dropping
        # points here can only reduce the fidelity of the live spectrum.
        wavelength, intensity = chart_wavelength, chart_intensity
        smsr_db: float | None = None
        peaks: list[dict[str, Any]] = []
        frame_stats: Any | None = None
        if not saturated and wavelength_values is not None and intensity_values is not None:
            window = getattr(self, "window", None)
            cached_analysis_matches = bool(
                window is not None
                and frame_revision is not None
                and getattr(window, "latest_spectrum_analysis_revision", None)
                == frame_revision
                and getattr(window, "latest_spectrum_wavelength", None)
                is wavelength_values
                and getattr(window, "latest_spectrum_intensity", None)
                is intensity_values
            )
            if cached_analysis_matches:
                frame_stats = getattr(window, "latest_spectrum_stats", None)
                smsr_db = _number(getattr(window, "latest_spectrum_smsr_db", None))
                annotations = getattr(window, "latest_spectrum_peak_annotations", ())
            else:
                mode_result = None
                try:
                    frame_stats = calculate_stats(wavelength_values, intensity_values)
                    mode_result = calculate_smsr(wavelength_values, intensity_values)
                    smsr_db = _number(mode_result.smsr_db)
                except (TypeError, ValueError):
                    pass
                try:
                    annotations = find_spectrum_peak_annotations(
                        zip(full_wavelength, full_intensity),
                        stats=frame_stats,
                        smsr_result=mode_result,
                    )
                except (TypeError, ValueError):
                    annotations = ()
            try:
                peaks = [
                    {
                        "label": annotation.label,
                        "centroidNm": _number(annotation.centroid_nm),
                        "peakWavelengthNm": _number(annotation.peak_wavelength_nm),
                        "peakIntensity": _number(annotation.peak_intensity),
                    }
                    for annotation in annotations
                ]
            except (TypeError, ValueError):
                pass

        self._spectrum_cache_source = (
            wavelength_values,
            intensity_values,
            saturated,
            locked_center_nm,
            frame_revision,
        )
        self._spectrum_cache_payload = (
            wavelength,
            intensity,
            smsr_db,
            peaks,
            frame_stats,
        )
        self._bump_series_revision("spectrum")
        return self._spectrum_cache_payload

    def _pd_snapshot(self, panel: Any) -> list[dict[str, float]]:
        """Reuse the PD trace until its bounded display buffer changes."""

        times = panel.plot_times
        values = panel.plot_values
        cache_key = (
            getattr(panel, "_plot_revision", None),
            len(times),
            times[0] if times else None,
            times[-1] if times else None,
            values[-1] if values else None,
        )
        if cache_key == self._pd_cache_key:
            return self._pd_cache_payload
        payload = _downsample_pd_history(times, values)
        self._pd_cache_key = cache_key
        self._pd_cache_payload = payload
        self._bump_series_revision("pd")
        return payload

    def _install_nonblocking_dialogs(self) -> None:
        """Prevent the hidden compatibility window from opening modal dialogs."""

        from PySide6.QtWidgets import QMessageBox

        self.window._dialogs_are_nonblocking = True

        def notice(level: str) -> Callable[..., Any]:
            def capture(_parent: Any, title: str, message: str, *_args: Any, **_kwargs: Any) -> Any:
                self._notice_sequence += 1
                self.notices.append(
                    {
                        "id": str(self._notice_sequence),
                        "level": level,
                        "title": str(title),
                        "message": str(message),
                    }
                )
                return QMessageBox.StandardButton.Ok

            return capture

        QMessageBox.information = staticmethod(notice("info"))
        QMessageBox.warning = staticmethod(notice("warning"))
        QMessageBox.critical = staticmethod(notice("error"))

        def reject_question(
            _parent: Any,
            title: str,
            message: str,
            *_args: Any,
            **_kwargs: Any,
        ) -> Any:
            self._notice_sequence += 1
            self.notices.append(
                {
                    "id": str(self._notice_sequence),
                    "level": "warning",
                    "title": str(title),
                    "message": f"需要在新界面中确认：{message}",
                }
            )
            return QMessageBox.StandardButton.No

        QMessageBox.question = staticmethod(reject_question)

    def ping(self) -> dict[str, Any]:
        return {"status": "ok", "mode": self.mode}

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        actions: dict[str, Callable[[dict[str, Any]], Any]] = {
            "app.configure": self._configure,
            "device.refresh": self._refresh_device,
            "powerSupply.connect": lambda _p: self._set_power_supply_connected(True),
            "powerSupply.disconnect": lambda _p: self._set_power_supply_connected(False),
            "powerSupply.setCurrent": self._set_current,
            "powerSupply.setVoltage": self._set_voltage,
            "powerSupply.setOutput": self._set_output,
            "powerSupply.read": self._read_power_supply,
            "powerMeter.start": lambda _p: self._start_power_meter(),
            "powerMeter.stop": lambda _p: self.window.stop_power_meter(),
            "powerMeter.setRelativeZero": self._set_relative_zero,
            "spectrometer.start": lambda _p: self._start_spectrometer(),
            "spectrometer.stop": lambda _p: self.window.stop_spectrometer(),
            "spectrometer.saveCsv": self._save_spectrum_csv,
            "automatic.start": self._start_automatic,
            "automatic.pause": lambda _p: self._pause_automatic(),
            "automatic.retry": lambda _p: self.window.retry_automatic_test(),
            "automatic.end": lambda _p: self.window.end_automatic_test(),
            "automatic.reset": lambda _p: self.window.reset_automatic_test(),
            "shippingReport.inspect": self._inspect_shipping_report,
            "shippingReport.batchInspect": self._inspect_shipping_report_batch,
            "shippingReport.batchGenerateItem": self._generate_shipping_report_batch_item,
            "shippingReport.preview": self._preview_shipping_report,
            "shippingReport.generate": self._generate_shipping_report,
            "pd.refresh": lambda _p: self.window.pd_panel.refresh_devices(),
            "pd.configure": self._configure_pd,
            "pd.start": self._start_pd,
            "pd.stop": lambda _p: self.window.pd_panel.stop_acquisition(),
            "charts.reset": lambda _p: self.window.reset_curves(),
            "app.stopAll": lambda _p: self.window.emergency_stop(),
            "app.shutdown": lambda _p: self._shutdown(),
        }
        action = actions.get(method)
        if action is None:
            raise KeyError(method)
        self.notices.clear()
        result = action(params)
        if method != "shippingReport.preview":
            self.window.save_input_settings()
        return self.snapshot() if result is None else result

    @staticmethod
    def _set_combo_data(combo: Any, value: Any) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _set_combo_text(combo: Any, value: Any) -> None:
        text = str(value).strip()
        if not text:
            return
        index = combo.findText(text)
        if index >= 0:
            combo.setCurrentIndex(index)
        elif combo.isEditable():
            combo.setEditText(text)

    def _set_power_meter_resource(self, value: Any) -> None:
        resource = str(value).strip()
        index = self.window._find_power_meter_resource_index(resource)
        if index >= 0:
            self.window.power_meter_combo.setCurrentIndex(index)
        else:
            self._set_combo_text(self.window.power_meter_combo, resource)

    def _configure(self, params: dict[str, Any]) -> None:
        window = self.window
        fields = {
            "sn": window.sn_field,
            "productModel": window.product_model_field,
            "batch": window.batch_field,
            "station": window.test_station_field,
            "outputDir": window.output_dir_field,
        }
        for key, widget in fields.items():
            if key in params:
                widget.setText(str(params[key]))

        # Select the controller before applying current values so that the Qt
        # widgets enforce the I2C-controller 20 A limit without constraining TDK values.
        if "powerSupplyKind" in params:
            self._set_combo_data(window.power_supply_controller_combo, params["powerSupplyKind"])

        values = {
            "initialCurrentA": window.auto_initial_current_spin,
            "targetCurrentA": window.auto_target_current_spin,
            "currentStepA": window.auto_current_step_spin,
            "pointTimeoutS": window.auto_point_timeout_spin,
            "rampDownStepA": window.auto_ramp_down_step_spin,
            "rampDownIntervalS": window.auto_ramp_down_interval_spin,
            "pauseRampDownTimeoutS": window.auto_pause_ramp_down_timeout_spin,
            "setCurrentA": window.set_current_spin,
            "tdkVoltageV": window.tdk_voltage_spin,
            "powerMeterWavelengthNm": window.power_wavelength_spin,
            "softwareGain": window.software_gain_spin,
            "powerMeterIntervalMs": window.power_meter_interval_spin,
            "integrationTimeUs": window.integration_spin,
            "spectrometerIntervalMs": window.interval_spin,
            "spectrumAverageFrames": window.spectrum_average_spin,
            "stableWindowS": window.stable_window_spin,
            "stableToleranceW": window.stable_tolerance_spin,
        }
        for key, widget in values.items():
            if key in params:
                widget.setValue(float(params[key]))

        if "tdkResource" in params:
            self._set_combo_text(window.tdk_resource_combo, params["tdkResource"])
        if "powerMeterResource" in params:
            self._set_power_meter_resource(params["powerMeterResource"])
        if "spectrometerResource" in params:
            spectrometer_resource = str(params["spectrometerResource"]).strip()
            if spectrometer_resource:
                self._set_combo_text(window.spectrometer_combo, spectrometer_resource)
            elif window.spectrometer_combo.count():
                window.spectrometer_combo.setCurrentIndex(0)
        if "spectrometerDeviceId" in params:
            self._set_combo_data(window.spectrometer_combo, int(params["spectrometerDeviceId"]))
        if "useSpectrometer" in params:
            window.auto_use_spectrometer_check.setChecked(bool(params["useSpectrometer"]))
        if "autoIntegration" in params:
            window.auto_integration_check.setChecked(bool(params["autoIntegration"]))
        window.refresh_preflight_checklist()

    def _refresh_device(self, params: dict[str, Any]) -> None:
        device = str(params.get("device", "all"))
        if device in ("all", "powerSupply") and self.window._selected_power_supply_kind() == "tdk":
            self.window.refresh_tdk_resources()
        if device in ("all", "powerMeter"):
            self.window.auto_detect_power_meters()
        if device in ("all", "spectrometer"):
            self.window.auto_detect_spectrometers()

    def _set_power_supply_connected(self, desired: bool) -> None:
        if bool(self.window._manual_i2c_connected()) != desired:
            self.window.connect_i2c_device()

    def _set_current(self, params: dict[str, Any]) -> None:
        current_a = float(params["currentA"])
        if not math.isfinite(current_a) or current_a < 0:
            raise ValueError("电流必须是大于或等于 0 A 的有限数值")
        controller_kind = self.window._selected_power_supply_kind()
        if controller_kind != "tdk" and current_a > 20.0:
            raise ValueError("CH341 最大电流不能超过 20 A")
        self.window.set_current_spin.setValue(current_a)
        self.window.apply_output_current()

    def _set_voltage(self, params: dict[str, Any]) -> None:
        self.window.tdk_voltage_spin.setValue(float(params["voltageV"]))
        self.window.apply_tdk_output_voltage()

    def _set_output(self, params: dict[str, Any]) -> None:
        desired = bool(params.get("enabled"))
        current = bool(
            self.window._manual_i2c_connected()
            and getattr(self.window.manual_ch341_controller, "output_enabled", False)
        )
        if self.window._selected_power_supply_kind() != "tdk":
            raise ValueError("I²C 控制器没有独立输出开关")
        if current != desired:
            self.window.toggle_tdk_output()

    def _set_relative_zero(self, params: dict[str, Any]) -> None:
        if self.window.power_meter_detect_thread is not None:
            raise ValueError("功率计正在识别，请等待识别完成后再调零")
        self.window.set_power_meter_relative_zero(bool(params.get("enabled")))

    def _start_power_meter(self) -> None:
        if self.window.power_meter_detect_thread is not None:
            raise ValueError("功率计正在识别，请等待识别完成后再开始采集")
        self.window.start_power_meter()

    def _read_power_supply(self, params: dict[str, Any]) -> None:
        readers = {
            "inputVoltage": self.window.read_input_voltage,
            "outputVoltage": self.window.read_output_voltage,
            "outputCurrent": self.window.read_output_current,
            "temperature": self.window.read_temperature,
        }
        name = str(params.get("value", ""))
        reader = readers.get(name)
        if reader is None:
            raise ValueError("未知电源读取项")
        reader()

    def _start_automatic(self, params: dict[str, Any]) -> None:
        if self.window.power_meter_detect_thread is not None:
            raise ValueError("功率计正在识别，请等待识别完成后再开始自动测试")
        self._configure(params)
        self.window.start_automatic_test()
        self._connect_spectrum_capture()

    def _pause_automatic(self) -> None:
        state = _enum_value(self.window.automatic_orchestrator.state)
        if state in ("idle", "completed", "paused", "ramping_down"):
            raise ValueError("当前自动测试状态不能暂停")
        self.window.pause_automatic_test("操作者暂停", operator_requested=True)

    def _start_spectrometer(self) -> None:
        self.window.start_spectrometer()
        self._connect_spectrum_capture()

    def _connect_spectrum_capture(self) -> None:
        reader = self.window.spectrometer_reader
        if reader is None or self._connected_spectrum_reader_id == id(reader):
            return
        reader.reading.connect(self._capture_spectrum_reading)
        self._connected_spectrum_reader_id = id(reader)

    def _capture_spectrum_reading(self, reading: Any) -> None:
        self.latest_spectrometer_reading = reading

    def _save_spectrum_csv(self, params: dict[str, Any]) -> None:
        if self.window.latest_spectrum_wavelength is None or self.window.latest_spectrum_intensity is None:
            raise ValueError("当前没有可保存的光谱数据")
        from combined_test.persistence import save_spectrum_curve

        requested = str(params.get("path", "")).strip()
        if requested:
            path = Path(requested).expanduser()
        else:
            root = Path(self.window.output_dir_field.text()).expanduser()
            path = root / f"spectrum_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        save_spectrum_curve(
            path,
            self.window.latest_spectrum_wavelength,
            self.window.latest_spectrum_intensity,
        )
        self.window.statusBar().showMessage(f"光谱已保存：{path}")
        self.window.add_log(f"光谱 CSV 已保存：{path}")

    def _inspect_shipping_report(self, params: dict[str, Any]) -> dict[str, Any]:
        source_path = str(params.get("path", "")).strip()
        if not source_path:
            raise ValueError("请选择 Excel 测试文件")
        inspection = inspect_shipping_workbook(source_path)
        payload = inspection.to_payload()
        payload["preferences"] = load_shipping_report_preferences(self.window.input_settings)
        return payload

    def _generate_shipping_report(self, params: dict[str, Any]) -> dict[str, Any]:
        source_path = str(params.get("sourcePath", "")).strip()
        output_path = str(params.get("outputPath", "")).strip()
        if not source_path:
            raise ValueError("请选择 Excel 测试文件")
        if not output_path:
            raise ValueError("请选择 PDF 保存位置")
        request = ShippingReportRequest.from_mapping(params)
        target = generate_shipping_report(source_path, output_path, request)
        save_shipping_report_preferences(self.window.input_settings, request)
        self.window.statusBar().showMessage(f"出货报告已生成：{target}")
        self.window.add_log(f"出货报告已生成：{target}")
        return {"outputPath": str(target)}

    def _inspect_shipping_report_batch(self, params: dict[str, Any]) -> dict[str, Any]:
        root_path = str(params.get("rootPath", "")).strip()
        if not root_path:
            raise ValueError("请选择 Excel 批量根目录")
        payload = inspect_shipping_report_batch(root_path).to_payload()
        payload["preferences"] = load_shipping_report_preferences(self.window.input_settings)
        return payload

    def _generate_shipping_report_batch_item(self, params: dict[str, Any]) -> dict[str, Any]:
        source_path = str(params.get("sourcePath", "")).strip()
        output_directory = str(params.get("outputDirectory", "")).strip()
        if not source_path:
            raise ValueError("请选择 Excel 测试文件")
        if not output_directory:
            raise ValueError("请选择 PDF 保存文件夹")
        target, request = generate_shipping_report_batch_item(source_path, output_directory, params)
        save_shipping_report_preferences(self.window.input_settings, request)
        self.window.statusBar().showMessage(f"批量出货报告已生成：{target}")
        self.window.add_log(f"批量出货报告已生成：{target}")
        return {"outputPath": str(target), "sourcePath": source_path, "sn": request.sn}

    def _preview_shipping_report(self, params: dict[str, Any]) -> dict[str, Any]:
        source_path = str(params.get("sourcePath", "")).strip()
        if not source_path:
            raise ValueError("请选择 Excel 测试文件")
        request = ShippingReportRequest.from_mapping(params)
        return {"pages": render_shipping_report_preview(source_path, request)}

    def _shutdown(self) -> dict[str, bool]:
        """Apply the existing emergency boundary and flush acquisition threads."""

        self.window.emergency_stop()
        for reader in (
            self.window.power_meter_reader,
            self.window.spectrometer_reader,
            self.window.pd_panel.reader,
            self.window.excel_save_thread,
        ):
            if reader is not None and hasattr(reader, "wait"):
                reader.wait(5000)

        controller = self.window.manual_ch341_controller
        active_current_a = float(self.window.active_output_current_a or 0.0)
        if self.window.power_supply_controller_kind == "tdk":
            output_enabled = bool(getattr(controller, "output_enabled", False))
            if (
                self.window.automatic_controller.output_shutdown_unconfirmed
                or output_enabled
                or active_current_a > 0.005
            ):
                raise RuntimeError(
                    "无法确认 TDK 输出已经关闭；程序将保持打开，请检查电源面板后重试退出"
                )
        elif active_current_a > 0.005:
            raise RuntimeError(
                f"无法确认电源电流已经归零（最近设定 {active_current_a:g} A）；"
                "程序将保持打开，请检查电源后重试退出"
            )
        discard_controller = getattr(
            self.window,
            "_discard_manual_power_supply_controller",
            None,
        )
        if callable(discard_controller):
            discard_controller("Tauri 后端已确认安全退出")
        return {"shutdownConfirmed": True}

    def _configure_pd(self, params: dict[str, Any]) -> None:
        panel = self.window.pd_panel
        combos = {
            "device": panel.device_combo,
            "channel": panel.channel_combo,
            "terminal": panel.terminal_combo,
            "range": panel.range_combo,
        }
        for key, combo in combos.items():
            if key in params:
                self._set_combo_text(combo, params[key])
                self._set_combo_data(combo, params[key])
        values = {
            "sampleRateHz": panel.sample_rate_spin,
            "blockSize": panel.block_size_spin,
            "scale": panel.scale_spin,
            "offset": panel.offset_spin,
        }
        for key, widget in values.items():
            if key in params:
                widget.setValue(float(params[key]))
        if "unit" in params:
            panel.unit_edit.setText(str(params["unit"]))
        if "save" in params:
            panel.save_checkbox.setChecked(bool(params["save"]))
        if "outputDir" in params:
            panel.output_dir_edit.setText(str(params["outputDir"]))

    def _start_pd(self, params: dict[str, Any]) -> None:
        self._configure_pd(params)
        self.window.pd_panel.start_acquisition()

    def _device_state(self, connected: bool, running: bool, failed: str) -> str:
        if failed:
            return "error"
        if connected or running:
            return "connected"
        return "disconnected"

    def snapshot(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        window = self.window
        snapshot_params = params or {}
        requested_view = str(snapshot_params.get("view", "full"))
        view = requested_view if requested_view in {"automatic", "manual", "pd"} else "full"
        since = snapshot_params.get("since")
        since_revisions = since if isinstance(since, dict) else None
        cursors = snapshot_params.get("cursors")
        series_cursors = cursors if isinstance(cursors, dict) else {}
        include_live_charts = view in {"automatic", "manual", "full"}
        include_pd_points = view in {"pd", "full"}
        power_supply = window.get_power_supply()
        power_connected = bool(power_supply is not None and power_supply.connected)
        power_meter_running = window.power_meter_reader is not None
        power_meter_detecting = window.power_meter_detect_thread is not None
        spectrum_running = window.spectrometer_reader is not None
        power_reading = window.latest_power_meter_reading
        automatic = window.automatic_orchestrator
        currents = list(automatic.currents)
        current_index = int(automatic.current_index)

        plots = window.live_plots
        spectrum_saturated = bool(window.latest_spectrum_saturated)
        spectrum_center_locked_nm = _number(window.spectrum_center_locked_nm)
        if include_live_charts:
            (
                wavelength,
                intensity,
                spectrum_smsr_db,
                spectrum_peaks,
                spectrum_frame_stats,
            ) = self._spectrum_snapshot(
                window.latest_spectrum_wavelength,
                window.latest_spectrum_intensity,
                spectrum_saturated,
                spectrum_center_locked_nm,
                int(getattr(window, "spectrum_frame_revision", 0)),
            )
        else:
            wavelength, intensity, spectrum_smsr_db, spectrum_peaks, spectrum_frame_stats = (
                [],
                [],
                None,
                [],
                None,
            )
        power_points = self._power_snapshot(plots) if include_live_charts else []
        spectrum_points = self._spectrum_points_snapshot(wavelength, intensity)
        stable_points = self._stable_snapshot(window) if include_live_charts else []

        panel = window.pd_panel
        pd_points = self._pd_snapshot(panel) if include_pd_points else []
        series_revisions = self._current_series_revisions()

        def include_series(name: str, active_for_view: bool) -> bool:
            if not active_for_view:
                return False
            if since_revisions is None:
                return True
            previous = since_revisions.get(name)
            return (
                not isinstance(previous, (int, float))
                or int(previous) != series_revisions[name]
            )

        def append_patch(
            name: str,
            points: list[dict[str, float]],
            x_key: str,
        ) -> dict[str, Any] | None:
            cursor = _number(series_cursors.get(name))
            if since_revisions is None or cursor is None or not points:
                return None
            cursor_index = next(
                (
                    index
                    for index, point in enumerate(points)
                    if math.isclose(point[x_key], cursor, rel_tol=0.0, abs_tol=1e-9)
                ),
                None,
            )
            if cursor_index is None or cursor_index >= len(points) - 1:
                return None
            return {
                "startX": points[0][x_key],
                "points": points[cursor_index + 1 :],
            }

        measurements: dict[str, Any] = {}
        series_patches: dict[str, Any] = {}
        if include_series("power", include_live_charts):
            power_patch = append_patch("power", power_points, "elapsedS")
            if power_patch is None:
                measurements["power"] = power_points
            else:
                series_patches["power"] = power_patch
        if include_series("stable", include_live_charts):
            measurements["stable"] = stable_points
        if include_series("spectrum", include_live_charts):
            measurements["spectrum"] = spectrum_points
            measurements["spectrumPeaks"] = spectrum_peaks
        include_pd_series = include_series("pd", include_pd_points)
        if include_pd_series:
            pd_patch = append_patch("pd", pd_points, "elapsedS")
            if pd_patch is not None:
                series_patches["pd"] = pd_patch
        settings_error = ""
        if view in {"automatic", "full"}:
            try:
                window.collect_automatic_test_settings()
            except Exception as exc:
                settings_error = str(exc)

        progress = 0.0
        if currents:
            progress = max(0.0, min(1.0, (current_index + 1) / len(currents)))

        return {
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "seriesRevisions": series_revisions,
            **({"seriesPatches": series_patches} if series_patches else {}),
            "backend": {
                "connected": True,
                "mode": self.mode,
                "pythonVersion": __import__("platform").python_version(),
                "runtimeLogPath": str(runtime_log_path() or ""),
                "notices": list(self.notices[-8:]),
            },
            "configuration": {
                "sn": window.sn_field.text(),
                "productModel": window.product_model_field.text(),
                "batch": window.batch_field.text(),
                "station": window.test_station_field.text(),
                "outputDir": window.output_dir_field.text(),
                "powerSupplyKind": window._selected_power_supply_kind(),
                "tdkResource": window.tdk_resource_combo.currentText(),
                "setCurrentA": window.set_current_spin.value(),
                "tdkVoltageV": window.tdk_voltage_spin.value(),
                "powerMeterResource": window._selected_power_resource(),
                "powerMeterWavelengthNm": window.power_wavelength_spin.value(),
                "softwareGain": window.software_gain_spin.value(),
                "powerMeterIntervalMs": window.power_meter_interval_spin.value(),
                "spectrometerResource": (
                    window.spectrometer_combo.currentText()
                    if window._selected_spectrometer_device_id() is not None
                    else ""
                ),
                "integrationTimeUs": window.integration_spin.value(),
                "autoIntegration": window.auto_integration_check.isChecked(),
                "spectrometerIntervalMs": window.interval_spin.value(),
                "spectrumAverageFrames": window.spectrum_average_spin.value(),
                "stableWindowS": window.stable_window_spin.value(),
                "stableToleranceW": window.stable_tolerance_spin.value(),
                "initialCurrentA": window.auto_initial_current_spin.value(),
                "targetCurrentA": window.auto_target_current_spin.value(),
                "currentStepA": window.auto_current_step_spin.value(),
                "pointTimeoutS": window.auto_point_timeout_spin.value(),
                "rampDownStepA": window.auto_ramp_down_step_spin.value(),
                "rampDownIntervalS": window.auto_ramp_down_interval_spin.value(),
                "pauseRampDownTimeoutS": window.auto_pause_ramp_down_timeout_spin.value(),
                "useSpectrometer": window.auto_use_spectrometer_check.isChecked(),
            },
            "devices": {
                "powerSupply": {
                    "state": self._device_state(power_connected, False, ""),
                    "label": (
                        "TDK RS232"
                        if window._selected_power_supply_kind() == "tdk"
                        else "CH341 I²C"
                    ),
                    "detail": window.i2c_status_label.text(),
                    "connected": power_connected,
                    "outputEnabled": bool(getattr(power_supply, "output_enabled", False)) if power_supply else False,
                    "activeCurrentA": _number(window.active_output_current_a),
                    "resources": [window.tdk_resource_combo.itemText(i) for i in range(window.tdk_resource_combo.count())],
                },
                "powerMeter": {
                    "state": (
                        "connecting"
                        if power_meter_detecting
                        else self._device_state(False, power_meter_running, window._power_meter_fault_message)
                    ),
                    "label": "功率计",
                    "detail": window._power_meter_fault_message or ("采集中" if power_meter_running else "已停止"),
                    "running": power_meter_running,
                    "detecting": power_meter_detecting,
                    "ready": bool(power_meter_running and getattr(window.power_meter_reader, "is_ready", False)),
                    "powerW": _number(getattr(power_reading, "power_w", None)),
                    "stable": bool(getattr(power_reading, "stable", False)),
                    "resources": [window.power_meter_combo.itemText(i) for i in range(window.power_meter_combo.count())],
                },
                "spectrometer": {
                    "state": self._device_state(False, spectrum_running, window._spectrometer_fault_message),
                    "label": "Ocean Insight",
                    "detail": window._spectrometer_fault_message or ("采集中" if spectrum_running else "已停止"),
                    "running": spectrum_running,
                    "ready": bool(spectrum_running and getattr(window.spectrometer_reader, "is_ready", False)),
                    "peakWavelengthNm": None if spectrum_saturated else _number(getattr(spectrum_frame_stats, "peak_wavelength_nm", None)),
                    "centroidNm": None if spectrum_saturated else _number(getattr(spectrum_frame_stats, "centroid_nm", None)),
                    "fwhmNm": None if spectrum_saturated else _number(getattr(spectrum_frame_stats, "fwhm_nm", None)),
                    "smsrDb": spectrum_smsr_db,
                    "saturated": spectrum_saturated,
                    "spectrumWindow": (
                        {
                            "centerNm": spectrum_center_locked_nm,
                            "halfRangeNm": SPECTRUM_CENTER_LOCK_HALF_RANGE_NM,
                        }
                        if spectrum_center_locked_nm is not None
                        else None
                    ),
                    "resources": [window.spectrometer_combo.itemText(i) for i in range(window.spectrometer_combo.count())],
                },
            },
            "automaticTest": {
                "state": _enum_value(automatic.state),
                "detail": window.automatic_test_status_label.text(),
                "controlsEnabled": True,
                "canStart": bool(
                    window.start_automatic_test_button.isEnabled()
                    and not window._manual_output_is_energized()
                ),
                "canRetry": _enum_value(automatic.state) == "paused",
                "canPause": _enum_value(automatic.state)
                not in ("idle", "completed", "paused", "ramping_down"),
                "canEnd": _enum_value(automatic.state) not in ("idle", "completed", "ramping_down"),
                "settingsError": settings_error,
                "currents": currents,
                "currentIndex": current_index,
                "currentA": _number(automatic.current_a),
                "progress": progress,
                "pauseReason": automatic.pause_reason,
                "terminalOutcome": _enum_value(window.automatic_controller.terminal_outcome),
                "terminalReason": window.automatic_controller.terminal_reason,
            },
            "measurements": measurements,
            "pd": {
                "state": "running" if panel.reader is not None else "idle",
                "status": panel.status_label.text(),
                "devices": [panel.device_combo.itemText(i) for i in range(panel.device_combo.count())],
                "channels": [panel.channel_combo.itemText(i) for i in range(panel.channel_combo.count())],
                "ranges": [
                    {"label": panel.range_combo.itemText(i), "value": panel.range_combo.itemData(i)}
                    for i in range(panel.range_combo.count())
                ],
                "settings": {
                    "device": panel.device_combo.currentText(),
                    "channel": panel.channel_combo.currentText(),
                    "terminal": panel.terminal_combo.currentData(),
                    "range": panel.range_combo.currentData(),
                    "sampleRateHz": panel.sample_rate_spin.value(),
                    "blockSize": panel.block_size_spin.value(),
                    "scale": panel.scale_spin.value(),
                    "offset": panel.offset_spin.value(),
                    "unit": panel.unit_edit.text(),
                    "save": panel.save_checkbox.isChecked(),
                    "outputDir": panel.output_dir_edit.text(),
                },
                "currentValue": panel.current_value_label.text(),
                "voltage": panel.voltage_label.text(),
                "mean": panel.mean_label.text(),
                "standardDeviation": panel.std_label.text(),
                "rangeText": panel.range_label.text(),
                "sampleCount": panel.count_label.text(),
                **(
                    {"points": pd_points}
                    if include_pd_series and "pd" not in series_patches
                    else {}
                ),
            },
            "safety": {
                "hardwareAccess": True,
                "commandMode": "controller_owned",
                "detail": "设备命令、自动流程和安全下电均由现有 Python 控制器执行",
                "outputShutdownUnconfirmed": window.automatic_controller.output_shutdown_unconfirmed,
            },
            "status": {
                "message": window.statusBar().currentMessage(),
            },
        }
