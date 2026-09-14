import copy
import tempfile
import unittest
from pathlib import Path

from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings
from tauri_bridge.test_script_presets import normalize_script_presets


def preset():
    return {"id": "preset-one", "name": "36 A 测试", "parameters": {
        "selectedScriptId": "standard", "currentPlanMode": "range",
        "initialCurrentA": 4, "targetCurrentA": 36, "currentStepA": 2,
        "pointTimeoutS": 120, "rampDownStepA": 0.5, "rampDownIntervalS": 0.5,
        "customCurrentPoints": [],
    }}


class TestScriptPresetTests(unittest.TestCase):
    def test_create_copy_modify_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            backend = HeadlessBackend(JsonSettings(path))
            first = preset()
            backend.dispatch("app.configure", {"testScriptPresets": [first], "selectedScriptPresetId": first["id"]})
            second = copy.deepcopy(first)
            second.update(id="preset-two", name="另一组合")
            first["parameters"]["pointTimeoutS"] = 180
            backend.dispatch("app.configure", {"testScriptPresets": [first, second], "selectedScriptPresetId": second["id"]})
            restored = HeadlessBackend(JsonSettings(path)).configuration
            self.assertEqual(restored["testScriptPresets"], [first, second])
            self.assertEqual(restored["selectedScriptPresetId"], "preset-two")
            self.assertEqual(restored["selectedScriptId"], "standard")
            self.assertEqual(restored["targetCurrentA"], 20)

    def test_rejects_invalid_scripts_without_replacing_saved_library(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = HeadlessBackend(JsonSettings(Path(directory) / "settings.json"))
            backend.dispatch("app.configure", {"testScriptPresets": [preset()]})
            for key, value in (("currentStepA", 0), ("pointTimeoutS", float("nan")), ("rampDownIntervalS", -1), ("targetCurrentA", 2)):
                invalid = preset()
                invalid["parameters"][key] = value
                with self.subTest(key=key), self.assertRaises(ValueError):
                    backend.dispatch("app.configure", {"testScriptPresets": [invalid]})
                self.assertEqual(backend.configuration["testScriptPresets"], [preset()])
            with self.assertRaises(ValueError):
                backend.dispatch("app.configure", {"selectedScriptPresetId": "preset-missing"})

    def test_names_are_unique_and_aging_parameters_remain_fixed(self):
        second = preset()
        second["id"] = "preset-two"
        with self.assertRaises(ValueError):
            normalize_script_presets([preset(), second])
        aging = preset()
        aging["parameters"].update(selectedScriptId="aging", currentPlanMode="custom", customCurrentPoints=[{"currentA": 10, "dwellS": 30}])
        saved = normalize_script_presets([aging])[0]["parameters"]
        self.assertEqual(saved["initialCurrentA"], 2)
        self.assertEqual(saved["targetCurrentA"], 20)
        self.assertEqual(saved["customCurrentPoints"], [{"currentA": 20, "dwellS": 7200}])
