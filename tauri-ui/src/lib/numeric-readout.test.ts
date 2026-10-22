import assert from "node:assert/strict"
import test from "node:test"
import { numberFlowReadout } from "./numeric-readout.ts"

test("animated readouts preserve precision, trailing zeros, signs and carry boundaries", () => {
  for (const text of ["0.860", "0.861", "976.200", "96.85", "9.999", "10.000", "99.999", "100.000", "-0.000", "-12.340", "0", "1000"]) {
    const readout = numberFlowReadout(text)
    assert.ok(readout, text)
    assert.equal(new Intl.NumberFormat("en-US", readout.format).format(readout.value), text)
    assert.equal(readout.format.useGrouping, false)
  }
})

test("missing, nondecimal and precision-losing readouts stay static and unchanged", () => {
  for (const text of ["", "—", "--", "NaN", "Infinity", "1.2e-6", "1.2E+6", "01.20", "+1.20", " 1.20 ", "1,234.50", "0.860 W", "9007199254740993", "0.1234567890123456789", "0.000000000000000000001"]) {
    assert.equal(numberFlowReadout(text), null, text)
  }
})

test("instrument current keeps two integer places and two decimal places without losing larger values", () => {
  for (const [text, expected] of [["0.00", "00.00"], ["9.90", "09.90"], ["10.00", "10.00"], ["14.00", "14.00"], ["99.99", "99.99"], ["100.01", "100.01"], ["-1.23", "-01.23"]]) {
    const readout = numberFlowReadout(text, 2)
    assert.ok(readout, text)
    assert.equal(new Intl.NumberFormat("en-US", readout.format).format(readout.value), expected)
  }
  assert.equal(numberFlowReadout("--", 2), null)
})
