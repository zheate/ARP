import assert from "node:assert/strict"
import test from "node:test"
import { compactSpectrumAxisMaximum, suggestedSpectrumAxisMaximum } from "./shipping-report-axis.ts"

test("peaks near 25000 use a compact 30000 ceiling instead of jumping to 50000", () => {
  for (const peak of [24500, 25000, 26000]) {
    assert.equal(compactSpectrumAxisMaximum(peak), 30000)
  }
  assert.equal(compactSpectrumAxisMaximum(35000), 40000)
  assert.equal(compactSpectrumAxisMaximum(9000), 10000)
})

test("automatic ceiling covers the largest displayed spectrum and ignores higher currents", () => {
  const points = [
    { currentA: 2, spectrumPeakCounts: 9000 },
    { currentA: 16, spectrumPeakCounts: 25000 },
    { currentA: 18, spectrumPeakCounts: 22000 },
    { currentA: 20, spectrumPeakCounts: 45000 },
  ]
  assert.equal(suggestedSpectrumAxisMaximum(points, 2), 10000)
  assert.equal(suggestedSpectrumAxisMaximum(points, 18), 30000)
  assert.equal(suggestedSpectrumAxisMaximum(points, 18 - 5e-10), 30000)
  assert.equal(suggestedSpectrumAxisMaximum(points, 20), 50000)
  assert.equal(suggestedSpectrumAxisMaximum(points, Number.NaN), 50000)
})

test("invalid or empty peaks keep a valid default ceiling", () => {
  for (const peak of [0, -1, Number.NaN, Infinity]) {
    assert.equal(compactSpectrumAxisMaximum(peak), 1)
  }
  assert.equal(suggestedSpectrumAxisMaximum([], 18), 1)
  assert.equal(suggestedSpectrumAxisMaximum([
    { currentA: 2, spectrumPeakCounts: null },
    { currentA: 4, spectrumPeakCounts: Number.NaN },
    { currentA: 6, spectrumPeakCounts: 25000 },
  ], 18), 30000)
})
