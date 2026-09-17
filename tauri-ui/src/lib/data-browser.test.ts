import assert from "node:assert/strict"
import test from "node:test"
import {
  comparisonRows,
  dateBounds,
  extractComparisonSummary,
  extractSummaryMetrics,
  flowLabel,
  numberText,
  powerChartRows,
  requestGuard,
  timeText,
  type DataDetail,
  type DataPoint,
} from "./data-browser.ts"

function point(currentA: number, powerW: number | null, rowId = 1, extra?: Partial<DataPoint>): DataPoint {
  return {
    rowId,
    currentA,
    powerW,
    voltageV: null,
    efficiencyPercent: null,
    peakWavelengthNm: null,
    centroidNm: null,
    fwhmNm: null,
    pibPercent: null,
    smsrDb: null,
    ...extra,
  }
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
  assert.equal(numberText(null), "\u2014")
  assert.equal(numberText(Number.NaN), "\u2014")
  assert.equal(numberText(undefined), "\u2014")
  assert.equal(numberText(-.00001), "0.000")
  assert.equal(numberText(-.001), "-0.001")
})

test("a record without a status or timestamp never renders undefined placeholders", () => {
  assert.equal(flowLabel(undefined), "完成情况未知")
  assert.equal(flowLabel(""), "完成情况未知")
  assert.equal(flowLabel("completed"), "已完成")
  assert.equal(flowLabel("brand_new_status"), "未知状态（brand_new_status）")
  assert.equal(timeText(undefined), "时间未记录")
  assert.equal(timeText(null), "时间未记录")
  assert.equal(timeText(Number.NaN), "时间未记录")
  assert.equal(timeText(1789002000000).length, 19)
  assert.match(timeText(1789002000000), /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/)
})

test("extractSummaryMetrics finds maximum power point, peak efficiency, and primary wavelength", () => {
  assert.deepEqual(extractSummaryMetrics([]), {
    maxPowerPoint: null,
    maxEfficiencyPoint: null,
    primaryWavelengthNm: null,
  })

  const points: DataPoint[] = [
    point(1, 0, 1, { voltageV: 1.5, efficiencyPercent: 0 }),
    point(2, 10, 2, { voltageV: 2.0, efficiencyPercent: 50, peakWavelengthNm: 976.2 }),
    point(4, 25, 3, { voltageV: 2.5, efficiencyPercent: 48, peakWavelengthNm: 976.8 }),
  ]
  const summary = extractSummaryMetrics(points)
  assert.equal(summary.maxPowerPoint?.currentA, 4)
  assert.equal(summary.maxPowerPoint?.powerW, 25)
  assert.equal(summary.maxEfficiencyPoint?.currentA, 2)
  assert.equal(summary.maxEfficiencyPoint?.efficiencyPercent, 50)
  assert.equal(summary.primaryWavelengthNm, 976.8)
})

test("extractComparisonSummary calculates delta Pmax, percentage, and shared point count", () => {
  const base = [
    point(2, 10, 1),
    point(4, 20, 2),
  ]
  const other = [
    point(2, 9, 1),
    point(4, 18, 2),
    point(6, 25, 3),
  ]
  const summary = extractComparisonSummary(base, other)
  assert.equal(summary.baseMaxPowerW, 20)
  assert.equal(summary.baseMaxCurrentA, 4)
  assert.equal(summary.otherMaxPowerW, 25)
  assert.equal(summary.otherMaxCurrentA, 6)
  assert.equal(summary.deltaMaxPowerW, 5)
  assert.equal(summary.deltaMaxPowerPercent, 25)
  assert.equal(summary.sharedPointCount, 2)
})

test("five curves preserve each selected parameter even when power is missing", async () => {
  const { metricChartRows } = await import("./data-browser.ts")
  const details = Array.from({ length: 5 }, (_, index) => ({ points: [point(index + 1, null, 1, { voltageV: 10 + index, efficiencyPercent: 0 })] })) as DataDetail[]
  const rows = metricChartRows(details, "voltageV")
  assert.equal(rows.length, 5)
  rows.forEach((row, index) => assert.equal(row[`series${index}`], 10 + index))
  assert.equal(metricChartRows(details, "powerW").length, 0)
  assert.equal(metricChartRows(details, "efficiencyPercent")[0].series0, 0)
})

test("parameter deltas never interpolate, invent missing values or match duplicate currents", async () => {
  const { metricComparisonRows } = await import("./data-browser.ts")
  const details = [
    { points: [point(2, 10, 1, { efficiencyPercent: 40 }), point(4, 12, 2, { efficiencyPercent: 50 })] },
    { points: [point(2, 11, 1, { efficiencyPercent: 45 }), point(3, 13, 2, { efficiencyPercent: 47 }), point(4, 12, 3, { efficiencyPercent: 0 }), point(4, null)] },
    { points: [point(2, null, 1, { efficiencyPercent: 0 })] },
  ] as DataDetail[]
  const rows = metricComparisonRows(details, "efficiencyPercent")
  assert.deepEqual(rows[0].deltas, [0, 5, -40])
  assert.deepEqual(rows[1].values, [null, 47, null])
  assert.deepEqual(rows[1].deltas, [null, null, null])
  assert.equal(rows[2].ambiguous[1], true)
  assert.equal(rows[2].deltas[1], null)
})

test("wavelength and efficiency axes resolve measured differences without changing power zero reference", async () => {
  const { metricYAxisDomain } = await import("./data-browser.ts")
  const details = [{points:[point(2,10,1,{peakWavelengthNm:976.1}), point(4,20,2,{peakWavelengthNm:976.4})]}] as DataDetail[]
  const domain = metricYAxisDomain(details, "peakWavelengthNm")!
  assert.ok(domain[0] > 975 && domain[0] < 976.1)
  assert.ok(domain[1] > 976.4 && domain[1] < 977)
  assert.equal(metricYAxisDomain(details, "powerW"), undefined)
  assert.equal(metricYAxisDomain(details, "efficiencyPercent"), undefined)
})
