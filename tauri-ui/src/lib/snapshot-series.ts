export type AppendSeriesPatch<T> = { startX: number; points: T[] }

export function isOlderSeriesRevision(previous: number | undefined, incoming: number | undefined): boolean {
  return previous !== undefined && incoming !== undefined && incoming < previous
}

// Commands carry full histories while the stream has its own append cursor.
// Their ranges can overlap; incoming samples replace matching timestamps.
export function mergeSnapshotSeries<T>(
  previous: T[] | undefined,
  replacement: T[] | undefined,
  patch: AppendSeriesPatch<T> | undefined,
  getX: (point: T) => number,
  previousRevision?: number,
  incomingRevision?: number,
): T[] | undefined {
  if (isOlderSeriesRevision(previousRevision, incomingRevision)) return previous
  if (replacement !== undefined) return replacement
  if (!patch) return previous

  // Normal live patches append ordered timestamps. Allocate only the output
  // array instead of rebuilding a Map and sorting every retained sample.
  // Validate both ranges so resets, duplicates and overlaps keep the general
  // last-write-wins merge below.
  const history = previous ?? []
  let previousX = -Infinity
  let trimIndex = 0
  let ordered = Number.isFinite(patch.startX)
  for (let index = 0; ordered && index < history.length; index += 1) {
    const value = getX(history[index])
    ordered = Number.isFinite(value) && value > previousX
    previousX = value
    if (value < patch.startX) trimIndex = index + 1
  }
  for (let index = 0; ordered && index < patch.points.length; index += 1) {
    const value = getX(patch.points[index])
    ordered = Number.isFinite(value) && value > previousX
    previousX = value
  }
  if (ordered) {
    if (trimIndex === 0 && patch.points.length === 0 && previous) return previous
    const kept = history.length - trimIndex
    const merged = new Array<T>(kept + patch.points.length)
    for (let index = 0; index < kept; index += 1) merged[index] = history[trimIndex + index]
    for (let index = 0; index < patch.points.length; index += 1) merged[kept + index] = patch.points[index]
    return merged
  }

  const points = new Map<number, T>()
  for (const point of previous ?? []) {
    const x = getX(point)
    if (x >= patch.startX) points.set(x, point)
  }
  for (const point of patch.points) points.set(getX(point), point)
  return [...points].sort(([left], [right]) => left - right).map(([, point]) => point)
}
