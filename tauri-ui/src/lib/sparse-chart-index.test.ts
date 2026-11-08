import assert from "node:assert/strict"
import test from "node:test"
import { sparseChartIndex } from "./sparse-chart-index.ts"

test("sparse indexes preserve original row positions, duplicates, zero and invalid gaps", () => {
  const rows: readonly Readonly<Record<string, number | null>>[] = Object.freeze([
    Object.freeze({ x: 2, a: -0 }), Object.freeze({ x: 1, b: 3 }),
    Object.freeze({ x: 1, a: 4 }), Object.freeze({ x: 1, a: null }),
    Object.freeze({ x: null, a: 7 }), Object.freeze({ x: 0, a: NaN, b: Infinity }),
  ])
  const result = sparseChartIndex(rows, ["a", "b", "a", "missing"])!
  assert.deepEqual([...result], [["a", [0, 2, 4]], ["b", [1]], ["missing", []]])
  assert(Object.is(rows[result.get("a")![0]].a, -0))
})

test("dense, inherited or accessor series retain the original scan path", () => {
  assert.equal(sparseChartIndex([{ x: 1, a: 2, b: 3 }], ["a", "b"]), null)
  assert.equal(sparseChartIndex([Object.assign(Object.create({ a: 1 }), { x: 2 })], ["a"]), null)
  let reads = 0
  const row = { x: 1, get a() { reads++; return 2 } }
  assert.equal(sparseChartIndex([row], ["a"]), null)
  assert.equal(reads, 0)
  const hidden = Object.defineProperty({ x: 1 }, "a", { value: 2 })
  assert.deepEqual([...sparseChartIndex([hidden], ["a"])!], [["a", [0]]])
})

test("indexed connected paths match a full scan for visible subsets and irregular grids", () => {
  let seed = 17
  const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 2 ** 32 }
  for (let trial = 0; trial < 32; trial++) {
    const rows = Array.from({ length: 1024 }, (_, index) => ({
      x: index % 37 === 0 ? NaN : Math.round(random() * 50),
      [`s${index % 8}`]: index % 23 === 0 ? null : random(),
    }))
    const keys = Array.from({ length: 8 }, (_, i) => `s${i}`)
    const result = sparseChartIndex(rows, keys)!
    const visible = rows.flatMap((row, i) => row.x >= 10 && row.x <= 40 && i % 3 ? [i] : [])
    const mask = new Set(visible)
    for (const key of keys) {
      const expected = visible.filter(i => typeof rows[i][key] === "number" && Number.isFinite(rows[i][key]))
      assert.deepEqual(result.get(key)!.filter(i => mask.has(i)), expected)
    }
  }
})
