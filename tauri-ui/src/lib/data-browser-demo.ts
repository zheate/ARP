import { DATA_METRICS, type DataDetail, type DataPoint, type DataRecord, type SpectrumSeries } from "./data-browser.ts"

// Shared browser/native demo fixtures. No filesystem, bridge, or database writes.
const profiles = [
  { slope: 1.68, shift: 0, width: 0.42, temperature: "25 °C" },
  { slope: 1.56, shift: 0.32, width: 0.54, temperature: "35 °C" },
  { slope: 1.78, shift: -0.18, width: 0.36, temperature: "25 °C" },
  { slope: 1.48, shift: 0.54, width: 0.65, temperature: "45 °C" },
  { slope: 1.73, shift: 0.12, width: 0.46, temperature: "25 °C" },
]
const now = new Date()
const start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 9).getTime()
const records: DataRecord[] = Array.from({ length: 55 }, (_, index) => {
  const profile = profiles[index % profiles.length]
  const timeMs = start - index * 8 * 3600_000
  const sn = `DEMO-LD-${String(Math.floor(index / 2) + 1).padStart(3, "0")}`
  return {
    id: `demo:record-${index + 1}`, storage: "database", sourcePath: `/demo/${sn}-${index + 1}.xlsx`,
    fileName: `${sn}-${index + 1}.xlsx`, version: "demo-v1", timeMs, modifiedAtMs: timeMs,
    timeSource: "session", sn, snSource: "metadata", station: index % 2 ? "封盖后" : "封盖前",
    stationId: "虚拟工位", temperature: profile.temperature, testMode: "automatic",
    sessionId: `demo-session-${index + 1}`, flowStatus: "completed", readStatus: "readable", error: "",
    pointCount: 10, validPointCount: 10, minimumCurrentA: 2, maximumCurrentA: 20,
  }
})

function detailFor(record: DataRecord, includeSpectra: boolean): DataDetail {
  const index = records.findIndex((item) => item.id === record.id)
  const profile = profiles[index % profiles.length]
  const points: DataPoint[] = Array.from({ length: 10 }, (_, step) => {
    const currentA = (step + 1) * 2
    const voltageV = 1.92 + currentA * 0.025 + (index % 5) * 0.035
    const powerW = Math.max(0, currentA - 1.7) * profile.slope * (1 - Math.max(0, currentA - 12) * 0.004)
    return {
      rowId: step + 1, currentA, voltageV, powerW, efficiencyPercent: powerW / (currentA * voltageV) * 100,
      peakWavelengthNm: 975.35 + currentA * 0.042 + profile.shift,
      centroidNm: 975.32 + currentA * 0.045 + profile.shift,
      fwhmNm: profile.width + currentA * 0.006,
      pibPercent: 98.5 - currentA * 0.065 - (index % 5) * 0.36,
      smsrDb: index % 5 === 3 ? null : 36.5 - currentA * 0.14 - (index % 5) * 0.8,
    }
  })
  const result: DataDetail = {
    record: { ...record }, points,
    metadata: { "数据性质": "虚拟演示，不代表实测结果", "测试条件": `${profile.temperature} · 连续电流扫描`, "会话编号": record.sessionId, "备注": "包含不同温度、不同站别和重复 SN 的示例，可筛选并选择最多五条记录对比。" },
    columnLabels: Object.fromEntries(DATA_METRICS.map((item) => [item.key, item.label])),
    warnings: ["当前为虚拟测试数据，仅用于展示界面，不会写入真实测试数据库。"],
  }
  if (includeSpectra) result.spectra = points.map((point): SpectrumSeries => {
    const wavelengthNm = Array.from({ length: 501 }, (_, i) => 970 + i * 0.024)
    const sigma = point.fwhmNm! / 2.35482
    const intensity = wavelengthNm.map((wavelength) => 50 + point.powerW! * 1100 * Math.exp(-0.5 * ((wavelength - point.peakWavelengthNm!) / sigma) ** 2))
    return { currentA: point.currentA, wavelengthNm, intensity, peakWavelengthNm: point.peakWavelengthNm!, maxIntensity: Math.max(...intensity) }
  })
  return result
}

export function demoDataRequest(method: string, params: Record<string, unknown>) {
  if (method === "dataBrowser.inspect") {
    const record = records.find((item) => item.id === params.recordId || item.sourcePath === params.path)
    if (!record) throw new Error("未找到虚拟测试记录")
    return detailFor(record, params.includeSpectra === true)
  }
  if (method === "dataBrowser.list") {
    const query = String(params.sn ?? "").trim().toLowerCase()
    const filtered = records.filter((record) => (!query || record.sn.toLowerCase().includes(query))
      && (!params.station || record.station === params.station)
      && (!params.status || record.flowStatus === params.status)
      && (params.fromMs == null || record.timeMs >= Number(params.fromMs))
      && (params.toMs == null || record.timeMs < Number(params.toMs)))
    const pageSize = Math.max(1, Math.min(200, Number(params.pageSize) || 50))
    const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize))
    const page = Math.max(1, Math.min(pageCount, Number(params.page) || 1))
    return { records: filtered.slice((page - 1) * pageSize, page * pageSize).map((record) => ({ ...record })), total: filtered.length,
      fileCount: records.length, page, pageCount, rootPath: "虚拟数据库（仅内存）", indexedAtMs: Date.now(),
      warnings: [], errorCount: 0, stations: ["封盖前", "封盖后"], statuses: ["completed"] }
  }
  throw new Error("演示模式仅展示虚拟记录，不读取或导入真实文件。")
}
