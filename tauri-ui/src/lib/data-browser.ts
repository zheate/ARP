export interface DataRecord {
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

export interface DataDetail {
  record: DataRecord
  points: DataPoint[]
  metadata: Record<string, string>
  warnings: string[]
  columnLabels: Record<string, string>
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
export function flowLabel(status: string): string {
  return FLOW_LABELS[status] ?? `未知状态（${status}）`
}
export function numberText(value: number | null | undefined, digits = 3): string {
  if (value == null || !Number.isFinite(value)) return "--"
  const text = value.toFixed(digits)
  return Number(text) === 0 ? (0).toFixed(digits) : text
}
export function recordLabel(record: DataRecord): string {
  return `${record.sn || record.fileName} · ${record.station || "站别未记录"} · ${new Date(record.timeMs).toLocaleString()}`
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
