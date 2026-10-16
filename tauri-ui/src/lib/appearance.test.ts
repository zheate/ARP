import assert from "node:assert/strict"
import test from "node:test"
import { chartTrailBoundary, DEFAULT_APPEARANCE, isAnimatedLatestPointMarker, LATEST_POINT_MARKER_OPTIONS, latestPointMarkerSize, normalizeAppearance, normalizeLatestPointMarkerStyle } from "./appearance.ts"

test("stored endpoint styles restore both approved characters and all existing choices", () => {
  const existing = ["point", "paper-plane", "paw", "comet", "ecg", "flappy-bird"]
  for (const id of [...existing, "bee", "dinosaur", "green-dinosaur"]) {
    assert.equal(normalizeLatestPointMarkerStyle(JSON.parse(JSON.stringify(id))), id)
    assert.ok(LATEST_POINT_MARKER_OPTIONS.some((option) => option.id === id))
  }
  for (const invalid of [null, undefined, "fish", "gecko", "dino", {}, 1]) {
    assert.equal(normalizeLatestPointMarkerStyle(invalid), "point")
  }
})

test("only animated endpoint styles opt into shared curve reveal", () => {
  for (const option of LATEST_POINT_MARKER_OPTIONS) {
    assert.equal(isAnimatedLatestPointMarker(option.id), ["flappy-bird", "bee", "dinosaur", "green-dinosaur"].includes(option.id))
  }
  assert.equal(isAnimatedLatestPointMarker(undefined), false)
  assert.equal(latestPointMarkerSize("bee"), 32)
  assert.equal(latestPointMarkerSize("dinosaur"), 32)
  assert.equal(latestPointMarkerSize("green-dinosaur"), 32)
  assert.equal(latestPointMarkerSize("flappy-bird"), 24)
})

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
