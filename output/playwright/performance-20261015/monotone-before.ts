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
      const widths: number[] = []
      const slopes: number[] = []
      for (let index = start; index < end - 1; index += 1) {
        const left = points[index]!
        const right = points[index + 1]!
        const width = right.x - left.x
        widths.push(width)
        slopes.push((right.y - left.y) / width)
      }
      const tangents = [slopes[0]]
      for (let index = 1; index < count - 1; index += 1) {
        const left = slopes[index - 1]
        const right = slopes[index]
        if (left === 0 || right === 0 || Math.sign(left) !== Math.sign(right)) {
          tangents.push(0)
        } else {
          // Weighted harmonic mean also supports nonuniform wavelength grids.
          const leftWeight = 2 * widths[index] + widths[index - 1]
          const rightWeight = widths[index] + 2 * widths[index - 1]
          tangents.push((leftWeight + rightWeight) / (leftWeight / left + rightWeight / right))
        }
      }
      tangents.push(slopes[slopes.length - 1])
      for (let index = 0; index < count - 1; index += 1) {
        const left = points[start + index]!
        const right = points[start + index + 1]!
        const third = widths[index] / 3
        const low = Math.min(left.y, right.y)
        const high = Math.max(left.y, right.y)
        const clamp = (value: number) => Math.max(low, Math.min(high, value))
        path.bezierCurveTo(
          left.x + third, clamp(left.y + tangents[index] * third),
          right.x - third, clamp(right.y - tangents[index + 1] * third),
          right.x, right.y,
        )
      }
    }
    start = end
  }
}
