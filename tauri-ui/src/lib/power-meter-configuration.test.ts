import assert from "node:assert/strict"
import test from "node:test"

import type { AppConfiguration } from "./backend.ts"
import { mergeAuthoritativePowerMeterConfiguration } from "./power-meter-configuration.ts"

const current = {
  powerMeterResource: "ASRL7::INSTR",
  powerMeterSerialNumber: "PROBE-C",
  powerMeterDriverKind: "laserpoint",
  powerMeterWavelengthNm: 980,
  softwareGain: 1.2,
  powerMeterProfileId: "profile-c",
  powerMeterProfileName: "方案 C",
  powerMeterProfileVersion: 3,
  powerMeterProfileNotes: "C draft",
  powerMeterProfileWavelengthPending: false,
  sn: "LOCAL-DRAFT",
} as AppConfiguration

test("a delayed profile response cannot overwrite a newly staged resource", () => {
  const authoritative = {
    ...current,
    powerMeterResource: "ASRL6::INSTR",
    powerMeterSerialNumber: "PROBE-B",
    powerMeterProfileId: "profile-b",
    powerMeterProfileName: "方案 B",
    powerMeterProfileVersion: 4,
    softwareGain: 0.95,
  }

  assert.equal(mergeAuthoritativePowerMeterConfiguration(current, authoritative), current)
})

test("the matching resource receives authoritative profile fields and keeps unrelated drafts", () => {
  const authoritative = {
    ...current,
    powerMeterResource: "LaserPoint | asrl7::instr | 探头 SN PROBE-C",
    powerMeterProfileVersion: 4,
    softwareGain: 1.25,
    sn: "PERSISTED-SN",
  }

  const merged = mergeAuthoritativePowerMeterConfiguration(current, authoritative)
  assert.equal(merged.powerMeterProfileVersion, 4)
  assert.equal(merged.softwareGain, 1.25)
  assert.equal(merged.sn, "LOCAL-DRAFT")
})
