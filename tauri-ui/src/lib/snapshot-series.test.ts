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

test("ordered power updates trim to 1200 points without mutating snapshots", () => {
  const previous = Object.freeze(Array.from({ length: 1200 }, (_, i) => Object.freeze({ elapsedS: i, powerW: i / 10 })))
  const appended = Object.freeze([{ elapsedS: 1200, powerW: 120 }, { elapsedS: 1201, powerW: 121 }])
  const result = mergeSnapshotSeries(previous as unknown as typeof appended[number][], undefined, {
    startX: 2, points: appended as unknown as typeof appended[number][],
  }, x)!
  assert.equal(result.length, 1200)
  assert.equal(result[0], previous[2])
  assert.equal(result[1197], previous[1199])
  assert.equal(result[1198], appended[0])
  assert.equal(result[1199], appended[1])
  assert.equal(previous.length, 1200)
  assert.equal(previous[0].elapsedS, 0)
})

test("unordered histories and duplicate appended timestamps retain last-write-wins ordering", () => {
  const previous = [{ elapsedS: 3, value: 30 }, { elapsedS: 1, value: 10 }, { elapsedS: 1, value: 11 }]
  assert.deepEqual(mergeSnapshotSeries(previous, undefined, {
    startX: 1, points: [{ elapsedS: 4, value: 40 }, { elapsedS: 4, value: 41 }],
  }, x), [{ elapsedS: 1, value: 11 }, { elapsedS: 3, value: 30 }, { elapsedS: 4, value: 41 }])
})

test("an empty ordered patch preserves identity when nothing expired", () => {
  const previous = [{ elapsedS: 2, value: 2 }, { elapsedS: 3, value: 3 }]
  assert.equal(mergeSnapshotSeries(previous, undefined, { startX: 2, points: [] }, x), previous)
  assert.deepEqual(mergeSnapshotSeries(previous, undefined, { startX: 4, points: [] }, x), [])
  assert.deepEqual(mergeSnapshotSeries(undefined, undefined, { startX: 0, points: previous }, x), previous)
})
