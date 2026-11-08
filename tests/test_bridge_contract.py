from __future__ import annotations
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from tauri_bridge.errors import CommandRejected
from tauri_bridge.protocol import PROTOCOL_VERSION
from tauri_bridge.service import BridgeService

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "bridge-contract.json").read_text())

class BridgeContractTests(unittest.TestCase):
    def test_shared_handshake(self):
        service = BridgeService(SimpleNamespace(ping=lambda: {"status": "ok", "mode": "active"}))
        response = service.handle_line(json.dumps(FIXTURE["pingRequest"]))
        self.assertEqual(response["v"], FIXTURE["version"])
        self.assertEqual(response["id"], FIXTURE["pingResponse"]["id"])
        for key, value in FIXTURE["pingResponse"]["result"].items():
            self.assertEqual(response["result"][key], value)

    def test_explicit_failure_keeps_code_and_runtime(self):
        backend = SimpleNamespace(runtime_id="contract-runtime", dispatch=Mock(side_effect=CommandRejected("device_not_connected", "请先连接电源控制器")))
        request = {"v": PROTOCOL_VERSION, "id": "contract-2", "method": "powerSupply.setCurrent", "params": {"currentA": 1}}
        response = BridgeService(backend).handle_line(json.dumps(request))
        self.assertEqual(response, FIXTURE["errorResponse"])
        backend.dispatch.assert_called_once()

    def test_incompatible_version_never_dispatches(self):
        backend = SimpleNamespace(dispatch=Mock())
        result = BridgeService(backend).handle_line(json.dumps({**FIXTURE["pingRequest"], "v": 1}))
        self.assertEqual(result["error"]["code"], "unsupported_version")
        backend.dispatch.assert_not_called()
