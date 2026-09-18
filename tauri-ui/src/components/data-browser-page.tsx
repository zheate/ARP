import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  ArrowLeftRight,
  BarChart3,
  ChevronLeft,
  ChevronRight,
  Database,
  FolderOpen,
  Maximize2,
  Minimize2,
  RefreshCw,
  Search,
  X,
} from "lucide-react"
import { open as openDialog } from "@tauri-apps/plugin-dialog"
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener"
import { Button } from "@/components/ui/button"
import {
  CanvasLineChart,
  type CanvasChartLine,
} from "@/components/canvas-line-chart"
import { SpectralWaterfallChart } from "@/components/spectral-waterfall-chart"
import { sendBackendRequest } from "@/lib/backend"
import { openResultFile } from "@/lib/result-file"
import {
  DATA_METRICS,
  MAX_COMPARISONS,
  SERIES_COLORS,
  comparisonLabels,
  dateBounds,
  extractSummaryMetrics,
  flowLabel,
  measuredCurrentMatch,
  metricChartRows,
  metricComparisonRows,
  metricYAxisDomain,
  numberText,
  recordLabel,
  requestGuard,
  spectrumChartRows,
  timeText,
  type DataDetail,
  type DataList,
  type DataMetricKey,
  type DataRecord,
  type SpectrumSeries,
} from "@/lib/data-browser"
import "./data-browser.css"

const DIRECTORY_KEY = "power-test.data-browser.directory.v1"
const PAGE_SIZE = 50
const EMPTY_DETAILS: DataDetail[] = []
type CurveView = "metrics" | "spectrum" | "multiSpectrum" | "evolution"
type DetailLoader = (
  record: DataRecord,
  spectra?: boolean,
) => Promise<DataDetail>
function storedDirectory() {
  try {
    return localStorage.getItem(DIRECTORY_KEY) ?? ""
  } catch {
    return ""
  }
}
function message(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}
function finite(value: number | null | undefined): value is number {
  return value != null && Number.isFinite(value)
}
function recordKey(record: DataRecord) {
  return JSON.stringify([record.id, record.version])
}
function modeText(mode: string) {
  return (
    ({ automatic: "自动测试", manual: "手动测试" } as Record<string, string>)[
      mode
    ] ||
    mode ||
    "未记录"
  )
}
function currentRange(record: DataRecord) {
  return finite(record.minimumCurrentA) && finite(record.maximumCurrentA)
    ? `${numberText(record.minimumCurrentA, 2)}–${numberText(record.maximumCurrentA, 2)} A`
    : "未记录"
}
function signed(value: number | null) {
  return finite(value)
    ? `${value > 0 ? "+" : ""}${numberText(value)}`
    : "不可计算"
}

function PointNavigator({
  values,
  index,
  onChange,
  comparing,
}: {
  values: number[]
  index: number
  onChange: (index: number) => void
  comparing: boolean
}) {
  const label = comparing ? "当前比较电流" : "当前测试点"
  return (
    <div className="db-point-navigator" aria-label="定位实测点">
      <span>定位实测点</span>
      <Button
        size="sm"
        variant="ghost"
        aria-label="上一个实测点"
        disabled={index <= 0}
        onClick={() => onChange(index - 1)}
      >
        <ChevronLeft size={15} />
      </Button>
      <input
        type="range"
        aria-label="实测点位置"
        min={0}
        max={Math.max(0, values.length - 1)}
        step={1}
        value={Math.max(0, index)}
        disabled={values.length < 2}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      <Button
        size="sm"
        variant="ghost"
        aria-label="下一个实测点"
        disabled={index >= values.length - 1 || !values.length}
        onClick={() => onChange(index + 1)}
      >
        <ChevronRight size={15} />
      </Button>
      <select
        aria-label={label}
        value={Math.max(0, index)}
        disabled={!values.length}
        onChange={(event) => onChange(Number(event.target.value))}
      >
        {values.map((current, position) => (
          <option key={position} value={position}>
            #{position + 1} · {numberText(current, comparing ? 6 : 3)} A
          </option>
        ))}
      </select>
      <span className="db-point-count">
        {values.length ? index + 1 : 0} / {values.length}
      </span>
    </div>
  )
}

/** Versioned, bounded page-local cache. List summaries and detail owners share in-flight reads. */
function useDetailLoader(refresh: number): DetailLoader {
  const cache = useMemo(
    () => ({
      generation: refresh,
      points: new Map<string, DataDetail>(),
      spectra: new Map<string, DataDetail>(),
      pending: new Map<string, Promise<DataDetail>>(),
    }),
    [refresh],
  )
  return useCallback(
    (record: DataRecord, spectra = false) => {
      const key = recordKey(record)
      const cached =
        cache.spectra.get(key) ?? (!spectra ? cache.points.get(key) : undefined)
      if (cached) return Promise.resolve(cached)
      const requestKey = `${key}:${spectra}`
      const pending =
        cache.pending.get(`${key}:true`) ?? cache.pending.get(requestKey)
      if (pending) return pending
      const request = sendBackendRequest<DataDetail>("dataBrowser.inspect", {
        ...(record.storage === "database"
          ? { recordId: record.id }
          : { path: record.sourcePath, version: record.version }),
        includeSpectra: spectra,
      })
        .then((detail) => {
          // Keep arrays in a small separate cache; list summaries never retain spectra.
          const { spectra: _spectra, ...plain } = detail
          cache.points.set(key, plain)
          if (spectra) cache.spectra.set(key, detail)
          while (cache.points.size > 120)
            cache.points.delete(cache.points.keys().next().value!)
          while (cache.spectra.size > 10)
            cache.spectra.delete(cache.spectra.keys().next().value!)
          return detail
        })
        .finally(() => cache.pending.delete(requestKey))
      cache.pending.set(requestKey, request)
      return request
    },
    [cache],
  )
}

const RecordRow = memo(function RecordRow({
  record,
  current,
  checked,
  limit,
  active,
  load,
  onView,
  onToggle,
}: {
  record: DataRecord
  current: boolean
  checked: boolean
  limit: boolean
  active: boolean
  load: DetailLoader
  onView: (record: DataRecord) => void
  onToggle: (record: DataRecord) => void
}) {
  const element = useRef<HTMLDivElement>(null)
  const [summary, setSummary] = useState<ReturnType<
    typeof extractSummaryMetrics
  > | null>(null)
  const [error, setError] = useState("")
  useEffect(() => {
    if (!active || record.readStatus !== "readable" || !element.current) return
    let cancelled = false
    // Only visible rows request lightweight details, never an entire directory's workbooks.
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return
      observer.disconnect()
      setSummary(null)
      setError("")
      void load(record)
        .then((detail) => {
          if (!cancelled) setSummary(extractSummaryMetrics(detail.points))
        })
        .catch((reason) => {
          if (!cancelled) setError(message(reason))
        })
    })
    observer.observe(element.current)
    return () => {
      cancelled = true
      observer.disconnect()
    }
  }, [active, record, load])
  const peak = summary?.maxPowerPoint
  const disabledReason = !active
    ? "数据浏览不可用"
    : record.readStatus === "error" || !record.pointCount
      ? "没有可比较的测试点"
      : limit && !checked
        ? "最多比较 5 条记录"
        : "加入对比"
  return (
    <div ref={element} className="db-record-item" data-selected={current}>
      <span className="db-check" title={disabledReason}>
        <input
          type="checkbox"
          aria-label={`加入对比 ${recordLabel(record)}`}
          checked={checked}
          disabled={
            !active ||
            record.readStatus === "error" ||
            !record.pointCount ||
            (limit && !checked)
          }
          onChange={() => onToggle(record)}
        />
      </span>
      <button
        className="db-record-button"
        disabled={!active}
        onClick={() => onView(record)}
        aria-current={current ? "true" : undefined}
        title={`${recordLabel(record)} · ${flowLabel(record.flowStatus)} · 电流范围 ${currentRange(record)}${error ? ` · ${error}` : ""}`}
      >
        <span className="db-record-top">
          <strong>{record.sn || record.fileName}</strong>
          <span className="db-record-result" title="最大功率及对应实测电流">
            {peak && finite(peak.currentA)
              ? `${numberText(peak.powerW, 1)} W @ ${numberText(peak.currentA, 1)} A`
              : record.readStatus === "error" || error
                ? "读取失败"
                : summary
                  ? "功率未记录"
                  : "摘要读取中…"}
          </span>
        </span>
        <span className="db-record-bottom">
          <time>{timeText(record.timeMs)}</time>
          <span>
            {modeText(record.testMode)} · {record.pointCount} 点
          </span>
        </span>
      </button>
    </div>
  )
})

export const DataBrowserPage = memo(function DataBrowserPage({
  visible,
  defaultDirectory,
  enabled,
  disabledReason,
}: {
  visible: boolean
  defaultDirectory: string
  enabled: boolean
  disabledReason?: string
}) {
  const [source, setSource] = useState<"database" | "excel">("database")
  const [root, setRoot] = useState(storedDirectory)
  const [directoryDraft, setDirectoryDraft] = useState(storedDirectory)
  const [sourceOpen, setSourceOpen] = useState(false)
  const [sn, setSn] = useState("")
  const [station, setStation] = useState("")
  const [status, setStatus] = useState("")
  const [testMode, setTestMode] = useState("")
  const [dateMode, setDateMode] = useState("all")
  const [from, setFrom] = useState("")
  const [to, setTo] = useState("")
  const [page, setPage] = useState(1)
  const [refresh, setRefresh] = useState(0)
  const [listing, setListing] = useState<DataList | null>(null)
  const [listBusy, setListBusy] = useState(false)
  const [listError, setListError] = useState("")
  const [currentRecord, setCurrentRecord] = useState<DataRecord | null>(null)
  const [singleDetail, setSingleDetail] = useState<DataDetail | null>(null)
  const [detailBusy, setDetailBusy] = useState(false)
  const [detailError, setDetailError] = useState("")
  const [pointRows, setPointRows] = useState<Record<string, number>>({})
  const [compareRecordIds, setCompareRecordIds] = useState<string[]>([])
  const [baselineRecordId, setBaselineRecordId] = useState<string | null>(null)
  const [recordColorMap, setRecordColorMap] = useState<Record<string, string>>(
    {},
  )
  const [compareDetails, setCompareDetails] = useState<DataDetail[]>([])
  const [compareBusy, setCompareBusy] = useState(false)
  const [compareError, setCompareError] = useState("")
  const [compareCurrent, setCompareCurrent] = useState<number | null>(null)
  const [comparing, setComparing] = useState(false)
  const [tab, setTab] = useState<"curves" | "points" | "info">("curves")
  const [curveView, setCurveView] = useState<CurveView>("metrics")
  const [singleMetric, setSingleMetric] = useState<DataMetricKey>("powerW")
  const [compareMetric, setCompareMetric] = useState<DataMetricKey>("powerW")
  const [spectrumDetails, setSpectrumDetails] = useState<DataDetail[]>([])
  const [spectraBusy, setSpectraBusy] = useState(false)
  const [spectraError, setSpectraError] = useState("")
  const [spectraRetry, setSpectraRetry] = useState(0)
  const [spectrumChoices, setSpectrumChoices] = useState<
    Record<string, number[]>
  >({})
  const [expanded, setExpanded] = useState(false)
  const [fileBusy, setFileBusy] = useState(false)
  const [importBusy, setImportBusy] = useState(false)
  const [notice, setNotice] = useState("")
  const [importWarnings, setImportWarnings] = useState<string[]>([])
  const importLock = useRef(false)
  const fileLock = useRef(false)
  const listGuard = useRef(requestGuard())
  const scanStamp = useRef("")
  const recordRegistry = useRef(new Map<string, DataRecord>())
  const filteredIndex = useRef<{
    key: string
    result: DataList
    records: DataRecord[]
  } | null>(null)
  const detailScroll = useRef<HTMLDivElement>(null)
  const recordsScroll = useRef<HTMLDivElement>(null)
  const loadDetail = useDetailLoader(refresh)
  const demoComparisonPending = useRef(
    typeof window !== "undefined" &&
      new URLSearchParams(window.location.search).get("demo") === "1" &&
      new URLSearchParams(window.location.search).get("compare") === "1",
  )

  useEffect(() => {
    if (!root && defaultDirectory) {
      setRoot(defaultDirectory)
      setDirectoryDraft(defaultDirectory)
    }
  }, [root, defaultDirectory])
  useEffect(() => {
    if (visible) setRefresh((value) => value + 1)
  }, [visible])
  useEffect(() => {
    if (detailScroll.current) detailScroll.current.scrollTop = 0
  }, [currentRecord?.id, comparing, tab])
  useEffect(() => {
    if (recordsScroll.current) recordsScroll.current.scrollTop = 0
  }, [page])

  useEffect(() => {
    const guard = listGuard.current,
      ticket = guard.next()
    if (!visible || !enabled || (source === "excel" && !root)) {
      setListBusy(false)
      return
    }
    setListBusy(true)
    setListError("")
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const dates = dateBounds(dateMode, from, to)
          const stamp = `${source}|${root}|${refresh}`
          const params = {
            source,
            rootPath: root,
            sn,
            station,
            status,
            ...dates,
          }
          let result: DataList
          if (testMode) {
            // The existing contract has no test-mode filter. Filter all matching summary pages locally.
            const indexKey = JSON.stringify([params, refresh])
            let index = filteredIndex.current
            if (index?.key !== indexKey) {
              const first = await sendBackendRequest<DataList>(
                "dataBrowser.list",
                {
                  ...params,
                  refresh: scanStamp.current !== stamp,
                  page: 1,
                  pageSize: 200,
                },
              )
              const records = [...first.records]
              for (let next = 2; next <= first.pageCount; next++) {
                if (!guard.current(ticket)) return
                const batch = await sendBackendRequest<DataList>(
                  "dataBrowser.list",
                  { ...params, refresh: false, page: next, pageSize: 200 },
                )
                records.push(...batch.records)
              }
              index = { key: indexKey, result: first, records }
              if (guard.current(ticket)) filteredIndex.current = index
            }
            const records = index.records.filter((record) =>
              testMode === "unrecorded"
                ? !record.testMode
                : record.testMode === testMode,
            )
            const pageCount = Math.max(
                1,
                Math.ceil(records.length / PAGE_SIZE),
              ),
              currentPage = Math.min(page, pageCount)
            result = {
              ...index.result,
              total: records.length,
              pageCount,
              page: currentPage,
              records: records.slice(
                (currentPage - 1) * PAGE_SIZE,
                currentPage * PAGE_SIZE,
              ),
            }
          } else {
            result = await sendBackendRequest<DataList>("dataBrowser.list", {
              ...params,
              refresh: scanStamp.current !== stamp,
              page,
              pageSize: PAGE_SIZE,
            })
          }
          if (!guard.current(ticket)) return
          scanStamp.current = stamp
          for (const record of result.records)
            recordRegistry.current.set(record.id, record)
          setListing(result)
          setPage(result.page)
          if (demoComparisonPending.current && result.records.length >= 2) {
            demoComparisonPending.current = false
            const records = result.records
              .filter(
                (record) =>
                  record.readStatus === "readable" && record.pointCount > 0,
              )
              .slice(0, MAX_COMPARISONS)
            setCompareRecordIds(records.map((record) => record.id))
            setBaselineRecordId(records[0]?.id ?? null)
            setRecordColorMap(
              Object.fromEntries(
                records.map((record, index) => [
                  record.id,
                  SERIES_COLORS[index],
                ]),
              ),
            )
            setComparing(records.length >= 2)
          }
          setCurrentRecord((previous) => {
            const next =
              result.records.find((record) => record.id === previous?.id) ??
              result.records.find(
                (record) => record.readStatus === "readable",
              ) ??
              null
            return previous && next && recordKey(previous) === recordKey(next)
              ? previous
              : next
          })
        } catch (error) {
          if (guard.current(ticket)) {
            setListError(message(error))
            setListing(null)
          }
        } finally {
          if (guard.current(ticket)) setListBusy(false)
        }
      })()
    }, 250)
    return () => {
      clearTimeout(timer)
      guard.invalidate()
    }
  }, [
    visible,
    enabled,
    source,
    root,
    sn,
    station,
    status,
    testMode,
    dateMode,
    from,
    to,
    page,
    refresh,
  ])

  // Viewing a record has no dependency on basket selection, baseline, metric or spectrum mode.
  useEffect(() => {
    let cancelled = false
    setDetailError("")
    setDetailBusy(false)
    if (!visible || !enabled || !currentRecord) {
      setSingleDetail(null)
      return
    }
    setSingleDetail(null)
    setDetailBusy(true)
    void loadDetail(currentRecord)
      .then((detail) => {
        if (!cancelled) setSingleDetail(detail)
      })
      .catch((error) => {
        if (!cancelled) setDetailError(message(error))
      })
      .finally(() => {
        if (!cancelled) setDetailBusy(false)
      })
    return () => {
      cancelled = true
    }
  }, [visible, enabled, currentRecord, loadDetail])

  const compareTargets = useMemo(
    () =>
      compareRecordIds.flatMap((id) => {
        const record =
          listing?.records.find((item) => item.id === id) ??
          recordRegistry.current.get(id)
        return record ? [record] : []
      }),
    [compareRecordIds, listing],
  )
  const singleTargets = useMemo(
    () => (currentRecord ? [currentRecord] : []),
    [currentRecord],
  )
  const targets = comparing ? compareTargets : singleTargets
  useEffect(() => {
    let cancelled = false
    setCompareError("")
    setCompareBusy(false)
    if (!visible || !enabled || !comparing || compareTargets.length < 2) return
    setCompareBusy(true)
    void Promise.all(compareTargets.map((record) => loadDetail(record)))
      .then((details) => {
        if (!cancelled) setCompareDetails(details)
      })
      .catch((error) => {
        if (!cancelled) setCompareError(message(error))
      })
      .finally(() => {
        if (!cancelled) setCompareBusy(false)
      })
    return () => {
      cancelled = true
    }
  }, [visible, enabled, comparing, compareTargets, loadDetail])

  const needsSpectra = tab === "curves" && curveView !== "metrics"
  useEffect(() => {
    let cancelled = false
    setSpectraBusy(false)
    setSpectraError("")
    if (!visible || !enabled || !needsSpectra || !targets.length) return
    setSpectraBusy(true)
    setSpectrumDetails([])
    // A missing spectrum from one record must not hide the others.
    void Promise.allSettled(targets.map((record) => loadDetail(record, true)))
      .then((results) => {
        if (cancelled) return
        setSpectrumDetails(
          results.flatMap((result) =>
            result.status === "fulfilled" ? [result.value] : [],
          ),
        )
        setSpectraError(
          results
            .flatMap((result, index) =>
              result.status === "rejected"
                ? [`${recordLabel(targets[index])}：${message(result.reason)}`]
                : [],
            )
            .join("；"),
        )
      })
      .finally(() => {
        if (!cancelled) setSpectraBusy(false)
      })
    return () => {
      cancelled = true
    }
  }, [visible, enabled, needsSpectra, targets, loadDetail, spectraRetry])

  function resetSelection() {
    listGuard.current.invalidate()
    setListing(null)
    setCurrentRecord(null)
    setSingleDetail(null)
    setCompareDetails([])
    setSpectrumDetails([])
    setCompareRecordIds([])
    setBaselineRecordId(null)
    setComparing(false)
    setCompareCurrent(null)
    setPointRows({})
    setSpectrumChoices({})
    setPage(1)
    setStation("")
    setStatus("")
    setTestMode("")
    setTab("curves")
    setCurveView("metrics")
    recordRegistry.current.clear()
    filteredIndex.current = null
  }
  function changeSource(next: "database" | "excel") {
    if (next === source) return
    resetSelection()
    setSource(next)
    setNotice("")
    setImportWarnings([])
  }
  function applyDirectory(value: string) {
    const next = value.trim()
    if (!next) {
      setListError("请输入或选择数据目录")
      return
    }
    resetSelection()
    setRoot(next)
    setDirectoryDraft(next)
    setRefresh((value) => value + 1)
    try {
      localStorage.setItem(DIRECTORY_KEY, next)
    } catch {
      /* Keep this session's directory. */
    }
  }
  async function chooseDirectory() {
    try {
      const path = await openDialog({
        directory: true,
        multiple: false,
        title: "选择测试 Excel 目录",
        defaultPath: root || undefined,
      })
      if (typeof path === "string") applyDirectory(path)
    } catch (error) {
      setListError(message(error))
    }
  }
  async function importRecords() {
    if (importLock.current || !enabled || !root) return
    importLock.current = true
    setImportBusy(true)
    setNotice("")
    setImportWarnings([])
    try {
      const result = await sendBackendRequest<{
        imported: number
        skipped: number
        warnings: string[]
      }>("dataBrowser.import", { rootPath: root })
      resetSelection()
      setSource("database")
      setRefresh((value) => value + 1)
      setNotice(
        `已导入 ${result.imported} 条 · 跳过 ${result.skipped} 条重复、未完成或无有效数据的记录`,
      )
      setImportWarnings(result.warnings)
    } catch (error) {
      setNotice(`导入未完成：${message(error)}`)
    } finally {
      importLock.current = false
      setImportBusy(false)
    }
  }
  function toggleCompare(record: DataRecord) {
    if (!enabled) return
    if (compareRecordIds.includes(record.id)) {
      const next = compareRecordIds.filter((id) => id !== record.id)
      setCompareRecordIds(next)
      if (baselineRecordId === record.id) setBaselineRecordId(next[0] ?? null)
      if (next.length < 2) {
        setComparing(false)
        setTab("curves")
        setCurveView("metrics")
      }
    } else if (compareRecordIds.length < MAX_COMPARISONS) {
      recordRegistry.current.set(record.id, record)
      const used = new Set(compareRecordIds.map((id) => recordColorMap[id]))
      const remembered = recordColorMap[record.id]
      const color =
        remembered && !used.has(remembered)
          ? remembered
          : SERIES_COLORS.find((value) => !used.has(value))!
      setRecordColorMap((previous) => ({ ...previous, [record.id]: color }))
      setCompareRecordIds((previous) => [...previous, record.id])
      if (!baselineRecordId) setBaselineRecordId(record.id)
    } else {
      setNotice("最多比较 5 条记录")
    }
  }
  function clearCompare() {
    setCompareRecordIds([])
    setBaselineRecordId(null)
    setComparing(false)
    setTab("curves")
    setCurveView("metrics")
  }
  function viewRecord(record: DataRecord) {
    setCurrentRecord(record)
    setComparing(false)
    setTab("curves")
    setCurveView("metrics")
  }
  async function fileAction(record: DataRecord, locate: boolean) {
    if (fileLock.current || !record.sourcePath) return
    fileLock.current = true
    setFileBusy(true)
    setNotice("")
    try {
      if (locate) {
        await revealItemInDir(record.sourcePath)
        setNotice("已在文件夹中显示原始 Excel")
      } else {
        const result = await openResultFile(
          record.sourcePath,
          openPath,
          revealItemInDir,
        )
        setNotice(
          result.status === "revealed"
            ? `已定位文件：${result.openError}`
            : "已打开原始 Excel",
        )
      }
    } catch (error) {
      setNotice(`原始 Excel 无法打开：${message(error)}。已存档数据仍可查看。`)
    } finally {
      fileLock.current = false
      setFileBusy(false)
    }
  }
  function filter(change: () => void) {
    listGuard.current.invalidate()
    change()
    setPage(1)
  }
  function selectPoint(rowId: number) {
    if (currentRecord)
      setPointRows((previous) => ({ ...previous, [currentRecord.id]: rowId }))
  }

  const details = useMemo(
    () =>
      comparing
        ? compareDetails
        : singleDetail
          ? [singleDetail]
          : EMPTY_DETAILS,
    [comparing, compareDetails, singleDetail],
  )
  const busy = comparing ? compareBusy : detailBusy
  const error = comparing ? compareError : detailError
  const detail = singleDetail
  const validPoints = useMemo(
    () => detail?.points.filter((point) => finite(point.currentA)) ?? [],
    [detail],
  )
  const currentPoint =
    validPoints.find(
      (point) => point.rowId === pointRows[currentRecord?.id ?? ""],
    ) ?? validPoints[0]
  const availableMetrics = useMemo(
    () =>
      DATA_METRICS.filter((metric) =>
        details.some((item) =>
          item.points.some(
            (point) => finite(point.currentA) && finite(point[metric.key]),
          ),
        ),
      ),
    [details],
  )
  const requestedMetric = comparing ? compareMetric : singleMetric
  const metric =
    availableMetrics.find((item) => item.key === requestedMetric) ??
    availableMetrics[0] ??
    DATA_METRICS[0]
  const metricKey = metric.key
  const changeMetric = (value: DataMetricKey) =>
    comparing ? setCompareMetric(value) : setSingleMetric(value)
  const labels = useMemo(
    () => comparisonLabels(compareTargets),
    [compareTargets],
  )
  const colorFor = (id: string) => recordColorMap[id] ?? SERIES_COLORS[0]
  const rows = useMemo(
    () => metricChartRows(details, metricKey),
    [details, metricKey],
  )
  const yDomain = useMemo(
    () => metricYAxisDomain(details, metricKey),
    [details, metricKey],
  )
  const differences = useMemo(
    () => metricComparisonRows(details, metricKey),
    [details, metricKey],
  )
  const baselineIndex = Math.max(
    0,
    details.findIndex((item) => item.record.id === baselineRecordId),
  )
  const baseId = details[baselineIndex]?.record.id
  const currents = useMemo(
    () =>
      [
        ...new Set(
          details.flatMap((item) =>
            item.points
              .filter((point) => finite(point.currentA))
              .map((point) => Number(point.currentA.toFixed(6))),
          ),
        ),
      ].sort((a, b) => a - b),
    [details],
  )
  const activeCurrent =
    compareCurrent != null && currents.includes(compareCurrent)
      ? compareCurrent
      : currents[0]
  const currentComparison = differences.find(
    (row) => row.currentA === activeCurrent,
  )
  const lines: CanvasChartLine[] = details.map((item, index) => ({
    key: `series${index}`,
    label: comparing
      ? (labels.get(item.record.id) ?? recordLabel(item.record))
      : metric.label,
    color: comparing ? colorFor(item.record.id) : SERIES_COLORS[0],
    showPoints: true,
    connectNulls: true,
    pointStyle: "hollow",
    pointSize: 5,
    lineWidth: 2,
    interpolation: "linear",
    hideLegend: true,
  }))
  const annotatedPoint = comparing
    ? measuredCurrentMatch(
        details[baselineIndex]?.points ?? [],
        activeCurrent ?? NaN,
      ).item
    : currentPoint
  const annotations =
    annotatedPoint && finite(annotatedPoint[metricKey])
      ? [
          {
            x: annotatedPoint.currentA,
            y: annotatedPoint[metricKey],
            label: `${comparing ? "当前电流 " : ""}${numberText(annotatedPoint.currentA, 2)} A`,
            color: comparing && baseId ? colorFor(baseId) : SERIES_COLORS[0],
          },
        ]
      : []
  const singleSpectra = useMemo(
    () =>
      spectrumDetails.find((item) => item.record.id === currentRecord?.id)
        ?.spectra ?? [],
    [spectrumDetails, currentRecord?.id],
  )
  const selectedSpectrumIndices = useMemo(
    () =>
      currentRecord
        ? (spectrumChoices[currentRecord.id] ??
          singleSpectra.map((_, index) => index))
        : [],
    [currentRecord, spectrumChoices, singleSpectra],
  )
  const spectrumMatches = useMemo(
    () =>
      details.map((item) => {
        const spectra =
          spectrumDetails.find((loaded) => loaded.record.id === item.record.id)
            ?.spectra ?? []
        return measuredCurrentMatch(spectra, activeCurrent ?? NaN)
      }),
    [details, spectrumDetails, activeCurrent],
  )
  const singleMatch = useMemo(
    () => measuredCurrentMatch(singleSpectra, currentPoint?.currentA ?? NaN),
    [singleSpectra, currentPoint?.currentA],
  )
  const displayedSpectra = useMemo<(SpectrumSeries | null)[]>(
    () =>
      comparing
        ? spectrumMatches.map((match) => match.item)
        : curveView === "multiSpectrum"
          ? selectedSpectrumIndices.flatMap((index) =>
              singleSpectra[index] ? [singleSpectra[index]] : [],
            )
          : [singleMatch.item],
    [
      comparing,
      spectrumMatches,
      curveView,
      selectedSpectrumIndices,
      singleSpectra,
      singleMatch.item,
    ],
  )
  const spectralRows = useMemo(
    () => spectrumChartRows(displayedSpectra),
    [displayedSpectra],
  )
  const spectrumLines: CanvasChartLine[] = displayedSpectra.map(
    (spectrum, index) => ({
      key: `series${index}`,
      label: comparing
        ? (labels.get(details[index]?.record.id) ?? "记录")
        : `${numberText(spectrum?.currentA, 3)} A`,
      color: comparing
        ? colorFor(details[index]?.record.id)
        : SERIES_COLORS[index % SERIES_COLORS.length],
      lineWidth: 1.5,
      connectNulls: true,
      interpolation: "linear",
      hideLegend: true,
    }),
  )
  const testModes = [
    ...new Set([
      "automatic",
      "manual",
      ...(listing?.records.map((record) => record.testMode).filter(Boolean) ??
        []),
      ...(testMode && testMode !== "unrecorded" ? [testMode] : []),
    ]),
  ]
  const pointColumns = DATA_METRICS.filter((item) =>
    detail?.points.some((point) => finite(point[item.key])),
  )
  const maximum = detail
    ? extractSummaryMetrics(detail.points).maxPowerPoint
    : null
  const warnings = details.flatMap((item) =>
    item.warnings.map(
      (warning) =>
        `${comparing ? `${labels.get(item.record.id)}：` : ""}${warning}`,
    ),
  )
  const deltaUnit = metric.unit === "%" ? "百分点" : metric.unit
  function matchStatus(
    index: number,
    currentA: number,
    value: number | null,
    ambiguous: boolean,
  ) {
    if (ambiguous) return "重复电流点"
    if (!finite(value)) return "缺测"
    return measuredCurrentMatch(details[index].points, currentA).status
  }
  function deltaText(value: number | null, base: number | null) {
    if (!finite(value) || !finite(base)) return "Δ 不可计算"
    const delta = value - base
    return `Δ ${signed(delta)} ${deltaUnit} · ${base === 0 ? "基准为 0，相对差不可计算" : `${signed((delta / base) * 100)}%`}`
  }

  return (
    <section className="data-browser" aria-label="数据浏览">
      <div className="db-filters">
        <div className="db-filter-row">
          <div className="db-search">
            <Search size={16} />
            <input
              aria-label="搜索 SN"
              value={sn}
              disabled={!enabled}
              onChange={(event) => filter(() => setSn(event.target.value))}
              placeholder="搜索产品 SN…"
            />
          </div>
          <label>
            时间
            <select
              aria-label="日期范围"
              value={dateMode}
              disabled={!enabled}
              onChange={(event) =>
                filter(() => setDateMode(event.target.value))
              }
            >
              <option value="all">全部时间</option>
              <option value="today">今天</option>
              <option value="week">最近 7 天</option>
              <option value="custom">自定义</option>
            </select>
          </label>
          <label>
            测试类型
            <select
              aria-label="测试类型筛选"
              value={testMode}
              disabled={!enabled}
              onChange={(event) =>
                filter(() => setTestMode(event.target.value))
              }
            >
              <option value="">全部类型</option>
              {testModes.map((mode) => (
                <option key={mode} value={mode}>
                  {modeText(mode)}
                </option>
              ))}
              <option value="unrecorded">未记录</option>
            </select>
          </label>
          <Button
            variant="outline"
            size="sm"
            disabled={!enabled || listBusy || importBusy}
            onClick={() => setRefresh((value) => value + 1)}
          >
            <RefreshCw size={15} className={listBusy ? "animate-spin" : ""} />
            刷新
          </Button>
          <Button
            variant="ghost"
            size="sm"
            aria-expanded={sourceOpen}
            aria-controls="db-source-options"
            onClick={() => setSourceOpen((value) => !value)}
          >
            <Database size={15} />
            数据源与更多筛选
          </Button>
          {(sn || station || status || testMode || dateMode !== "all") && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                filter(() => {
                  setSn("")
                  setStation("")
                  setStatus("")
                  setTestMode("")
                  setDateMode("all")
                  setFrom("")
                  setTo("")
                })
              }
            >
              清除筛选
            </Button>
          )}
        </div>
        {dateMode === "custom" && (
          <div className="db-date-range">
            <label>
              开始日期
              <input
                aria-label="开始日期"
                type="date"
                value={from}
                disabled={!enabled}
                onChange={(event) => filter(() => setFrom(event.target.value))}
              />
            </label>
            <label>
              结束日期
              <input
                aria-label="结束日期"
                type="date"
                value={to}
                disabled={!enabled}
                onChange={(event) => filter(() => setTo(event.target.value))}
              />
            </label>
          </div>
        )}
        <div className="db-source-summary">
          <span title={listing?.rootPath || root}>
            {source === "database"
              ? "测试数据库 · 已完成流程自动存档"
              : `Excel 目录 · ${root || "未选择"}`}
          </span>
          {listing && (
            <span>
              {source === "excel" ? "扫描完成" : "已读取"} · {listing.total}{" "}
              条记录
              {listing.errorCount > 0 ? ` · ${listing.errorCount} 条异常` : ""}
            </span>
          )}
          {(station || status) && (
            <span>
              附加筛选：
              {[station, status && flowLabel(status)]
                .filter(Boolean)
                .join(" · ")}
            </span>
          )}
          {!!listing?.warnings.length && (
            <details className="db-diagnostics">
              <summary>扫描诊断（{listing.warnings.length}）</summary>
              <ul>
                {listing.warnings.map((warning, index) => (
                  <li key={index}>{warning}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
        {sourceOpen && (
          <div id="db-source-options" className="db-source-options">
            <div className="db-filter-row">
              <div
                className="db-view-switch"
                role="group"
                aria-label="数据来源"
              >
                <button
                  aria-pressed={source === "database"}
                  disabled={importBusy || !enabled}
                  onClick={() => changeSource("database")}
                >
                  <Database size={15} />
                  测试数据库
                </button>
                <button
                  aria-pressed={source === "excel"}
                  disabled={importBusy || !enabled}
                  onClick={() => changeSource("excel")}
                >
                  <FolderOpen size={15} />
                  Excel 文件
                </button>
              </div>
              <label>
                站别
                <select
                  aria-label="测试站别筛选"
                  value={station}
                  disabled={!enabled}
                  onChange={(event) =>
                    filter(() => setStation(event.target.value))
                  }
                >
                  <option value="">全部站别</option>
                  {listing?.stations.map((item) => (
                    <option key={item}>{item}</option>
                  ))}
                </select>
              </label>
              {source === "excel" && (
                <label>
                  流程状态
                  <select
                    aria-label="记录状态筛选"
                    value={status}
                    disabled={!enabled}
                    onChange={(event) =>
                      filter(() => setStatus(event.target.value))
                    }
                  >
                    <option value="">全部状态</option>
                    {listing?.statuses.map((item) => (
                      <option key={item} value={item}>
                        {flowLabel(item)}
                      </option>
                    ))}
                    <option value="read_error">读取失败</option>
                  </select>
                </label>
              )}
            </div>
            {source === "excel" && (
              <form
                className="db-directory"
                onSubmit={(event) => {
                  event.preventDefault()
                  applyDirectory(directoryDraft)
                }}
              >
                <input
                  aria-label="数据目录"
                  value={directoryDraft}
                  disabled={!enabled || importBusy}
                  onChange={(event) => setDirectoryDraft(event.target.value)}
                  placeholder="测试 Excel 所在目录"
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={!enabled || importBusy}
                  onClick={() => void chooseDirectory()}
                >
                  选择目录
                </Button>
                <Button
                  type="submit"
                  variant="outline"
                  size="sm"
                  disabled={!enabled || listBusy || importBusy}
                >
                  读取目录
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={!enabled || !root || importBusy || listBusy}
                  onClick={() => void importRecords()}
                >
                  {importBusy ? "正在导入…" : "导入已完成测试"}
                </Button>
              </form>
            )}
            <p className="db-help">
              {source === "database"
                ? "浏览已存档数据，原始 Excel 同步保留。切换到 Excel 文件可导入历史记录。"
                : "目录浏览保持只读；导入将已完成、有效的历史测试纳入数据库。"}
            </p>
          </div>
        )}
      </div>
      {!enabled && (
        <div className="db-notice">{disabledReason || "数据浏览已锁定"}</div>
      )}
      {listError && (
        <div className="db-notice is-error" role="alert">
          {listError}
        </div>
      )}
      {notice && (
        <div className="db-notice db-notice-row" role="status">
          <span>{notice}</span>
          <button
            aria-label="关闭提示"
            onClick={() => {
              setNotice("")
              setImportWarnings([])
            }}
          >
            <X size={16} />
          </button>
          {!!importWarnings.length && (
            <details>
              <summary>导入详情（{importWarnings.length}）</summary>
              <ul>
                {importWarnings.map((warning, index) => (
                  <li key={index}>{warning}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
      <div
        className={`db-workspace ${expanded ? "is-expanded" : ""}`}
        data-comparing={comparing}
      >
        <aside
          className="db-list-panel"
          aria-label="测试记录列表"
          hidden={expanded}
        >
          <div className="db-list-heading">
            <div className="db-panel-title">
              测试记录 <span className="db-count">{listing?.total ?? 0}</span>
              <span className="db-panel-meta">
                {listBusy ? "读取中…" : "按时间倒序"}
              </span>
            </div>
            <p className="db-help">
              最多选择 5 条记录进行比较 · 右侧数值为最大功率
            </p>
          </div>
          <div className="db-records" ref={recordsScroll} aria-busy={listBusy}>
            {!listing?.records.length ? (
              <div className="db-empty">
                <Database size={26} />
                <p>{listBusy ? "正在读取测试记录…" : "未找到测试记录"}</p>
                <span>调整筛选条件，或从数据源中选择 Excel 目录。</span>
              </div>
            ) : (
              listing.records.map((record) => (
                <RecordRow
                  key={recordKey(record)}
                  record={record}
                  current={!comparing && currentRecord?.id === record.id}
                  checked={compareRecordIds.includes(record.id)}
                  limit={compareRecordIds.length >= MAX_COMPARISONS}
                  active={visible && enabled && !expanded}
                  load={loadDetail}
                  onView={viewRecord}
                  onToggle={toggleCompare}
                />
              ))
            )}
          </div>
          {!!compareRecordIds.length && (
            <div className="db-basket" aria-label="比较篮">
              <div className="db-basket-actions">
                <strong>
                  已选择 {compareRecordIds.length} / {MAX_COMPARISONS}
                </strong>
                <Button size="sm" variant="ghost" onClick={clearCompare}>
                  清空
                </Button>
                <Button
                  size="sm"
                  disabled={comparing || compareRecordIds.length < 2 || !enabled}
                  onClick={() => {
                    setComparing(true)
                    setTab("curves")
                    setCurveView("metrics")
                  }}
                >
                  <ArrowLeftRight size={14} />
                  {comparing ? `比较中 ${compareRecordIds.length} 条` : "开始比较"}
                </Button>
              </div>
              <details>
                <summary>
                  查看已选记录
                  {compareRecordIds.length === 1 ? " · 至少选择 2 条" : ""}
                </summary>
                <div className="db-basket-records">
                  {compareTargets.map((record) => (
                    <div key={record.id}>
                      <i style={{ background: colorFor(record.id) }} />
                      <span title={recordLabel(record)}>
                        {labels.get(record.id)}
                      </span>
                      <button
                        aria-label={`移除 ${recordLabel(record)}`}
                        onClick={() => toggleCompare(record)}
                      >
                        <X size={14} />
                      </button>
                    </div>
                  ))}
                </div>
              </details>
            </div>
          )}
          <footer className="db-pagination">
            <span>
              第 {listing?.page ?? 1} / {listing?.pageCount ?? 1} 页
            </span>
            <div>
              <Button
                aria-label="上一页"
                variant="ghost"
                size="sm"
                disabled={listBusy || !enabled || page <= 1}
                onClick={() => setPage((value) => value - 1)}
              >
                <ChevronLeft size={16} />
              </Button>
              <Button
                aria-label="下一页"
                variant="ghost"
                size="sm"
                disabled={
                  listBusy || !enabled || page >= (listing?.pageCount ?? 1)
                }
                onClick={() => setPage((value) => value + 1)}
              >
                <ChevronRight size={16} />
              </Button>
            </div>
          </footer>
        </aside>
        <article className="db-detail-panel" aria-label="测试详情">
          <div className="db-panel-heading">
            <div
              className="db-segmented-tabs"
              role="tablist"
              aria-label="详情视图"
            >
              {(
                [
                  ["curves", comparing ? "曲线叠加" : "曲线分析"],
                  ["points", comparing ? "数值对照" : "测试点"],
                  ["info", "记录信息"],
                ] as const
              ).map(([id, label]) => (
                <button
                  key={id}
                  role="tab"
                  aria-selected={tab === id}
                  aria-controls="db-analysis-panel"
                  onClick={() => setTab(id)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="db-actions">
              {comparing ? (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setComparing(false)
                    setTab("curves")
                    setCurveView("metrics")
                  }}
                >
                  返回单记录
                </Button>
              ) : (
                currentRecord && (
                  <Button
                    size="sm"
                    variant="ghost"
                    title={
                      compareRecordIds.length >= MAX_COMPARISONS &&
                      !compareRecordIds.includes(currentRecord.id)
                        ? "最多比较 5 条记录"
                        : "加入比较篮"
                    }
                    disabled={
                      !enabled ||
                      !currentRecord.pointCount ||
                      currentRecord.readStatus === "error" ||
                      (compareRecordIds.length >= MAX_COMPARISONS &&
                        !compareRecordIds.includes(currentRecord.id))
                    }
                    onClick={() => toggleCompare(currentRecord)}
                  >
                    {compareRecordIds.includes(currentRecord.id)
                      ? "移出对比"
                      : "加入对比"}
                  </Button>
                )
              )}
              {!comparing && currentRecord && (
                <details
                  className="db-more"
                  onKeyDown={(event) => {
                    if (event.key === "Escape") event.currentTarget.open = false
                  }}
                >
                  <summary>更多</summary>
                  <div>
                    <button
                      disabled={fileBusy || !currentRecord.sourcePath}
                      onClick={(event) => {
                        event.currentTarget
                          .closest("details")
                          ?.removeAttribute("open")
                        void fileAction(currentRecord, false)
                      }}
                    >
                      打开原始 Excel
                    </button>
                    <button
                      disabled={fileBusy || !currentRecord.sourcePath}
                      onClick={(event) => {
                        event.currentTarget
                          .closest("details")
                          ?.removeAttribute("open")
                        void fileAction(currentRecord, true)
                      }}
                    >
                      在文件夹中显示
                    </button>
                  </div>
                </details>
              )}
              <Button
                size="sm"
                variant="ghost"
                aria-label={expanded ? "收起详情" : "展开详情"}
                onClick={() => setExpanded((value) => !value)}
              >
                {expanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
              </Button>
            </div>
          </div>
          <div
            id="db-analysis-panel"
            role="tabpanel"
            aria-label={
              tab === "info"
                ? "记录信息"
                : tab === "points"
                  ? comparing
                    ? "数值对照"
                    : "测试点"
                  : comparing
                    ? "曲线叠加"
                    : "曲线分析"
            }
            className="db-detail-scroll"
            ref={detailScroll}
            aria-busy={busy}
          >
            {busy ? (
              <div className="db-empty">正在读取测试数据…</div>
            ) : error ? (
              <div className="db-notice is-error" role="alert">
                {error}
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setRefresh((value) => value + 1)}
                >
                  重试
                </Button>
              </div>
            ) : !details.length ? (
              <div className="db-empty">
                <BarChart3 size={28} />
                <p>选择左侧记录查看详情</p>
                <span>勾选 2 至 5 条记录后开始比较。</span>
              </div>
            ) : (
              <>
                {!comparing && detail && (
                  <div className="db-record-heading">
                    <h2>{detail.record.sn || detail.record.fileName}</h2>
                    <div className="db-identity">
                      <span>
                        时间 {timeText(detail.record.timeMs)}
                        {detail.record.timeSource === "modified"
                          ? "（文件修改时间）"
                          : ""}
                      </span>
                      <span>测试站 {detail.record.station || "未记录"}</span>
                      <span>温度 {detail.record.temperature || "未记录"}</span>
                      <span>{modeText(detail.record.testMode)}</span>
                      <span>电流 {currentRange(detail.record)}</span>
                      <span>{detail.record.pointCount} 点</span>
                    </div>
                  </div>
                )}
                {comparing && (
                  <div className="db-comparison-heading">
                    <strong>{details.length} 条记录比较</strong>
                    <label>
                      基准记录
                      <select
                        aria-label="基准记录"
                        value={baseId ?? ""}
                        onChange={(event) =>
                          setBaselineRecordId(event.target.value)
                        }
                      >
                        {details.map((item) => (
                          <option key={item.record.id} value={item.record.id}>
                            {labels.get(item.record.id)}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                )}
                {comparing && (
                  <div className="db-chart-legend" aria-label="比较记录图例">
                    {details.map((item) => (
                      <span
                        key={item.record.id}
                        data-record-id={item.record.id}
                        title={recordLabel(item.record)}
                      >
                        <i style={{ background: colorFor(item.record.id) }} />
                        {labels.get(item.record.id)}
                        {item.record.id === baseId && <b>基准</b>}
                      </span>
                    ))}
                  </div>
                )}
                {comparing &&
                  new Set(
                    details.map((item) => item.record.temperature || "未记录"),
                  ).size > 1 && (
                    <p className="db-help">
                      注意：测试温度不同，请结合记录信息中的测试条件比较。
                    </p>
                  )}
                {comparing &&
                  details.some(
                    (item) => item.record.flowStatus !== "completed",
                  ) && (
                    <p className="db-help">
                      注意：包含流程未完成或状态未知的记录，测试点可能不完整。
                    </p>
                  )}
                {(tab === "curves" || (comparing && tab === "points")) && (
                  <div className="db-chart-toolbar">
                    {tab === "curves" && (
                      <label>
                        曲线内容
                        <select
                          aria-label="曲线内容"
                          value={curveView}
                          onChange={(event) =>
                            setCurveView(event.target.value as CurveView)
                          }
                        >
                          <option value="metrics">指标曲线</option>
                          <option value="spectrum">
                            {comparing ? "同电流光谱叠加" : "单电流光谱"}
                          </option>
                          {!comparing && (
                            <>
                              <option value="multiSpectrum">多电流光谱</option>
                              <option value="evolution">光谱随电流变化</option>
                            </>
                          )}
                        </select>
                      </label>
                    )}
                    {(tab === "points" || curveView === "metrics") && (
                      <label>
                        指标
                        <select
                          aria-label="分析指标"
                          value={metricKey}
                          disabled={!availableMetrics.length}
                          onChange={(event) =>
                            changeMetric(event.target.value as DataMetricKey)
                          }
                        >
                          {!availableMetrics.length && (
                            <option value="powerW">无可用指标</option>
                          )}
                          {availableMetrics.map((item) => (
                            <option key={item.key} value={item.key}>
                              {item.label} ({item.unit})
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                  </div>
                )}
                {tab === "curves" && (
                  <>
                    {curveView === "metrics" ? (
                      <div className="db-chart-section">
                        <div className="db-chart-title">
                          <strong>
                            {metric.label}—电流{comparing ? "叠加" : "曲线"}
                          </strong>
                          <span>实测点 · {metric.unit}</span>
                        </div>
                        <div className="db-chart">
                          {rows.length ? (
                            <CanvasLineChart
                              data={rows}
                              xKey="currentA"
                              lines={lines}
                              yDomain={yDomain}
                              ariaLabel={`${comparing ? "多记录" : "单记录"}${metric.label}电流曲线`}
                              yLabel={`${metric.label} (${metric.unit})`}
                              annotations={annotations}
                              preserveAllPoints
                            />
                          ) : (
                            <div className="db-empty">
                              未记录有效的{metric.label}数据
                            </div>
                          )}
                          {!!rows.length && (
                            <span className="db-axis-label">电流 (A)</span>
                          )}
                        </div>
                      </div>
                    ) : (
                      <div
                        className="db-spectrum-section"
                        aria-busy={spectraBusy}
                      >
                        {spectraError && (
                          <div className="db-notice is-error">
                            光谱读取失败：{spectraError}
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() =>
                                setSpectraRetry((value) => value + 1)
                              }
                            >
                              重试光谱
                            </Button>
                          </div>
                        )}
                        <p className="db-help">
                          {curveView === "evolution"
                            ? "热力颜色与脊线高度按本记录的全场最大强度归一化，悬停读数为原始响应。"
                            : "纵轴：原始强度（光谱仪响应值），未做归一化。"}{" "}
                          光谱响应不代表绝对光功率。
                        </p>
                        {spectraBusy ? (
                          <div className="db-chart db-empty">正在读取光谱…</div>
                        ) : curveView === "evolution" && !comparing ? (
                          <SpectralWaterfallChart
                            spectra={singleSpectra}
                            sn={detail?.record.sn}
                          />
                        ) : (
                          <>
                            {curveView === "multiSpectrum" && !comparing && (
                              <fieldset className="db-spectrum-choices">
                                <legend>叠加电流</legend>
                                {singleSpectra.map((spectrum, index) => (
                                  <label key={index}>
                                    <input
                                      type="checkbox"
                                      checked={selectedSpectrumIndices.includes(
                                        index,
                                      )}
                                      onChange={() =>
                                        currentRecord &&
                                        setSpectrumChoices((previous) => ({
                                          ...previous,
                                          [currentRecord.id]:
                                            selectedSpectrumIndices.includes(
                                              index,
                                            )
                                              ? selectedSpectrumIndices.filter(
                                                  (item) => item !== index,
                                                )
                                              : [
                                                  ...selectedSpectrumIndices,
                                                  index,
                                                ].sort((a, b) => a - b),
                                        }))
                                      }
                                    />
                                    {numberText(spectrum.currentA, 3)} A
                                    {singleSpectra.filter(
                                      (item) =>
                                        item.currentA.toFixed(6) ===
                                        spectrum.currentA.toFixed(6),
                                    ).length > 1
                                      ? ` · 光谱 ${index + 1}`
                                      : ""}
                                  </label>
                                ))}
                              </fieldset>
                            )}
                            <div className="db-chart-title">
                              <strong>
                                {comparing
                                  ? "同电流光谱叠加"
                                  : curveView === "multiSpectrum"
                                    ? "多电流光谱叠加"
                                    : "当前测试点光谱"}
                              </strong>
                              <span>
                                {comparing
                                  ? `${numberText(activeCurrent, 3)} A`
                                  : curveView === "spectrum"
                                    ? singleMatch.status
                                    : `${displayedSpectra.length} 条光谱`}
                              </span>
                            </div>
                            {!comparing && (
                              <div className="db-chart-legend">
                                {spectrumLines.map(
                                  (line, index) =>
                                    displayedSpectra[index] && (
                                      <span key={line.key}>
                                        <i style={{ background: line.color }} />
                                        {line.label}
                                      </span>
                                    ),
                                )}
                              </div>
                            )}
                            <div className="db-chart">
                              {spectralRows.length ? (
                                <CanvasLineChart
                                  data={spectralRows}
                                  xKey="wavelengthNm"
                                  lines={spectrumLines}
                                  ariaLabel="原始强度光谱叠加"
                                  yLabel="原始强度 (响应值)"
                                  preserveAllPoints
                                />
                              ) : (
                                <div className="db-empty">
                                  {curveView === "multiSpectrum" &&
                                  singleSpectra.length
                                    ? "请选择要叠加的电流"
                                    : `此电流无可用光谱${singleMatch.status === "重复电流点" && !comparing ? "：重复电流点，请使用多电流光谱逐条核对" : ""}`}
                                </div>
                              )}
                              {!!spectralRows.length && (
                                <span className="db-axis-label">波长 (nm)</span>
                              )}
                            </div>
                          </>
                        )}
                      </div>
                    )}
                    <PointNavigator
                      comparing={comparing}
                      values={
                        comparing
                          ? currents
                          : validPoints.map((point) => point.currentA)
                      }
                      index={
                        comparing
                          ? Math.max(0, currents.indexOf(activeCurrent))
                          : currentPoint
                            ? validPoints.indexOf(currentPoint)
                            : 0
                      }
                      onChange={(index) =>
                        comparing
                          ? setCompareCurrent(currents[index])
                          : selectPoint(validPoints[index].rowId)
                      }
                    />
                    {!comparing && currentPoint && (
                      <div
                        className="db-current-point"
                        aria-label="当前测试点读数"
                      >
                        <div className="db-current-label">
                          <strong>当前测试点</strong>
                          <span>
                            #{validPoints.indexOf(currentPoint) + 1} ·{" "}
                            {numberText(currentPoint.currentA)} A
                          </span>
                          <button onClick={() => setTab("points")}>
                            在测试点表中查看
                          </button>
                        </div>
                        <dl>
                          {DATA_METRICS.filter((item) =>
                            finite(currentPoint[item.key]),
                          ).map((item) => (
                            <div key={item.key}>
                              <dt>{item.label}</dt>
                              <dd>
                                {numberText(currentPoint[item.key])}{" "}
                                <small>{item.unit}</small>
                              </dd>
                            </div>
                          ))}
                        </dl>
                      </div>
                    )}
                    {!comparing && maximum && finite(maximum.currentA) && (
                      <p className="db-help">
                        本记录最大功率 {numberText(maximum.powerW)} W @{" "}
                        {numberText(maximum.currentA)} A。读数显示至小数点后 3
                        位，原始数据不变。
                      </p>
                    )}
                    {comparing && (
                      <div className="db-current-comparison">
                        <div className="db-table-scroll">
                          <table className="db-data-table db-current-table">
                            <thead>
                              <tr>
                                <th>
                                  记录 · 当前 {numberText(activeCurrent, 3)} A
                                </th>
                                <th>
                                  {curveView === "metrics"
                                    ? `${metric.label} (${metric.unit})`
                                    : "光谱"}
                                </th>
                                <th>
                                  {curveView === "metrics"
                                    ? "相对基准"
                                    : "实测电流 (A)"}
                                </th>
                                <th>匹配状态</th>
                              </tr>
                            </thead>
                            <tbody>
                              {details.map((item, index) => {
                                const value =
                                  currentComparison?.values[index] ?? null
                                const base =
                                  currentComparison?.values[baselineIndex] ??
                                  null
                                const match = spectrumMatches[index]
                                return (
                                  <tr key={item.record.id}>
                                    <td title={labels.get(item.record.id)}>
                                      {labels.get(item.record.id)}
                                    </td>
                                    <td>
                                      {curveView === "metrics"
                                        ? finite(value)
                                          ? numberText(value)
                                          : "缺测"
                                        : match.item
                                          ? `${match.item.wavelengthNm.length} 点`
                                          : "缺测"}
                                    </td>
                                    <td>
                                      {curveView === "metrics"
                                        ? index === baselineIndex
                                          ? "基准"
                                          : deltaText(value, base)
                                        : match.item
                                          ? numberText(match.item.currentA, 6)
                                          : "缺测"}
                                    </td>
                                    <td>
                                      {curveView === "metrics"
                                        ? matchStatus(
                                            index,
                                            activeCurrent ?? NaN,
                                            value,
                                            currentComparison?.ambiguous[
                                              index
                                            ] ?? false,
                                          )
                                        : spectraBusy
                                          ? "读取中…"
                                          : match.status}
                                    </td>
                                  </tr>
                                )
                              })}
                            </tbody>
                          </table>
                        </div>
                        <p className="db-help">
                          容差匹配沿用电流保留 6
                          位小数的分组规则；不插值。缺测或重复电流点不计算差值。
                        </p>
                      </div>
                    )}
                  </>
                )}
                {tab === "points" &&
                  (comparing ? (
                    <div className="db-table-section">
                      <div className="db-chart-title">
                        <strong>{metric.label}逐点数值对照</strong>
                        <span>Δ = 当前记录 − 基准 · {deltaUnit}</span>
                      </div>
                      <div className="db-table-scroll">
                        <table className="db-data-table db-comparison-table">
                          <thead>
                            <tr>
                              <th>电流 (A)</th>
                              {details.map((item) => (
                                <th key={item.record.id}>
                                  {labels.get(item.record.id)}
                                  <small>
                                    {item.record.id === baseId
                                      ? "基准值"
                                      : "绝对值 / Δ / 相对差"}{" "}
                                    ({metric.unit})
                                  </small>
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {differences.map((row) => (
                              <tr
                                key={row.currentA}
                                data-selected={row.currentA === activeCurrent}
                              >
                                <td>
                                  <button
                                    aria-label={`查看 ${numberText(row.currentA, 6)} A 对比曲线`}
                                    onClick={() => {
                                      setCompareCurrent(row.currentA)
                                      setTab("curves")
                                      setCurveView("metrics")
                                    }}
                                  >
                                    {numberText(row.currentA, 6)}
                                  </button>
                                </td>
                                {row.values.map((value, index) => (
                                  <td key={details[index].record.id}>
                                    {finite(value) ? numberText(value) : "缺测"}
                                    <small>
                                      {matchStatus(
                                        index,
                                        row.currentA,
                                        value,
                                        row.ambiguous[index],
                                      )}
                                    </small>
                                    {index !== baselineIndex && (
                                      <small>
                                        {deltaText(
                                          value,
                                          row.values[baselineIndex],
                                        )}
                                      </small>
                                    )}
                                  </td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      <p className="db-help">
                        每列保留记录身份，切换基准只更新差值。数值显示至小数点后
                        3 位；电流匹配精度为 6 位小数。不插值，基准为 0
                        时相对差不可计算。
                      </p>
                    </div>
                  ) : (
                    <div className="db-table-section">
                      <div className="db-chart-title">
                        <strong>原始测试点</strong>
                        <span>
                          当前 #
                          {currentPoint
                            ? validPoints.indexOf(currentPoint) + 1
                            : "未选择"}
                        </span>
                        <button onClick={() => setTab("curves")}>
                          返回曲线分析
                        </button>
                      </div>
                      <div className="db-table-scroll">
                        <table className="db-data-table">
                          <thead>
                            <tr>
                              <th>点序号</th>
                              <th>电流 (A)</th>
                              {pointColumns.map((item) => (
                                <th key={item.key}>
                                  {item.label} ({item.unit})
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {detail?.points.map((point, index) => (
                              <tr
                                key={point.rowId}
                                data-selected={
                                  point.rowId === currentPoint?.rowId
                                }
                                onClick={() =>
                                  finite(point.currentA) &&
                                  selectPoint(point.rowId)
                                }
                              >
                                <td>
                                  <button
                                    disabled={!finite(point.currentA)}
                                    aria-label={`选择测试点 ${index + 1}`}
                                    aria-pressed={
                                      point.rowId === currentPoint?.rowId
                                    }
                                    onClick={() => selectPoint(point.rowId)}
                                  >
                                    {index + 1}
                                    {point.rowId === currentPoint?.rowId
                                      ? " · 当前"
                                      : ""}
                                  </button>
                                </td>
                                <td>
                                  {finite(point.currentA)
                                    ? numberText(point.currentA)
                                    : "缺测"}
                                </td>
                                {pointColumns.map((item) => (
                                  <td key={item.key}>
                                    {finite(point[item.key])
                                      ? numberText(point[item.key])
                                      : "缺测"}
                                  </td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      <p className="db-help">
                        点击行设置当前测试点，返回曲线后保留选择。显示至小数点后
                        3 位；只展示有数据的指标列，缺测不记为 0。
                      </p>
                    </div>
                  ))}
                {tab === "info" &&
                  details.map((item) => (
                    <section className="db-info-section" key={item.record.id}>
                      <h3>
                        {comparing
                          ? labels.get(item.record.id)
                          : "记录来源与测试条件"}
                      </h3>
                      <dl className="db-info">
                        {Object.entries({
                          SN: item.record.sn,
                          测试时间: timeText(item.record.timeMs),
                          时间来源:
                            item.record.timeSource === "session"
                              ? "会话时间"
                              : "文件修改时间",
                          测试模式: modeText(item.record.testMode),
                          测试站: item.record.station,
                          工位编号: item.record.stationId,
                          温度: item.record.temperature,
                          "Session ID": item.record.sessionId,
                          数据来源:
                            item.record.storage === "database"
                              ? "测试数据库"
                              : "Excel 目录",
                          文件路径: item.record.sourcePath,
                          流程状态: `${flowLabel(item.record.flowStatus)}（不代表产品合格判定）`,
                          读取状态:
                            item.record.readStatus === "readable"
                              ? "可读取"
                              : `读取失败：${item.record.error}`,
                          文件版本: item.record.version,
                          测试点数量: String(item.record.pointCount),
                          有效点数量: String(item.record.validPointCount),
                          最小电流: finite(item.record.minimumCurrentA)
                            ? `${numberText(item.record.minimumCurrentA)} A`
                            : "未记录",
                          最大电流: finite(item.record.maximumCurrentA)
                            ? `${numberText(item.record.maximumCurrentA)} A`
                            : "未记录",
                        }).map(([key, value]) => (
                          <div className="db-info-row" key={key}>
                            <dt>{key}</dt>
                            <dd>{value || "未记录"}</dd>
                          </div>
                        ))}
                      </dl>
                      <div className="db-file-actions">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={fileBusy || !item.record.sourcePath}
                          onClick={() => void fileAction(item.record, false)}
                        >
                          打开原始 Excel
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={fileBusy || !item.record.sourcePath}
                          onClick={() => void fileAction(item.record, true)}
                        >
                          在文件夹中显示
                        </Button>
                      </div>
                      {!!Object.keys(item.metadata).length && (
                        <details className="db-metadata">
                          <summary>
                            原始元数据与设备配置（
                            {Object.keys(item.metadata).length}）
                          </summary>
                          <dl className="db-info">
                            {Object.entries(item.metadata).map(
                              ([key, value]) => (
                                <div className="db-info-row" key={key}>
                                  <dt>{key}</dt>
                                  <dd>{value || "未记录"}</dd>
                                </div>
                              ),
                            )}
                          </dl>
                        </details>
                      )}
                      <div className="db-info-warnings">
                        <strong>数据与格式警告</strong>
                        {item.warnings.length ? (
                          <ul>
                            {item.warnings.map((warning, index) => (
                              <li key={index}>{warning}</li>
                            ))}
                          </ul>
                        ) : (
                          <p>当前读取结果未报告警告</p>
                        )}
                      </div>
                    </section>
                  ))}
                {tab !== "info" && !!warnings.length && (
                  <details className="db-warning-summary">
                    <summary>数据与格式警告（{warnings.length}）</summary>
                    <ul>
                      {warnings.map((warning, index) => (
                        <li key={index}>{warning}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </>
            )}
          </div>
        </article>
      </div>
    </section>
  )
})
