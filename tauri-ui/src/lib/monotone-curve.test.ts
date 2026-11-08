import assert from "node:assert/strict"
import test from "node:test"

import { traceMonotoneCurve, type CurvePoint } from "./monotone-curve.ts"

function record(points: readonly (CurvePoint | null)[]) {
  const commands: { kind: string; values: number[] }[] = []
  traceMonotoneCurve({
    moveTo: (...values) => { commands.push({ kind: "move", values }) },
    lineTo: (...values) => { commands.push({ kind: "line", values }) },
    bezierCurveTo: (...values) => { commands.push({ kind: "curve", values }) },
  }, points)
  return commands
}

test("spectrum interpolation passes through every sample without overshoot on an irregular grid", () => {
  const points = Object.freeze([
    { x: 449.2, y: -6 }, { x: 449.48, y: 20 }, { x: 449.73, y: 190 },
    { x: 450, y: 620 }, { x: 450.29, y: 615 }, { x: 450.54, y: 140 },
    { x: 450.81, y: 12 }, { x: 451.08, y: -4 }, { x: 451.33, y: -4 },
  ].map((point) => Object.freeze(point)))
  const before = JSON.stringify(points)
  const commands = record(points)
  assert.equal(commands.length, points.length)
  assert.deepEqual(commands[0], { kind: "move", values: [points[0].x, points[0].y] })
  for (let index = 1; index < points.length; index += 1) {
    const left = points[index - 1]
    const right = points[index]
    const command = commands[index]
    assert.equal(command.kind, "curve")
    const [x1, y1, x2, y2, x3, y3] = command.values
    assert.deepEqual([x3, y3], [right.x, right.y])
    assert.ok(left.x < x1 && x1 < x2 && x2 < right.x)
    let previousY = left.y
    for (let step = 0; step <= 100; step += 1) {
      const t = step / 100
      const u = 1 - t
      const y = u ** 3 * left.y + 3 * u ** 2 * t * y1 + 3 * u * t ** 2 * y2 + t ** 3 * y3
      assert.ok(y >= Math.min(left.y, right.y) - 1e-9 && y <= Math.max(left.y, right.y) + 1e-9)
      assert.ok((right.y - left.y) * (y - previousY) >= -1e-9)
      previousY = y
    }
  }
  // Both sides of the peak share a horizontal tangent, with no artificial top.
  assert.equal(commands[3].values[3], 620)
  assert.equal(commands[4].values[1], 620)
  assert.equal(JSON.stringify(points), before)
})

test("missing data and nonincreasing wavelengths start a new curve", () => {
  const commands = record([
    { x: 1, y: 0 }, { x: 2, y: 10 }, null,
    { x: 3, y: 20 }, { x: 4, y: 30 },
    { x: 4, y: 40 }, { x: 5, y: 50 },
    { x: 3, y: 60 }, { x: 4, y: 70 },
    { x: 5, y: NaN }, { x: 6, y: 80 },
  ])
  assert.deepEqual(commands.map((command) => command.kind), [
    "move", "line", "move", "line", "move", "line", "move", "line", "move",
  ])
  assert.ok(commands.every((command) => command.values.every(Number.isFinite)))
})

test("empty, singleton, and two-point spectra remain valid", () => {
  assert.deepEqual(record([]), [])
  assert.deepEqual(record([null, { x: Infinity, y: 0 }]), [])
  assert.deepEqual(record([{ x: 1, y: 2 }]), [{ kind: "move", values: [1, 2] }])
  assert.deepEqual(record([{ x: 1, y: 2 }, { x: 2, y: 4 }]), [
    { kind: "move", values: [1, 2] }, { kind: "line", values: [2, 4] },
  ])
})

test("curve commands retain the pre-optimization values across irregular grids and gaps", async () => {
  const { createHash } = await import("node:crypto")
  const hash = createHash("sha256")
  let seed = 73129
  const random = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0
    return seed / 2 ** 32
  }
  const path = {
    moveTo: (...args: number[]) => { hash.update(JSON.stringify(["moveTo", ...args])) },
    lineTo: (...args: number[]) => { hash.update(JSON.stringify(["lineTo", ...args])) },
    bezierCurveTo: (...args: number[]) => { hash.update(JSON.stringify(["bezierCurveTo", ...args])) },
  }
  for (let trial = 0; trial < 128; trial += 1) {
    let x = 0
    const points = Array.from({ length: trial + 3 }, () => {
      x += random() * 2 + 0.01
      const chance = random()
      return chance < 0.03 ? null : {
        x: chance < 0.06 ? x - 3 : x,
        y: chance < 0.08 ? NaN : Math.round((random() - 0.5) * 1000) / 7,
      }
    })
    traceMonotoneCurve(path, points)
  }
  // Captured from the original implementation, before changing its working storage.
  assert.equal(hash.digest("hex"), "884ae3a2dc16a0ecd5fbc5de6a790c7f403b53bef09a2c9a0ba1fae014e214c9")
})
