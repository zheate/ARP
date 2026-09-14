import { mergeSnapshotSeries as fallback } from './baseline-series.mjs';
export function mergeSnapshotSeries(previous, replacement, patch, getX, previousRevision, incomingRevision) {
  if (previousRevision !== undefined && incomingRevision !== undefined && incomingRevision < previousRevision) return previous;
  if (replacement !== undefined) return replacement;
  if (!patch) return previous;
  const history = previous ?? [];
  let previousX = -Infinity;
  let trimIndex = 0;
  let ordered = Number.isFinite(patch.startX);
  for (let index = 0; ordered && index < history.length; index += 1) {
    const value = getX(history[index]);
    ordered = Number.isFinite(value) && value > previousX;
    previousX = value;
    if (value < patch.startX) trimIndex = index + 1;
  }
  for (let index = 0; ordered && index < patch.points.length; index += 1) {
    const value = getX(patch.points[index]);
    ordered = Number.isFinite(value) && value > previousX;
    previousX = value;
  }
  if (ordered) {
    if (trimIndex === 0 && patch.points.length === 0 && previous) return previous;
    const kept = history.length - trimIndex;
    const merged = new Array(kept + patch.points.length);
    for (let index = 0; index < kept; index += 1) merged[index] = history[trimIndex + index];
    for (let index = 0; index < patch.points.length; index += 1) merged[kept + index] = patch.points[index];
    return merged;
  }
  return fallback(previous, replacement, patch, getX, previousRevision, incomingRevision);
}
