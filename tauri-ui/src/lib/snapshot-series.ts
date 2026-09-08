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
  const points = new Map<number, T>()
  for (const point of previous ?? []) {
    const x = getX(point)
    if (x >= patch.startX) points.set(x, point)
  }
  for (const point of patch.points) points.set(getX(point), point)
  return [...points].sort(([left], [right]) => left - right).map(([, point]) => point)
}
