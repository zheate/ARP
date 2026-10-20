/** Display elapsed seconds without changing the time-series coordinates. */
export function formatElapsedMinutesSeconds(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "—"
  // Match the chart tooltip's four-decimal precision, including minute carries.
  const seconds = Math.round(value * 10000) / 10000
  const minutes = Math.floor(seconds / 60)
  const remainder = (seconds % 60).toLocaleString("en-US", {
    minimumIntegerDigits: 2,
    maximumFractionDigits: 4,
    useGrouping: false,
  })
  return `${minutes}m${remainder}s`
}
