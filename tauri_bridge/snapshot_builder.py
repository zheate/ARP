"""Build immutable frontend snapshots from backend runtime state."""

from __future__ import annotations

import math
import platform
import time
from datetime import datetime, timezone
from typing import Any

from combined_test.automation import AutomaticTestState, ORDINARY_AUTO_POST_STABLE_DELAY_S, automatic_post_stable_delay_s
from combined_test.runtime_logging import runtime_log_path
from combined_test.spectrum import SPECTRUM_CENTER_LOCK_HALF_RANGE_NM

from .station_automatic import automatic_power_stability_span_limit_w, automatic_power_stability_window_s
from .station_support import (
    BUILTIN_TEST_SCRIPTS,
    device_state,
    finite,
    power_meter_serial_number,
)


class SnapshotBuilder:
    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def build(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        backend = self._backend
        # Keep lock ordering one-way: backend state, then station state.
        with backend._snapshot_lock:
            view_params = params or {}
            backend.validate_runtime_target(view_params)
            active_st = backend.runtime
            with backend._lock:
                shared = {
                    "notices": list(backend.notices),
                    "status_message": backend.status_message,
                    "power_supply_resources": list(backend.power_supply_resources),
                    "ch341_discovery_state": backend.ch341_discovery_state,
                    "ch341_scan_error": backend.ch341_scan_error,
                    "ch341_descriptors": list(backend.ch341_descriptors),
                    "power_meter_options": list(backend.power_meter_options),
                    "power_meter_detecting": backend.power_meter_detect_thread is not None,
                    "power_meter_detection_detail": backend.power_meter_detection_detail,
                    "power_supply_detecting": backend.power_supply_detect_thread is not None,
                    "power_supply_detection_detail": backend.power_supply_detection_detail,
                    "power_supply_detection_kind": backend.power_supply_detection_kind,
                    "spectrometer_detecting": backend.spectrometer_detect_thread is not None,
                    "spectrometer_detection_detail": backend.spectrometer_detection_detail,
                    "spectrometer_resources": [dict(item) for item in backend.spectrometer_resources],
                }
            with active_st._lock:
                return self._build_with_station_lock(backend, view_params, shared)

    def _build_with_station_lock(
        self,
        backend: Any,
        params: dict[str, Any],
        shared: dict[str, Any],
    ) -> dict[str, Any]:
        view = str(params.get("view", "full"))
        # Accept old station-1 views during upgrades, but never route an old
        # station-2 view to this runtime.
        view = {"automatic_1": "automatic", "manual_1": "manual", "pd_1": "pd"}.get(view, view)
        if view not in {"automatic", "manual", "pd", "shipping", "data", "full"}:
            raise ValueError(f"未知视图：{view}")
        active_st = backend.runtime

        notices = shared["notices"]
        status_message = shared["status_message"]
        power_supply_resources = shared["power_supply_resources"]
        ch341_discovery_state = shared["ch341_discovery_state"]
        ch341_scan_error = shared["ch341_scan_error"]
        ch341_descriptors = shared["ch341_descriptors"]
        power_meter_options = shared["power_meter_options"]
        power_meter_detecting = shared["power_meter_detecting"]
        power_meter_detection_detail = shared["power_meter_detection_detail"]
        power_supply_detection_matches = (
            shared["power_supply_detection_kind"] == active_st.power_supply_kind
        )
        power_supply_detecting = shared["power_supply_detecting"] and power_supply_detection_matches
        power_supply_detection_detail = (
            shared["power_supply_detection_detail"]
            if power_supply_detection_matches
            else ""
        )
        spectrometer_detecting = shared["spectrometer_detecting"]
        spectrometer_detection_detail = shared["spectrometer_detection_detail"]
        spectrometer_resources = shared["spectrometer_resources"]

        include_live = view in {"automatic", "manual", "full"}
        include_pd = view in {"pd", "full"}
        raw_since = params.get("since")
        since_revisions = raw_since if isinstance(raw_since, dict) else None
        with active_st._pd_lock:
            pd_revision = active_st.series_revisions["pd"]
            pd_state = {
                key: (dict(value) if key == "settings" else list(value) if isinstance(value, list) else value)
                for key, value in active_st.pd_state.items() if key != "points"
            }
            if include_pd and (since_revisions is None or since_revisions.get("pd") != pd_revision):
                pd_state["points"] = list(active_st.pd_state["points"])
        status_message = active_st.status_message or status_message

        supply = active_st._power_supply()
        frame = active_st.latest_spectrum
        spectrum_saturated = bool(frame and frame.saturation and frame.saturation.saturated)
        spectrum_stats = None if frame is None else frame.stats
        power_value = finite(
            None if active_st.latest_power is None else active_st.latest_power.power_w
        )
        power_freshness = active_st.measurement_freshness(
            received_at_monotonic_s=active_st.latest_power_received_monotonic_s,
            sampled_at_utc=active_st.latest_power_sampled_at_utc,
            running=bool(
                active_st.power_meter_reader and active_st.power_meter_reader.is_ready
            ),
            interval_ms=float(active_st.configuration["powerMeterIntervalMs"]),
            valid=power_value is not None,
        )
        spectrum_freshness = active_st.measurement_freshness(
            received_at_monotonic_s=active_st.latest_spectrum_received_monotonic_s,
            sampled_at_utc=active_st.latest_spectrum_sampled_at_utc,
            running=bool(
                active_st.spectrometer_reader and active_st.spectrometer_reader.is_ready
            ),
            interval_ms=float(active_st.configuration["spectrometerIntervalMs"]),
            valid=frame is not None,
            saturated=spectrum_saturated,
        )
        automatic_readiness = active_st.automatic_readiness()
        stability_reading = active_st.automatic_stability_reading
        auto_state = active_st.automatic.state
        if auto_state in {AutomaticTestState.IDLE, AutomaticTestState.STARTING, AutomaticTestState.SETTING_CURRENT}:
            stability_reading = None
        stability_window_s = automatic_power_stability_window_s(
            active_st.configuration.get("selectedScriptId", "standard"),
            active_st.configuration["stableWindowS"],
            active_st.automatic.current_a,
        )
        stability_quality = (
            power_freshness["quality"]
            if stability_reading is active_st.latest_power else "stale"
        )
        if auto_state in {AutomaticTestState.WAITING_STABLE, AutomaticTestState.WAITING_VOLTAGE} and stability_quality != "live":
            stability_reading = None
        hold_required_s = (
            automatic_post_stable_delay_s(active_st.automatic.current_index, len(active_st.automatic.currents))
            if 0 <= active_st.automatic.current_index < len(active_st.automatic.currents)
            else ORDINARY_AUTO_POST_STABLE_DELAY_S
        )
        automatic_stability = {
            "powerStable": bool(stability_reading and stability_reading.stable),
            "elapsedS": (
                stability_window_s if stability_reading and stability_reading.stable else
                math.floor(min(stability_window_s, max(0.0, stability_reading.stable_duration_s)) * 10 + 1e-9) / 10
                if stability_reading else 0.0
            ),
            "requiredS": stability_window_s,
            "holdElapsedS": (
                math.floor(min(hold_required_s, max(0.0, active_st.automatic_stable_hold_elapsed_s)) * 10 + 1e-9) / 10
                if stability_reading and stability_reading.stable else 0.0
            ),
            "holdRequiredS": hold_required_s,
            "spanW": finite(stability_reading.stable_span_w) if stability_reading else None,
            "spanLimitW": automatic_power_stability_span_limit_w(active_st.automatic.current_a),
            "percent": finite(stability_reading.stability_percent) if stability_reading else None,
            "limitPercent": float(active_st.configuration["stabilityLimitPercent"]),
            "quality": stability_quality,
            "hasReading": stability_reading is not None,
            "wavelengthRequired": bool(active_st.automatic.settings and active_st.automatic.settings.use_spectrometer),
            "wavelengthStable": bool(active_st.latest_wavelength_stable and spectrum_freshness["quality"] == "live"),
        }

        raw_since = params.get("since")
        since_revisions = raw_since if isinstance(raw_since, dict) else None
        raw_cursors = params.get("cursors")
        series_cursors = raw_cursors if isinstance(raw_cursors, dict) else {}
        merged_revisions = {
            "power": active_st.series_revisions["power"],
            "stable": active_st.series_revisions["stable"],
            "aging": active_st.series_revisions["aging"],
            "spectrum": active_st.series_revisions["spectrum"],
            "pd": pd_revision,
        }

        def include_series(name: str, active_for_view: bool) -> bool:
            if not active_for_view:
                return False
            if since_revisions is None:
                return True
            previous = since_revisions.get(name)
            return not isinstance(previous, (int, float)) or int(previous) != merged_revisions[name]

        def append_patch(
            name: str,
            points: list[dict[str, Any]],
            x_key: str,
        ) -> dict[str, Any] | None:
            cursor = finite(series_cursors.get(name))
            if since_revisions is None or cursor is None or not points:
                return None
            cursor_index = next(
                (
                    index
                    for index, point in enumerate(points)
                    if math.isclose(float(point[x_key]), cursor, rel_tol=0.0, abs_tol=1e-9)
                ),
                None,
            )
            if cursor_index is None or cursor_index >= len(points) - 1:
                return None
            return {
                "startX": float(points[0][x_key]),
                "points": points[cursor_index + 1 :],
            }

        measurements: dict[str, Any] = {}
        series_patches: dict[str, Any] = {}
        if include_series("power", include_live):
            power_points = list(active_st.power_points)
            power_patch = append_patch("power", power_points, "elapsedS")
            if power_patch is None:
                measurements["power"] = power_points
            else:
                series_patches["power"] = power_patch
        if include_series("stable", include_live):
            measurements["stable"] = active_st.build_stable_snapshot()
        if include_series("aging", include_live):
            measurements["agingPower"] = active_st.build_aging_power_snapshot()
        if include_series("spectrum", include_live):
            spectrum, spectrum_peaks = active_st.build_spectrum_snapshot(
                allow_cached_grid=since_revisions is not None,
            )
            measurements["spectrum"] = spectrum
            measurements["spectrumPeaks"] = spectrum_peaks

        pd_snapshot = {key: value for key, value in pd_state.items() if key != "points"}
        if include_series("pd", include_pd):
            pd_points = pd_state["points"]
            pd_patch = append_patch("pd", pd_points, "elapsedS")
            if pd_patch is None:
                pd_snapshot["points"] = pd_points
            else:
                series_patches["pd"] = pd_patch

        # CH341 device ports map
        online_map = {d.location_path: d.device_index for d in ch341_descriptors}
        ports = [
            {
                "locationPath": loc,
                "name": None,
                "online": True,
                "deviceIndex": online_map[loc],
            }
            for loc in online_map
        ]
        ch341_snapshot = {
            "discoveryState": ch341_discovery_state,
            "error": ch341_scan_error,
            "ports": ports,
            "connectedPort": active_st.connected_ch341_port,
        }

        if active_st.power_supply_identity_error:
            power_supply_detail = active_st.power_supply_identity_error
        elif supply is not None and active_st.power_supply_kind == "ch341" and active_st.connected_ch341_port:
            power_supply_detail = f"Device {active_st.connected_ch341_port['deviceIndex']} · 已连接"
        else:
            power_supply_detail = "已连接" if supply is not None else "未连接"

        currents = list(active_st.automatic.currents)
        current_index = int(active_st.automatic.current_index)
        progress = max(0.0, min(1.0, (current_index + 1) / len(currents))) if currents else 0.0
        dwell_elapsed = None
        run_elapsed = None
        if active_st.automatic_started_at is not None and active_st.automatic.state != AutomaticTestState.IDLE:
            run_end = active_st.automatic_finished_at
            # Freeze only after the worker has actually exited. A completion
            # snapshot during final journal/export work still has live time.
            run_elapsed = math.floor(max(0.0, (time.monotonic() if run_end is None else run_end) - active_st.automatic_started_at))
        if active_st.automatic_hold_started_at is not None and active_st.automatic.current_dwell_s > 0.0:
            elapsed_s = min(
                active_st.automatic.current_dwell_s,
                max(0.0, time.monotonic() - active_st.automatic_hold_started_at),
            )
            # The UI displays whole seconds. Quantizing this transport field
            # prevents the 200 ms live snapshot loop from becoming a timer
            # stream while the device data itself is unchanged.
            dwell_elapsed = (
                active_st.automatic.current_dwell_s
                if elapsed_s >= active_st.automatic.current_dwell_s
                else float(math.floor(elapsed_s))
            )

        return {
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "seriesRevisions": merged_revisions,
            **({"seriesPatches": series_patches} if series_patches else {}),
            "backend": {
                "connected": True,
                "mode": backend.mode,
                "runtimeId": backend.runtime_id,
                "pythonVersion": platform.python_version(),
                "runtimeLogPath": str(runtime_log_path() or ""),
                "notices": notices,
            },
            "configuration": dict(active_st.configuration),
            "testScripts": [dict(item) for item in BUILTIN_TEST_SCRIPTS],
            "devices": {
                "powerSupply": {
                    "state": "connecting" if power_supply_detecting else device_state(supply is not None, False, active_st.power_supply_identity_error),
                    "label": "TDK RS232" if active_st.power_supply_kind == "tdk" else "CH341 I²C",
                    "detail": power_supply_detection_detail if power_supply_detecting or (supply is None and power_supply_detection_detail) else power_supply_detail,
                    "connected": supply is not None,
                    "detecting": power_supply_detecting,
                    "outputEnabled": bool(supply and supply.output_enabled),
                    "activeCurrentA": finite(active_st.active_current_a),
                    "inputVoltageV": finite(active_st.latest_power_supply_input_voltage_v),
                    "outputVoltageV": finite(active_st.latest_power_supply_output_voltage_v),
                    "outputCurrentA": finite(active_st.latest_power_supply_output_current_a),
                    "temperatureC": finite(active_st.latest_power_supply_temperature_c),
                    "resources": power_supply_resources,
                    "ch341": ch341_snapshot,
                },
                "powerMeter": {
                    "state": "connecting" if power_meter_detecting else device_state(False, active_st.power_meter_reader is not None, active_st.power_meter_error),
                    "label": "功率计",
                    "detail": active_st.power_meter_error or ("采集中" if active_st.power_meter_reader else power_meter_detection_detail or "已停止"),
                    "running": active_st.power_meter_reader is not None,
                    "detecting": power_meter_detecting,
                    "ready": bool(active_st.power_meter_reader and active_st.power_meter_reader.is_ready),
                    "powerW": power_value,
                    "stable": bool(active_st.latest_power and active_st.latest_power.stable),
                    "stableElapsedS": active_st.power_stable_elapsed_s if power_freshness["quality"] == "live" else None,
                    "stabilityPercent": finite(
                        getattr(active_st.latest_power, "stability_percent", None)
                        if active_st.latest_power
                        else None
                    ),
                    "stabilityLimitPercent": float(
                        active_st.configuration["stabilityLimitPercent"]
                    ),
                    **power_freshness,
                    "resources": [item.label() for item in power_meter_options],
                    "coefficientLibrary": backend.power_meter_profiles.library(),
                    "resourceProfiles": [
                        {
                            "resource": item.resource,
                            "driverKind": item.driver_kind,
                            "serialNumber": power_meter_serial_number(item),
                            "softwareGain": backend.discovery._power_meter_profile_gain(item),
                            "coefficientLibrary": backend.power_meter_profiles.library(item.driver_kind, power_meter_serial_number(item)),
                        }
                        for item in power_meter_options
                    ],
                },
                "spectrometer": {
                    "state": "connecting" if spectrometer_detecting else device_state(False, active_st.spectrometer_reader is not None, active_st.spectrometer_error),
                    "label": "Ocean Insight",
                    "detail": active_st.spectrometer_error or ("采集中" if active_st.spectrometer_reader else spectrometer_detection_detail or "已停止"),
                    "running": active_st.spectrometer_reader is not None,
                    "detecting": spectrometer_detecting,
                    "ready": bool(active_st.spectrometer_reader and active_st.spectrometer_reader.is_ready),
                    "peakWavelengthNm": None if spectrum_saturated else finite(getattr(spectrum_stats, "peak_wavelength_nm", None)),
                    "centroidNm": None if spectrum_saturated else finite(getattr(spectrum_stats, "centroid_nm", None)),
                    "fwhmNm": None if spectrum_saturated else finite(getattr(spectrum_stats, "fwhm_nm", None)),
                    "pib": None if frame is None or not math.isfinite(getattr(frame, "pib", math.nan)) else finite(frame.pib * 100.0 if frame.pib <= 1.0 else frame.pib),
                    "smsrDb": None if frame is None or frame.smsr is None else finite(frame.smsr.smsr_db),
                    "saturated": spectrum_saturated,
                    **spectrum_freshness,
                    "spectrumWindow": (
                        {
                            "centerNm": active_st.spectrum_center_locked_nm,
                            "halfRangeNm": SPECTRUM_CENTER_LOCK_HALF_RANGE_NM,
                        }
                        if active_st.spectrum_center_locked_nm is not None
                        else None
                    ),
                    "resources": [item["label"] for item in spectrometer_resources],
                },
            },
            "automaticTest": {
                "state": active_st.automatic.state.value,
                "detail": active_st.automatic_detail,
                "controlsEnabled": True,
                "canStart": automatic_readiness["ready"],
                "readiness": automatic_readiness,
                "canPause": active_st.automatic.state not in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED, AutomaticTestState.PAUSED, AutomaticTestState.RAMPING_DOWN},
                "canEnd": active_st.automatic.state not in {AutomaticTestState.IDLE, AutomaticTestState.COMPLETED, AutomaticTestState.RAMPING_DOWN},
                "settingsError": (
                    ""
                    if automatic_readiness["ready"]
                    else str(automatic_readiness["blockers"][0]["message"])
                ),
                "currents": currents,
                "dwellTimesS": list(active_st.automatic.dwell_times_s),
                "currentIndex": current_index,
                "currentA": finite(active_st.automatic.current_a),
                "currentDwellS": active_st.automatic.current_dwell_s,
                "currentDwellElapsedS": dwell_elapsed,
                "samplePath": active_st.automatic_sample_path,
                "elapsedS": run_elapsed,
                "runContext": dict(active_st.automatic_run_context) if active_st.automatic_run_context else None,
                "stability": automatic_stability,
                "progress": progress,
                "pauseReason": active_st.automatic.pause_reason,
                "pauseKind": active_st.automatic_pause_kind,
                "pauseStartedAtUtc": active_st.automatic_pause_started_at_utc,
                "pauseDeadlineAtUtc": active_st.automatic_pause_deadline_at_utc,
                "pauseActions": list(active_st.automatic_pause_actions),
                "terminalOutcome": active_st.automatic_terminal_outcome,
                "terminalReason": active_st.automatic_terminal_reason,
                "savedPointCount": len(active_st.record_store.recorded_currents),
                "exportedPointCount": len(active_st.record_store.exported_currents),
                "journalPath": "" if active_st.record_store.journal_path is None else str(active_st.record_store.journal_path),
                "canNextDevice": active_st.automatic.state == AutomaticTestState.COMPLETED and active_st.shutdown_confirmed,
                "canRetryExport": active_st.automatic.state == AutomaticTestState.COMPLETED and active_st.export_status == "failed",
                "exportStatus": active_st.export_status,
                "exportPath": active_st.export_path,
                "exportError": active_st.export_error,
                "reference": (
                    active_st._test_reference.reference_payload()
                    if active_st._test_reference is not None
                    else None
                ),
            },
            "manualTest": {
                "pendingCount": (
                    len(active_st.record_store.unsaved_records())
                    if active_st.record_store.current_session is not None
                    and active_st.record_store.current_session.mode == "manual"
                    else 0
                ),
                "exportStatus": active_st.export_status,
                "exportPath": active_st.export_path,
                "exportError": active_st.export_error,
            },
            "measurements": measurements,
            "pd": pd_snapshot,
            "safety": {
                "hardwareAccess": True,
                "commandMode": "controller_owned",
                "detail": "设备命令、自动流程和安全下电由无界面 Python 后台执行",
                "controlOwner": "manual" if active_st.manual_controls_allowed() else "automatic",
                "manualControlsAllowed": active_st.manual_controls_allowed(),
                "shutdown": active_st.shutdown_snapshot(),
            },
            "status": {"message": status_message},
        }
