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
  /** Present on existing journal-backed database details; legacy workbooks use metadata. */
  session?: { software_version?: string; calculation_version?: string }
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
  if (!validTestTime(value)) return "时间未记录"
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
      const existing = result.get(key)
      if (existing) existing.push(point)
      else result.set(key, [point])
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
      const existing = result.get(key)
      if (existing) existing.push(point)
      else result.set(key, [point])
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
  const rows: Record<string, number | null>[] = []
  series.forEach((spectrum, index) => {
    if (!spectrum) return
    const key = `series${index}`
    // forEach keeps the previous flatMap semantics for holes in imported arrays.
    spectrum.wavelengthNm.forEach((wavelength, sample) => {
      const intensity = spectrum.intensity[sample]
      if (Number.isFinite(wavelength) && Number.isFinite(intensity)) {
        rows.push({ wavelengthNm: wavelength, [key]: intensity })
      }
    })
  })
  return rows.sort((a, b) => a.wavelengthNm! - b.wavelengthNm!)
}

export function finiteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value)
}

export interface LinearFit {
  slope: number
  intercept: number
  rSquared: number | null
  pointCount: number
  xMin: number
  xMax: number
}
export type FitResult = { fit: LinearFit; reason?: never } | { fit: null; reason: string }

/** Centred ordinary least squares, using native numbers, never rendered or rounded samples. */
export function linearRegression(samples: readonly { x: number | null; y: number | null }[]): FitResult {
  const points = samples.filter((p): p is { x: number; y: number } => finiteNumber(p.x) && finiteNumber(p.y))
  if (points.length < 2) return { fit: null, reason: "有效实测点少于 2 个" }
  // Subtract an origin before averaging to retain precision for large offsets.
  const origin = points[0]
  const meanX = points.reduce((sum, p) => sum + (p.x - origin.x) / points.length, 0)
  const meanY = points.reduce((sum, p) => sum + (p.y - origin.y) / points.length, 0)
  let xx = 0, xy = 0, yy = 0, xMin = Infinity, xMax = -Infinity
  for (const p of points) {
    const dx = p.x - origin.x - meanX, dy = p.y - origin.y - meanY
    xx += dx * dx; xy += dx * dy; yy += dy * dy
    xMin = Math.min(xMin, p.x); xMax = Math.max(xMax, p.x)
  }
  if (xx === 0) return { fit: null, reason: "有效测试点的电流全部相同" }
  const slope = xy / xx
  const intercept = origin.y + meanY - slope * (origin.x + meanX)
  const residual = points.reduce((sum, p) => {
    const error = p.y - origin.y - meanY - slope * (p.x - origin.x - meanX)
    return sum + error * error
  }, 0)
  // R² is undefined for a constant response; a valid zero slope is still useful.
  const rSquared = yy === 0 ? null : Math.max(0, Math.min(1, 1 - residual / yy))
  if (![slope, intercept, residual, xx, yy].every(Number.isFinite) || (rSquared !== null && !Number.isFinite(rSquared))) {
    return { fit: null, reason: "数值范围过大，无法可靠计算" }
  }
  return { fit: { slope, intercept, rSquared, pointCount: points.length, xMin, xMax } }
}

export function fitMetricRange(points: readonly DataPoint[], metric: DataMetricKey, a: number | null, b: number | null): FitResult {
  if (!finiteNumber(a) || !finiteNumber(b)) return { fit: null, reason: "请先选择 A 和 B 游标" }
  if (a === b) return { fit: null, reason: "A 与 B 电流相同，区间宽度为 0" }
  const low = Math.min(a, b), high = Math.max(a, b)
  return linearRegression(points.filter((p) => p.currentA >= low && p.currentA <= high)
    .map((p) => ({ x: p.currentA, y: p[metric] })))
}

export function cursorDifference(a: DataPoint | undefined, b: DataPoint | undefined, metric: DataMetricKey) {
  const deltaI = a && b && finiteNumber(a.currentA) && finiteNumber(b.currentA) ? b.currentA - a.currentA : null
  const deltaY = a && b && finiteNumber(a[metric]) && finiteNumber(b[metric]) ? b[metric] - a[metric] : null
  const slope = deltaI !== null && deltaI !== 0 && deltaY !== null ? deltaY / deltaI : null
  return { deltaI: finiteNumber(deltaI) ? deltaI : null, deltaY: finiteNumber(deltaY) ? deltaY : null,
    slope: finiteNumber(slope) ? slope : null }
}

/** Adjacent valid measured samples in current order; equal currents have no defined slope. */
export function adjacentSlopes(points: readonly DataPoint[], metric: DataMetricKey) {
  const valid = points.filter((p) => finiteNumber(p.currentA) && finiteNumber(p[metric])).slice().sort((a, b) => a.currentA - b.currentA)
  return valid.slice(1).flatMap((b, index) => {
    const a = valid[index], slope = cursorDifference(a, b, metric).slope
    return slope === null ? [] : [{ xMin: a.currentA, xMax: b.currentA, slope }]
  })
}

export function slopeExtent(points: readonly DataPoint[], metric: DataMetricKey) {
  const slopes = adjacentSlopes(points, metric)
  return slopes.length ? slopes.reduce((range, p) => ({ min: Math.min(range.min, p.slope), max: Math.max(range.max, p.slope) }),
    { min: Infinity, max: -Infinity }) : null
}

/** Shared six-decimal measured-current grouping. Duplicates remain visible but are never averaged. */
export function commonMeasuredCurrents(details: readonly Pick<DataDetail, "points">[]): number[] {
  if (!details.length) return []
  const sets = details.map((d) => new Set(d.points.filter((p) => finiteNumber(p.currentA)).map((p) => p.currentA.toFixed(6))))
  return [...sets[0]].filter((key) => sets.every((set) => set.has(key))).map(Number).sort((a, b) => a - b)
}

export function validTestTime(time: number | null | undefined): time is number {
  return finiteNumber(time) && Number.isFinite(new Date(time).getTime())
}

export function sameSnHistory(records: readonly DataRecord[], sn: string): DataRecord[] {
  if (!sn) return []
  return records.filter((r) => r.sn === sn).slice().sort((a, b) => {
    const at = validTestTime(a.timeMs) ? a.timeMs : Infinity
    const bt = validTestTime(b.timeMs) ? b.timeMs : Infinity
    const timeOrder = at === bt ? 0 : at < bt ? -1 : 1
    const tieA = a.sessionId || "", tieB = b.sessionId || ""
    return timeOrder || (tieA < tieB ? -1 : tieA > tieB ? 1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0)
  })
}

export type HistoryMetric = "maxPower" | DataMetricKey
export function historyMetricRows(details: readonly DataDetail[], sn: string, metric: HistoryMetric, current: number | null) {
  const byId = new Map(details.map((d) => [d.record.id, d]))
  return sameSnHistory(details.map((d) => d.record), sn).map((record) => {
    const detail = byId.get(record.id)!
    const maximum = extractSummaryMetrics(detail.points).maxPowerPoint
    const match = current === null ? null : measuredCurrentMatch(detail.points, current)
    const raw = metric === "maxPower" ? maximum?.powerW : match?.item?.[metric]
    const value = finiteNumber(raw) ? raw : null
    const currents = detail.points.map((p) => p.currentA).filter(finiteNumber)
    return { record, value, maxPowerPoint: maximum, maximumCurrentA: currents.length ? currents.reduce((a, b) => Math.max(a, b)) : null,
      status: metric === "maxPower" ? (value === null ? "缺测" : "最大实测功率") : match?.status === "重复电流点" ? match.status : value === null ? "缺测" : match!.status }
  })
}

export function firstLatestChange(rows: ReturnType<typeof historyMetricRows>, metric: HistoryMetric) {
  const dated = rows.filter((row) => validTestTime(row.record.timeMs))
  const first = dated[0], latest = dated[dated.length - 1]
  const rawDelta = dated.length >= 2 && first.value !== null && latest.value !== null ? latest.value - first.value : null
  const delta = finiteNumber(rawDelta) ? rawDelta : null
  // Wavelength, widths, ratios in percent and logarithmic dB do not get a relative percent.
  const relative = delta !== null && first.value !== null && first.value !== 0
    && (metric === "maxPower" || metric === "powerW" || metric === "voltageV") ? delta / first.value * 100 : null
  return { first, latest, delta, relative: finiteNumber(relative) ? relative : null }
}

// Keys verified against station_support.py, excel_export.py and data_library.py.
// Only known persisted fields are interpreted. No device identity is inferred from a resource string.
const CONDITION_FIELDS = [
  ["powerSupplyKind", "电源控制器"], ["tdkResource", "TDK 电源资源"], ["ruResource", "RU 电源资源"], ["ruBaudRate", "RU 波特率"], ["ruAddress", "RU 地址"], ["ruVoltageV", "RU 设定电压"], ["ch341SelectedLocationPath", "CH341 物理端口"],
  ["powerMeterResource", "功率计资源"], ["powerMeterSerialNumber", "功率计探头 SN", "功率计探头 SN"],
  ["powerMeterDriverKind", "功率计驱动", "功率计驱动"], ["powerMeterWavelengthNm", "功率计校准波长 (nm)", "功率计校准波长 (nm)"],
  ["softwareGain", "软件功率系数", "功率系数"], ["powerMeterProfileId", "校准方案 ID"],
  ["powerMeterProfileName", "校准方案名称", "系数方案"], ["powerMeterProfileVersion", "校准方案版本", "系数方案版本"],
  ["powerMeterProfileNotes", "校准条件备注", "方案条件备注"], ["spectrometerResource", "光谱仪资源"],
  ["spectrometerSerialNumber", "光谱仪 SN"], ["integrationTimeUs", "积分时间 (μs)"], ["autoIntegration", "自动积分"],
  ["spectrumAverageFrames", "光谱平均帧数"], ["spectrumSmoothing", "光谱平滑"],
  ["pibCenterWavelengthNm", "PIB 中心波长 (nm)"], ["pibHalfRangeNm", "PIB 半宽 (nm)"],
  ["stableWindowS", "稳定窗口 (s)"], ["stabilityLimitPercent", "稳定限值 (%)"],
  ["currentPlanMode", "电流计划模式"], ["initialCurrentA", "起始电流 (A)"], ["targetCurrentA", "目标电流 (A)"],
  ["currentStepA", "电流步进 (A)"], ["customCurrentPoints", "自定义电流点"],
  ["selectedScriptId", "脚本 ID"], ["selectedScriptPresetId", "脚本预设 ID"],
  ["软件版本", "软件版本"], ["算法版本", "算法版本"], ["出货报告格式版本", "文件格式版本"],
] as const
const NUMERIC_CONDITIONS = new Set([
  "powerMeterWavelengthNm", "softwareGain", "powerMeterProfileVersion", "integrationTimeUs", "spectrumAverageFrames",
  "pibCenterWavelengthNm", "pibHalfRangeNm", "stableWindowS", "stabilityLimitPercent", "initialCurrentA", "targetCurrentA", "currentStepA",
])

function objectValue(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value)
}
function canonicalValue(value: unknown): string | null {
  if (value === null || value === undefined || (typeof value === "number" && !Number.isFinite(value))) return null
  if (Array.isArray(value)) return `[${value.map((v) => canonicalValue(v) ?? "null").join(", ")}]`
  if (objectValue(value)) return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}: ${canonicalValue(value[key]) ?? "null"}`).join(", ")}}`
  const text = String(value).trim()
  if (!text || ["未记录", "None", "null"].includes(text)) return null
  if (["true", "false"].includes(text.toLowerCase())) return text.toLowerCase() === "true" ? "是" : "否"
  return text
}

function recordedConditions(detail: DataDetail) {
  const metadata = detail.metadata
  let settings: Record<string, unknown> = {}
  let malformed = false
  if (metadata["测试配置"]) {
    try { const value: unknown = JSON.parse(metadata["测试配置"]); if (objectValue(value)) settings = value; else malformed = true }
    catch { malformed = true }
  }
  const values = new Map<string, { label: string; value: string | null; identity?: boolean }>()
  const r = detail.record
  for (const [key, label, value, identity] of [
    ["testMode", "测试模式", r.testMode, false], ["station", "测试站", r.station, false],
    ["stationId", "工位编号", r.stationId, false], ["temperature", "测试温度", r.temperature, false],
    ["sessionId", "会话 ID", r.sessionId, true], ["recordVersion", "记录版本（缓存标识）", r.version, true],
  ] as const) values.set(key, { label, value: canonicalValue(value), identity })
  for (const [key, label, alias] of CONDITION_FIELDS) {
    const sessionVersion = key === "软件版本" ? detail.session?.software_version : key === "算法版本" ? detail.session?.calculation_version : null
    const candidates = [settings[key], metadata[key], alias ? metadata[alias] : null, sessionVersion].map(canonicalValue)
      .map((value) => value !== null && NUMERIC_CONDITIONS.has(key) && Number.isFinite(Number(value)) ? String(Number(value)) : value)
      .filter((v): v is string => v !== null)
    const distinct = [...new Set(candidates)]
    values.set(key, { label, value: distinct.length > 1 ? `记录内字段冲突：${distinct.join(" / ")}` : distinct[0] ?? null })
  }
  // Preserve actual device snapshots verbatim across both existing storage forms.
  let malformedDevices = false
  for (const role of ["电源", "功率计", "光谱仪"]) {
    let value = canonicalValue(metadata[role])
    if (metadata["设备信息"]) {
      try {
        const devices: unknown = JSON.parse(metadata["设备信息"])
        if (Array.isArray(devices)) {
          const device = devices.find((d): d is Record<string, unknown> => objectValue(d) && d.role === role)
          if (device) value = [device.kind, device.resource, device.detail].map(canonicalValue).filter(Boolean).join(" | ") || null
        } else malformedDevices = true
      } catch { malformedDevices = true }
    }
    values.set(`device:${role}`, { label: `${role}设备快照`, value })
  }
  if (malformed) values.set("rawConfiguration", { label: "测试配置（无法解析的原文）", value: metadata["测试配置"] })
  if (malformedDevices) values.set("rawDevices", { label: "设备信息（无法解析的原文）", value: metadata["设备信息"] })
  return values
}

export function compareMetadata(details: readonly DataDetail[], baselineRecordId: string | null) {
  const columns = details.map(recordedConditions)
  const baselineIndex = Math.max(0, details.findIndex((d) => d.record.id === baselineRecordId))
  const keys = [...new Set(columns.flatMap((map) => [...map.keys()]))]
  const rows = keys.flatMap((key) => {
    const field = columns.find((map) => map.has(key))!.get(key)!
    const values = columns.map((map) => map.get(key)?.value ?? null)
    const mandatory = ["testMode", "station", "stationId", "temperature", "sessionId", "recordVersion"].includes(key)
    if (!mandatory && values.every((v) => v === null)) return []
    const known = values.filter((v): v is string => v !== null)
    const different = new Set(known).size > 1
    const missing = values.some((v) => v === null)
    const baseline = values[baselineIndex]
    return [{ key, label: field.label, identity: !!field.identity, different, missing,
      cells: values.map((value, index) => ({ recordId: details[index].record.id, value,
        state: value === null ? "missing" as const : baseline === null ? "no-baseline" as const : value !== baseline ? "different" as const : "same" as const })) }]
  })
  return { rows, baselineIndex,
    differentCount: rows.filter((r) => r.different && !r.identity).length,
    identityCount: rows.filter((r) => r.different && r.identity).length,
    missingCount: rows.filter((r) => r.missing).length }
}
