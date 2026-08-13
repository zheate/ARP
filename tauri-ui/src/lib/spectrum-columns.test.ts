import assert from "node:assert/strict"
import test from "node:test"

import { decodeColumnarSpectrum, mergeSpectrumColumns } from "./spectrum-columns.ts"

test("columnar spectrum preserves every value and its order", () => {
  const wavelengthNm = Array.from({ length: 8000 }, (_, index) => 950 + index * 0.01)
  const intensity = Array.from({ length: 8000 }, (_, index) => Number((Math.sin(index / 19) * 1000).toPrecision(15)))
  const decoded = decodeColumnarSpectrum({ wavelengthNm, intensity })

  assert.deepEqual(decoded.wavelengthNm, wavelengthNm)
  assert.deepEqual(decoded.intensity, intensity)
})

test("columnar spectrum rejects legacy rows and mismatched axes", () => {
  assert.throws(
    () => decodeColumnarSpectrum([{ wavelengthNm: 976, intensity: 1000 }]),
    /列式数组协议/,
  )
  assert.throws(
    () => decodeColumnarSpectrum({ wavelengthNm: [975, 976], intensity: [1000] }),
    /长度不一致/,
  )
  assert.throws(
    () => decodeColumnarSpectrum({ wavelengthNm: [975], intensity: [1000], points: [] }),
    /只能包含/,
  )
  assert.throws(
    () => decodeColumnarSpectrum({ wavelengthNm: [975], intensity: [Number.NaN] }),
    /intensity/,
  )
})

test("columnar spectrum accepts grid-cached frames without the wavelength axis", () => {
  const decoded = decodeColumnarSpectrum({ gridRevision: 4, intensity: [1, 2, 3] })

  assert.equal(decoded.wavelengthNm, undefined)
  assert.deepEqual(decoded.intensity, [1, 2, 3])
  assert.equal(decoded.gridRevision, 4)

  assert.throws(
    () => decodeColumnarSpectrum({ intensity: [1, 2, 3] }),
    /gridRevision/,
  )
  assert.throws(
    () => decodeColumnarSpectrum({ gridRevision: 4, intensity: [1, Number.NaN] }),
    /intensity/,
  )
})

test("mergeSpectrumColumns reuses the cached grid only when the revision matches", () => {
  const previous = { wavelengthNm: [940, 941, 942], intensity: [10, 20, 30], gridRevision: 3 }

  const cached = mergeSpectrumColumns(previous, { gridRevision: 3, intensity: [11, 21, 31] })
  assert.deepEqual(cached, { wavelengthNm: [940, 941, 942], intensity: [11, 21, 31], gridRevision: 3 })

  const mismatch = mergeSpectrumColumns(previous, { gridRevision: 4, intensity: [11, 21, 31] })
  assert.deepEqual(mismatch, { wavelengthNm: [], intensity: [11, 21, 31], gridRevision: 4 })

  const full = mergeSpectrumColumns(previous, { wavelengthNm: [950, 951], intensity: [1, 2], gridRevision: 4 })
  assert.deepEqual(full, { wavelengthNm: [950, 951], intensity: [1, 2], gridRevision: 4 })

  const empty = mergeSpectrumColumns(undefined, undefined)
  assert.deepEqual(empty, { wavelengthNm: [], intensity: [] })
})
