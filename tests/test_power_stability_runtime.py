"""SN persistence and shared-reader learning, without opening hardware."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from combined_test.models import PowerMeterReading
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings


class PowerMeterNoiseRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "settings.json"
        self.backend = HeadlessBackend(JsonSettings(self.path))
        self.station = self.backend.runtime
        self.manager = self.station.power_stability
        self.station.configuration.update(powerMeterSerialNumber="PROBE-A", softwareGain=1.04)
        self.reader = Mock(is_ready=True)
        self.station.power_meter_reader = self.reader
        self.station.latest_power = self.reading(0)
        self.manager.configure()

    def tearDown(self):
        self.backend.export_worker.graceful_stop(timeout_s=2)
        self.directory.cleanup()

    def reading(self, t):
        return PowerMeterReading(t, (round(t * 10) % 2) * 0.104, False, 0, 0, 0,
                                 transaction_started_monotonic_s=1001 + t)

    def learn(self):
        samples = [(i / 10, (i % 2) * 0.104) for i in range(201)]
        self.manager._finish_learning(self.manager.job_epoch, samples, self.manager.identity(), self.manager.policy())
        self.assertIsNotNone(self.manager.model, self.manager.state)

    def test_twenty_seconds_uses_existing_reader_and_remembers_sn(self):
        done = threading.Event()
        original = self.manager._finish_learning
        def finish(*args):
            try:
                original(*args)
            finally:
                done.set()
        with patch("tauri_bridge.power_stability_runtime.time.monotonic", return_value=1000), patch.object(self.manager, "_finish_learning", side_effect=finish):
            self.manager.start_background({"confirmedDark": True})
            for i in range(200):
                self.manager.observe(self.reading(i / 10), 1001 + i / 10)
            self.assertEqual(self.manager.job, "learn")
            self.assertIsNone(self.manager.model)
            self.manager.observe(self.reading(20), 1021)
            self.assertTrue(done.wait(2))
        self.assertIsNone(self.manager.job)
        self.assertEqual(self.manager.model.sample_count, 201)
        self.assertAlmostEqual(self.manager.noise_jump_w(), 0.104)
        self.reader.update_noise_jump.assert_called_with(self.manager.noise_jump_w())
        self.assertIn("PROBE-A", self.backend.settings.get("runtime/powerBackgrounds"))

    def test_restart_port_and_gain_changes_reuse_sn_without_recheck(self):
        self.learn()
        restarted = HeadlessBackend(JsonSettings(self.path))
        try:
            st = restarted.runtime
            st.configuration.update(powerMeterSerialNumber="probe-a", powerMeterResource="ASRL99::INSTR", softwareGain=2)
            st.power_stability.configure()
            self.assertAlmostEqual(st.power_stability.noise_jump_w(), 0.2)
            self.assertIsNone(st.power_stability.job)
            self.assertIn("已记住", st.power_stability.state)
            st.configuration["powerMeterSerialNumber"] = "OTHER-PROBE"
            st.power_stability.configure()
            self.assertEqual(st.power_stability.noise_jump_w(), 0)
            st.configuration["powerMeterSerialNumber"] = "PROBE-A"
            st.power_stability.configure()
            self.assertAlmostEqual(st.power_stability.noise_jump_w(), 0.2)
        finally:
            restarted.export_worker.graceful_stop(timeout_s=2)

    def test_cancel_disconnect_and_failures_keep_previously_learned_step(self):
        self.learn()
        with patch("tauri_bridge.power_stability_runtime.time.monotonic", return_value=1000):
            self.manager.start_background({"confirmedDark": True})
        self.manager.cancel_background()
        self.assertAlmostEqual(self.manager.noise_jump_w(), 0.104)
        self.manager._finish_learning(self.manager.job_epoch, [(i / 10, 0) for i in range(201)], self.manager.identity(), self.manager.policy())
        self.assertAlmostEqual(self.manager.noise_jump_w(), 0.104)

    def test_old_dual_settings_no_longer_create_response_or_background_gates(self):
        self.station.power_meter_reader = None
        self.backend.dispatch("app.configure", {"powerStabilityMode": "dual", "powerStabilityParametersConfirmed": True})
        self.assertNotIn("powerStabilityMode", self.station.configuration)
        self.assertEqual(self.manager.snapshot()["mode"], "legacy")
        readiness = self.station.automatic_readiness()
        self.assertFalse(any("响应参数" in str(item) or "复核本底" in str(item) for item in readiness.get("blockers", [])))

    def test_learning_requires_zero_current_and_blocks_concurrent_output(self):
        self.station.active_current_a = 1
        with self.assertRaises(ValueError):
            self.manager.start_background({"confirmedDark": True})
        self.station.active_current_a = 0
        with patch("tauri_bridge.power_stability_runtime.time.monotonic", return_value=1000):
            self.manager.start_background({"confirmedDark": True})
        with self.assertRaises(Exception):
            self.backend.dispatch("powerSupply.setCurrent", {"currentA": 1})
