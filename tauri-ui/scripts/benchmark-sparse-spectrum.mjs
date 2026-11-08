// node --expose-gc --experimental-strip-types tauri-ui/scripts/benchmark-sparse-spectrum.mjs
// Frozen synthetic input; no browser, device backend or product data writes.
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { performance } from 'node:perf_hooks'
import { spectrumChartRows } from '../src/lib/data-browser.ts'
import { sparseChartIndex } from '../src/lib/sparse-chart-index.ts'

// Original implementation at 9ca676a9; kept here only for differential timing.
const reference = series => series.flatMap((spectrum, index) => spectrum ? spectrum.wavelengthNm.flatMap((wavelength, sample) => {
  const intensity = spectrum.intensity[sample]
  return Number.isFinite(wavelength) && Number.isFinite(intensity)
    ? [{ wavelengthNm: wavelength, [`series${index}`]: intensity }] : []
}) : []).sort((a, b) => a.wavelengthNm - b.wavelengthNm)
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
function fingerprint(rows) {
  const values = Buffer.allocUnsafe(rows.length * 20)
  rows.forEach((row, index) => {
    const key = Object.keys(row).find(key => key !== 'wavelengthNm')
    values.writeDoubleLE(row.wavelengthNm, index * 20)
    values.writeDoubleLE(row[key], index * 20 + 8)
    values.writeUInt32LE(Number(key.slice(6)), index * 20 + 16)
  })
  return createHash('sha256').update(values).digest('hex')
}
assert.equal(typeof global.gc, 'function', 'use --expose-gc')
function measureIndex(rows, keys) {
  // A separate activation prevents the previous loop iteration's index from
  // remaining in a reused local slot during the next retained-heap probe.
  global.gc()
  const startHeap = process.memoryUsage().heapUsed
  const started = performance.now()
  const indexes = sparseChartIndex(rows, keys)
  const indexMs = performance.now() - started
  global.gc()
  const indexHeap = process.memoryUsage().heapUsed - startHeap
  assert.equal([...indexes.values()].reduce((n, row) => n + row.length, 0), rows.length)
  return { index_build_ms_single_sample: indexMs, retained_index_heap_bytes_single_probe: indexHeap }
}
const report = []
for (const currents of [1, 12, 24]) {
  const input = Array.from({ length: currents }, (_, current) => ({
    wavelengthNm: Array.from({ length: 8192 }, (_, i) => 970 + i * 12 / 8191),
    intensity: Array.from({ length: 8192 }, (_, i) => i === 0 ? -0 : 100 + current + Math.sin(i / 71)),
  }))
  const expected = fingerprint(reference(input))
  const samples = [[], []]
  for (let i = 0; i < 15; i++) {
    for (const version of i % 2 ? [1, 0] : [0, 1]) {
      global.gc()
      const started = performance.now()
      const actual = [reference, spectrumChartRows][version](input)
      const elapsed = performance.now() - started
      assert.equal(fingerprint(actual), expected)
      if (i >= 3) samples[version].push(elapsed)
    }
  }
  const rows = spectrumChartRows(input)
  const indexStats = measureIndex(rows, input.map((_, i) => `series${i}`))
  report.push({ currents, pixels: 8192, rows: rows.length, iterations_per_version: 12,
    before_median_ms: median(samples[0]), after_median_ms: median(samples[1]),
    original_value_bits_and_row_order_sha256: expected, bit_exact: true,
    ...indexStats })
}
console.log(JSON.stringify(report, null, 2))
