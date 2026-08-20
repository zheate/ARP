import assert from "node:assert/strict"
import test from "node:test"

import { spectrumPeakAnnotations } from "./spectrum-annotations.ts"

test("spectrum peak markers point to peak wavelength instead of centroid", () => {
  const annotations = spectrumPeakAnnotations([
    { label: "P1", centroidNm: 976.24, peakWavelengthNm: 976.18, peakIntensity: 12_400 },
  ])

  assert.deepEqual(annotations, [{ label: "P1", x: 976.18, y: 12_400 }])
})
