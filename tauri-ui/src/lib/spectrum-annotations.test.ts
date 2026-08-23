import assert from "node:assert/strict"
import test from "node:test"

import { spectrumFwhmAnnotation, spectrumPeakAnnotations, spectrumPeakLegend } from "./spectrum-annotations.ts"

test("spectrum peak markers point to peak wavelength instead of centroid", () => {
  const annotations = spectrumPeakAnnotations([
    { label: "P1", peakWavelengthNm: 976.18, peakIntensity: 12_400 },
  ])

  assert.deepEqual(annotations, [{ label: "P1", x: 976.18, y: 12_400, color: "#3b82f6" }])
})

test("spectrum peak legend only includes detected peaks with wavelength values", () => {
  assert.deepEqual(spectrumPeakLegend([
    { label: "P1", peakWavelengthNm: 976.18, peakIntensity: 12_400 },
  ]), [
    { label: "Peak 1", color: "#3b82f6", value: "976.180 nm" },
  ])
  assert.deepEqual(spectrumPeakLegend([
    { label: "P1", peakWavelengthNm: 976.18, peakIntensity: 12_400 },
    { label: "P3", peakWavelengthNm: 974.02, peakIntensity: 900 },
  ]), [
    { label: "Peak 1", color: "#3b82f6", value: "976.180 nm" },
    { label: "Peak 3", color: "#9ca3af", value: "974.020 nm" },
  ])
  assert.deepEqual(spectrumPeakLegend([]), [])
})

test("FWHM annotation interpolates the displayed curve's half-maximum crossings", () => {
  const [annotation] = spectrumFwhmAnnotation(
    [0, 1, 2, 3, 4],
    [0, 25, 100, 60, 0],
  )

  assert.equal(annotation.label, "FWHM 1.833 nm")
  assert.ok(Math.abs(annotation.startX - 4 / 3) < 1e-12)
  assert.ok(Math.abs(annotation.endX - 19 / 6) < 1e-12)
  assert.equal(annotation.y, 50)
  assert.equal(annotation.color, "#eab308")
  assert.deepEqual(spectrumFwhmAnnotation([0, 1, 2], [60, 100, 60]), [])
})
