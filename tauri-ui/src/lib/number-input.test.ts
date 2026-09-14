import test from "node:test"
import assert from "node:assert/strict"
import { normalizeNumericInput, stepNumericValue } from "./number-input.ts"

test("normalizeNumericInput converts Chinese punctuation to decimal point", () => {
  assert.equal(normalizeNumericInput("0。935"), "0.935")
  assert.equal(normalizeNumericInput("0．935"), "0.935")
  assert.equal(normalizeNumericInput("0，935"), "0.935")
  assert.equal(normalizeNumericInput("。"), "0.")
  assert.equal(normalizeNumericInput("。935"), "0.935")
})

test("normalizeNumericInput auto-prefixes leading zero for dot inputs", () => {
  assert.equal(normalizeNumericInput(".935"), "0.935")
  assert.equal(normalizeNumericInput("."), "0.")
  assert.equal(normalizeNumericInput("-.5"), "-0.5")
})

test("normalizeNumericInput strips invalid characters and redundant dots", () => {
  assert.equal(normalizeNumericInput("abc"), "")
  assert.equal(normalizeNumericInput("0.9.3.5"), "0.935")
  assert.equal(normalizeNumericInput("00935"), "935")
  assert.equal(normalizeNumericInput("0"), "0")
  assert.equal(normalizeNumericInput("0.0"), "0.0")
})

test("stepNumericValue preserves decimal precision and respects min/max", () => {
  assert.equal(stepNumericValue("0.935", "0.01", 1), 0.945)
  assert.equal(stepNumericValue("0.935", "0.01", -1), 0.925)
  assert.equal(stepNumericValue("12", "0.1", 1), 12.1)
  assert.equal(stepNumericValue("0", "1", -1, 0, 10), 0)
  assert.equal(stepNumericValue("10", "1", 1, 0, 10), 10)
})
