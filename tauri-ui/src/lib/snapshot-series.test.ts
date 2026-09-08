import assert from "node:assert/strict"
import test from "node:test"

import { mergeSnapshotSeries } from "./snapshot-series.ts"

const x = (point: { elapsedS: number }) => point.elapsedS

test("a stream delta after a full command snapshot does not duplicate power samples", () => {
  const full = [{ elapsedS: 10, powerW: 1 }, { elapsedS: 11, powerW: 2 }]
  const points = mergeSnapshotSeries(full, undefined, {
    startX: 10,
    points: [{ elapsedS: 11, powerW: 2 }, { elapsedS: 12, powerW: 3 }],
  }, x, 11, 12)
  assert.deepEqual(points, [...full, { elapsedS: 12, powerW: 3 }])
})

test("PD overlaps replace matching samples, trim the rolling window, and stay ordered", () => {
  const points = mergeSnapshotSeries(
    [{ elapsedS: 1, value: 10 }, { elapsedS: 2, value: 20 }, { elapsedS: 4, value: 40 }],
    undefined,
    { startX: 2, points: [{ elapsedS: 4, value: 41 }, { elapsedS: 3, value: 30 }, { elapsedS: 3, value: 31 }] },
    x,
  )
  assert.deepEqual(points, [{ elapsedS: 2, value: 20 }, { elapsedS: 3, value: 31 }, { elapsedS: 4, value: 41 }])
})

test("a full reset clears history and a delayed pre-reset delta cannot restore it", () => {
  const before = [{ elapsedS: 100, powerW: 5 }]
  const reset = mergeSnapshotSeries(before, [], undefined, x, 100, 101)
  assert.deepEqual(reset, [])
  const delayed = mergeSnapshotSeries(reset, undefined, {
    startX: 0, points: [{ elapsedS: 101, powerW: 6 }],
  }, x, 101, 100)
  assert.deepEqual(delayed, [])
  const restarted = mergeSnapshotSeries(delayed, [{ elapsedS: 0, powerW: 0 }], undefined, x, 101, 102)
  assert.deepEqual(restarted, [{ elapsedS: 0, powerW: 0 }])
})

test("a delayed full command response cannot overwrite newer stream history", () => {
  const latest = [{ elapsedS: 10, value: 1 }, { elapsedS: 11, value: 2 }]
  assert.equal(mergeSnapshotSeries(latest, [latest[0]], undefined, x, 11, 10), latest)
})

test("an unchanged series keeps its reference and an empty delta can trim history", () => {
  const points = [{ elapsedS: 1, powerW: 1 }, { elapsedS: 2, powerW: 2 }]
  assert.equal(mergeSnapshotSeries(points, undefined, undefined, x), points)
  assert.deepEqual(mergeSnapshotSeries(points, undefined, { startX: 2, points: [] }, x), [points[1]])
})
