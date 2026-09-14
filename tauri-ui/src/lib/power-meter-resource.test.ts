import assert from "node:assert/strict"
import test from "node:test"

import { powerMeterCoefficientLibraryForResource, powerMeterIdentityForResource, powerMeterProfileForResource, powerMeterResourceValue } from "./power-meter-resource.ts"

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

test("finds the coefficient library for a staged resource instead of reusing the live probe", () => {
  const liveLibrary = { driverKind: "caihuang", serialNumber: "LIVE", selectedProfileId: "live", selectedVersion: 1, profiles: [] }
  const stagedLibrary = { driverKind: "laserpoint", serialNumber: "STAGED", selectedProfileId: "staged", selectedVersion: 2, profiles: [] }
  const device = {
    state: "connected" as const,
    label: "功率计",
    detail: "",
    coefficientLibrary: liveLibrary,
    resourceProfiles: [{
      resource: "ASRL6::INSTR",
      driverKind: "laserpoint",
      serialNumber: "STAGED",
      softwareGain: 0.97,
      coefficientLibrary: stagedLibrary,
    }],
  }

  assert.equal(powerMeterCoefficientLibraryForResource(device, "ASRL6::INSTR", "ASRL7::INSTR"), stagedLibrary)
  assert.equal(powerMeterCoefficientLibraryForResource(device, "ASRL7::INSTR", "ASRL7::INSTR"), liveLibrary)
  assert.equal(powerMeterCoefficientLibraryForResource(device, "ASRL8::INSTR", "ASRL7::INSTR"), null)
})

test("an explicitly detected resource without a probe serial cannot inherit the previous probe identity", () => {
  const device = {
    state: "connected" as const,
    label: "功率计",
    detail: "",
    coefficientLibrary: { driverKind: "caihuang", serialNumber: "PREVIOUS-PROBE", selectedProfileId: "old", selectedVersion: 1, profiles: [] },
    resourceProfiles: [{ resource: "ASRL8::INSTR", driverKind: "unknown", serialNumber: "", softwareGain: 1 }],
  }

  assert.deepEqual(
    powerMeterIdentityForResource(
      device,
      "ASRL8::INSTR",
      "ASRL7::INSTR",
      { driverKind: "caihuang", serialNumber: "PREVIOUS-PROBE" },
    ),
    { driverKind: "unknown", serialNumber: "" },
  )
  assert.equal(
    powerMeterIdentityForResource(
      device,
      "ASRL8::INSTR",
      "ASRL8::INSTR",
      { driverKind: "caihuang", serialNumber: "PREVIOUS-PROBE" },
    ).serialNumber,
    "",
  )
})
