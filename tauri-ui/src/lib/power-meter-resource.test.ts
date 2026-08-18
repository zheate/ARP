import assert from "node:assert/strict"
import test from "node:test"

import { powerMeterResourceValue } from "./power-meter-resource.ts"

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
