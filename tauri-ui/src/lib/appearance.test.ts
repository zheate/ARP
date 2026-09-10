import assert from "node:assert/strict"
import test from "node:test"
import { changedDigitIndexes, chartTrailBoundary, DEFAULT_APPEARANCE, normalizeAppearance } from "./appearance.ts"

test("invalid stored appearance falls back safely and numeric ranges stay bounded", () => {
  for (const value of [null, [], "dark", undefined]) assert.deepEqual(normalizeAppearance(value), DEFAULT_APPEARANCE)
  assert.deepEqual(normalizeAppearance({ canvas: "unknown", density: "huge", gridOpacity: 2, lineWidth: -1, trailSeconds: Infinity, trailEnabled: "false" }), {
    ...DEFAULT_APPEARANCE, gridOpacity: 0.8, lineWidth: 1,
  })
  assert.equal(DEFAULT_APPEARANCE.highlightChanges, false)
})

test("stored appearance preserves independent valid choices", () => {
  const preferences = { canvas: "engineering", density: "compact", gridOpacity: 0.4, lineWidth: 2.5, trailSeconds: 10, trailEnabled: false, highlightChanges: true }
  assert.deepEqual(normalizeAppearance(JSON.parse(JSON.stringify(preferences))), preferences)
})

test("trail uses sample time and clamps to the visible domain", () => {
  assert.equal(chartTrailBoundary(60, 5, [0, 60]), 55)
  assert.equal(chartTrailBoundary(52, 5, [0, 60]), 47)
  assert.equal(chartTrailBoundary(3, 5, [0, 60]), 0)
  assert.equal(chartTrailBoundary(null, 5, [0, 60]), null)
  assert.equal(chartTrailBoundary(NaN, 5, [0, 60]), null)
  assert.equal(chartTrailBoundary(60, 0, [0, 60]), null)
})

test("readout highlights only changed digits without moving punctuation or entering from missing data", () => {
  assert.deepEqual(changedDigitIndexes("0.860", "0.861"), [4])
  assert.deepEqual(changedDigitIndexes("0.899", "0.900"), [2, 3, 4])
  assert.deepEqual(changedDigitIndexes("0.860", "0.860"), [])
  assert.deepEqual(changedDigitIndexes("—", "0.861"), [])
  assert.deepEqual(changedDigitIndexes("9.999", "10.000"), [])
})
