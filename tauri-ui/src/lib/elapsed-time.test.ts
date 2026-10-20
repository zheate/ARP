import assert from "node:assert/strict"
import test from "node:test"
import { formatElapsedMinutesSeconds } from "./elapsed-time.ts"

test("elapsed labels retain seconds within each minute and carry at minute boundaries", () => {
  for (const [seconds, expected] of [
    [0, "0m00s"], [59, "0m59s"], [60, "1m00s"],
    [370, "6m10s"], [398.485, "6m38.485s"], [3600, "60m00s"],
    [59.99999, "1m00s"], [120.00001, "2m00s"],
  ] as const) assert.equal(formatElapsedMinutesSeconds(seconds), expected)
})

test("invalid elapsed values do not produce misleading time labels", () => {
  for (const value of [-1, NaN, Infinity]) assert.equal(formatElapsedMinutesSeconds(value), "—")
})
