from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from combined_test.automation import AutomaticTestState
from tauri_bridge.errors import CommandRejected
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings


class WorkflowAccessTests(unittest.TestCase):
    def test_each_device_reserves_its_workflow_and_releases_after_stop(self):
        for workflow in ("manual", "automatic"):
            for method, attribute in (
                ("powerMeter.start", "power_meter_reader"),
                ("spectrometer.start", "spectrometer_reader"),
                ("powerSupply.connect", "power_supply_controller"),
            ):
                with self.subTest(workflow=workflow, method=method), tempfile.TemporaryDirectory() as directory:
                    backend = HeadlessBackend(JsonSettings(Path(directory) / "settings.json"))
                    st = backend.runtime
                    other = "automatic" if workflow == "manual" else "manual"
                    fake = SimpleNamespace(is_ready=True, is_connected=True, output_enabled=False)
                    name = {"powerMeter.start": "start_power_meter", "spectrometer.start": "start_spectrometer", "powerSupply.connect": "connect_power_supply"}[method]
                    # Exercise the real dispatcher without importing/opening hardware.
                    with patch.object(st, name, side_effect=lambda: setattr(st, attribute, fake)), patch.object(backend, "snapshot", return_value={}), patch.object(backend, "_persist_configuration"):
                        backend.dispatch(method, {"workflow": workflow})
                        self.assertEqual(st.workflow_access.owner, workflow)
                        self.assertTrue(st.workflow_access.busy())
                        with self.assertRaises(CommandRejected):
                            backend.dispatch("powerMeter.start", {"workflow": other})
                    setattr(st, attribute, None)
                    self.assertFalse(st.workflow_access.busy())
                    with st.workflow_access.command("powerMeter.start", {"workflow": other}):
                        self.assertEqual(st.workflow_access.owner, other)

    def test_failed_activation_rolls_back_but_partial_connection_stays_locked(self):
        with tempfile.TemporaryDirectory() as directory:
            st = HeadlessBackend(JsonSettings(Path(directory) / "settings.json")).runtime
            for partial in (False, True):
                with self.assertRaises(RuntimeError):
                    with st.workflow_access.command("powerMeter.start", {"workflow": "manual"}):
                        if partial:
                            st.power_meter_reader = object()
                        raise RuntimeError("connection failed")
                self.assertEqual(st.workflow_access.owner, "manual" if partial else None)
                self.assertEqual(st.workflow_access.busy(), partial)

    def test_pause_and_pending_activation_keep_other_workflow_locked(self):
        with tempfile.TemporaryDirectory() as directory:
            st = HeadlessBackend(JsonSettings(Path(directory) / "settings.json")).runtime
            with st.workflow_access.command("powerMeter.start", {"workflow": "automatic"}):
                with self.assertRaises(CommandRejected):
                    with st.workflow_access.command("spectrometer.start", {"workflow": "manual"}):
                        self.fail("pending activation must reserve devices")
                with self.assertRaises(CommandRejected):
                    with st.workflow_access.command("spectrometer.start", {}):
                        self.fail("legacy manual commands must also respect ownership")
            st.automatic.state = AutomaticTestState.PAUSED
            with self.assertRaises(CommandRejected):
                with st.workflow_access.command("powerMeter.start", {"workflow": "manual"}):
                    self.fail("pause must retain ownership")
            # Safety commands remain available from every view.
            with st.workflow_access.command("app.stopAll", {"workflow": "manual"}):
                pass

    def test_snapshots_hide_other_workflow_and_previous_series_without_deleting_data(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = HeadlessBackend(JsonSettings(Path(directory) / "settings.json"))
            st = backend.runtime
            old = {"elapsedS": 1.234567890123, "powerW": 2.345678901234}
            st.power_points.append(old)
            with st.workflow_access.command("powerMeter.start", {"workflow": "manual"}):
                pass
            self.assertEqual(backend.snapshot({"view": "manual"})["measurements"]["power"], [])
            st.series_revisions["power"] += 1
            st.stable_power_points[1.23456789] = 9.87654321
            st.series_revisions["stable"] += 1
            manual = backend.snapshot({"view": "manual"})
            self.assertEqual(manual["measurements"]["power"], [old])
            self.assertEqual(backend.snapshot({"view": "automatic"})["measurements"]["power"], [])
            self.assertEqual(backend.snapshot({"view": "automatic"})["measurements"]["stable"], [])
            self.assertEqual(list(st.power_points), [old])
            with st.workflow_access.command("spectrometer.start", {"workflow": "automatic"}):
                pass
            self.assertEqual(backend.snapshot({"view": "automatic"})["measurements"]["power"], [])
            self.assertEqual(list(st.power_points), [old])


if __name__ == "__main__":
    unittest.main()
