from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from tauri_bridge.protocol import PROTOCOL_VERSION
from tauri_bridge.service import BridgeService


REPO_ROOT = Path(__file__).resolve().parent.parent


class TauriBridgeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = BridgeService()

    def test_snapshot_is_read_only_and_does_not_claim_devices_are_connected(self) -> None:
        response = self.service.handle_line(
            json.dumps(
                {
                    "v": PROTOCOL_VERSION,
                    "id": "snapshot-1",
                    "method": "app.snapshot",
                    "params": {},
                }
            )
        )

        self.assertTrue(response["ok"])
        snapshot = response["result"]
        self.assertTrue(snapshot["backend"]["connected"])
        self.assertEqual(snapshot["backend"]["mode"], "read_only")
        self.assertFalse(snapshot["safety"]["hardwareAccess"])
        self.assertFalse(snapshot["automaticTest"]["controlsEnabled"])
        self.assertTrue(
            all(
                device["state"] == "disconnected"
                for device in snapshot["devices"].values()
            )
        )

    def test_unknown_method_returns_structured_error(self) -> None:
        response = self.service.handle_line(
            json.dumps(
                {
                    "v": PROTOCOL_VERSION,
                    "id": "unknown-1",
                    "method": "devices.connect",
                    "params": {},
                }
            )
        )

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "method_not_found")

    def test_injected_backend_owns_snapshot_and_mutating_commands(self) -> None:
        class FakeBackend:
            def __init__(self) -> None:
                self.calls = []

            def ping(self):
                return {"status": "ok", "mode": "active"}

            def snapshot(self):
                return {"backend": {"mode": "active"}, "value": 1}

            def dispatch(self, method, params):
                self.calls.append((method, params))
                return {"backend": {"mode": "active"}, "value": params["value"]}

        backend = FakeBackend()
        service = BridgeService(backend)
        response = service.handle_line(
            json.dumps(
                {
                    "v": 1,
                    "id": "request-3",
                    "method": "app.configure",
                    "params": {"value": 7},
                }
            )
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["value"], 7)
        self.assertEqual(backend.calls, [("app.configure", {"value": 7})])

    def test_cli_round_trip_returns_one_json_line(self) -> None:
        request = json.dumps(
            {
                "v": PROTOCOL_VERSION,
                "id": "ping-1",
                "method": "system.ping",
                "params": {},
            }
        )
        completed = subprocess.run(
            [sys.executable, "-m", "tauri_bridge"],
            cwd=REPO_ROOT,
            input=request + "\n",
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )

        response = json.loads(completed.stdout.strip())
        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["status"], "ok")
        self.assertIn(response["result"]["mode"], {"active", "read_only"})


if __name__ == "__main__":
    unittest.main()
