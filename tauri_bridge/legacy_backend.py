"""Tauri adapter for the existing, tested Qt application controller.

The Qt window stays hidden.  Its widgets are used as the compatibility model
while the existing device threads, automatic controller, archive, and safety
shutdown code remain the single owner of application state.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from enum import Enum
from typing import Any, Callable


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _series(values: Any, *, limit: int = 1200) -> list[float]:
    if values is None:
        return []
    output: list[float] = []
    for value in list(values)[-limit:]:
        number = _number(value)
        if number is not None:
            output.append(number)
    return output


class LegacyWindowBackend:
    """Expose the current Qt application through serializable commands."""

    mode = "active"

    def __init__(self, window: Any) -> None:
        self.window = window
        self.notices: list[dict[str, str]] = []
        self.latest_spectrometer_reading: Any | None = None
        self._connected_spectrum_reader_id: int | None = None
        self.selected_history_session_id = ""
        self.comparison_session_ids: list[str] = []
        self.history_filters: dict[str, str] = {}
        self._install_nonblocking_dialogs()

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
            "records.exportCurrent": lambda _p: self.window.save_pending_excel_records(),
            "records.resume": self._resume_session,
            "records.reexport": self._reexport_session,
            "records.select": self._select_session,
            "records.compare": self._compare_sessions,
            "records.setFilters": self._set_history_filters,
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

        if "powerSupplyKind" in params:
            self._set_combo_data(window.power_supply_controller_combo, params["powerSupplyKind"])
        if "tdkResource" in params:
            self._set_combo_text(window.tdk_resource_combo, params["tdkResource"])
        if "powerMeterResource" in params:
            self._set_combo_text(window.power_meter_combo, params["powerMeterResource"])
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
        self.window.set_current_spin.setValue(float(params["currentA"]))
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
            self.window.history_export_thread,
        ):
            if reader is not None and hasattr(reader, "wait"):
                reader.wait(5000)

    def _select_history_session(self, session_id: str) -> None:
        table = self.window.records_history_table
        self.window.refresh_history_records()
        for row in range(table.rowCount()):
            item = table.item(row, 9)
            if item is not None and item.text() == session_id:
                table.selectRow(row)
                self.window.show_selected_history_session()
                return
        raise ValueError("找不到指定测试记录")

    def _resume_session(self, params: dict[str, Any]) -> None:
        self._select_history_session(str(params.get("sessionId", "")))
        self.window.prepare_resume_selected_session()

    def _reexport_session(self, params: dict[str, Any]) -> None:
        self._select_history_session(str(params.get("sessionId", "")))
        self.window.reexport_selected_history_session()

    def _select_session(self, params: dict[str, Any]) -> None:
        session_id = str(params.get("sessionId", ""))
        self._select_history_session(session_id)
        self.selected_history_session_id = session_id

    def _compare_sessions(self, params: dict[str, Any]) -> None:
        session_ids = [str(value) for value in params.get("sessionIds", []) if str(value)]
        if len(session_ids) > 5:
            raise ValueError("最多同时对比五轮测试")
        self.comparison_session_ids = session_ids

    def _set_history_filters(self, params: dict[str, Any]) -> None:
        self.history_filters = {
            key: str(params.get(key, "")).strip()
            for key in ("sn", "productModel", "batch", "station", "mode", "status", "dateFrom", "dateTo")
        }

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

    def _history(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        from combined_test.test_archive import SessionFilters

        archive = self.window._archive_for_history()
        if archive is None:
            return [], {"sessions": 0, "completionRate": None, "invalidAttemptRate": None, "retestRate": None, "medianDurationS": None}
        filters = SessionFilters(
            sn=self.history_filters.get("sn", ""),
            product_model=self.history_filters.get("productModel", ""),
            batch=self.history_filters.get("batch", ""),
            station=self.history_filters.get("station", ""),
            mode=self.history_filters.get("mode", ""),
            status=self.history_filters.get("status", ""),
            date_from=self.history_filters.get("dateFrom", ""),
            date_to=self.history_filters.get("dateTo", ""),
            limit=1000,
        )
        sessions = archive.list_sessions(filters)
        rows = []
        for session in sessions:
            rows.append(
                {
                    "sessionId": session.session_id,
                    "sn": session.sn,
                    "productModel": session.product_model,
                    "batch": session.batch,
                    "station": session.station,
                    "mode": session.mode,
                    "startedAt": session.started_at_utc,
                    "endedAt": session.ended_at_utc,
                    "status": _enum_value(session.status),
                    "terminationReason": session.termination_reason,
                    "shutdownConfirmed": session.shutdown_confirmed,
                    "workbookPath": str(session.workbook_path),
                    "exportState": _enum_value(session.export_state),
                    "exportError": session.export_error,
                }
            )
        stats = archive.session_statistics(filters)
        summary = {
            "sessions": int(stats["sessions"]),
            "completionRate": _number(stats["completion_rate"]),
            "invalidAttemptRate": _number(stats["invalid_attempt_rate"]),
            "retestRate": _number(stats["retest_rate"]),
            "medianDurationS": _number(stats["median_duration_s"]),
        }
        return rows, summary

    @staticmethod
    def _attempt_row(attempt: Any) -> dict[str, Any]:
        return {
            "attemptId": attempt.attempt_id,
            "sequenceIndex": attempt.sequence_index,
            "targetCurrentA": _number(attempt.target_current_a),
            "attemptNo": attempt.attempt_no,
            "createdAt": attempt.created_at_utc,
            "validity": _enum_value(attempt.validity),
            "invalidReason": attempt.invalid_reason,
            "selected": attempt.selected,
            "currentA": _number(attempt.current_a),
            "voltageV": _number(attempt.voltage_v),
            "powerW": _number(attempt.power_w),
            "efficiency": _number(attempt.efficiency),
            "peakWavelengthNm": _number(attempt.peak_wavelength_nm),
            "centroidNm": _number(attempt.centroid_nm),
            "fwhmNm": _number(attempt.fwhm_nm),
            "pib": _number(attempt.pib),
            "smsrDb": _number(attempt.smsr_db),
        }

    def _history_detail(self) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        archive = self.window._archive_for_history()
        if archive is None or not self.selected_history_session_id:
            return None, []
        try:
            session = archive.get_session(self.selected_history_session_id)
        except KeyError:
            return None, []
        detail = {
            "sessionId": session.session_id,
            "sn": session.sn,
            "productModel": session.product_model,
            "batch": session.batch,
            "station": session.station,
            "status": _enum_value(session.status),
            "terminationReason": session.termination_reason,
            "shutdownConfirmed": session.shutdown_confirmed,
            "settings": dict(session.settings),
        }
        return detail, [self._attempt_row(value) for value in archive.list_attempts(session.session_id)]

    def _comparison(self) -> list[dict[str, Any]]:
        archive = self.window._archive_for_history()
        if archive is None:
            return []
        output = []
        for session_id in self.comparison_session_ids:
            try:
                session = archive.get_session(session_id)
            except KeyError:
                continue
            points = [
                self._attempt_row(value)
                for value in archive.list_attempts(session_id, selected_only=True)
            ]
            output.append({"sessionId": session_id, "label": f"{session.sn} · {session.started_at_utc}", "points": points})
        return output

    def snapshot(self) -> dict[str, Any]:
        window = self.window
        power_supply = window.get_power_supply()
        power_connected = bool(power_supply is not None and power_supply.connected)
        power_meter_running = window.power_meter_reader is not None
        spectrum_running = window.spectrometer_reader is not None
        power_reading = window.latest_power_meter_reading
        spectrum_reading = self.latest_spectrometer_reading
        automatic = window.automatic_orchestrator
        currents = list(automatic.currents)
        current_index = int(automatic.current_index)
        history, history_summary = self._history()
        history_detail, history_attempts = self._history_detail()

        records = []
        for record in window.record_store.snapshot():
            records.append(
                {
                    "currentA": _number(record.current_a),
                    "voltageV": _number(record.voltage_v),
                    "powerW": _number(record.power_w),
                    "efficiency": _number(record.efficiency),
                    "peakWavelengthNm": _number(record.peak_wavelength_nm),
                    "centroidNm": _number(record.centroid_nm),
                    "fwhmNm": _number(record.fwhm_nm),
                    "pib": _number(record.pib),
                    "smsrDb": _number(record.smsr_db),
                }
            )

        plots = window.live_plots
        wavelength = _series(window.latest_spectrum_wavelength, limit=2400)
        intensity = _series(window.latest_spectrum_intensity, limit=2400)
        power_points = [
            {"elapsedS": x, "powerW": y}
            for x, y in zip(_series(plots.power_curve_times), _series(plots.power_curve_values))
        ]
        spectrum_points = [
            {"wavelengthNm": x, "intensity": y}
            for x, y in zip(wavelength, intensity)
        ]
        stable_points = [
            {
                "currentA": float(current),
                "powerW": _number(power),
                "efficiencyPercent": _number(window.efficiency_points.get(current)),
            }
            for current, power in sorted(window.stable_power_points.items())
        ]

        panel = window.pd_panel
        pd_points = [
            {"elapsedS": x, "value": y}
            for x, y in zip(_series(panel.plot_times), _series(panel.plot_values))
        ]
        session = window.record_store.current_session
        settings_error = ""
        try:
            window.collect_automatic_test_settings()
        except Exception as exc:
            settings_error = str(exc)

        progress = 0.0
        if currents:
            progress = max(0.0, min(1.0, (current_index + 1) / len(currents)))

        return {
            "capturedAt": datetime.now(timezone.utc).isoformat(),
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
                    "peakWavelengthNm": _number(getattr(spectrum_reading, "peak_wavelength_nm", None)),
                    "centroidNm": _number(getattr(spectrum_reading, "centroid_nm", None)),
                    "fwhmNm": _number(getattr(spectrum_reading, "fwhm_nm", None)),
                    "saturated": bool(window.latest_spectrum_saturated),
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
            "measurements": {
                "power": power_points,
                "stable": stable_points,
                "spectrum": spectrum_points,
            },
            "records": {
                "current": records,
                "unsavedCount": len(window.record_store.unsaved_records()),
                "workbookPath": str(window.excel_workbook_path) if window.excel_workbook_path else "",
                "sessionId": session.session_id if session else "",
                "history": history,
                "summary": history_summary,
                "detail": history_detail,
                "attempts": history_attempts,
                "comparison": self._comparison(),
                "filters": dict(self.history_filters),
            },
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
                "points": pd_points,
            },
            "safety": {
                "hardwareAccess": True,
                "commandMode": "controller_owned",
                "detail": "设备命令、自动流程和安全下电均由现有 Python 控制器执行",
                "outputShutdownUnconfirmed": window.automatic_controller.output_shutdown_unconfirmed,
            },
            "status": {
                "message": window.statusBar().currentMessage(),
                "log": window.log_text.text(),
            },
        }
