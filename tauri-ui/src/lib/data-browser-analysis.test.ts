import assert from "node:assert/strict"
import test from "node:test"
import {
  adjacentSlopes,
  commonMeasuredCurrents,
  compareMetadata,
  cursorDifference,
  firstLatestChange,
  fitMetricRange,
  historyMetricRows,
  linearRegression,
  sameSnHistory,
  timeText,
  type DataDetail,
  type DataPoint,
  type DataRecord,
} from "./data-browser.ts"

const point = (currentA: number, powerW: number | null, rowId = currentA): DataPoint => ({
  currentA,
  powerW,
  rowId,
  voltageV: currentA * 0.025 + 2,
  efficiencyPercent: 40,
  peakWavelengthNm: 975 + currentA * 0.04,
  centroidNm: null,
  fwhmNm: null,
  pibPercent: null,
  smsrDb: null,
})
function record(id: string, timeMs = 1000, sn = "ABC001"): DataRecord {
  return {
    id,
    sn,
    timeMs,
    version: "v1",
    modifiedAtMs: timeMs,
    timeSource: "session",
    snSource: "metadata",
    storage: "database",
    sourcePath: "",
    fileName: id,
    station: "S1",
    stationId: "1",
    temperature: "25",
    testMode: "automatic",
    sessionId: id,
    flowStatus: "completed",
    readStatus: "readable",
    error: "",
    pointCount: 3,
    validPointCount: 3,
    minimumCurrentA: 2,
    maximumCurrentA: 6,
  }
}
function detail(
  id: string,
  currents = [2, 4, 6],
  metadata: Record<string, string> = {},
): DataDetail {
  return {
    record: record(id),
    points: currents.map((c, i) => point(c, 3 * c + 2, i + 1)),
    metadata,
    warnings: [],
    columnLabels: {},
  }
}
const close = (actual: number, expected: number) =>
  assert.ok(Math.abs(actual - expected) < 1e-10, `${actual} != ${expected}`)

test("linear regression uses ordinary least squares and exact measured extent", () => {
  const input = [
    { x: 8, y: 17 },
    { x: 10, y: 21 },
    { x: 18, y: 37 },
  ]
  const before = structuredClone(input)
  assert.deepEqual(linearRegression(input), {
    fit: { slope: 2, intercept: 1, rSquared: 1, pointCount: 3, xMin: 8, xMax: 18 },
  })
  assert.deepEqual(input, before)
})
test("regression ignores missing and non-finite samples including x=NaN", () => {
  const result = linearRegression([
    { x: 1, y: 3 },
    { x: null, y: 4 },
    { x: NaN, y: 5 },
    { x: 2, y: null },
    { x: 3, y: Infinity },
    { x: 4, y: 9 },
  ])
  assert.equal(result.fit?.pointCount, 2)
  assert.equal(result.fit?.slope, 2)
})
test("regression rejects empty, single, equal-x and numeric overflow", () => {
  for (const samples of [
    [],
    [{ x: 1, y: 2 }],
    [
      { x: 1, y: 1 },
      { x: 1, y: 2 },
    ],
    [
      { x: -1e308, y: 1 },
      { x: 1e308, y: 2 },
    ],
  ]) {
    const result = linearRegression(samples)
    assert.equal(result.fit, null)
    assert.ok(result.reason)
  }
})
test("non-perfect fit reports standard R squared, constant response has undefined R squared", () => {
  const result = linearRegression([
    { x: 1, y: 1 },
    { x: 2, y: 2 },
    { x: 3, y: 2 },
  ]).fit!
  close(result.slope, 0.5)
  close(result.intercept, 2 / 3)
  close(result.rSquared!, 0.75)
  const constant = linearRegression([
    { x: 1, y: 7 },
    { x: 2, y: 7 },
  ]).fit!
  assert.equal(constant.slope, 0)
  assert.equal(constant.rSquared, null)
})
test("regression retains precision for small changes around large offsets", () => {
  const result = linearRegression([
    { x: 1e9, y: 976 },
    { x: 1e9 + 1, y: 976.04 },
    { x: 1e9 + 2, y: 976.08 },
  ]).fit!
  close(result.slope, 0.04)
  close(result.rSquared!, 1)
})
test("range fit includes all finite native points in inclusive A B bounds without rounding or extending", () => {
  const points = [
    point(2, 999),
    point(8, 17),
    point(12, null),
    point(14, 29),
    point(18, 37),
    point(20, 999),
  ]
  const result = fitMetricRange(points, "powerW", 18, 8).fit!
  assert.equal(result.slope, 2)
  assert.equal(result.pointCount, 3)
  assert.equal(result.xMin, 8)
  assert.equal(result.xMax, 18)
  assert.equal(fitMetricRange(points, "powerW", 8, 8).fit, null)
  assert.equal(fitMetricRange(points, "powerW", 9, 13).fit, null)
  assert.equal(fitMetricRange(points, "powerW", null, 18).fit, null)
  close(fitMetricRange(points, "voltageV", 8, 18).fit!.slope, 0.025)
  close(fitMetricRange(points, "peakWavelengthNm", 8, 18).fit!.slope, 0.04)
})
test("cursor deltas are directional, missing-safe and never divide by zero", () => {
  assert.deepEqual(cursorDifference(point(18, 37), point(8, 17), "powerW"), {
    deltaI: -10,
    deltaY: -20,
    slope: 2,
  })
  assert.deepEqual(cursorDifference(point(8, null), point(8, 17), "powerW"), {
    deltaI: 0,
    deltaY: null,
    slope: null,
  })
  assert.equal(cursorDifference(undefined, point(8, 17), "powerW").slope, null)
})
test("adjacent slopes sort a copy, skip non-finite values and zero-width pairs", () => {
  const points = [point(3, 8), point(1, 2), point(2, null), point(3, 9), point(4, 13)]
  const before = structuredClone(points)
  assert.deepEqual(
    adjacentSlopes(points, "powerW").map((p) => p.slope),
    [3, 4],
  )
  assert.deepEqual(points, before)
})
test("common currents: identical, partial intersection, disjoint, empty", () => {
  assert.deepEqual(commonMeasuredCurrents([detail("a"), detail("b")]), [2, 4, 6])
  assert.deepEqual(
    commonMeasuredCurrents([
      detail("a", [5, 10, 15, 20]),
      detail("b", [5, 10, 20]),
      detail("c", [5, 10, 15, 20]),
    ]),
    [5, 10, 20],
  )
  assert.deepEqual(commonMeasuredCurrents([detail("a", [1]), detail("b", [2])]), [])
  assert.deepEqual(commonMeasuredCurrents([]), [])
  assert.deepEqual(commonMeasuredCurrents([detail("a"), detail("b", [])]), [])
})
test("common currents reuse six decimals and preserve duplicate ambiguity in values", () => {
  const a = detail("a", [2, 2, 4, NaN]),
    b = detail("b", [2.00000001, 4.00000001])
  assert.deepEqual(commonMeasuredCurrents([a, b]), [2, 4])
  const rows = historyMetricRows([a, b], "ABC001", "powerW", 2)
  assert.equal(rows[0].value, null)
  assert.equal(rows[0].status, "重复电流点")
  assert.equal(rows[1].status, "容差匹配")
  assert.equal(rows[1].value, b.points[0].powerW)
  assert.deepEqual(commonMeasuredCurrents([detail("a", [2]), detail("b", [2.0000006])]), [])
})
test("exact SN history excludes similar names and blank identities, and never mutates input", () => {
  const records = [
    record("b", 2000),
    record("other", 500, "ABC-001"),
    record("a", 1000),
    record("case", 1, "abc001"),
  ]
  const before = structuredClone(records)
  assert.deepEqual(
    sameSnHistory(records, "ABC001").map((r) => r.id),
    ["a", "b"],
  )
  assert.deepEqual(sameSnHistory(records, ""), [])
  assert.deepEqual(records, before)
})
test("history orders missing times last, ties by session ID then record ID", () => {
  const a = record("a", 1000),
    b = record("b", 1000),
    c = record("c", NaN),
    d = record("d", Infinity)
  a.sessionId = b.sessionId = "same"
  assert.deepEqual(
    sameSnHistory([d, b, c, a], "ABC001").map((r) => r.id),
    ["a", "b", "c", "d"],
  )
  a.sessionId = "z"
  assert.deepEqual(
    sameSnHistory([a, b], "ABC001").map((r) => r.id),
    ["b", "a"],
  )
  assert.equal(timeText(1e30), "时间未记录")
})
test("first latest uses true temporal endpoints, keeps modified time provenance and never substitutes missing values", () => {
  const a = detail("a"),
    b = detail("b"),
    c = detail("c")
  a.record.timeMs = 1000
  b.record.timeMs = 2000
  c.record.timeMs = NaN
  a.record.timeSource = "modified"
  b.points[1].powerW = null
  const rows = historyMetricRows([c, b, a], "ABC001", "powerW", 4)
  const change = firstLatestChange(rows, "powerW")
  assert.equal(change.first?.record.id, "a")
  assert.equal(change.latest?.record.id, "b")
  assert.equal(change.first?.record.timeSource, "modified")
  assert.equal(change.delta, null)
  b.points[1].powerW = 12
  const valid = firstLatestChange(historyMetricRows([b, a], "ABC001", "powerW", 4), "powerW")
  assert.equal(valid.delta, -2)
  close(valid.relative!, (-2 / 14) * 100)
  assert.equal(
    firstLatestChange(
      historyMetricRows([b, a], "ABC001", "peakWavelengthNm", 4),
      "peakWavelengthNm",
    ).relative,
    null,
  )
  assert.equal(
    firstLatestChange(historyMetricRows([a], "ABC001", "maxPower", null), "maxPower").delta,
    null,
  )
})
test("maximum power stays paired with its native current and maximum current includes missing-power points", () => {
  const a = detail("a")
  a.points = [point(2, 30), point(20, 25), point(24, null)]
  const row = historyMetricRows([a], "ABC001", "maxPower", null)[0]
  assert.equal(row.value, 30)
  assert.equal(row.maxPowerPoint?.currentA, 2)
  assert.equal(row.maximumCurrentA, 24)
})
test("metadata identical, one differing field, one missing field are separate", () => {
  const a = detail("a", [2], { softwareGain: "1", powerMeterSerialNumber: "PM01" }),
    b = structuredClone(a)
  b.record.id = "b"
  assert.equal(compareMetadata([a, b], "a").differentCount, 0)
  b.metadata.softwareGain = "2"
  let diff = compareMetadata([a, b], "a")
  assert.equal(diff.differentCount, 1)
  assert.equal(diff.rows.find((r) => r.key === "softwareGain")?.cells[1].state, "different")
  delete b.metadata.powerMeterSerialNumber
  diff = compareMetadata([a, b], "a")
  assert.equal(diff.differentCount, 1)
  assert.equal(diff.missingCount, 1)
  assert.equal(diff.rows.find((r) => r.key === "powerMeterSerialNumber")?.cells[1].state, "missing")
})
test("metadata baseline changes cell reference without reordering 3 to 5 records or mutation", () => {
  for (const count of [3, 4, 5]) {
    const details = Array.from({ length: count }, (_, i) =>
      detail(String(i), [2], { softwareGain: i === 0 ? "1" : "2" }),
    )
    const before = structuredClone(details),
      diff = compareMetadata(details, "1")
    const row = diff.rows.find((r) => r.key === "softwareGain")!
    assert.deepEqual(
      row.cells.map((c) => c.recordId),
      details.map((d) => d.record.id),
    )
    assert.equal(row.cells[0].state, "different")
    assert.equal(row.cells[1].state, "same")
    assert.deepEqual(details, before)
  }
})
test("metadata uses actual JSON config and Excel aliases; false, zero, malformed JSON are preserved", () => {
  const a = detail("a", [2], {
    测试配置: JSON.stringify({
      softwareGain: 0,
      autoIntegration: false,
      powerMeterSerialNumber: "PM1",
    }),
    设备信息: JSON.stringify([{ role: "功率计", kind: "ophir", resource: "USB", detail: "" }]),
  })
  const b = detail("b", [2], {
    功率系数: "0",
    autoIntegration: "False",
    "功率计探头 SN": "PM1",
    功率计: "ophir | USB",
  })
  assert.equal(compareMetadata([a, b], "a").differentCount, 0)
  b.metadata = { 测试配置: "{bad json" }
  const diff = compareMetadata([a, b], "b")
  assert.equal(diff.rows.find((r) => r.key === "softwareGain")?.cells[0].state, "no-baseline")
  assert.equal(diff.rows.find((r) => r.key === "rawConfiguration")?.cells[1].value, "{bad json")
})
test("metadata reports both known differences and missing cells in a multi-record field", () => {
  const records = [
    detail("a", [2], { softwareGain: "1" }),
    detail("b", [2], { softwareGain: "2" }),
    detail("c"),
  ]
  const row = compareMetadata(records, "a").rows.find((r) => r.key === "softwareGain")!
  assert.equal(row.different, true)
  assert.equal(row.missing, true)
  assert.deepEqual(
    row.cells.map((c) => c.state),
    ["same", "different", "missing"],
  )
})
test("numeric metadata formatting is normalized only for known numerical fields, while IDs stay exact", () => {
  const a = detail("a", [2], {
    测试配置: JSON.stringify({ softwareGain: 1, powerMeterSerialNumber: "001" }),
    功率系数: "1.0",
  })
  const b = detail("b", [2], {
    softwareGain: "1.000",
    powerMeterSerialNumber: "1",
    设备信息: "invalid",
  })
  const diff = compareMetadata([a, b], "a")
  assert.equal(diff.rows.find((r) => r.key === "softwareGain")?.different, false)
  assert.equal(diff.rows.find((r) => r.key === "powerMeterSerialNumber")?.different, true)
  assert.equal(diff.rows.find((r) => r.key === "rawDevices")?.cells[1].value, "invalid")
})
test("software versions consume existing journal session fields and legacy metadata without using cache versions", () => {
  const a = detail("a"),
    b = detail("b", [2], { 软件版本: "1.2", 算法版本: "2" })
  a.session = { software_version: "1.2", calculation_version: "2" }
  a.record.version = "different-cache-stamp"
  const diff = compareMetadata([a, b], "a")
  assert.equal(diff.differentCount, 0)
  assert.equal(diff.rows.find((r) => r.key === "软件版本")?.cells[0].value, "1.2")
  assert.equal(diff.rows.find((r) => r.key === "recordVersion")?.different, true)
})
