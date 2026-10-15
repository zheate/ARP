export type CurvePoint = { x: number; y: number }

type CurvePath = Pick<CanvasRenderingContext2D, "moveTo" | "lineTo" | "bezierCurveTo">

// Render-only Hermite interpolation: preserve every sample and flatten the
// tangent at local extrema so narrow peaks do not overshoot their measured top.
export function traceMonotoneCurve(path: CurvePath, points: readonly (CurvePoint | null)[]): void {
  let start = 0
  while (start < points.length) {
    const first = points[start]
    if (!first || !Number.isFinite(first.x) || !Number.isFinite(first.y)) {
      start += 1
      continue
    }
    let end = start + 1
    while (end < points.length) {
      const previous = points[end - 1]!
      const current = points[end]
      if (!current || !Number.isFinite(current.x) || !Number.isFinite(current.y) || current.x <= previous.x) break
      end += 1
    }

    path.moveTo(first.x, first.y)
    const count = end - start
    if (count === 2) {
      const last = points[start + 1]!
      path.lineTo(last.x, last.y)
    } else if (count > 2) {
      // Each segment needs only its two neighbouring slopes. Keep the original
      // arithmetic order without allocating widths/slopes/tangents per frame.
      let width = points[start + 1]!.x - first.x
      let slope = (points[start + 1]!.y - first.y) / width
      let leftTangent = slope
      for (let index = start; index < end - 1; index += 1) {
        const left = points[index]!
        const right = points[index + 1]!
        let nextWidth = 0
        let nextSlope = 0
        let rightTangent = slope
        if (index < end - 2) {
          const next = points[index + 2]!
          nextWidth = next.x - right.x
          nextSlope = (next.y - right.y) / nextWidth
          if (slope === 0 || nextSlope === 0 || Math.sign(slope) !== Math.sign(nextSlope)) {
            rightTangent = 0
          } else {
            // Weighted harmonic mean also supports nonuniform wavelength grids.
            const leftWeight = 2 * nextWidth + width
            const rightWeight = nextWidth + 2 * width
            rightTangent = (leftWeight + rightWeight) / (leftWeight / slope + rightWeight / nextSlope)
          }
        }
        const third = width / 3
        const low = Math.min(left.y, right.y)
        const high = Math.max(left.y, right.y)
        path.bezierCurveTo(
          left.x + third, Math.max(low, Math.min(high, left.y + leftTangent * third)),
          right.x - third, Math.max(low, Math.min(high, right.y - rightTangent * third)),
          right.x, right.y,
        )
        width = nextWidth
        slope = nextSlope
        leftTangent = rightTangent
      }
    }
    start = end
  }
}
