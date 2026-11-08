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

const PEAK_STYLES = [
  { shortLabel: "P1", label: "Peak 1", color: "#3b82f6" },
  { shortLabel: "P2", label: "Peak 2", color: "var(--chart-1)" },
  { shortLabel: "P3", label: "Peak 3", color: "#9ca3af" },
] as const

function normalizedPeakLabel(label: string): string {
  return label.replace(/^PEAK\s*(\d+)$/i, "P$1").toUpperCase()
}

export function formatSpectrumNumber(value: number | null | undefined, digits = 3): string {
  if (value == null || !Number.isFinite(value)) return "--"
  return value.toFixed(digits).replace(/0+$/, "").replace(/\.$/, ".0")
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
      value: `${formatSpectrumNumber(wavelengthNm)} nm`,
    }]
  })
}
