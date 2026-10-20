import assert from "node:assert/strict"
import test from "node:test"
import { curveRevealFrame, retargetCurveReveal, traceRevealedCurve } from "./chart-curve-reveal.ts"
import { traceMonotoneCurve } from "./monotone-curve.ts"

function recorder() {
  const commands: number[][] = []
  return { commands, moveTo: (...p: number[]) => { commands.push(p) }, lineTo: (...p: number[]) => { commands.push(p) }, bezierCurveTo: (...p: number[]) => { commands.push(p) } }
}

test("reveal follows received targets, retargets continuously, and finishes without new samples", () => {
  const first = retargetCurveReveal(null, 10, 0, true)
  const next = retargetCurveReveal(first, 11, 100, true)
  assert.deepEqual(curveRevealFrame(next, 150), { x: 10.5, progress: 0.5, active: true })
  const burst = retargetCurveReveal(next, 12, 150, true)
  assert.equal(burst.fromX, 10.5)
  assert.equal(curveRevealFrame(burst, 1000).x, 12)
  assert.equal(curveRevealFrame(burst, 1000).active, false)
  assert.equal(retargetCurveReveal(burst, 12, 170, true), burst)
  assert.equal(curveRevealFrame(retargetCurveReveal(burst, 0, 180, true), 180).x, 0)
  assert.equal(curveRevealFrame(retargetCurveReveal(burst, 13, 200, false), 200).x, 13)
})

test("partial linear paths keep gaps and return the exact visible endpoint without mutating samples", () => {
  const points = Object.freeze([{ x: 0, y: 2 }, { x: 2, y: 6 }, null, { x: 4, y: 8 }, { x: 6, y: 0 }])
  const before = JSON.stringify(points)
  const path = recorder()
  assert.deepEqual(traceRevealedCurve(path, points, false, 1), { x: 1, y: 4 })
  assert.deepEqual(path.commands, [[0, 2], [1, 4]])
  const gap = recorder()
  assert.deepEqual(traceRevealedCurve(gap, points, false, 3), { x: 2, y: 6 })
  assert.equal(JSON.stringify(points), before)
  assert.equal(traceRevealedCurve(recorder(), [], true, 0), null)
  assert.equal(traceRevealedCurve(recorder(), points, false, -1), null)
})

test("revealed cubic is the exact prefix of the original monotone curve at every progress", () => {
  const points = [{ x: 0, y: 1 }, { x: 2.3, y: 7 }, { x: 5.1, y: 3 }, { x: 9, y: 10 }]
  const full = recorder()
  traceMonotoneCurve(full, points)
  const finished = recorder()
  assert.deepEqual(traceRevealedCurve(finished, points, true), points[points.length - 1])
  assert.deepEqual(finished.commands, full.commands)
  for (let segment = 1; segment < points.length; segment++) for (let step = 1; step < 20; step++) {
    const t = step / 20, u = 1 - t
    const left = points[segment - 1], right = points[segment]
    const x = left.x + (right.x - left.x) * t
    const [, y1, , y2] = full.commands[segment]
    const expectedY = u ** 3 * left.y + 3 * u ** 2 * t * y1 + 3 * u * t ** 2 * y2 + t ** 3 * right.y
    const prefix = recorder()
    const end = traceRevealedCurve(prefix, points, true, x)!
    assert.ok(Math.abs(end.x - x) < 1e-10 && Math.abs(end.y - expectedY) < 1e-10)
    assert.deepEqual(prefix.commands[prefix.commands.length - 1].slice(-2), [end.x, end.y])
  }
})

test("direction follows the revealed cubic tangent at boundaries and between samples", () => {
  const points = Object.freeze([{ x: 0, y: 1 }, { x: 2.3, y: 7 }, { x: 5.1, y: 3 }, { x: 9, y: 10 }])
  const before = JSON.stringify(points)
  const full = recorder()
  traceMonotoneCurve(full, points)
  for (let segment = 1; segment < points.length; segment++) for (const t of [0, 0.001, 0.25, 0.5, 0.75, 0.999, 1]) {
    const left = points[segment - 1], right = points[segment], u = 1 - t
    const [x1, y1, x2, y2] = full.commands[segment]
    const dx = u * u * (x1 - left.x) + 2 * u * t * (x2 - x1) + t * t * (right.x - x2)
    const dy = u * u * (y1 - left.y) + 2 * u * t * (y2 - y1) + t * t * (right.y - y2)
    let angle = NaN
    traceRevealedCurve(recorder(), points, true, left.x + (right.x - left.x) * t, (value) => { angle = value })
    assert.ok(Math.abs(angle - Math.atan2(dy, dx)) < 1e-10, `segment ${segment}, t=${t}`)
  }
  let angle = NaN
  traceRevealedCurve(recorder(), [{ x: 0, y: 0 }, { x: 2, y: 4 }], false, 1, value => { angle = value })
  assert.equal(angle, Math.atan2(4, 2))
  traceRevealedCurve(recorder(), [{ x: 0, y: 0 }, { x: 2, y: 4 }, null, { x: 5, y: 6 }], false, 5, value => { angle = value })
  assert.equal(angle, 0)
  assert.equal(JSON.stringify(points), before)
})
