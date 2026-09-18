export interface DataRecord {
  storage?: "database"
  id: string
  sourcePath: string
  fileName: string
  version: string
  modifiedAtMs: number
  timeMs: number
  timeSource: "session" | "modified"
  sn: string
  snSource: "metadata" | "directory" | "missing"
  station: string
  stationId: string
  temperature: string
  testMode: string
  sessionId: string
  flowStatus: string
  readStatus: "readable" | "error"
  error: string
  pointCount: number
  validPointCount: number
  minimumCurrentA: number | null
  maximumCurrentA: number | null
}

/** Unavailable reads as an em dash, never as a zero or a bare placeholder (DESIGN §6). */
export const UNAVAILABLE = "—"


export interface DataPoint {
  rowId: number
  currentA: number
  voltageV: number | null
  powerW: number | null
  efficiencyPercent: number | null
  peakWavelengthNm: number | null
  centroidNm: number | null
  fwhmNm: number | null
  pibPercent: number | null
  smsrDb: number | null
}

export interface SpectrumSeries {
  currentA: number
  wavelengthNm: number[]
  intensity: number[]
  peakWavelengthNm: number
  maxIntensity: number
}

export interface DataDetail {
  record: DataRecord
  points: DataPoint[]
  metadata: Record<string, string>
  warnings: string[]
  columnLabels: Record<string, string>
  spectra?: SpectrumSeries[] | null
}


export interface DataList {
  records: DataRecord[]
  total: number
  fileCount: number
  page: number
  pageCount: number
  rootPath: string
  indexedAtMs: number
  warnings: string[]
  errorCount: number
  stations: string[]
  statuses: string[]
}

const FLOW_LABELS: Record<string, string> = {
  completed: "已完成", running: "未完成", stopped_by_operator: "操作员停止",
  aborted_safely: "安全中止", shutdown_unconfirmed: "下电未确认",
  failed: "测试失败", error: "测试失败", unknown: "完成情况未知",
}
export function flowLabel(status: string | null | undefined): string {
  if (!status) return FLOW_LABELS.unknown
  return FLOW_LABELS[status] ?? `未知状态（${status}）`
}
export function numberText(value: number | null | undefined, digits = 3): string {
  if (value == null || !Number.isFinite(value)) return UNAVAILABLE
  const text = value.toFixed(digits)
  return Number(text) === 0 ? (0).toFixed(digits) : text
}
/** One sortable stamp for every surface: `YYYY-MM-DD HH:mm:ss` (DESIGN §15). */
export function timeText(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "时间未记录"
  const date = new Date(value)
  const pad = (part: number) => String(part).padStart(2, "0")
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}
export function recordLabel(record: DataRecord): string {
  return `${record.sn || record.fileName} · ${record.station || "站别未记录"} · ${timeText(record.timeMs)}`
}

export function comparisonRows(base: DataPoint[], other: DataPoint[]) {
  const group = (points: DataPoint[]) => {
    const result = new Map<string, DataPoint[]>()
    for (const point of points) {
      if (!Number.isFinite(point.currentA) || point.powerW == null || !Number.isFinite(point.powerW)) continue
      const key = point.currentA.toFixed(6)
      result.set(key, [...(result.get(key) ?? []), point])
    }
    return result
  }
  const left = group(base), right = group(other)
  return [...left.keys()].filter((key) => right.has(key)).sort((a, b) => Number(a) - Number(b)).map((key) => {
    const a = left.get(key)!, b = right.get(key)!
    const ambiguous = a.length !== 1 || b.length !== 1
    const basePowerW = ambiguous ? null : a[0].powerW!
    const otherPowerW = ambiguous ? null : b[0].powerW!
    const deltaW = basePowerW === null || otherPowerW === null ? null : otherPowerW - basePowerW
    return { currentA: Number(key), basePowerW, otherPowerW, deltaW, ambiguous,
      deltaPercent: deltaW === null || basePowerW === 0 ? null : deltaW / basePowerW! * 100 }
  })
}

export function powerChartRows(details: DataDetail[]) {
  return details.flatMap((detail, index) => detail.points
    .filter((point) => Number.isFinite(point.currentA) && point.powerW !== null && Number.isFinite(point.powerW))
    .map((point) => ({ currentA: point.currentA, base: index === 0 ? point.powerW : null, other: index === 1 ? point.powerW : null })))
    .sort((a, b) => a.currentA - b.currentA)
}

/** Each async owner has its own guard; invalidation also covers unmount/hidden pages. */
export function requestGuard() {
  let sequence = 0
  return { next: () => ++sequence, current: (ticket: number) => ticket === sequence, invalidate: () => { sequence++ } }
}

export function dateBounds(mode: string, from: string, to: string, now = new Date()): { fromMs?: number; toMs?: number } {
  if (mode === "all") return {}
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const end = new Date(start); end.setDate(end.getDate() + 1)
  if (mode === "week") start.setDate(start.getDate() - 6)
  if (mode === "custom") {
    if (!from || !to) throw new Error("请选择开始日期和结束日期")
    const parse = (value: string) => {
      const parts = value.split("-").map(Number)
      const parsed = new Date(parts[0], parts[1] - 1, parts[2])
      if (parts.length !== 3 || !Number.isFinite(parsed.getTime()) || parsed.getFullYear() !== parts[0] || parsed.getMonth() !== parts[1] - 1 || parsed.getDate() !== parts[2]) throw new Error("日期无效")
      return parsed
    }
    const first = parse(from), last = parse(to)
    if (first > last) throw new Error("开始日期不能晚于结束日期")
    last.setDate(last.getDate() + 1)
    return { fromMs: first.getTime(), toMs: last.getTime() }
  }
  return { fromMs: start.getTime(), toMs: end.getTime() }
}

export interface SummaryMetrics {
  maxPowerPoint: DataPoint | null
  maxEfficiencyPoint: DataPoint | null
  primaryWavelengthNm: number | null
}

export function extractSummaryMetrics(points: DataPoint[]): SummaryMetrics {
  let maxPowerPoint: DataPoint | null = null
  let maxEfficiencyPoint: DataPoint | null = null

  for (const point of points) {
    if (point.powerW != null && Number.isFinite(point.powerW)) {
      if (!maxPowerPoint || point.powerW > maxPowerPoint.powerW!) {
        maxPowerPoint = point
      }
    }
    if (point.efficiencyPercent != null && Number.isFinite(point.efficiencyPercent)) {
      if (!maxEfficiencyPoint || point.efficiencyPercent > maxEfficiencyPoint.efficiencyPercent!) {
        maxEfficiencyPoint = point
      }
    }
  }

  const primaryWavelengthNm = maxPowerPoint?.peakWavelengthNm ?? maxPowerPoint?.centroidNm ?? null

  return { maxPowerPoint, maxEfficiencyPoint, primaryWavelengthNm }
}

export interface ComparisonSummary {
  baseMaxPowerW: number | null
  baseMaxCurrentA: number | null
  otherMaxPowerW: number | null
  otherMaxCurrentA: number | null
  deltaMaxPowerW: number | null
  deltaMaxPowerPercent: number | null
  sharedPointCount: number
}

export function extractComparisonSummary(basePoints: DataPoint[], otherPoints: DataPoint[]): ComparisonSummary {
  const baseSummary = extractSummaryMetrics(basePoints)
  const otherSummary = extractSummaryMetrics(otherPoints)
  const diffs = comparisonRows(basePoints, otherPoints)
  const sharedPointCount = diffs.filter((d) => !d.ambiguous && d.deltaW !== null).length

  const baseMax = baseSummary.maxPowerPoint?.powerW ?? null
  const otherMax = otherSummary.maxPowerPoint?.powerW ?? null
  const deltaMaxPowerW = baseMax != null && otherMax != null ? otherMax - baseMax : null
  const deltaMaxPowerPercent = deltaMaxPowerW != null && baseMax != null && baseMax > 0 ? (deltaMaxPowerW / baseMax) * 100 : null

  return {
    baseMaxPowerW: baseMax,
    baseMaxCurrentA: baseSummary.maxPowerPoint?.currentA ?? null,
    otherMaxPowerW: otherMax,
    otherMaxCurrentA: otherSummary.maxPowerPoint?.currentA ?? null,
    deltaMaxPowerW,
    deltaMaxPowerPercent,
    sharedPointCount,
  }
}

export const DATA_METRICS = [
  { key: "powerW", label: "功率", unit: "W" },
  { key: "voltageV", label: "电压", unit: "V" },
  { key: "efficiencyPercent", label: "电光效率", unit: "%" },
  { key: "peakWavelengthNm", label: "峰值波长", unit: "nm" },
  { key: "centroidNm", label: "质心波长", unit: "nm" },
  { key: "fwhmNm", label: "FWHM", unit: "nm" },
  { key: "pibPercent", label: "PIB", unit: "%" },
  { key: "smsrDb", label: "SMSR", unit: "dB" },
] as const
export type DataMetricKey = typeof DATA_METRICS[number]["key"]
export const SERIES_COLORS = ["#ed7624", "#3788df", "#31a58c", "#a776cf", "#d75481"]
export const MAX_COMPARISONS = 5

export function metricChartRows(details: DataDetail[], metric: DataMetricKey): Record<string, number | null>[] {
  return details.flatMap((detail, index) => detail.points
    .filter((point) => Number.isFinite(point.currentA) && point[metric] != null && Number.isFinite(point[metric]))
    .map((point) => ({ currentA: point.currentA, [`series${index}`]: point[metric] })))
    .sort((a, b) => a.currentA - b.currentA)
}

/** Match measured currents only. Repeated currents are ambiguous, even if one value is missing. */
export function metricComparisonRows(details: DataDetail[], metric: DataMetricKey) {
  const groups = details.map((detail) => {
    const result = new Map<string, DataPoint[]>()
    for (const point of detail.points) {
      if (!Number.isFinite(point.currentA)) continue
      const key = point.currentA.toFixed(6)
      result.set(key, [...(result.get(key) ?? []), point])
    }
    return result
  })
  const currents = [...new Set(groups.flatMap((group) => [...group.keys()]))].sort((a, b) => Number(a) - Number(b))
  return currents.map((current) => {
    const ambiguous = groups.map((group) => (group.get(current)?.length ?? 0) > 1)
    const values = groups.map((group, index) => {
      const value = group.get(current)?.[0]?.[metric]
      return ambiguous[index] || value == null || !Number.isFinite(value) ? null : value
    })
    const base = values[0]
    return { currentA: Number(current), values, ambiguous,
      deltas: values.map((value) => value == null || base == null ? null : value - base) }
  })
}

/** A wavelength near 976 nm needs its measured range, otherwise drift looks flat. */
export function metricYAxisDomain(details: DataDetail[], metric: DataMetricKey): [number, number] | undefined {
  if (metric === "powerW") return undefined
  let low = Infinity, high = -Infinity
  for (const detail of details) for (const point of detail.points) {
    const value = point[metric]
    if (value == null || !Number.isFinite(value)) continue
    low = Math.min(low, value); high = Math.max(high, value)
  }
  if (!Number.isFinite(low)) return undefined
  const minimumPadding = metric === "efficiencyPercent" || metric === "pibPercent" || metric === "smsrDb" ? 0.2 : 0.02
  const padding = Math.max((high - low) * 0.12, minimumPadding)
  return [low - padding, high + padding]
}

/** Same six-decimal current grouping as the existing comparison, with its provenance exposed. */
export function measuredCurrentMatch<T extends { currentA: number }>(items: readonly T[], currentA: number) {
  const matches = items.filter((item) => Number.isFinite(item.currentA)
    && item.currentA.toFixed(6) === currentA.toFixed(6))
  if (matches.length > 1) return { item: null, status: "重复电流点" } as const
  if (!matches.length) return { item: null, status: "缺测" } as const
  return { item: matches[0], status: matches[0].currentA === currentA ? "精确实测点" : "容差匹配" } as const
}

/** Date and time distinguish repeated SNs; a stable ID disambiguates simultaneous sessions. */
export function comparisonLabels(records: DataRecord[]): Map<string, string> {
  const labels = records.map((record) => `${record.sn || record.fileName} · ${timeText(record.timeMs)}`)
  return new Map(records.map((record, index) => [record.id,
    labels.filter((label) => label === labels[index]).length > 1
      ? `${labels[index]} · ${index + 1} (${(record.sessionId || record.id).slice(-8)})`
      : labels[index],
  ]))
}

/** Display only: retain each spectrum's own wavelength grid and every valid native sample. */
export function spectrumChartRows(series: readonly (SpectrumSeries | null)[]): Record<string, number | null>[] {
  return series.flatMap((spectrum, index) => spectrum ? spectrum.wavelengthNm.flatMap((wavelength, sample) => {
    const intensity = spectrum.intensity[sample]
    return Number.isFinite(wavelength) && Number.isFinite(intensity)
      ? [{ wavelengthNm: wavelength, [`series${index}`]: intensity }]
      : []
  }) : []).sort((a, b) => a.wavelengthNm - b.wavelengthNm)
}
