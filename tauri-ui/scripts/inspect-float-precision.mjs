// Replay the production data-browser calculations on Python and Rust wire values.
// Node's JSON parser is used only as the receiving JS engine; every original
// float is checked against Python's independent little-endian binary64 oracle.
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { DATA_METRICS, cursorDifference, extractSummaryMetrics, fitMetricRange,
  measuredCurrentMatch, numberText, spectrumChartRows } from '../src/lib/data-browser.ts'

const [fixtures, directory] = process.argv.slice(2)
assert(fixtures && directory, 'provide fixture and Rust replay directories')
const read = (path) => JSON.parse(readFileSync(path, 'utf8'))
const bits = (value) => { const b = Buffer.allocUnsafe(8); b.writeDoubleLE(value); return b.readBigUInt64LE() }
const at = (value, path) => path.split('/').slice(1).reduce((v, key) => v[key.replaceAll('~1', '/').replaceAll('~0', '~')], value)
const reports = []
for (const entry of read(join(fixtures, 'cases.json'))) {
  const name = entry.name
  const original = read(join(fixtures, `${name}.input.json`))
  const forwarded = read(join(directory, `${name}.forwarded.json`)).message.snapshot
  if (process.env.ARP_REQUIRE_EXACT === '1') assert.deepEqual(forwarded, original.result, name)
  const oracle = readFileSync(join(fixtures, `${name}.bits.bin`))
  const pointers = read(join(fixtures, `${name}.pointers.json`))
  const categories = {}
  let mismatches = 0
  for (const [i, pointer] of pointers.entries()) {
    const expected = at(original, pointer)
    assert.equal(bits(expected), oracle.readBigUInt64LE(i * 8), `Python -> JS: ${name}${pointer}`)
    const actual = at({ result: forwarded }, pointer)
    if (bits(actual) === bits(expected)) continue
    mismatches++
    const category = pointer.includes('/wavelengthNm/') ? 'wavelength_nm'
      : pointer.includes('/intensity/') ? 'intensity' : 'metadata_and_metrics'
    const row = categories[category] ??= { count: 0, max_absolute_error: 0 }
    row.count++
    row.max_absolute_error = Math.max(row.max_absolute_error, Math.abs(actual - expected))
  }
  const report = { name, original_python_to_js_bit_mismatches: 0,
    forwarded_bit_mismatches: mismatches, categories }
  if (entry.source) {
    const before = original.result, after = forwarded
    const fields = ['currentA', ...DATA_METRICS.map(({ key }) => key)]
    let formattedChanges = 0, groupingChanges = 0, downstreamNumericChanges = 0, downstreamMaxError = 0
    assert.equal(after.points.length, before.points.length)
    for (const [i, point] of before.points.entries()) {
      for (const key of fields) {
        if (numberText(point[key]) !== numberText(after.points[i][key])) formattedChanges++
      }
      const a = measuredCurrentMatch(before.points, point.currentA)
      const b = measuredCurrentMatch(after.points, point.currentA)
      if (a.status !== b.status || a.item?.rowId !== b.item?.rowId) groupingChanges++
    }
    const a = extractSummaryMetrics(before.points), b = extractSummaryMetrics(after.points)
    assert.equal(a.maxPowerPoint?.rowId, b.maxPowerPoint?.rowId)
    assert.equal(a.maxEfficiencyPoint?.rowId, b.maxEfficiencyPoint?.rowId)
    const compareNumbers = (x, y) => {
      if (typeof x === 'number' && typeof y === 'number' && !Object.is(x, y)) {
        downstreamNumericChanges++
        downstreamMaxError = Math.max(downstreamMaxError, Math.abs(x - y))
      } else if (x && y && typeof x === 'object' && typeof y === 'object') {
        assert.deepEqual(Object.keys(y), Object.keys(x))
        for (const key of Object.keys(x)) compareNumbers(x[key], y[key])
      } else if (typeof x !== 'number') assert.deepEqual(y, x)
    }
    for (const { key } of DATA_METRICS) {
      compareNumbers(cursorDifference(before.points[0], before.points.at(-1), key),
        cursorDifference(after.points[0], after.points.at(-1), key))
      compareNumbers(fitMetricRange(before.points, key, before.points[0]?.currentA, before.points.at(-1)?.currentA),
        fitMetricRange(after.points, key, before.points[0]?.currentA, before.points.at(-1)?.currentA))
    }
    const spectra = before.spectra ?? []
    const peakIndex = (s) => s.intensity.reduce((best, y, i) => y > s.intensity[best] ? i : best, 0)
    assert.equal((after.spectra ?? []).length, spectra.length)
    for (const [i, spectrum] of spectra.entries()) {
      assert.equal(after.spectra[i].wavelengthNm.length, spectrum.wavelengthNm.length)
      assert.equal(after.spectra[i].intensity.length, spectrum.intensity.length)
      assert.equal(peakIndex(after.spectra[i]), peakIndex(spectrum))
    }
    assert.equal(spectrumChartRows(after.spectra ?? []).length, spectrumChartRows(spectra).length)
    Object.assign(report, { point_count: before.points.length, spectrum_count: spectra.length,
      spectrum_samples: spectra.reduce((n, s) => n + s.intensity.length, 0),
      formatted_metric_changes_3_decimals: formattedChanges, current_match_changes: groupingChanges,
      max_power_and_efficiency_point_changes: 0, spectrum_peak_index_changes: 0,
      cursor_and_fit_numeric_changes: downstreamNumericChanges, cursor_and_fit_max_absolute_error: downstreamMaxError })
  }
  reports.push(report)
}
writeFileSync(join(directory, 'javascript.json'), JSON.stringify({ cases: reports }, null, 2) + '\n')
console.log(JSON.stringify(reports, null, 2))
if (process.env.ARP_REQUIRE_EXACT === '1') {
  assert.equal(reports.reduce((sum, row) => sum + row.forwarded_bit_mismatches, 0), 0)
}
