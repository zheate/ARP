import assert from "node:assert/strict"
import test from "node:test"

import { powerMeterProfileForResource, powerMeterResourceValue } from "./power-meter-resource.ts"

test("extracts an Ophir resource embedded in a display label", () => {
  assert.equal(
    powerMeterResourceValue("Ophir USB | OPHIR::USB::3103403 | USB SN 3103403"),
    "OPHIR::USB::3103403",
  )
})

test("normalizes raw Ophir and serial VISA resources", () => {
  assert.equal(powerMeterResourceValue(" ophir::usb::abc123 "), "OPHIR::USB::ABC123")
  assert.equal(powerMeterResourceValue("Coherent | asrl10::instr | SN 42"), "ASRL10::INSTR")
})

test("preserves an unknown resource after trimming whitespace", () => {
  assert.equal(powerMeterResourceValue(" custom-resource "), "custom-resource")
})

test("finds the serial-bound gain for a selected display resource", () => {
  const profiles = [
    { resource: "ASRL7::INSTR", driverKind: "caihuang", serialNumber: "W500-CB-40", softwareGain: 1.04 },
    { resource: "ASRL6::INSTR", driverKind: "laserpoint", serialNumber: "222483", softwareGain: 0.97 },
  ]

  assert.deepEqual(
    powerMeterProfileForResource(profiles, "LaserPoint | ASRL6::INSTR | 探头 SN 222483"),
    profiles[1],
  )
})
