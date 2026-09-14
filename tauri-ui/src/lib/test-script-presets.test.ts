import assert from "node:assert/strict"
import test from "node:test"
import type { AppConfiguration } from "./backend.ts"
import { applyScriptPreset, captureScriptParameters, scriptPresetChanged, scriptPresetCompatible } from "./test-script-presets.ts"

const config = {
  sn: "keep-sn", tdkResource: "keep-port", setCurrentA: 0, powerSupplyKind: "tdk",
  selectedScriptId: "hold-points", currentPlanMode: "custom", initialCurrentA: 4,
  targetCurrentA: 36, currentStepA: 2, pointTimeoutS: 120, rampDownStepA: 0.5,
  rampDownIntervalS: 0.5, customCurrentPoints: [{ currentA: 10, dwellS: 30 }],
} as AppConfiguration

test("recall restores all script parameters, preserves hardware and sample identity, and isolates point edits", () => {
  const preset = { id: "preset-one", name: "36 A", parameters: captureScriptParameters(config) }
  const restored = applyScriptPreset({ ...config, sn: "new-sn", initialCurrentA: 1, pointTimeoutS: 99 }, preset)
  assert.equal(restored.sn, "new-sn")
  assert.equal(restored.tdkResource, "keep-port")
  assert.equal(restored.setCurrentA, 0)
  assert.equal(restored.initialCurrentA, 4)
  assert.equal(restored.pointTimeoutS, 120)
  assert.equal(restored.selectedScriptId, "hold-points")
  assert.equal(scriptPresetChanged(restored, preset), false)
  restored.customCurrentPoints[0].dwellS = 60
  assert.equal(preset.parameters.customCurrentPoints[0].dwellS, 30)
  assert.equal(scriptPresetChanged(restored, preset), true)
})

test("script selection prevents incompatible currents and AU on TDK", () => {
  const preset = { id: "preset-one", name: "36 A", parameters: captureScriptParameters(config) }
  assert.equal(scriptPresetCompatible(config, preset), true)
  assert.equal(scriptPresetCompatible({ ...config, powerSupplyKind: "ch341" }, preset), false)
  const aging = { ...preset, parameters: { ...preset.parameters, selectedScriptId: "aging", targetCurrentA: 20 } }
  assert.equal(scriptPresetCompatible(config, aging), false)
  assert.equal(scriptPresetCompatible({ ...config, powerSupplyKind: "ch341" }, aging), true)
})
