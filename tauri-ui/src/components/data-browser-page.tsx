import { memo, useEffect, useMemo, useRef, useState } from "react"
import {
  ArrowLeft,
  ArrowLeftRight,
  BarChart3,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Database,
  FileSpreadsheet,
  FolderOpen,
  Info,
  Maximize2,
  Minimize2,
  RefreshCw,
  Search,
  Table,
  X,
} from "lucide-react"
import { open as openDialog } from "@tauri-apps/plugin-dialog"
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener"
import { Button } from "@/components/ui/button"
import {
  CanvasLineChart,
  type CanvasChartLine,
} from "@/components/canvas-line-chart"
import { sendBackendRequest } from "@/lib/backend"
import { openResultFile } from "@/lib/result-file"
import {
  DATA_METRICS,
  MAX_COMPARISONS,
  SERIES_COLORS,
  dateBounds,
  extractSummaryMetrics,
  flowLabel,
  metricChartRows,
  metricComparisonRows,
  metricYAxisDomain,
  numberText,
  recordLabel,
  requestGuard,
  timeText,
  type DataDetail,
  type DataList,
  type DataMetricKey,
  type DataRecord,
} from "@/lib/data-browser"
import { SpectralWaterfallChart } from "@/components/spectral-waterfall-chart"
import "./data-browser.css"

const DIRECTORY_KEY = "power-test.data-browser.directory.v1"
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
function inspectParams(record: DataRecord) {
  return record.storage === "database"
    ? { recordId: record.id }
    : { path: record.sourcePath, version: record.version }
}

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
  const [sn, setSn] = useState("")
  const [station, setStation] = useState("")
  const [status, setStatus] = useState("")
  const [dateMode, setDateMode] = useState("all")
  const [from, setFrom] = useState("")
  const [to, setTo] = useState("")
  const [page, setPage] = useState(1)
  const [refresh, setRefresh] = useState(0)
  const [listing, setListing] = useState<DataList | null>(null)
  const [listBusy, setListBusy] = useState(false)
  const [listError, setListError] = useState("")
  const [selected, setSelected] = useState<DataRecord | null>(null)
  const [details, setDetails] = useState<DataDetail[]>([])
  const [detailBusy, setDetailBusy] = useState(false)
  const [detailError, setDetailError] = useState("")
  const [tab, setTab] = useState("overview")
  const [metricKey, setMetricKey] = useState<DataMetricKey>("powerW")
  const [pointRow, setPointRow] = useState<number | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [chosen, setChosen] = useState<DataRecord[]>([])
  const [comparing, setComparing] = useState(false)
  const [fileBusy, setFileBusy] = useState(false)
  const [importBusy, setImportBusy] = useState(false)
  const [notice, setNotice] = useState("")
  const importLock = useRef(false)
  const listGuard = useRef(requestGuard())
  const detailGuard = useRef(requestGuard())
  const scanStamp = useRef("")
  const detailScroll = useRef<HTMLDivElement>(null)
  const recordsScroll = useRef<HTMLDivElement>(null)
  const demoComparisonPending = useRef(
    typeof window !== "undefined"
    && new URLSearchParams(window.location.search).get("demo") === "1"
    && new URLSearchParams(window.location.search).get("compare") === "1",
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
  }, [selected?.id, comparing, tab])
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
      let dates: ReturnType<typeof dateBounds>
      try {
        dates = dateBounds(dateMode, from, to)
      } catch (error) {
        setListError(message(error))
        setListBusy(false)
        return
      }
      const stamp = `${source}|${root}|${refresh}`
      void sendBackendRequest<DataList>("dataBrowser.list", {
        source,
        rootPath: root,
        refresh: scanStamp.current !== stamp,
        sn,
        station,
        status,
        ...dates,
        page,
        pageSize: 50,
      })
        .then((result) => {
          if (!guard.current(ticket)) return
          scanStamp.current = stamp
          setListing(result)
          setPage(result.page)
          if (demoComparisonPending.current && result.records.length >= 2) {
            demoComparisonPending.current = false
            setChosen(result.records.slice(0, 5))
            setComparing(true)
            setMetricKey("efficiencyPercent")
          }
          setSelected(
            (previous) =>
              result.records.find((record) => record.id === previous?.id) ??
              result.records.find(
                (record) => record.readStatus === "readable",
              ) ??
              null,
          )
        })
        .catch((error) => {
          if (guard.current(ticket)) {
            setListError(message(error))
            setListing(null)
            setSelected(null)
          }
        })
        .finally(() => {
          if (guard.current(ticket)) setListBusy(false)
        })
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
    dateMode,
    from,
    to,
    page,
    refresh,
  ])

  useEffect(() => {
    const guard = detailGuard.current,
      ticket = guard.next()
    setDetails([])
    setDetailError("")
    setDetailBusy(false)
    if (!visible || !enabled) return
    const targets = comparing ? chosen : selected ? [selected] : []
    if (!targets.length || (comparing && targets.length < 2)) return
    setDetailBusy(true)
    void Promise.all(
      targets.map((record) =>
        sendBackendRequest<DataDetail>("dataBrowser.inspect", {
          ...inspectParams(record),
          includeSpectra: tab === "spectra",
        }),
      ),
    )
      .then((result) => {
        if (guard.current(ticket)) {
          setDetails(result)
          setPointRow(result[0]?.points[0]?.rowId ?? null)
        }
      })
      .catch((error) => {
        if (guard.current(ticket)) setDetailError(message(error))
      })
      .finally(() => {
        if (guard.current(ticket)) setDetailBusy(false)
      })
    return () => guard.invalidate()
  }, [
    visible,
    enabled,
    comparing,
    chosen,
    selected?.id,
    selected?.version,
    tab === "spectra",
    refresh,
  ])

  function resetSelection() {
    listGuard.current.invalidate()
    detailGuard.current.invalidate()
    setListing(null)
    setSelected(null)
    setDetails([])
    setChosen([])
    setComparing(false)
    setPage(1)
    setStation("")
    setStatus("")
  }
  function changeSource(next: "database" | "excel") {
    resetSelection()
    setSource(next)
    setNotice("")
  }
  function useDirectory(value: string) {
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
      /* Retain current session choice. */
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
      if (typeof path === "string") useDirectory(path)
    } catch (error) {
      setListError(message(error))
    }
  }
  async function importRecords() {
    if (importLock.current || !enabled || !root) return
    importLock.current = true
    setImportBusy(true)
    setNotice("")
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
        `已导入 ${result.imported} 条；跳过 ${result.skipped} 条重复、未完成或无有效数据的记录。${result.warnings.join("；")}`,
      )
    } catch (error) {
      setNotice(`导入未完成：${message(error)}`)
    } finally {
      importLock.current = false
      setImportBusy(false)
    }
  }
  function toggleChosen(record: DataRecord) {
    setChosen((previous) => {
      const next = previous.some((item) => item.id === record.id)
        ? previous.filter((item) => item.id !== record.id)
        : previous.length < MAX_COMPARISONS
          ? [...previous, record]
          : previous
      return next
    })
  }
  useEffect(() => {
    if (chosen.length < 2) setComparing(false)
  }, [chosen.length])
  async function fileAction(record: DataRecord, locate: boolean) {
    if (fileBusy) return
    setFileBusy(true)
    setNotice("")
    try {
      if (locate) {
        await revealItemInDir(record.sourcePath)
        setNotice("已定位原始 Excel")
      } else {
        const result = await openResultFile(
          record.sourcePath,
          openPath,
          revealItemInDir,
        )
        setNotice(
          result.status === "revealed"
            ? `已定位文件：${result.openError}`
            : "已打开 Excel",
        )
      }
    } catch (error) {
      setNotice(
        `原始 Excel 无法打开：${message(error)}。数据库内的测试记录仍可查看。`,
      )
    } finally {
      setFileBusy(false)
    }
  }
  function filter(change: () => void) {
    listGuard.current.invalidate()
    change()
    setPage(1)
  }
  const metric = DATA_METRICS.find((item) => item.key === metricKey)!
  const detail = details[0]
  const currentPoint = detail?.points.find((point) => point.rowId === pointRow)
  const summary = useMemo(
    () => (detail ? extractSummaryMetrics(detail.points) : null),
    [detail],
  )
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
  const lines = useMemo<CanvasChartLine[]>(
    () =>
      details.map((item, index) => ({
        key: `series${index}`,
        label: comparing
          ? `${index + 1}. ${item.record.sn || item.record.fileName}`
          : metric.label,
        color: SERIES_COLORS[index],
        showPoints: true,
        connectNulls: true,
        pointStyle: "hollow",
        pointSize: 5,
        lineWidth: 2.3,
        dash: index ? [index * 3 + 2, 3] : undefined,
        hideLegend: true,
      })),
    [details, comparing, metric.label],
  )
  const annotations =
    !comparing && currentPoint?.[metricKey] != null
      ? [
          {
            x: currentPoint.currentA,
            y: currentPoint[metricKey],
            label: `${numberText(currentPoint.currentA, 2)} A`,
            color: SERIES_COLORS[0],
          },
        ]
      : []

  return (
    <section className="data-browser" aria-label="数据浏览">
      <div className="db-filters">
        <div className="db-library-header">
          <div className="db-source-tabs" role="tablist" aria-label="数据来源">
            <button
              role="tab"
              aria-selected={source === "database"}
              onClick={() => changeSource("database")}
              disabled={importBusy}
            >
              <Database size={17} />
              测试数据库
            </button>
            <button
              role="tab"
              aria-selected={source === "excel"}
              onClick={() => changeSource("excel")}
              disabled={importBusy}
            >
              <FolderOpen size={17} />
              Excel 文件
            </button>
          </div>
          <span className="db-library-note">
            {source === "database"
              ? "已完成测试自动存档 · Excel 同步保留"
              : "浏览原始文件，导入已完成测试到数据库"}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={!enabled || listBusy || importBusy}
            onClick={() => setRefresh((value) => value + 1)}
          >
            <RefreshCw size={15} className={listBusy ? "animate-spin" : ""} />
            刷新
          </Button>
        </div>
        {source === "excel" && (
          <form
            className="db-directory"
            onSubmit={(event) => {
              event.preventDefault()
              useDirectory(directoryDraft)
            }}
          >
            <input
              aria-label="数据目录"
              value={directoryDraft}
              onChange={(event) => setDirectoryDraft(event.target.value)}
              placeholder="测试 Excel 所在目录"
              disabled={!enabled || importBusy}
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
              size="sm"
              disabled={!enabled || !root || importBusy || listBusy}
              onClick={() => void importRecords()}
            >
              <Database size={15} />
              {importBusy ? "正在导入…" : "导入已完成测试"}
            </Button>
          </form>
        )}
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
          {dateMode === "custom" && (
            <span className="db-date-range">
              <input
                aria-label="开始日期"
                type="date"
                value={from}
                onChange={(event) => filter(() => setFrom(event.target.value))}
              />
              <span>至</span>
              <input
                aria-label="结束日期"
                type="date"
                value={to}
                onChange={(event) => filter(() => setTo(event.target.value))}
              />
            </span>
          )}
          <label>
            站别
            <select
              aria-label="测试站别筛选"
              value={station}
              disabled={!enabled}
              onChange={(event) => filter(() => setStation(event.target.value))}
            >
              <option value="">全部站别</option>
              {listing?.stations.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </label>
          {source === "excel" && (
            <label>
              状态
              <select
                aria-label="记录状态筛选"
                value={status}
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
          {(sn || station || status || dateMode !== "all") && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                filter(() => {
                  setSn("")
                  setStation("")
                  setStatus("")
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
      </div>
      {!enabled && (
        <div className="db-notice is-locked">
          {disabledReason || "数据浏览已锁定"}
        </div>
      )}
      {listError && (
        <div className="db-notice is-error" role="alert">
          {listError}
        </div>
      )}
      {listing?.warnings.map((warning) => (
        <div className="db-notice" key={warning}>
          {warning}
        </div>
      ))}
      {notice && (
        <div className="db-notice" role="status">
          {notice}
        </div>
      )}
      {chosen.length > 0 && (
        <div className="db-selection">
          <div className="db-selected-items">
            <strong>
              已选 {chosen.length}/{MAX_COMPARISONS}
            </strong>
            {chosen.map((record, index) => (
              <span
                className="db-selected-item"
                key={record.id}
                title={recordLabel(record)}
              >
                <i style={{ background: SERIES_COLORS[index] }} />
                <span>
                  {index === 0 ? "基准 · " : ""}
                  {record.sn || record.fileName}
                </span>
                <button
                  aria-label={`移除${recordLabel(record)}`}
                  onClick={() => toggleChosen(record)}
                >
                  <X size={14} />
                </button>
              </span>
            ))}
          </div>
          <div className="db-selection-actions">
            <Button
              size="sm"
              disabled={chosen.length < 2 || !enabled}
              onClick={() => {
                setComparing(true)
                setTab("overview")
              }}
            >
              <ArrowLeftRight size={15} />
              对比曲线
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setChosen([])
                setComparing(false)
              }}
            >
              清空
            </Button>
          </div>
        </div>
      )}
      <div className={`db-workspace ${expanded ? "is-expanded" : ""}`}>
        <aside
          className="db-list-panel"
          aria-label="测试记录列表"
          hidden={expanded}
        >
          <div className="db-panel-heading">
            <div className="db-panel-title">
              测试记录 <span className="db-count">{listing?.total ?? 0}</span>
            </div>
            <span className="db-panel-meta">
              {listBusy ? "读取中…" : "勾选加入对比"}
            </span>
          </div>
          <div className="db-records" ref={recordsScroll} aria-busy={listBusy}>
            {!listing?.records.length ? (
              <div className="db-empty">
                <Database size={28} />
                <p>
                  {listBusy
                    ? "正在读取测试记录…"
                    : source === "database"
                      ? "尚无已完成的测试"
                      : "未找到测试文件"}
                </p>
                <span>
                  {source === "database"
                    ? "完成测试后自动入库，也可从 Excel 文件导入。"
                    : "选择目录或调整筛选条件。"}
                </span>
              </div>
            ) : (
              listing.records.map((record) => (
                <div
                  className="db-record-item"
                  key={record.id}
                  data-selected={!comparing && selected?.id === record.id}
                >
                  <input
                    type="checkbox"
                    aria-label={`选择对比 ${recordLabel(record)}`}
                    checked={chosen.some((item) => item.id === record.id)}
                    disabled={
                      !enabled ||
                      record.pointCount === 0 ||
                      record.readStatus === "error" ||
                      (chosen.length >= MAX_COMPARISONS &&
                        !chosen.some((item) => item.id === record.id))
                    }
                    onChange={() => toggleChosen(record)}
                  />
                  <button
                    className="db-record-button"
                    disabled={!enabled}
                    onClick={() => {
                      setSelected(record)
                      setComparing(false)
                      setDetailError("")
                    }}
                  >
                    <strong title={record.sn || record.fileName}>
                      {record.sn || record.fileName}
                    </strong>
                    <time>{timeText(record.timeMs)}</time>
                    <span className="db-record-bottom">
                      <span>
                        {record.station || "站别未记录"} · {record.pointCount}{" "}
                        点
                      </span>
                      <span
                        className="db-status"
                        data-kind={
                          record.readStatus === "error"
                            ? "error"
                            : record.flowStatus === "completed"
                              ? "complete"
                              : "other"
                        }
                      >
                        {record.flowStatus === "completed" && (
                          <CheckCircle2 size={13} />
                        )}
                        {record.readStatus === "error"
                          ? "读取失败"
                          : flowLabel(record.flowStatus)}
                      </span>
                    </span>
                  </button>
                </div>
              ))
            )}
          </div>
          <footer className="db-pagination">
            <span>
              第 {listing?.page ?? 1} / {listing?.pageCount ?? 1} 页
            </span>
            <div className="db-pagination-controls">
              <Button
                aria-label="上一页"
                variant="ghost"
                size="sm"
                disabled={listBusy || page <= 1}
                onClick={() => setPage((value) => value - 1)}
              >
                <ChevronLeft size={17} />
              </Button>
              <Button
                aria-label="下一页"
                variant="ghost"
                size="sm"
                disabled={listBusy || page >= (listing?.pageCount ?? 1)}
                onClick={() => setPage((value) => value + 1)}
              >
                <ChevronRight size={17} />
              </Button>
            </div>
          </footer>
        </aside>
        <article className="db-detail-panel" aria-label="测试详情">
          <div className="db-panel-heading">
            <div className="db-panel-title">
              {expanded && (
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label="返回列表"
                  onClick={() => setExpanded(false)}
                >
                  <ArrowLeft size={16} />
                </Button>
              )}
              {comparing ? `${chosen.length} 条测试对比` : "记录详情"}
            </div>
            <div
              className="db-segmented-tabs"
              role="tablist"
              aria-label="详情视图"
            >
              {[
                { id: "overview", label: "曲线", icon: BarChart3 },
                {
                  id: "points",
                  label: comparing ? "数据对比" : "测试点",
                  icon: Table,
                },
                { id: "spectra", label: "光谱", icon: BarChart3 },
                { id: "info", label: "信息", icon: Info },
              ].map(({ id, label, icon: Icon }) => (
                <button
                  key={id}
                  role="tab"
                  aria-selected={tab === id}
                  onClick={() => setTab(id)}
                >
                  <Icon size={15} />
                  {label}
                </button>
              ))}
            </div>
            <div className="db-actions">
              {comparing && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setComparing(false)}
                >
                  退出对比
                </Button>
              )}
              {!comparing && detail && (
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label="打开 Excel"
                  title="打开原始 Excel"
                  disabled={fileBusy}
                  onClick={() => void fileAction(detail.record, false)}
                >
                  <FileSpreadsheet size={17} />
                </Button>
              )}
              <Button
                size="sm"
                variant="ghost"
                aria-label={expanded ? "收起详情" : "展开详情"}
                onClick={() => setExpanded((value) => !value)}
              >
                {expanded ? <Minimize2 size={17} /> : <Maximize2 size={17} />}
              </Button>
            </div>
          </div>
          <div
            className="db-detail-scroll"
            ref={detailScroll}
            aria-busy={detailBusy}
          >
            {detailBusy ? (
              <div className="db-empty">正在读取测试数据…</div>
            ) : detailError ? (
              <div className="db-notice is-error" role="alert">
                {detailError}
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setRefresh((value) => value + 1)}
                >
                  重试
                </Button>
              </div>
            ) : !detail ? (
              <div className="db-empty">
                <BarChart3 size={30} />
                <p>选择左侧记录查看详情</p>
                <span>勾选 2 至 5 条记录，可跨 SN、跨日期对比全部参数。</span>
              </div>
            ) : (
              <>
                {!comparing && (
                  <div className="db-record-heading">
                    <div>
                      <h2>{detail.record.sn || detail.record.fileName}</h2>
                      <p>
                        {detail.record.station || "站别未记录"} ·{" "}
                        {timeText(detail.record.timeMs)} ·{" "}
                        {detail.record.temperature || "温度未记录"}
                      </p>
                    </div>
                    <span className="db-readonly">
                      {detail.record.storage === "database"
                        ? "数据库存档"
                        : "Excel 文件"}
                    </span>
                  </div>
                )}
                {comparing && (
                  <details className="db-comparison-context">
                    <summary>
                      测试条件与基准 ·{" "}
                      {details[0].record.sn || details[0].record.fileName}
                    </summary>
                    <div className="db-compare-records">
                      {details.map((item, index) => (
                        <div
                          className="db-compare-card"
                          key={item.record.id}
                          style={{ borderTopColor: SERIES_COLORS[index] }}
                        >
                          <div className="db-compare-card-head">
                            <strong>
                              {index + 1}.{" "}
                              {item.record.sn || item.record.fileName}
                            </strong>
                            {index === 0 ? (
                              <span>基准</span>
                            ) : (
                              <button
                                className="db-set-base"
                                onClick={() =>
                                  setChosen((previous) => [
                                    previous[index],
                                    ...previous.filter(
                                      (_, position) => position !== index,
                                    ),
                                  ])
                                }
                              >
                                设为基准
                              </button>
                            )}
                          </div>
                          <p>
                            {item.record.station || "站别未记录"} ·{" "}
                            {item.record.temperature || "温度未记录"}
                          </p>
                          <time>{timeText(item.record.timeMs)}</time>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
                {comparing &&
                  new Set(
                    details.map((item) => item.record.temperature || "未记录"),
                  ).size > 1 && (
                    <div className="db-notice">
                      测试温度不同，请结合测试条件比较。
                    </div>
                  )}
                {comparing &&
                  details.some(
                    (item) => item.record.flowStatus !== "completed",
                  ) && (
                    <div className="db-notice">
                      包含未完成或完成状态未知的 Excel 记录，测试点可能不完整。
                    </div>
                  )}
                {tab !== "info" && tab !== "spectra" && (
                  <div
                    className="db-metric-tabs"
                    role="tablist"
                    aria-label="对比参数"
                  >
                    {DATA_METRICS.map((item) => (
                      <button
                        key={item.key}
                        role="tab"
                        aria-selected={metricKey === item.key}
                        onClick={() => setMetricKey(item.key)}
                      >
                        {item.label}
                        <small>{item.unit}</small>
                      </button>
                    ))}
                  </div>
                )}
                {tab === "overview" && (
                  <>
                    {!comparing && (
                      <div className="db-metrics">
                        <div className="db-metric">
                          <span>最大光功率</span>
                          <strong>
                            {numberText(summary?.maxPowerPoint?.powerW, 2)}{" "}
                            <small>W</small>
                          </strong>
                        </div>
                        <div className="db-metric">
                          <span>最高电光效率</span>
                          <strong>
                            {numberText(
                              summary?.maxEfficiencyPoint?.efficiencyPercent,
                              2,
                            )}{" "}
                            <small>%</small>
                          </strong>
                        </div>
                        <div className="db-metric">
                          <span>峰值波长（最大功率处）</span>
                          <strong>
                            {numberText(
                              summary?.maxPowerPoint?.peakWavelengthNm,
                              2,
                            )}{" "}
                            <small>nm</small>
                          </strong>
                        </div>
                        <div className="db-metric">
                          <span>有效测试点</span>
                          <strong>
                            {detail.record.validPointCount}{" "}
                            <small>/ {detail.record.pointCount}</small>
                          </strong>
                        </div>
                      </div>
                    )}
                    <div className="db-chart-card">
                      <div className="db-chart-title">
                        <div className="db-chart-title-left">
                          <BarChart3 size={18} />
                          <strong>
                            {metric.label}与电流{comparing ? "对比" : "曲线"}
                          </strong>
                          <span>{metric.unit}</span>
                        </div>
                        {!comparing && (
                          <label className="db-point-select">
                            标注
                            <select
                              aria-label="选择测试点"
                              value={pointRow ?? ""}
                              onChange={(event) =>
                                setPointRow(Number(event.target.value))
                              }
                            >
                              {detail.points.map((point) => (
                                <option key={point.rowId} value={point.rowId}>
                                  {numberText(point.currentA, 2)} A ·{" "}
                                  {numberText(point[metricKey])} {metric.unit}
                                </option>
                              ))}
                            </select>
                          </label>
                        )}
                      </div>
                      {comparing && (
                        <div className="db-chart-legend">
                          {details.map((item, index) => (
                            <span
                              key={item.record.id}
                              title={recordLabel(item.record)}
                            >
                              <i style={{ background: SERIES_COLORS[index] }} />
                              {index + 1}.{" "}
                              {item.record.sn || item.record.fileName}
                              {!item.points.some(
                                (point) => point[metricKey] != null,
                              )
                                ? "（未记录此参数）"
                                : ""}
                            </span>
                          ))}
                        </div>
                      )}
                      <div className="db-chart">
                        {rows.length ? (
                          <CanvasLineChart
                            yDomain={yDomain}
                            data={rows}
                            xKey="currentA"
                            lines={lines}
                            ariaLabel={`${comparing ? "多条测试" : "历史"}${metric.label}电流曲线`}
                            xLabel="电流 (A)"
                            yLabel={`${metric.label} (${metric.unit})`}
                            annotations={annotations}
                            preserveAllPoints
                          />
                        ) : (
                          <div className="db-empty">
                            这些记录未包含有效的{metric.label}数据
                          </div>
                        )}
                      </div>
                      <p className="db-table-note">
                        每条曲线保留实际测量电流点。悬停查看数值，展开详情可放大图表。
                      </p>
                    </div>
                  </>
                )}
                {comparing && tab !== "info" && tab !== "spectra" && (
                  <div className="db-chart-card">
                    <div className="db-chart-title">
                      <strong>{metric.label}数值与差值</strong>
                      <span>差值 = 当前记录 − 基准</span>
                    </div>
                    <div className="db-table-scroll">
                      <table className="db-data-table">
                        <thead>
                          <tr>
                            <th>电流 (A)</th>
                            {details.map((item, index) => (
                              <th
                                key={item.record.id}
                                title={recordLabel(item.record)}
                              >
                                {index + 1}. {item.record.sn || "记录"} (
                                {metric.unit})
                                {index === 0 ? " · 基准" : " / 差值"}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {differences.map((row) => (
                            <tr key={row.currentA}>
                              <td>{numberText(row.currentA, 2)}</td>
                              {row.values.map((value, index) => (
                                <td key={index}>
                                  {row.ambiguous[index] ? (
                                    "重复电流点"
                                  ) : (
                                    <>
                                      {numberText(value)}
                                      {index > 0 && (
                                        <small className="db-delta">
                                          Δ {numberText(row.deltas[index])}
                                        </small>
                                      )}
                                    </>
                                  )}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <p className="db-table-note">
                      只在相同实测电流下计算差值，不插值；缺失数据或重复电流点不计算差值。效率与
                      PIB 的差值单位为百分点。
                    </p>
                  </div>
                )}
                {!comparing && tab === "points" && (
                  <div className="db-chart-card">
                    <div className="db-table-scroll">
                      <table className="db-data-table">
                        <thead>
                          <tr>
                            <th>电流 (A)</th>
                            {DATA_METRICS.map((item) => (
                              <th key={item.key}>
                                {item.label} ({item.unit})
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {detail.points.map((point) => (
                            <tr
                              key={point.rowId}
                              data-selected={pointRow === point.rowId}
                              onClick={() => setPointRow(point.rowId)}
                            >
                              <td>
                                <button
                                  onClick={() => {
                                    setPointRow(point.rowId)
                                    setTab("overview")
                                  }}
                                >
                                  {numberText(point.currentA, 2)}
                                </button>
                              </td>
                              {DATA_METRICS.map((item) => (
                                <td key={item.key}>
                                  {numberText(point[item.key])}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <p className="db-table-note">
                      点击电流可返回曲线并标注测试点。缺失或无效参数显示为 —。
                    </p>
                  </div>
                )}
                {tab === "spectra" && (
                  comparing ? (
                    <div className="space-y-6">
                      {details.map((item, idx) => (
                        <div key={item.record.id || idx} className="space-y-2">
                          <div className="db-spectrum-record-title flex flex-wrap items-center gap-2 font-semibold text-foreground px-1">
                            <i
                              className="inline-block w-2.5 h-2.5 rounded-full"
                              style={{ background: SERIES_COLORS[idx % SERIES_COLORS.length] }}
                            />
                            <span>{item.record.sn || `样本 ${idx + 1}`} - 光谱演化</span>
                            <span className="db-spectrum-record-meta text-muted-foreground font-normal">({recordLabel(item.record)})</span>
                          </div>
                          <SpectralWaterfallChart
                            spectra={item.spectra}
                            sn={item.record.sn}
                          />
                        </div>
                      ))}
                    </div>
                  ) : (
                    detail && (
                      <SpectralWaterfallChart
                        spectra={detail.spectra}
                        sn={detail.record.sn}
                      />
                    )
                  )
                )}
                {tab === "info" &&
                  details.map((item) => (
                    <div className="db-info-card" key={item.record.id}>
                      <h3>{recordLabel(item.record)}</h3>
                      <dl className="db-info">
                        <dt>存储位置</dt>
                        <dd>
                          {item.record.storage === "database"
                            ? listing?.rootPath
                            : "原始 Excel"}
                        </dd>
                        <dt>原始 Excel</dt>
                        <dd>
                          {item.record.sourcePath}
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={fileBusy}
                            onClick={() => void fileAction(item.record, true)}
                          >
                            定位文件
                          </Button>
                        </dd>
                        {Object.entries(item.metadata).map(([key, value]) => (
                          <div className="db-info-row" key={key}>
                            <dt>{key}</dt>
                            <dd>{value || "未记录"}</dd>
                          </div>
                        ))}
                      </dl>
                    </div>
                  ))}
                {details.flatMap((item) =>
                  item.warnings.map((warning) => (
                    <div
                      className="db-notice"
                      key={`${item.record.id}:${warning}`}
                    >
                      {comparing ? `${item.record.sn}：` : ""}
                      {warning}
                    </div>
                  )),
                )}
              </>
            )}
          </div>
        </article>
      </div>
    </section>
  )
})
