export type SpectrumPeak = {
  label: string
  peakWavelengthNm: number
  peakIntensity: number
}

export type SpectrumPeakLegendItem = {
  label: string
  color: string
  value: string
}

export type SpectrumWidthAnnotation = {
  label: string
  startX: number
  endX: number
  y: number
  color: string
}

const PEAK_STYLES = [
  { shortLabel: "P1", label: "Peak 1", color: "#3b82f6" },
  { shortLabel: "P2", label: "Peak 2", color: "#f97316" },
  { shortLabel: "P3", label: "Peak 3", color: "#9ca3af" },
] as const

function normalizedPeakLabel(label: string): string {
  return label.replace(/^PEAK\s*(\d+)$/i, "P$1").toUpperCase()
}

export function spectrumPeakAnnotations(peaks: SpectrumPeak[]) {
  return peaks.map((peak) => {
    const style = PEAK_STYLES.find((item) => item.shortLabel === normalizedPeakLabel(peak.label))
    return {
      label: peak.label,
      x: peak.peakWavelengthNm,
      y: peak.peakIntensity,
      color: style?.color,
    }
  })
}

export function spectrumPeakLegend(peaks: SpectrumPeak[]): SpectrumPeakLegendItem[] {
  return PEAK_STYLES.flatMap(({ shortLabel, label, color }) => {
    const peak = peaks.find((item) => normalizedPeakLabel(item.label) === shortLabel)
    const wavelengthNm = peak?.peakWavelengthNm
    if (typeof wavelengthNm !== "number" || !Number.isFinite(wavelengthNm)) return []
    return [{
      label,
      color,
      value: `${wavelengthNm.toFixed(3)} nm`,
    }]
  })
}

function interpolateCrossing(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  target: number,
): number | null {
  if (y1 === target) return x1
  if (y2 === target) return x2
  if (y1 === y2) return null
  const ratio = (target - y1) / (y2 - y1)
  if (!Number.isFinite(ratio) || ratio < 0 || ratio > 1) return null
  const crossing = x1 + (x2 - x1) * ratio
  return Number.isFinite(crossing) ? crossing : null
}

export function spectrumFwhmAnnotation(
  wavelengthNm: readonly number[],
  intensity: readonly number[],
): SpectrumWidthAnnotation[] {
  if (wavelengthNm.length < 3 || wavelengthNm.length !== intensity.length) return []
  if (!wavelengthNm.every(Number.isFinite) || !intensity.every(Number.isFinite)) return []

  let peakIndex = 0
  for (let index = 1; index < intensity.length; index += 1) {
    if (intensity[index] > intensity[peakIndex]) peakIndex = index
  }
  const peakIntensity = intensity[peakIndex]
  if (peakIntensity <= 0) return []
  const halfMaximum = peakIntensity / 2

  let leftInsideIndex = peakIndex
  while (leftInsideIndex > 0 && intensity[leftInsideIndex - 1] >= halfMaximum) {
    leftInsideIndex -= 1
  }
  let rightInsideIndex = peakIndex
  while (rightInsideIndex < intensity.length - 1 && intensity[rightInsideIndex + 1] >= halfMaximum) {
    rightInsideIndex += 1
  }
  if (leftInsideIndex === 0 || rightInsideIndex === intensity.length - 1) return []

  const leftCrossing = interpolateCrossing(
    wavelengthNm[leftInsideIndex - 1],
    intensity[leftInsideIndex - 1],
    wavelengthNm[leftInsideIndex],
    intensity[leftInsideIndex],
    halfMaximum,
  )
  const rightCrossing = interpolateCrossing(
    wavelengthNm[rightInsideIndex],
    intensity[rightInsideIndex],
    wavelengthNm[rightInsideIndex + 1],
    intensity[rightInsideIndex + 1],
    halfMaximum,
  )
  if (leftCrossing === null || rightCrossing === null) return []

  const startX = Math.min(leftCrossing, rightCrossing)
  const endX = Math.max(leftCrossing, rightCrossing)
  const fwhmNm = endX - startX
  if (!Number.isFinite(fwhmNm) || fwhmNm <= 0) return []

  return [{
    label: `FWHM ${fwhmNm.toFixed(3)} nm`,
    startX,
    endX,
    y: halfMaximum,
    color: "#eab308",
  }]
}
