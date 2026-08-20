export type SpectrumPeak = {
  label: string
  centroidNm: number
  peakWavelengthNm: number
  peakIntensity: number
}

export function spectrumPeakAnnotations(peaks: SpectrumPeak[]) {
  return peaks.map((peak) => ({
    label: peak.label,
    x: peak.peakWavelengthNm,
    y: peak.peakIntensity,
  }))
}
