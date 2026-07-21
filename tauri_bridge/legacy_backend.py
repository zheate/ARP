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

from combined_test.spectrum_math import calculate_smsr
from combined_test.spectrum import find_spectrum_peak_annotations


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _series(values: Any, *, limit: int | None = 1200) -> list[float]:
    if values is None:
        return []
    output: list[float] = []
    source = list(values)
    if limit is not None:
        source = source[-limit:]
    for value in source:
        number = _number(value)
        if number is not None:
            output.append(number)
    return output


def _downsample_spectrum(
    wavelength: list[float], intensity: list[float], *, limit: int = 800
) -> tuple[list[float], list[float]]:
    """Bound chart payload size while retaining narrow local extrema."""

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


class LegacyWindowBackend:
    """Expose the current Qt application through serializable commands."""

    mode = "active"

    def __init__(self, window: Any) -> None:
        self.window = window
        self.notices: list[dict[str, str]] = []
        self.latest_spectrometer_reading: Any | None = None
        self._connected_spectrum_reader_id: int | None = None
        self._spectrum_cache_source: tuple[Any, Any, bool] | None = None
        self._spectrum_cache_payload: tuple[
            list[float], list[float], float | None, list[dict[str, Any]]
        ] = ([], [], None, [])
        self._power_cache_key: tuple[Any, ...] | None = None
        self._power_cache_payload: list[dict[str, float]] = []
        self._spectrum_points_cache_key: tuple[int, int] | None = None
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
        payload = [
            {"elapsedS": x, "powerW": y}
            for x, y in zip(_series(times), _series(values))
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

        cache_key = (id(wavelength), id(intensity))
        if cache_key == self._spectrum_points_cache_key:
            return self._spectrum_points_cache_payload
        payload = [
            {"wavelengthNm": x, "intensity": y}
            for x, y in zip(wavelength, intensity)
        ]
        self._spectrum_points_cache_key = cache_key
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
        self, wavelength_values: Any, intensity_values: Any, saturated: bool
    ) -> tuple[list[float], list[float], float | None, list[dict[str, Any]]]:
        """Analyze a spectrum once per acquired frame, not once per UI poll."""

        cached_source = self._spectrum_cache_source
        if (
            cached_source is not None
            and cached_source[0] is wavelength_values
            and cached_source[1] is intensity_values
            and cached_source[2] == saturated
        ):
            return self._spectrum_cache_payload

        full_wavelength = _series(wavelength_values, limit=None)
        full_intensity = _series(intensity_values, limit=None)
        wavelength, intensity = _downsample_spectrum(full_wavelength, full_intensity)
        smsr_db: float | None = None
        peaks: list[dict[str, Any]] = []
        if not saturated and wavelength_values is not None and intensity_values is not None:
            window = getattr(self, "window", None)
            if hasattr(window, "latest_spectrum_smsr_db"):
                smsr_db = _number(window.latest_spectrum_smsr_db)
            else:
                try:
                    smsr_db = _number(calculate_smsr(wavelength_values, intensity_values).smsr_db)
                except (TypeError, ValueError):
                    pass
            try:
                peaks = [
                    {
                        "label": annotation.label,
                        "centroidNm": _number(annotation.centroid_nm),
                        "peakWavelengthNm": _number(annotation.peak_wavelength_nm),
                        "peakIntensity": _number(annotation.peak_intensity),
                    }
                    for annotation in find_spectrum_peak_annotations(
                        zip(full_wavelength, full_intensity)
                    )
                ]
            except (TypeError, ValueError):
                pass

        self._spectrum_cache_source = (wavelength_values, intensity_values, saturated)
        self._spectrum_cache_payload = (wavelength, intensity, smsr_db, peaks)
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
        payload = [
            {"elapsedS": x, "value": y}
            for x, y in zip(_series(times), _series(values))
        ]
        self._pd_cache_key = cache_key
        self._pd_cache_payload = payload
        self._bump_series_revision("pd")
        return payload

    def _install_nonblocking_dialogs(self) -> None:
        """Prevent the hidden compatibility window from opening modal dialogs."""

        from PySide6.QtWidgets import QMessageBox

        def notice(level: str) -> Callable[..., Any]:
            def capture(_parent: Any, title: str, message: str, *_args: Any, **_kwargs: Any) -> Any:
                self.notices.append(
                    {"level": level, "title": str(title), "message": str(message)}
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
            self.notices.append(
                {
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
        actions: dict[str, Callable[[dict[str, Any]], None]] = {
            "app.configure": self._configure,
            "device.refresh": self._refresh_device,
            "powerSupply.connect": lambda _p: self._set_power_supply_connected(True),
            "powerSupply.disconnect": lambda _p: self._set_power_supply_connected(False),
            "powerSupply.setCurrent": self._set_current,
            "powerSupply.setVoltage": self._set_voltage,
            "powerSupply.setOutput": self._set_output,
            "powerSupply.read": self._read_power_supply,
            "powerMeter.start": lambda _p: self.window.start_power_meter(),
            "powerMeter.stop": lambda _p: self.window.stop_power_meter(),
            "powerMeter.setRelativeZero": self._set_relative_zero,
            "spectrometer.start": lambda _p: self._start_spectrometer(),
            "spectrometer.stop": lambda _p: self.window.stop_spectrometer(),
            "spectrometer.saveCsv": self._save_spectrum_csv,
            "automatic.start": self._start_automatic,
            "automatic.retry": lambda _p: self.window.retry_automatic_test(),
            "automatic.end": lambda _p: self.window.end_automatic_test(),
            "automatic.reset": lambda _p: self.window.reset_automatic_test(),
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
        action(params)
        self.window.save_input_settings()
        return self.snapshot()

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
        # widgets enforce the CH341 20 A limit without constraining TDK values.
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
            "stableWindowS": window.stable_window_spin,
            "stableToleranceW": window.stable_tolerance_spin,
        }
        for key, widget in values.items():
            if key in params:
                widget.setValue(float(params[key]))

        if "tdkResource" in params:
            self._set_combo_text(window.tdk_resource_combo, params["tdkResource"])
        if "powerMeterResource" in params:
            self._set_combo_text(window.power_meter_combo, params["powerMeterResource"])
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
        if self.window._selected_power_supply_kind() == "ch341" and current_a > 20.0:
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
            raise ValueError("CH341 控制器没有独立输出开关")
        if current != desired:
            self.window.toggle_tdk_output()

    def _set_relative_zero(self, params: dict[str, Any]) -> None:
        self.window.set_power_meter_relative_zero(bool(params.get("enabled")))

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
        self._configure(params)
        self.window.start_automatic_test()
        self._connect_spectrum_capture()

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

    def _shutdown(self) -> None:
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
        spectrum_running = window.spectrometer_reader is not None
        power_reading = window.latest_power_meter_reading
        spectrum_reading = self.latest_spectrometer_reading
        automatic = window.automatic_orchestrator
        currents = list(automatic.currents)
        current_index = int(automatic.current_index)

        plots = window.live_plots
        spectrum_saturated = bool(window.latest_spectrum_saturated)
        if include_live_charts:
            wavelength, intensity, spectrum_smsr_db, spectrum_peaks = self._spectrum_snapshot(
                window.latest_spectrum_wavelength,
                window.latest_spectrum_intensity,
                spectrum_saturated,
            )
        else:
            wavelength, intensity, spectrum_smsr_db, spectrum_peaks = [], [], None, []
        power_points = self._power_snapshot(plots) if include_live_charts else []
        spectrum_points = self._spectrum_points_snapshot(wavelength, intensity)
        stable_points = self._stable_snapshot(window) if include_live_charts else []

        panel = window.pd_panel
        pd_points = self._pd_snapshot(panel) if include_pd_points else []
        series_revisions = self._current_series_revisions()

        def include_series(name: str, active_for_view: bool) -> bool:
            if since_revisions is None:
                return True
            previous = since_revisions.get(name)
            return active_for_view and (
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
                    "label": "TDK RS232" if window._selected_power_supply_kind() == "tdk" else "CH341 I²C",
                    "detail": window.i2c_status_label.text(),
                    "connected": power_connected,
                    "outputEnabled": bool(getattr(power_supply, "output_enabled", False)) if power_supply else False,
                    "activeCurrentA": _number(window.active_output_current_a),
                    "resources": [window.tdk_resource_combo.itemText(i) for i in range(window.tdk_resource_combo.count())],
                },
                "powerMeter": {
                    "state": self._device_state(False, power_meter_running, window._power_meter_fault_message),
                    "label": "功率计",
                    "detail": window._power_meter_fault_message or ("采集中" if power_meter_running else "已停止"),
                    "running": power_meter_running,
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
                    "peakWavelengthNm": None if spectrum_saturated else _number(getattr(spectrum_reading, "peak_wavelength_nm", None)),
                    "centroidNm": None if spectrum_saturated else _number(getattr(spectrum_reading, "centroid_nm", None)),
                    "fwhmNm": None if spectrum_saturated else _number(getattr(spectrum_reading, "fwhm_nm", None)),
                    "smsrDb": spectrum_smsr_db,
                    "saturated": spectrum_saturated,
                    "resources": [window.spectrometer_combo.itemText(i) for i in range(window.spectrometer_combo.count())],
                },
            },
            "automaticTest": {
                "state": _enum_value(automatic.state),
                "detail": window.automatic_test_status_label.text(),
                "controlsEnabled": True,
                "canStart": bool(window.start_automatic_test_button.isEnabled()),
                "canRetry": _enum_value(automatic.state) == "paused",
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
