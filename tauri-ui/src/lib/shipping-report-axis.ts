type SpectrumPoint = { currentA: number; spectrumPeakCounts?: number | null }

export function compactSpectrumAxisMaximum(peakCounts: number): number {
  if (peakCounts <= 0 || !Number.isFinite(peakCounts)) return 1
  const paddedPeak = peakCounts * 1.03
  const magnitude = 10 ** Math.floor(Math.log10(paddedPeak))
  const multiplier = [1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 6, 7, 8, 9, 10]
    .find((candidate) => candidate * magnitude >= paddedPeak) ?? 10
  return multiplier * magnitude
}

export function suggestedSpectrumAxisMaximum(points: readonly SpectrumPoint[], currentA: number): number {
  const selected = points.find((point) => Math.abs(point.currentA - currentA) < 1e-9)
  const maximumCurrent = selected?.currentA ?? currentA
  let peak = 0
  for (const point of points) {
    if (Number.isFinite(maximumCurrent) && point.currentA > maximumCurrent) continue
    if (typeof point.spectrumPeakCounts === "number" && Number.isFinite(point.spectrumPeakCounts)) {
      peak = Math.max(peak, point.spectrumPeakCounts)
    }
  }
  return compactSpectrumAxisMaximum(peak)
}
