import assert from "node:assert/strict"
import test from "node:test"
import { comparisonRows, dateBounds, numberText, powerChartRows, requestGuard, type DataDetail, type DataPoint } from "./data-browser.ts"

function point(currentA: number, powerW: number | null, rowId = 1): DataPoint {
  return { rowId, currentA, powerW, voltageV: null, efficiencyPercent: null, peakWavelengthNm: null, centroidNm: null, fwhmNm: null, pibPercent: null, smsrDb: null }
}
test("comparison aligns shared measured currents and computes signed difference", () => {
  const rows = comparisonRows([point(2, 20), point(4, 40)], [point(2.00000001, 18), point(6, 60)])
  assert.equal(rows.length, 1)
  assert.equal(rows[0].deltaW, -2)
  assert.equal(rows[0].deltaPercent, -10)
  assert.equal(comparisonRows([point(2, 0)], [point(2, 5)])[0].deltaPercent, null)
})
test("duplicate normalized currents are explicit and invalid values are not paired", () => {
  const rows = comparisonRows([point(2, 10), point(2.00000001, 11)], [point(2, 12)])
  assert.equal(rows[0].ambiguous, true)
  assert.equal(rows[0].deltaW, null)
  assert.deepEqual(comparisonRows([point(2, null)], [point(2, 0)]), [])
  assert.deepEqual(comparisonRows([point(2, 10)], [point(3, 12)]), [])
})
test("chart retains both original curves including duplicate currents", () => {
  const records = [{ points: [point(4, 20), point(2, 10), point(2, 11)] }, { points: [point(3, 18), point(5, null)] }] as DataDetail[]
  const rows = powerChartRows(records)
  assert.deepEqual(rows.map((row) => row.currentA), [2, 2, 3, 4])
  assert.equal(rows[2].base, null)
  assert.equal(rows[2].other, 18)
})
test("late requests and invalidated page owners cannot publish results", () => {
  const guard = requestGuard(), first = guard.next(), second = guard.next()
  assert.equal(guard.current(first), false)
  assert.equal(guard.current(second), true)
  guard.invalidate()
  assert.equal(guard.current(second), false)
})
test("dates use local natural days with an exclusive end", () => {
  const now = new Date(2026, 8, 10, 14)
  assert.deepEqual(dateBounds("today", "", "", now), { fromMs: new Date(2026, 8, 10).getTime(), toMs: new Date(2026, 8, 11).getTime() })
  assert.equal(dateBounds("week", "", "", now).fromMs, new Date(2026, 8, 4).getTime())
  assert.deepEqual(dateBounds("custom", "2026-09-10", "2026-09-10"), dateBounds("today", "", "", now))
  assert.throws(() => dateBounds("custom", "2026-09-11", "2026-09-10"))
  assert.throws(() => dateBounds("custom", "2026-02-31", "2026-03-01"))
})
test("missing values remain missing and negative zero is display-only", () => {
  assert.equal(numberText(null), "--")
  assert.equal(numberText(Number.NaN), "--")
  assert.equal(numberText(-.00001), "0.000")
  assert.equal(numberText(-.001), "-0.001")
})
