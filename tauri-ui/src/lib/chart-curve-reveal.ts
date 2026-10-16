import { traceMonotoneCurve, type CurvePoint } from "./monotone-curve.ts"

type CurvePath = Pick<CanvasRenderingContext2D, "moveTo" | "lineTo" | "bezierCurveTo">
export type CurveRevealMotion = { fromX: number; toX: number; startedAt: number; durationMs: number }

export function curveRevealFrame(motion: CurveRevealMotion, now: number) {
  const progress = motion.durationMs <= 0 ? 1 : Math.max(0, Math.min(1, (now - motion.startedAt) / motion.durationMs))
  return { x: motion.fromX + (motion.toX - motion.fromX) * progress, progress, active: progress < 1 }
}

export function retargetCurveReveal(previous: CurveRevealMotion | null, toX: number, now: number, animate: boolean): CurveRevealMotion {
  if (!previous || !animate || toX < previous.toX) return { fromX: toX, toX, startedAt: now, durationMs: 0 }
  if (toX === previous.toX) return previous
  return {
    fromX: curveRevealFrame(previous, now).x, toX, startedAt: now,
    // Follow arrivals without extrapolation or an unbounded display backlog.
    durationMs: Math.max(60, Math.min(200, now - previous.startedAt)),
  }
}

// Trace a prefix of the EXISTING curve and return its exact visible endpoint.
// Splitting its last cubic preserves the original interpolation and all samples.
export function traceRevealedCurve(path: CurvePath, points: readonly (CurvePoint | null)[], monotone: boolean, maximumX = Infinity): CurvePoint | null {
  let current: CurvePoint | null = null
  let endpoint: CurvePoint | null = null
  const prefix: CurvePath = {
    moveTo(x, y) {
      current = { x, y }
      if (x <= maximumX) { path.moveTo(x, y); endpoint = current }
    },
    lineTo(x, y) {
      const left = current
      current = { x, y }
      if (!left || left.x > maximumX) return
      const t = x <= maximumX ? 1 : Math.max(0, (maximumX - left.x) / (x - left.x))
      endpoint = t === 1 ? current : { x: maximumX, y: left.y + (y - left.y) * t }
      path.lineTo(endpoint.x, endpoint.y)
    },
    bezierCurveTo(x1, y1, x2, y2, x, y) {
      const left = current
      current = { x, y }
      if (!left || left.x > maximumX) return
      if (x <= maximumX) {
        path.bezierCurveTo(x1, y1, x2, y2, x, y)
        endpoint = current
        return
      }
      // Hermite control x coordinates are at 1/3 and 2/3, so x is linear in t.
      const t = Math.max(0, (maximumX - left.x) / (x - left.x))
      const ax = left.x + (x1 - left.x) * t, ay = left.y + (y1 - left.y) * t
      const bx = x1 + (x2 - x1) * t, by = y1 + (y2 - y1) * t
      const cx = x2 + (x - x2) * t, cy = y2 + (y - y2) * t
      const dx = ax + (bx - ax) * t, dy = ay + (by - ay) * t
      const ex = bx + (cx - bx) * t, ey = by + (cy - by) * t
      endpoint = { x: dx + (ex - dx) * t, y: dy + (ey - dy) * t }
      path.bezierCurveTo(ax, ay, dx, dy, endpoint.x, endpoint.y)
    },
  }
  if (monotone) traceMonotoneCurve(prefix, points)
  else {
    let drawing = false
    for (const point of points) {
      if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) { drawing = false; continue }
      if (drawing) prefix.lineTo(point.x, point.y)
      else prefix.moveTo(point.x, point.y)
      drawing = true
    }
  }
  return endpoint
}
