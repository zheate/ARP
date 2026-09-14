export function isOlderSeriesRevision(previous, incoming) {
  return previous !== void 0 && incoming !== void 0 && incoming < previous;
}
export function mergeSnapshotSeries(previous, replacement, patch, getX, previousRevision, incomingRevision) {
  if (isOlderSeriesRevision(previousRevision, incomingRevision)) return previous;
  if (replacement !== void 0) return replacement;
  if (!patch) return previous;
  const points = /* @__PURE__ */ new Map();
  for (const point of previous ?? []) {
    const x = getX(point);
    if (x >= patch.startX) points.set(x, point);
  }
  for (const point of patch.points) points.set(getX(point), point);
  return [...points].sort(([left], [right]) => left - right).map(([, point]) => point);
}

