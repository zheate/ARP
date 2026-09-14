import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import {
  AlertTriangle,
  ArrowLeft,
  ArrowLeftRight,
  BarChart3,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleSlash,
  ExternalLink,
  FileSpreadsheet,
  FolderOpen,
  HelpCircle,
  Info,
  Loader,
  Maximize2,
  Minimize2,
  RefreshCw,
  Search,
  Table,
  X,
  XCircle,
} from "lucide-react"
import { open as openDialog } from "@tauri-apps/plugin-dialog"
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { CanvasLineChart, type CanvasChartLine } from "@/components/canvas-line-chart"
import { sendBackendRequest } from "@/lib/backend"
import { openResultFile } from "@/lib/result-file"
import {
  comparisonRows,
  dateBounds,
  extractComparisonSummary,
  extractSummaryMetrics,
  flowLabel,
  numberText,
  powerChartRows,
  recordLabel,
  requestGuard,
  timeText,
  type DataDetail,
  type DataList,
  type DataRecord,
} from "@/lib/data-browser"

const DIRECTORY_KEY = "power-test.data-browser.directory.v1"
const EMPTY_RECORDS: DataRecord[] = []
const POINT_COLUMNS = [
  ["currentA", "电流", "A"],
  ["voltageV", "电压", "V"],
  ["powerW", "功率", "W"],
  ["efficiencyPercent", "效率", "%"],
  ["peakWavelengthNm", "峰值波长", "nm"],
  ["centroidNm", "质心波长", "nm"],
  ["fwhmNm", "FWHM", "nm"],
  ["pibPercent", "PIB", "%"],
  ["smsrDb", "SMSR", "dB"],
] as const

const DETAIL_TABS = [
  { id: "overview", label: "曲线概览", icon: BarChart3 },
  { id: "points", label: "测试点表", icon: Table },
  { id: "info", label: "测试信息", icon: Info },
] as const

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

/** State is always icon + text + semantic colour, never colour alone (DESIGN §16.1). */
function Status({ record }: { record: DataRecord }) {
  if (record.readStatus === "error") {
    return (
      <span className="db-status" data-kind="error" title={record.error || "文件读取失败"}>
        <XCircle size={12} />
        读取失败
      </span>
    )
  }
  const flow = record.flowStatus
  const kind = flow === "completed" ? "complete" : flow === "failed" || flow === "error" ? "error" : "other"
  const Icon =
    flow === "completed"
      ? CheckCircle2
      : flow === "failed" || flow === "error"
        ? XCircle
        : flow === "running"
          ? Loader
          : flow === "stopped_by_operator"
            ? CircleSlash
            : flow === "aborted_safely" || flow === "shutdown_unconfirmed"
              ? AlertTriangle
              : HelpCircle
  return (
    <span className="db-status" data-kind={kind}>
      <Icon size={12} />
      {flowLabel(flow)}
    </span>
  )
}

function Empty({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="db-empty">
      <FileSpreadsheet size={28} strokeWidth={1.3} />
      <p>{children}</p>
      {action}
    </div>
  )
}

function Metric({
  label,
  value,
  unit,
  sub,
  accent = false,
}: {
  label: string
  value: string
  unit?: string
  sub?: string
  accent?: boolean
}) {
  return (
    <div className="db-metric" data-accent-value={accent}>
      <span>{label}</span>
      <strong>
        {value} <small>{unit}</small>
      </strong>
      {sub && <span className="db-metric-sub">{sub}</span>}
    </div>
  )
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
  const [detail, setDetail] = useState<DataDetail | null>(null)
  const [detailBusy, setDetailBusy] = useState(false)
  const [detailError, setDetailError] = useState("")
  const [tab, setTab] = useState("overview")
  const [pointRow, setPointRow] = useState<number | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [chosen, setChosen] = useState<DataRecord[]>([])
  const [comparing, setComparing] = useState(false)
  const [comparisons, setComparisons] = useState<DataDetail[]>([])
  const [compareBusy, setCompareBusy] = useState(false)
  const [compareError, setCompareError] = useState("")
  const [fileBusy, setFileBusy] = useState(false)
  const [fileMessage, setFileMessage] = useState("")
  const listGuard = useRef(requestGuard())
  const detailGuard = useRef(requestGuard())
  const compareGuard = useRef(requestGuard())
  const scanStamp = useRef("")
  const listQueryRef = useRef("")
  const compareKeyRef = useRef("")
  const detailScrollRef = useRef<HTMLDivElement>(null)
  const recordsScrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (visible && !root && defaultDirectory) {
      setRoot(defaultDirectory)
      setDirectoryDraft(defaultDirectory)
    }
  }, [visible, root, defaultDirectory])

  useEffect(() => {
    if (visible) setRefresh((value) => value + 1)
  }, [visible])

  useEffect(() => {
    if (detailScrollRef.current) detailScrollRef.current.scrollTop = 0
  }, [selected?.id, comparing, tab])

  useEffect(() => {
    if (recordsScrollRef.current) recordsScrollRef.current.scrollTop = 0
  }, [page])

  useEffect(() => {
    const guard = listGuard.current
    const ticket = guard.next()
    if (!visible || !root || !enabled) {
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
      const stamp = `${root}\n${refresh}`
      const queryKey = `${root}\n${sn}\n${station}\n${status}\n${dateMode}\n${from}\n${to}\n${refresh}`
      void sendBackendRequest<DataList>("dataBrowser.list", {
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
          const queryChanged = listQueryRef.current !== queryKey
          listQueryRef.current = queryKey
          scanStamp.current = stamp
          setListing(result)
          setPage(result.page)
          setSelected((previous) => {
            const match = result.records.find((record) => record.id === previous?.id)
            if (match) return match
            if (previous && !queryChanged) return previous
            return result.records.find((record) => record.readStatus === "readable") ?? result.records[0] ?? null
          })
        })
        .catch((error) => {
          if (!guard.current(ticket)) return
          setListError(message(error))
          setListing(null)
          setSelected(null)
          setDetail(null)
        })
        .finally(() => {
          if (guard.current(ticket)) setListBusy(false)
        })
    }, 250)
    return () => {
      clearTimeout(timer)
      guard.invalidate()
    }
  }, [visible, enabled, root, sn, station, status, dateMode, from, to, page, refresh])

  useEffect(() => {
    const guard = detailGuard.current
    const ticket = guard.next()
    setDetail(null)
    setDetailError("")
    setFileMessage("")
    setDetailBusy(false)
    if (!visible || !enabled || !selected || comparing) return
    if (selected.readStatus === "error") {
      setDetailError(selected.error)
      return
    }
    setDetailBusy(true)
    void sendBackendRequest<DataDetail>("dataBrowser.inspect", {
      path: selected.sourcePath,
      version: selected.version,
    })
      .then((result) => {
        if (!guard.current(ticket)) return
        setDetail(result)
        setPointRow((previous) =>
          result.points.some((point) => point.rowId === previous) ? previous : result.points[0]?.rowId ?? null,
        )
      })
      .catch((error) => {
        if (guard.current(ticket)) setDetailError(message(error))
      })
      .finally(() => {
        if (guard.current(ticket)) setDetailBusy(false)
      })
    return () => guard.invalidate()
  }, [visible, enabled, selected?.id, selected?.version, refresh, comparing])

  useEffect(() => {
    const guard = compareGuard.current
    const ticket = guard.next()
    if (!visible || !enabled || !comparing || chosen.length !== 2) {
      compareKeyRef.current = ""
      setComparisons([])
      setCompareError("")
      setCompareBusy(false)
      return
    }
    const compareKey = `${refresh}|${chosen
      .map((record) => record.id)
      .slice()
      .sort()
      .join("|")}`
    if (compareKeyRef.current === compareKey) return
    setComparisons([])
    setCompareError("")
    setCompareBusy(false)
    setCompareBusy(true)
    void Promise.all(
      chosen.map((record) =>
        sendBackendRequest<DataDetail>("dataBrowser.inspect", { path: record.sourcePath }),
      ),
    )
      .then((result) => {
        if (guard.current(ticket)) {
          compareKeyRef.current = compareKey
          setComparisons(result)
        }
      })
      .catch((error) => {
        if (guard.current(ticket)) setCompareError(message(error))
      })
      .finally(() => {
        if (guard.current(ticket)) setCompareBusy(false)
      })
    return () => guard.invalidate()
  }, [visible, enabled, comparing, chosen, refresh])

  const useDirectory = (value: string) => {
    const next = value.trim()
    if (!next) {
      setListError("请输入或选择数据目录")
      return
    }
    listGuard.current.invalidate()
    detailGuard.current.invalidate()
    compareGuard.current.invalidate()
    setRoot(next)
    setDirectoryDraft(next)
    setPage(1)
    setStation("")
    setStatus("")
    setListing(null)
    setSelected(null)
    setDetail(null)
    setChosen([])
    setComparing(false)
    setComparisons([])
    setRefresh((value) => value + 1)
    try {
      localStorage.setItem(DIRECTORY_KEY, next)
    } catch {
      /* Session state is still usable. */
    }
  }

  const chooseDirectory = async () => {
    try {
      const path = await openDialog({
        directory: true,
        multiple: false,
        title: "选择测试数据目录",
        defaultPath: root || undefined,
      })
      if (typeof path === "string") useDirectory(path)
    } catch (error) {
      setListError(message(error))
    }
  }

  const selectRecord = (record: DataRecord) => {
    detailGuard.current.invalidate()
    setSelected(record)
    setDetail(null)
    setPointRow(null)
    setComparing(false)
    setDetailError("")
  }

  const toggleChosen = (record: DataRecord) => {
    setComparing(false)
    setChosen((previous) =>
      previous.some((item) => item.id === record.id)
        ? previous.filter((item) => item.id !== record.id)
        : previous.length < 2
          ? [...previous, record]
          : previous,
    )
  }

  const fileAction = async (record: DataRecord, locate: boolean) => {
    setFileBusy(true)
    setFileMessage("")
    try {
      if (locate) {
        await revealItemInDir(record.sourcePath)
        setFileMessage("已在文件夹中定位")
      } else {
        const outcome = await openResultFile(record.sourcePath, openPath, revealItemInDir)
        setFileMessage(
          outcome.status === "revealed" ? `无法直接打开，已定位文件：${outcome.openError}` : "已打开 Excel 文件",
        )
      }
    } catch (error) {
      setFileMessage(message(error))
    } finally {
      setFileBusy(false)
    }
  }

  const details = useMemo(() => (comparing ? comparisons : detail ? [detail] : []), [comparing, comparisons, detail])
  const chartRows = useMemo(() => powerChartRows(details), [details])
  const differences = useMemo(
    () => (comparisons.length === 2 ? comparisonRows(comparisons[0].points, comparisons[1].points) : []),
    [comparisons],
  )
  const currentPoint = detail?.points.find((point) => point.rowId === pointRow)
  const summaryMetrics = useMemo(() => (detail ? extractSummaryMetrics(detail.points) : null), [detail])
  const compSummary = useMemo(
    () => (comparisons.length === 2 ? extractComparisonSummary(comparisons[0].points, comparisons[1].points) : null),
    [comparisons],
  )
  const chartLines = useMemo<CanvasChartLine[]>(
    () =>
      details.map((item, index) => ({
        key: index === 0 ? "base" : "other",
        label: comparing ? recordLabel(item.record) : "功率",
        color: index === 0 ? "var(--chart-1, #FF7A1A)" : "var(--chart-2, #D5A98B)",
        showPoints: true,
        connectNulls: true,
        pointStyle: "hollow",
        pointSize: 6,
        lineWidth: 2,
        dash: index === 1 ? [6, 4] : undefined,
        hideLegend: true,
      })),
    [details, comparing],
  )
  const selectedRecord = detail?.record ?? selected
  const busy = comparing ? compareBusy : detailBusy
  const error = comparing ? compareError : detailError
  const displayListing = listError ? null : listing
  const records = displayListing?.records ?? EMPTY_RECORDS
  const emptyText = !enabled
    ? "数据浏览已锁定，暂不读取目录"
    : listError
      ? "读取失败，请按上方提示处理后重试"
      : listBusy
        ? "正在索引测试文件…"
        : !root
          ? "选择保存测试 Excel 的目录"
          : "没有符合条件的测试记录"

  const compareNotices = useMemo(() => {
    if (comparisons.length !== 2) return []
    const [base, other] = comparisons.map((item) => item.record)
    const notices: string[] = []
    if (base.sn && other.sn && base.sn !== other.sn) {
      notices.push(`两次测试的产品 SN 不同（${base.sn} / ${other.sn}），差值结果仅作参考。`)
    }
    const unfinished = [base, other].filter((record) => record.flowStatus !== "completed")
    if (unfinished.length) {
      notices.push(
        `未正常完成的记录：${unfinished
          .map((record) => `${record.sn || record.fileName}（${flowLabel(record.flowStatus)}）`)
          .join("、")}，已有点位的数据可能不完整。`,
      )
    }
    return notices
  }, [comparisons])

  const annotations = useMemo(
    () =>
      !comparing && currentPoint?.powerW != null
        ? [
            {
              x: currentPoint.currentA,
              y: currentPoint.powerW,
              label: `${numberText(currentPoint.currentA, 2)} A`,
              color: "var(--chart-1, #FF7A1A)",
            },
          ]
        : [],
    [comparing, currentPoint],
  )

  const updateFilter = (change: () => void) => {
    listGuard.current.invalidate()
    change()
    setPage(1)
  }

  const filtersDirty = Boolean(sn || station || status || dateMode !== "all" || from || to)
  const clearFilters = () =>
    updateFilter(() => {
      setSn("")
      setStation("")
      setStatus("")
      setDateMode("all")
      setFrom("")
      setTo("")
    })

  const compareDisabled = (record: DataRecord) =>
    record.validPointCount === 0 || (chosen.length === 2 && !chosen.some((item) => item.id === record.id))

  const compareHint = (record: DataRecord) =>
    record.validPointCount === 0
      ? "没有有效功率点，无法参与对比"
      : chosen.length === 2 && !chosen.some((item) => item.id === record.id)
        ? "已选满 2 条，移除一条后可重新选择"
        : "勾选两条记录后可对比两次测试"

  return (
    <section className="data-browser" aria-label="数据浏览">
      {/* Top Filter Card */}
      <div className="db-filters">
        <form
          className="db-directory"
          onSubmit={(event) => {
            event.preventDefault()
            useDirectory(directoryDraft)
          }}
        >
          <FolderOpen size={16} className="db-dir-icon" />
          <label className="sr-only" htmlFor="db-directory">
            数据目录
          </label>
          <input
            id="db-directory"
            value={directoryDraft}
            onChange={(event) => setDirectoryDraft(event.target.value)}
            placeholder="输入测试数据目录，按 Enter 读取…"
            disabled={!enabled}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!enabled || listBusy}
            onClick={() => void chooseDirectory()}
          >
            选择目录
          </Button>
          {defaultDirectory && defaultDirectory !== root && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={!enabled || listBusy}
              onClick={() => useDirectory(defaultDirectory)}
              title="快速填入测试输出目录"
            >
              当前输出目录
            </Button>
          )}
          <Button type="submit" variant="default" size="sm" disabled={!enabled || listBusy}>
            读取
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!enabled || !root || listBusy}
            onClick={() => setRefresh((value) => value + 1)}
          >
            <RefreshCw size={13} className={listBusy ? "animate-spin" : ""} />
            刷新
          </Button>
          <span className="db-readonly">只读浏览</span>
        </form>

        <div className="db-filter-row">
          <div className="db-search">
            <Search size={14} />
            <input
              aria-label="搜索 SN"
              value={sn}
              disabled={!enabled}
              onChange={(event) => updateFilter(() => setSn(event.target.value))}
              placeholder="搜索产品 SN / 文件名…"
            />
          </div>
          <label>
            时间
            <select
              aria-label="日期范围"
              value={dateMode}
              disabled={!enabled}
              onChange={(event) => updateFilter(() => setDateMode(event.target.value))}
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
                disabled={!enabled}
                onChange={(event) => updateFilter(() => setFrom(event.target.value))}
              />
              <span>至</span>
              <input
                aria-label="结束日期"
                type="date"
                value={to}
                disabled={!enabled}
                onChange={(event) => updateFilter(() => setTo(event.target.value))}
              />
            </span>
          )}
          <label>
            站别
            <select
              aria-label="测试站别筛选"
              value={station}
              disabled={!enabled}
              onChange={(event) => updateFilter(() => setStation(event.target.value))}
            >
              <option value="">全部站别</option>
              {listing?.stations.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </label>
          <label>
            状态
            <select
              aria-label="记录状态筛选"
              value={status}
              disabled={!enabled}
              onChange={(event) => updateFilter(() => setStatus(event.target.value))}
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
          {filtersDirty && (
            <Button type="button" variant="ghost" size="sm" disabled={!enabled} onClick={clearFilters}>
              <X size={13} />
              清除筛选
            </Button>
          )}
        </div>
      </div>

      {!enabled && disabledReason && (
        <div className="db-notice is-locked" role="status">
          {disabledReason}
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

      {/* Comparison Selection Bar */}
      {chosen.length > 0 && (
        <div className="db-selection">
          <div className="db-selected-items">
            <span className="db-selection-title">
              <ArrowLeftRight size={13} className="text-primary" />
              已选对比 ({chosen.length}/2)
            </span>
            {chosen.map((record, index) => (
              <span className="db-selected-item" key={record.id} title={recordLabel(record)}>
                <span className="db-selected-badge">{index === 0 ? "基准" : "对比"}</span>
                <span className="db-selected-name">{record.sn || record.fileName}</span>
                <span className="db-selected-meta">
                  {record.station || "站别未记录"} · {timeText(record.timeMs)}
                </span>
                <button aria-label={`移除${recordLabel(record)}`} onClick={() => toggleChosen(record)}>
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>
          <div className="db-selection-actions">
            <Button
              size="sm"
              disabled={chosen.length !== 2 || compareBusy}
              onClick={() => {
                setComparing(true)
                setTab("overview")
              }}
            >
              <ArrowLeftRight size={13} />
              {comparing ? "正在对比" : "对比曲线"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setChosen([])
                setComparing(false)
              }}
            >
              清空选择
            </Button>
          </div>
        </div>
      )}

      {/* Main Workspace */}
      <div className={`db-workspace ${expanded ? "is-expanded" : ""}`}>
        {/* Left Record List Panel */}
        <aside className="db-list-panel" aria-label="测试记录列表" hidden={expanded}>
          <div className="db-panel-heading">
            <div className="db-panel-title">
              <span>测试记录</span>
              <Badge variant="secondary" className="font-din text-xs">
                {displayListing?.total ?? 0}
              </Badge>
            </div>
            <div className="db-panel-meta" aria-live="polite">
              {listBusy ? (
                displayListing ? (
                  "正在读取…"
                ) : (
                  "首次索引整个目录…"
                )
              ) : displayListing ? (
                <>
                  {displayListing.errorCount > 0 && (
                    <>
                      <button
                        type="button"
                        className="db-fail-filter"
                        onClick={() => updateFilter(() => setStatus("read_error"))}
                      >
                        <AlertTriangle size={12} />
                        {displayListing.errorCount} 个读取失败
                      </button>
                      <span>·</span>
                    </>
                  )}
                  <span>
                    索引于{" "}
                    {new Date(displayListing.indexedAtMs).toLocaleTimeString(undefined, {
                      hour: "2-digit",
                      minute: "2-digit",
                      hour12: false,
                    })}
                  </span>
                </>
              ) : enabled ? (
                "选择目录开始"
              ) : (
                "已锁定"
              )}
            </div>
          </div>

          <div className="db-records" aria-busy={listBusy} ref={recordsScrollRef}>
            {!records.length ? (
              <Empty
                action={
                  listError ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={!enabled || listBusy}
                      onClick={() => setRefresh((value) => value + 1)}
                    >
                      重新读取
                    </Button>
                  ) : filtersDirty ? (
                    <Button type="button" variant="outline" size="sm" disabled={!enabled} onClick={clearFilters}>
                      清除筛选
                    </Button>
                  ) : root ? undefined : (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={!enabled || listBusy}
                      onClick={() => void chooseDirectory()}
                    >
                      选择目录
                    </Button>
                  )
                }
              >
                {emptyText}
              </Empty>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th className="db-col-check" title="勾选两条记录进行对比">
                      对比
                    </th>
                    <th>产品 SN</th>
                    <th className="db-col-station">站别</th>
                    <th className="db-col-time">时间</th>
                    <th className="db-col-status">状态</th>
                  </tr>
                </thead>
                <tbody>
                  {records.map((record) => (
                    <tr
                      key={record.id}
                      data-selected={record.id === selected?.id && !comparing}
                      onClick={() => selectRecord(record)}
                      title={record.sourcePath}
                    >
                      <td className="db-col-check">
                        <span
                          className="db-compare-toggle"
                          title={compareHint(record)}
                          onClick={(event) => event.stopPropagation()}
                        >
                          <input
                            type="checkbox"
                            aria-label={`选择对比 ${recordLabel(record)}`}
                            checked={chosen.some((item) => item.id === record.id)}
                            disabled={compareDisabled(record)}
                            onChange={() => toggleChosen(record)}
                          />
                        </span>
                      </td>
                      <td className="db-col-sn">
                        <button
                          type="button"
                          className="db-row-select"
                          onClick={() => selectRecord(record)}
                          title={record.sourcePath}
                        >
                          <span className="db-sn">{record.sn || record.fileName}</span>
                          {record.snSource === "directory" && (
                            <span className="db-sn-hint" title="SN 由标准目录结构推定，文件未记录 SN">
                              推定
                            </span>
                          )}
                        </button>
                      </td>
                      <td className="db-col-station" title={record.station || "站别未记录"}>
                        {record.station || "—"}
                      </td>
                      <td className="db-col-time">
                        <time
                          data-source={record.timeSource}
                          title={
                            record.timeSource === "modified"
                              ? "文件未记录开始时间，显示文件修改时间"
                              : "测试开始时间"
                          }
                        >
                          {timeText(record.timeMs)}
                        </time>
                      </td>
                      <td className="db-col-status">
                        <Status record={record} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <footer className="db-pagination">
            <span className="db-pagination-info">
              第 {displayListing?.page ?? 1} / {displayListing?.pageCount ?? 1} 页
            </span>
            <div className="db-pagination-controls">
              <Button
                aria-label="上一页"
                variant="ghost"
                size="sm"
                disabled={listBusy || (displayListing?.page ?? 1) <= 1}
                onClick={() => setPage((value) => value - 1)}
              >
                <ChevronLeft size={16} />
              </Button>
              <Button
                aria-label="下一页"
                variant="ghost"
                size="sm"
                disabled={listBusy || (displayListing?.page ?? 1) >= (displayListing?.pageCount ?? 1)}
                onClick={() => setPage((value) => value + 1)}
              >
                <ChevronRight size={16} />
              </Button>
            </div>
          </footer>
        </aside>

        {/* Right Detail Panel */}
        <article className="db-detail-panel" aria-label="测试详情">
          <div className="db-panel-heading">
            <div className="flex items-center gap-2">
              {expanded && (
                <Button size="sm" variant="ghost" onClick={() => setExpanded(false)}>
                  <ArrowLeft size={14} />
                  返回列表
                </Button>
              )}
              <div className="db-panel-title">{comparing ? "两次测试对比" : "记录详情"}</div>
            </div>

            {!comparing && (detail || comparing) && (
              <div className="db-segmented-tabs" role="tablist" aria-label="详情视图">
                {DETAIL_TABS.map(({ id, label, icon: TabIcon }) => (
                  <button
                    type="button"
                    role="tab"
                    aria-selected={tab === id}
                    key={id}
                    onClick={() => setTab(id)}
                  >
                    <TabIcon size={13} />
                    {label}
                  </button>
                ))}
              </div>
            )}

            <div className="db-actions">
              {comparing ? (
                <>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setChosen(([a, b]) => (a && b ? [b, a] : [a, b]))
                      setComparisons((previous) => (previous.length === 2 ? [previous[1], previous[0]] : previous))
                    }}
                  >
                    <ArrowLeftRight size={13} />
                    交换基准
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setComparing(false)}>
                    退出对比
                  </Button>
                </>
              ) : (
                selectedRecord && (
                  <>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label="打开 Excel"
                      title="打开 Excel"
                      disabled={fileBusy}
                      onClick={() => void fileAction(selectedRecord, false)}
                    >
                      <FileSpreadsheet size={15} />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label="打开所在文件夹"
                      title="打开所在文件夹"
                      disabled={fileBusy}
                      onClick={() => void fileAction(selectedRecord, true)}
                    >
                      <FolderOpen size={15} />
                    </Button>
                  </>
                )
              )}
              <Button
                size="sm"
                variant="ghost"
                aria-label={expanded ? "收起详情" : "展开详情"}
                title={expanded ? "收起" : "全屏展开"}
                onClick={() => setExpanded((value) => !value)}
              >
                {expanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
              </Button>
            </div>
          </div>

          <div className="db-detail-scroll" aria-busy={busy} ref={detailScrollRef}>
            {busy ? (
              <Empty>正在读取{comparing ? "对比" : "测试"}数据…</Empty>
            ) : error ? (
              <div className="db-notice is-error" role="alert">
                {error}
                <Button size="sm" variant="outline" onClick={() => setRefresh((value) => value + 1)}>
                  刷新重试
                </Button>
              </div>
            ) : !details.length ? (
              <Empty>
                {enabled ? "选择左侧记录，查看测试数据" : "数据浏览已锁定，解锁后可查看测试数据"}
              </Empty>
            ) : (
              <>
                {!comparing && selectedRecord && (
                  <>
                    <div className="db-record-heading">
                      <div>
                        <h2>{selectedRecord.sn || selectedRecord.fileName}</h2>
                        <p>
                          <span>{selectedRecord.station || "站别未记录"}</span>
                          <span>·</span>
                          <span>{timeText(selectedRecord.timeMs)}</span>
                          {selectedRecord.timeSource === "modified" && <span>（文件修改时间）</span>}
                        </p>
                      </div>
                      <Status record={selectedRecord} />
                    </div>
                    <div className="db-metadata-line">
                      <span>
                        测试温度 <b>{selectedRecord.temperature || "未记录"}</b>
                      </span>
                      <span>
                        测试模式{" "}
                        <b>
                          {({ automatic: "自动测试", manual: "手动测试" } as Record<string, string>)[
                            selectedRecord.testMode
                          ] ||
                            selectedRecord.testMode ||
                            "未记录"}
                        </b>
                      </span>
                      <span>
                        有效测试点 <b>{selectedRecord.validPointCount}</b> / {selectedRecord.pointCount}
                      </span>
                      {selectedRecord.stationId && (
                        <span>
                          工位编号 <b>{selectedRecord.stationId}</b>
                        </span>
                      )}
                    </div>
                  </>
                )}

                {comparing ? (
                  <>
                    {compareNotices.map((notice) => (
                      <div className="db-notice is-warning" key={notice} role="status">
                        {notice}
                      </div>
                    ))}
                    {compSummary && (
                      <div className="db-compare-summary">
                        <div className="db-compare-summary-item">
                          <span>基准 Pmax</span>
                          <strong>
                            {numberText(compSummary.baseMaxPowerW)} <small>W</small>
                          </strong>
                          <small>
                            {compSummary.baseMaxCurrentA != null
                              ? `@ ${numberText(compSummary.baseMaxCurrentA, 2)} A`
                              : "—"}
                          </small>
                        </div>
                        <div className="db-compare-summary-item">
                          <span>对比 Pmax</span>
                          <strong>
                            {numberText(compSummary.otherMaxPowerW)} <small>W</small>
                          </strong>
                          <small>
                            {compSummary.otherMaxCurrentA != null
                              ? `@ ${numberText(compSummary.otherMaxCurrentA, 2)} A`
                              : "—"}
                          </small>
                        </div>
                        <div
                          className="db-compare-summary-item"
                          data-accent={
                            compSummary.deltaMaxPowerW != null && compSummary.deltaMaxPowerW >= 0
                              ? "positive"
                              : "negative"
                          }
                        >
                          <span>ΔPmax (对比 − 基准)</span>
                          <strong>
                            {compSummary.deltaMaxPowerW != null && compSummary.deltaMaxPowerW > 0 ? "+" : ""}
                            {numberText(compSummary.deltaMaxPowerW)} <small>W</small>
                          </strong>
                          <small>
                            {compSummary.deltaMaxPowerPercent != null
                              ? `${compSummary.deltaMaxPowerPercent > 0 ? "+" : ""}${numberText(compSummary.deltaMaxPowerPercent, 2)}%`
                              : "—"}
                          </small>
                        </div>
                        <div className="db-compare-summary-item">
                          <span>共有有效点</span>
                          <strong>
                            {compSummary.sharedPointCount} <small>个点</small>
                          </strong>
                          <small>精确匹配</small>
                        </div>
                      </div>
                    )}
                    <div className="db-compare-records">
                      {comparisons.map((item, index) => (
                        <div className="db-compare-card" key={item.record.id}>
                          <div className="db-compare-card-head">
                            <span className={`db-series-label series-${index}`}>
                              {index === 0 ? "基准记录 (实线)" : "对比记录 (虚线)"}
                            </span>
                            <Status record={item.record} />
                          </div>
                          <strong title={item.record.sourcePath}>{item.record.sn || item.record.fileName}</strong>
                          <p>
                            {item.record.station || "站别未记录"} · 温度 {item.record.temperature || "未记录"}
                          </p>
                          <p>
                            {timeText(item.record.timeMs)}{" "}
                            {item.record.timeSource === "modified" ? "（文件修改时间）" : ""}
                          </p>
                          <div className="db-compare-card-foot">
                            <span className="text-xs text-muted-foreground">
                              共 {item.record.validPointCount} 个有效点
                            </span>
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={fileBusy}
                              onClick={() => void fileAction(item.record, false)}
                            >
                              <ExternalLink size={13} />
                              原文件
                            </Button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </>
                ) : (
                  tab === "overview" &&
                  detail && (
                    <>
                      {/* Metric Cards */}
                      <div className="db-metrics">
                        <Metric
                          label="最大光功率 Pmax"
                          value={numberText(summaryMetrics?.maxPowerPoint?.powerW)}
                          unit="W"
                          sub={
                            summaryMetrics?.maxPowerPoint
                              ? `@ ${numberText(summaryMetrics.maxPowerPoint.currentA, 2)} A`
                              : undefined
                          }
                          accent
                        />
                        <Metric
                          label="最高电光效率"
                          value={numberText(summaryMetrics?.maxEfficiencyPoint?.efficiencyPercent, 2)}
                          unit="%"
                          sub={
                            summaryMetrics?.maxEfficiencyPoint
                              ? `@ ${numberText(summaryMetrics.maxEfficiencyPoint.currentA, 2)} A`
                              : undefined
                          }
                        />
                        <Metric
                          label="峰值波长 (Pmax处)"
                          value={numberText(summaryMetrics?.primaryWavelengthNm, 2)}
                          unit="nm"
                          sub={summaryMetrics?.primaryWavelengthNm != null ? "原文件中心波长" : undefined}
                        />
                        <Metric
                          label="标注测试点"
                          value={numberText(currentPoint?.powerW)}
                          unit="W"
                          sub={
                            currentPoint
                              ? `${numberText(currentPoint.currentA, 2)} A · ${numberText(currentPoint.voltageV)} V${currentPoint.efficiencyPercent != null ? ` · ${numberText(currentPoint.efficiencyPercent, 2)} %` : ""}`
                              : undefined
                          }
                        />
                      </div>

                      {/* Chart Card */}
                      <div className="db-chart-card">
                        <div className="db-chart-title">
                          <div className="db-chart-title-left">
                            <BarChart3 size={15} className="text-primary" />
                            <strong>功率—电流曲线</strong>
                            <span>P-I 特性</span>
                          </div>
                          <label className="db-point-select">
                            <span>标注测试点</span>
                            <select
                              aria-label="选择测试点"
                              value={pointRow ?? ""}
                              onChange={(event) => setPointRow(Number(event.target.value))}
                            >
                              {detail.points.map((point) => (
                                <option key={point.rowId} value={point.rowId}>
                                  {numberText(point.currentA, 2)} A · {numberText(point.powerW)} W (行 {point.rowId})
                                </option>
                              ))}
                            </select>
                          </label>
                        </div>
                        <div className="db-chart">
                          {chartRows.length ? (
                            <CanvasLineChart
                              data={chartRows}
                              xKey="currentA"
                              lines={chartLines}
                              ariaLabel="历史功率电流曲线"
                              xLabel="电流 (A)"
                              yLabel="功率 (W)"
                              annotations={annotations}
                              preserveAllPoints
                            />
                          ) : (
                            <Empty>没有有效功率测试点</Empty>
                          )}
                        </div>
                      </div>
                    </>
                  )
                )}

                {comparing && (
                  <div className="db-chart-card">
                    <div className="db-chart-title">
                      <div className="db-chart-title-left">
                        <BarChart3 size={15} className="text-primary" />
                        <strong>功率—电流对比曲线</strong>
                      </div>
                      <div className="db-chart-legend">
                        {details.map((item, index) => (
                          <span key={item.record.id} className={`db-series-label series-${index}`}>
                            {recordLabel(item.record)}
                          </span>
                        ))}
                      </div>
                    </div>
                    <div className="db-chart">
                      {chartRows.length ? (
                        <CanvasLineChart
                          data={chartRows}
                          xKey="currentA"
                          lines={chartLines}
                          ariaLabel="两次测试功率对比曲线"
                          xLabel="电流 (A)"
                          yLabel="功率 (W)"
                          annotations={annotations}
                          preserveAllPoints
                        />
                      ) : (
                        <Empty>没有有效功率测试点</Empty>
                      )}
                    </div>
                  </div>
                )}

                {comparing && (
                  <div className="db-chart-card">
                    <div className="db-chart-title">
                      <div className="db-chart-title-left">
                        <strong>共有电流点差值</strong>
                        <span>对比 − 基准</span>
                      </div>
                    </div>
                    <div className="db-table-scroll">
                      <table className="db-data-table">
                        <thead>
                          <tr>
                            <th>电流 (A)</th>
                            <th>基准功率 (W)</th>
                            <th>对比功率 (W)</th>
                            <th>差值 (W)</th>
                            <th>变化 (%)</th>
                          </tr>
                        </thead>
                        <tbody>
                          {differences.map((row) => (
                            <tr key={row.currentA}>
                              <td>{numberText(row.currentA, 2)}</td>
                              {row.ambiguous ? (
                                <td colSpan={4}>重复电流点，无法唯一匹配</td>
                              ) : (
                                <>
                                  <td>{numberText(row.basePowerW)}</td>
                                  <td>{numberText(row.otherPowerW)}</td>
                                  <td
                                    style={{
                                      color:
                                        row.deltaW != null
                                          ? row.deltaW > 0
                                            ? "var(--app-success)"
                                            : row.deltaW < 0
                                              ? "var(--app-danger)"
                                              : undefined
                                          : undefined,
                                    }}
                                  >
                                    {row.deltaW != null && row.deltaW > 0 ? "+" : ""}
                                    {numberText(row.deltaW)}
                                  </td>
                                  <td
                                    style={{
                                      color:
                                        row.deltaPercent != null
                                          ? row.deltaPercent > 0
                                            ? "var(--app-success)"
                                            : row.deltaPercent < 0
                                              ? "var(--app-danger)"
                                              : undefined
                                          : undefined,
                                    }}
                                  >
                                    {row.deltaPercent != null && row.deltaPercent > 0 ? "+" : ""}
                                    {numberText(row.deltaPercent, 2)}
                                  </td>
                                </>
                              )}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {!differences.length && (
                        <p className="db-table-note">没有共有的有效电流点；曲线保留各自的测量数据。</p>
                      )}
                    </div>
                    <p className="db-table-note">仅比较共有电流点，不插值。基准功率为 0 时，百分比显示 —。</p>
                  </div>
                )}

                {!comparing && tab === "points" && detail && (
                  <div className="db-chart-card">
                    <div className="db-table-scroll">
                      <table className="db-data-table">
                        <thead>
                          <tr>
                            <th>源行</th>
                            {POINT_COLUMNS.map(([key, label, unit]) => (
                              <th
                                key={key}
                                title={
                                  detail.columnLabels[key]
                                    ? `原列名：${detail.columnLabels[key]}`
                                    : "文件未记录此列"
                                }
                              >
                                {label} ({unit})
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {detail.points.map((point) => (
                            <tr
                              key={point.rowId}
                              className="db-point-row"
                              data-selected={point.rowId === pointRow}
                              onClick={() => setPointRow(point.rowId)}
                            >
                              <td>
                                <button
                                  aria-label={`选择第 ${point.rowId} 行`}
                                  aria-pressed={point.rowId === pointRow}
                                  onClick={() => setPointRow(point.rowId)}
                                >
                                  {point.rowId}
                                </button>
                              </td>
                              {POINT_COLUMNS.map(([key]) => (
                                <td key={key}>{numberText(point[key])}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <p className="db-table-note">
                      — 表示未记录或无效值。峰值波长对应原文件“中心波长”列，质心波长单独显示。点击行可与曲线联动标注。
                    </p>
                  </div>
                )}

                {!comparing && tab === "info" && detail && (
                  <div className="db-info-card">
                    <dl className="db-info">
                      <dt>原始文件</dt>
                      <dd>{detail.record.sourcePath}</dd>
                      <dt>SN 来源</dt>
                      <dd>
                        {detail.record.snSource === "directory"
                          ? "由目录推定"
                          : detail.record.snSource === "metadata"
                            ? "文件元数据"
                            : "未记录"}
                      </dd>
                      <dt>时间来源</dt>
                      <dd>
                        {detail.record.timeSource === "session"
                          ? "测试开始时间（本地显示）"
                          : "文件修改时间"}
                      </dd>
                      {Object.entries(detail.metadata).map(([key, value]) => (
                        <div className="db-info-row" key={key}>
                          <dt>{key}</dt>
                          <dd>{value || "未记录"}</dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                )}

                {details.flatMap((item) =>
                  item.warnings.map((warning) => (
                    <div className="db-notice" key={`${item.record.id}-${warning}`}>
                      {comparing ? `${item.record.sn || item.record.fileName}：` : ""}
                      {warning}
                    </div>
                  )),
                )}
                {fileMessage && (
                  <div className="db-notice" role="status">
                    {fileMessage}
                  </div>
                )}
              </>
            )}
          </div>
        </article>
      </div>
    </section>
  )
})
