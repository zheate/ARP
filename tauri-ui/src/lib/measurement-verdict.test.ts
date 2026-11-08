import assert from "node:assert/strict"
import test from "node:test"
import { measurementVerdict } from "./measurement-verdict.ts"

test("a verdict requires a finite, live measurement, never stale or invalid values", () => {
  for (const value of [NaN, Infinity, -Infinity]) assert.equal(measurementVerdict(value, "live", true).label, "数据无效")
  for (const value of [null, undefined]) assert.equal(measurementVerdict(value, "live", true).label, "无实时数据")
  assert.equal(measurementVerdict(976, "stale", true).label, "数据过期")
  assert.equal(measurementVerdict(976, "invalid", true).label, "数据无效")
  assert.equal(measurementVerdict(976, undefined, true).tone, "muted")
})
test("valid limit decisions carry text and semantic color without changing the value", () => {
  assert.deepEqual(measurementVerdict(0, "live", true), { label: "达标", tone: "success" })
  assert.deepEqual(measurementVerdict(0.776, "live", false), { label: "超限", tone: "danger" })
})
