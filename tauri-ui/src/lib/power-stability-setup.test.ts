import assert from "node:assert/strict"
import test from "node:test"
import type { AppConfiguration } from "./backend"
import { powerStabilityResponseConfirmed, powerStabilityTolerance } from "./power-stability-setup.ts"

const saved = {
  powerStabilityTolerancePreset: "custom", powerStabilityAbsoluteDriftW: 0.35,
  powerStabilityRelativeDriftPercent: 0, powerStabilityReferenceS: 30,
  powerStabilityExtraNoisePercent: 0.1, powerStabilityParametersConfirmed: true,
  powerMeterSerialNumber: "probe-A", powerStabilityResponseS: 2,
} as AppConfiguration

test("standard tolerance leaves the saved custom values intact", () => {
  const standard = { ...saved, powerStabilityTolerancePreset: "standard" } as AppConfiguration
  assert.deepEqual(powerStabilityTolerance(standard), { absoluteW: 0, relativePercent: 0.2, referenceS: 20, extraNoisePercent: 0 })
  assert.deepEqual(powerStabilityTolerance(saved), { absoluteW: 0.35, relativePercent: 0, referenceS: 30, extraNoisePercent: 0.1 })
  assert.equal(standard.powerStabilityAbsoluteDriftW, 0.35)
})

test("switching tolerance preserves response confirmation but unsaved device edits do not", () => {
  assert.equal(powerStabilityResponseConfirmed({ ...saved, powerStabilityTolerancePreset: "standard" }, saved, true), true)
  for (const change of [{ powerMeterSerialNumber: "probe-B" }, { powerStabilityResponseS: 3 }, { softwareGain: 2 },
    { powerStabilityParametersConfirmed: false }, { powerStabilityQuantizationW: 0.1 }]) {
    assert.equal(powerStabilityResponseConfirmed({ ...saved, ...change }, saved, true), false)
  }
  assert.equal(powerStabilityResponseConfirmed(saved, saved, false), false)
  assert.equal(powerStabilityResponseConfirmed(saved, null, true), false)
})
