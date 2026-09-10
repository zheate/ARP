import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { ArrowLeftRight, BarChart3, ChevronLeft, ChevronRight, ExternalLink, FileSpreadsheet, FolderOpen, Maximize2, Minimize2, RefreshCw, Search, X } from "lucide-react"
import { open as openDialog } from "@tauri-apps/plugin-dialog"
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener"
import { Button } from "@/components/ui/button"
import { CanvasLineChart, type CanvasChartLine } from "@/components/canvas-line-chart"
import { sendBackendRequest } from "@/lib/backend"
import { openResultFile } from "@/lib/result-file"
import { comparisonRows, dateBounds, flowLabel, numberText, powerChartRows, recordLabel, requestGuard, type DataDetail, type DataList, type DataRecord } from "@/lib/data-browser"

const DIRECTORY_KEY = "power-test.data-browser.directory.v1"
const EMPTY_RECORDS: DataRecord[] = []
const POINT_COLUMNS = [
  ["currentA", "电流", "A"], ["voltageV", "电压", "V"], ["powerW", "功率", "W"],
  ["efficiencyPercent", "效率", "%"], ["peakWavelengthNm", "峰值波长", "nm"],
  ["centroidNm", "质心波长", "nm"], ["fwhmNm", "FWHM", "nm"], ["pibPercent", "PIB", "%"], ["smsrDb", "SMSR", "dB"],
] as const
function storedDirectory() {
  try { return localStorage.getItem(DIRECTORY_KEY) ?? "" } catch { return "" }
}
function message(error: unknown) { return error instanceof Error ? error.message : String(error) }
function Status({ record }: { record: DataRecord }) {
  return <span className="db-status" data-kind={record.readStatus === "error" ? "error" : record.flowStatus === "completed" ? "complete" : "other"}>{record.readStatus === "error" ? "读取失败" : flowLabel(record.flowStatus)}</span>
}
function Empty({ children }: { children: ReactNode }) {
  return <div className="db-empty"><FileSpreadsheet size={28} strokeWidth={1.3} /><p>{children}</p></div>
}
function Metric({ label, value, unit, accent = false }: { label: string; value: string; unit?: string; accent?: boolean }) {
  return <div className="db-metric" data-accent-value={accent}><span>{label}</span><strong>{value} <small>{unit}</small></strong></div>
}

export const DataBrowserPage = memo(function DataBrowserPage({ visible, defaultDirectory, enabled }: { visible: boolean; defaultDirectory: string; enabled: boolean }) {
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
  const records = listing?.records ?? EMPTY_RECORDS

  useEffect(() => {
    if (visible && !root && defaultDirectory) { setRoot(defaultDirectory); setDirectoryDraft(defaultDirectory) }
  }, [visible, root, defaultDirectory])
  useEffect(() => {
    // Re-entering refreshes the directory while the mounted component retains UI state.
    if (visible) setRefresh((value) => value + 1)
  }, [visible])

  useEffect(() => {
    const guard = listGuard.current
    const ticket = guard.next()
    if (!visible || !root || !enabled) { setListBusy(false); return }
    setListBusy(true); setListError("")
    const timer = window.setTimeout(() => {
      let dates: ReturnType<typeof dateBounds>
      try { dates = dateBounds(dateMode, from, to) }
      catch (error) { setListError(message(error)); setListBusy(false); return }
      const stamp = `${root}\n${refresh}`
      void sendBackendRequest<DataList>("dataBrowser.list", {
        rootPath: root, refresh: scanStamp.current !== stamp, sn, station, status, ...dates, page, pageSize: 50,
      }).then((result) => {
        if (!guard.current(ticket)) return
        scanStamp.current = stamp
        setListing(result); setPage(result.page)
        setSelected((previous) => result.records.find((record) => record.id === previous?.id)
          ?? result.records.find((record) => record.readStatus === "readable") ?? result.records[0] ?? null)
      }).catch((error) => {
        if (!guard.current(ticket)) return
        setListError(message(error)); setListing(null); setSelected(null); setDetail(null)
      }).finally(() => { if (guard.current(ticket)) setListBusy(false) })
    }, 250)
    return () => { clearTimeout(timer); guard.invalidate() }
  }, [visible, enabled, root, sn, station, status, dateMode, from, to, page, refresh])

  useEffect(() => {
    const guard = detailGuard.current
    const ticket = guard.next()
    setDetail(null); setDetailError(""); setFileMessage(""); setDetailBusy(false)
    if (!visible || !enabled || !selected || comparing) return
    if (selected.readStatus === "error") { setDetailError(selected.error); return }
    setDetailBusy(true)
    void sendBackendRequest<DataDetail>("dataBrowser.inspect", { path: selected.sourcePath, version: selected.version })
      .then((result) => {
        if (!guard.current(ticket)) return
        setDetail(result)
        setPointRow((previous) => result.points.some((point) => point.rowId === previous) ? previous : result.points[0]?.rowId ?? null)
      }).catch((error) => { if (guard.current(ticket)) setDetailError(message(error)) })
      .finally(() => { if (guard.current(ticket)) setDetailBusy(false) })
    return () => guard.invalidate()
  }, [visible, enabled, selected?.id, selected?.version, refresh, comparing])

  useEffect(() => {
    const guard = compareGuard.current
    const ticket = guard.next()
    setComparisons([]); setCompareError(""); setCompareBusy(false)
    if (!visible || !enabled || !comparing || chosen.length !== 2) return
    setCompareBusy(true)
    void Promise.all(chosen.map((record) => sendBackendRequest<DataDetail>("dataBrowser.inspect", { path: record.sourcePath })))
      .then((result) => { if (guard.current(ticket)) setComparisons(result) })
      .catch((error) => { if (guard.current(ticket)) setCompareError(message(error)) })
      .finally(() => { if (guard.current(ticket)) setCompareBusy(false) })
    return () => guard.invalidate()
  }, [visible, enabled, comparing, chosen, refresh])

  const useDirectory = (value: string) => {
    const next = value.trim()
    if (!next) { setListError("请输入或选择数据目录"); return }
    listGuard.current.invalidate(); detailGuard.current.invalidate(); compareGuard.current.invalidate()
    setRoot(next); setDirectoryDraft(next); setPage(1); setStation(""); setStatus("")
    setListing(null); setSelected(null); setDetail(null); setChosen([]); setComparing(false); setComparisons([])
    setRefresh((value) => value + 1)
    try { localStorage.setItem(DIRECTORY_KEY, next) } catch { /* Session state is still usable. */ }
  }
  const chooseDirectory = async () => {
    try {
      const path = await openDialog({ directory: true, multiple: false, title: "选择测试数据目录", defaultPath: root || undefined })
      if (typeof path === "string") useDirectory(path)
    } catch (error) { setListError(message(error)) }
  }
  const selectRecord = (record: DataRecord) => {
    detailGuard.current.invalidate(); setSelected(record); setDetail(null); setPointRow(null); setComparing(false); setDetailError("")
  }
  const toggleChosen = (record: DataRecord) => {
    setComparing(false)
    setChosen((previous) => previous.some((item) => item.id === record.id)
      ? previous.filter((item) => item.id !== record.id) : previous.length < 2 ? [...previous, record] : previous)
  }
  const fileAction = async (record: DataRecord, locate: boolean) => {
    setFileBusy(true); setFileMessage("")
    try {
      if (locate) { await revealItemInDir(record.sourcePath); setFileMessage("已在文件夹中定位") }
      else {
        const outcome = await openResultFile(record.sourcePath, openPath, revealItemInDir)
        setFileMessage(outcome.status === "revealed" ? `无法直接打开，已定位文件：${outcome.openError}` : "已打开 Excel 文件")
      }
    } catch (error) { setFileMessage(message(error)) }
    finally { setFileBusy(false) }
  }
  const details = useMemo(() => comparing ? comparisons : detail ? [detail] : [], [comparing, comparisons, detail])
  const chartRows = useMemo(() => powerChartRows(details), [details])
  const differences = useMemo(() => comparisons.length === 2 ? comparisonRows(comparisons[0].points, comparisons[1].points) : [], [comparisons])
  const currentPoint = detail?.points.find((point) => point.rowId === pointRow)
  const chartLines = useMemo<CanvasChartLine[]>(() => details.map((item, index) => ({
    key: index === 0 ? "base" : "other", label: comparing ? recordLabel(item.record) : "功率",
    color: index === 0 ? "var(--chart-1)" : "#aa86d9", showPoints: true, connectNulls: true,
    pointStyle: "hollow", pointSize: 6, lineWidth: 2, dash: index === 1 ? [6, 4] : undefined, hideLegend: true,
  })), [details, comparing])
  const selectedRecord = detail?.record ?? selected
  const busy = comparing ? compareBusy : detailBusy
  const error = comparing ? compareError : detailError
  const annotations = useMemo(() => !comparing && currentPoint?.powerW != null ? [{ x: currentPoint.currentA, y: currentPoint.powerW, label: `${numberText(currentPoint.currentA, 2)} A`, color: "var(--chart-1)" }] : [], [comparing, currentPoint])
  const updateFilter = (change: () => void) => { listGuard.current.invalidate(); change(); setPage(1) }

  return <section className="data-browser" aria-label="数据浏览">
    <header className="db-heading"><div><h1>数据浏览</h1><p>查找测试记录，复核每一次测量。</p></div><span className="db-readonly">原始数据只读</span></header>
    <div className="db-filters">
      <form className="db-directory" onSubmit={(event) => { event.preventDefault(); useDirectory(directoryDraft) }}>
        <FolderOpen size={17} /><label className="sr-only" htmlFor="db-directory">数据目录</label>
        <input id="db-directory" value={directoryDraft} onChange={(event) => setDirectoryDraft(event.target.value)} placeholder="输入测试数据目录，按 Enter 读取" disabled={!enabled} />
        <Button type="button" variant="outline" size="sm" disabled={!enabled || listBusy} onClick={() => void chooseDirectory()}>选择目录</Button>
        <Button type="submit" variant="outline" size="sm" disabled={!enabled || listBusy}>读取</Button>
        <Button type="button" variant="outline" size="sm" disabled={!enabled || !root || listBusy} onClick={() => setRefresh((value) => value + 1)}><RefreshCw size={14} className={listBusy ? "animate-spin" : ""} />刷新</Button>
      </form>
      <div className="db-filter-row">
        <label className="db-search"><Search size={16} /><input aria-label="搜索 SN" value={sn} onChange={(event) => updateFilter(() => setSn(event.target.value))} placeholder="搜索产品 SN…" /></label>
        <label>时间<select aria-label="日期范围" value={dateMode} onChange={(event) => updateFilter(() => setDateMode(event.target.value))}><option value="all">全部时间</option><option value="today">今天</option><option value="week">最近 7 天</option><option value="custom">自定义</option></select></label>
        {dateMode === "custom" && <><input aria-label="开始日期" type="date" value={from} onChange={(event) => updateFilter(() => setFrom(event.target.value))} /><span>至</span><input aria-label="结束日期" type="date" value={to} onChange={(event) => updateFilter(() => setTo(event.target.value))} /></>}
        <label>站别<select aria-label="测试站别筛选" value={station} onChange={(event) => updateFilter(() => setStation(event.target.value))}><option value="">全部站别</option>{listing?.stations.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>状态<select aria-label="记录状态筛选" value={status} onChange={(event) => updateFilter(() => setStatus(event.target.value))}><option value="">全部状态</option>{listing?.statuses.map((item) => <option key={item} value={item}>{flowLabel(item)}</option>)}<option value="read_error">读取失败</option></select></label>
      </div>
    </div>
    {listError && <div className="db-notice is-error" role="alert">{listError}</div>}
    {listing?.warnings.map((warning) => <div className="db-notice" key={warning}>{warning}</div>)}
    {chosen.length > 0 && <div className="db-selection"><strong>已选 {chosen.length} / 2</strong><div className="db-selected-items">{chosen.map((record, index) => <span key={record.id} title={recordLabel(record)}>{index === 0 ? "基准" : "对比"} · {record.sn || record.fileName}<button aria-label={`移除${recordLabel(record)}`} onClick={() => toggleChosen(record)}><X size={13} /></button></span>)}</div><Button size="sm" disabled={chosen.length !== 2 || compareBusy} onClick={() => { setComparing(true); setTab("overview") }}><ArrowLeftRight size={15} />对比曲线</Button></div>}
    <div className={`db-workspace ${expanded ? "is-expanded" : ""}`}>
      <aside className="db-list-panel" aria-label="测试记录列表" hidden={expanded}>
        <div className="db-panel-heading"><strong>测试记录 <span>{listing?.total ?? 0}</span></strong><small aria-live="polite">{listBusy ? "正在读取…" : listing ? `${listing.errorCount} 个文件读取失败` : "选择目录开始"}</small></div>
        <div className="db-records" aria-busy={listBusy}>
          {!records.length ? <Empty>{listBusy ? "正在索引测试文件…" : root ? "没有符合条件的测试记录" : "选择保存测试 Excel 的目录"}</Empty> : <table><thead><tr><th aria-label="选择对比" /><th>产品 / 测试站别</th><th>时间 / 状态</th></tr></thead><tbody>{records.map((record) => <tr key={record.id} data-selected={record.id === selected?.id && !comparing}>
            <td><input type="checkbox" aria-label={`选择对比 ${recordLabel(record)}`} checked={chosen.some((item) => item.id === record.id)} disabled={record.validPointCount === 0 || (chosen.length === 2 && !chosen.some((item) => item.id === record.id))} onChange={() => toggleChosen(record)} /></td>
            <td><button className="db-record-button" onClick={() => selectRecord(record)} title={record.sourcePath}><strong>{record.sn || record.fileName}</strong><small>{record.station || "站别未记录"}{record.snSource === "directory" ? " · SN 由目录推定" : ""}</small></button></td>
            <td><button className="db-record-button" onClick={() => selectRecord(record)}><time>{new Date(record.timeMs).toLocaleString(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", year: "numeric", hour12: false })}</time>{record.timeSource === "modified" && <small>文件修改时间</small>}<Status record={record} /></button></td>
          </tr>)}</tbody></table>}
        </div>
        <footer className="db-pagination"><span>第 {listing?.page ?? 1} / {listing?.pageCount ?? 1} 页</span><Button aria-label="上一页" variant="ghost" size="sm" disabled={listBusy || (listing?.page ?? 1) <= 1} onClick={() => setPage((value) => value - 1)}><ChevronLeft size={16} /></Button><Button aria-label="下一页" variant="ghost" size="sm" disabled={listBusy || (listing?.page ?? 1) >= (listing?.pageCount ?? 1)} onClick={() => setPage((value) => value + 1)}><ChevronRight size={16} /></Button></footer>
      </aside>
      <article className="db-detail-panel" aria-label="测试详情">
        <div className="db-panel-heading"><strong>{comparing ? "两次测试对比" : "记录详情"}</strong><div className="db-actions">{comparing && <Button size="sm" variant="ghost" onClick={() => setComparing(false)}>返回单次记录</Button>}<Button size="sm" variant="ghost" aria-label={expanded ? "收起详情" : "展开详情"} onClick={() => setExpanded((value) => !value)}>{expanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}{expanded ? "收起" : "展开"}</Button></div></div>
        <div className="db-detail-scroll" aria-busy={busy}>
          {busy ? <Empty>正在读取{comparing ? "对比" : "测试"}数据…</Empty> : error ? <div className="db-notice is-error" role="alert">{error}<Button size="sm" variant="outline" onClick={() => setRefresh((value) => value + 1)}>刷新重试</Button></div> : !details.length ? <Empty>选择左侧记录，查看测试数据</Empty> : <>
            {comparing ? <div className="db-compare-records">{comparisons.map((item, index) => <div key={item.record.id}><span className={`db-series-label series-${index}`}>{index === 0 ? "基准记录" : "对比记录"}</span><strong title={item.record.sourcePath}>{item.record.sn || item.record.fileName}</strong><p>{item.record.station || "站别未记录"} · {item.record.temperature || "温度未记录"}</p><p>{new Date(item.record.timeMs).toLocaleString()} {item.record.timeSource === "modified" ? "（文件修改）" : ""}</p><Status record={item.record} /><Button size="sm" variant="ghost" disabled={fileBusy} onClick={() => void fileAction(item.record, false)}><ExternalLink size={13} />原文件</Button></div>)}<Button variant="outline" size="sm" onClick={() => setChosen(([a, b]) => [b, a])}><ArrowLeftRight size={14} />交换基准</Button></div> : selectedRecord && <>
              <div className="db-record-heading"><div><h2>{selectedRecord.sn || selectedRecord.fileName}</h2><p>{selectedRecord.station || "站别未记录"} · {new Date(selectedRecord.timeMs).toLocaleString()} {selectedRecord.timeSource === "modified" ? "（文件修改时间）" : ""}</p></div><Status record={selectedRecord} /></div>
              <div className="db-metadata-line"><span>测试温度 <b>{selectedRecord.temperature || "未记录"}</b></span><span>测试模式 <b>{({ automatic: "自动测试", manual: "手动测试" } as Record<string, string>)[selectedRecord.testMode] || selectedRecord.testMode || "未记录"}</b></span><span>有效测试点 <b>{selectedRecord.validPointCount}</b></span>{selectedRecord.stationId && <span>工位 <b>{selectedRecord.stationId}</b></span>}</div>
              <div className="db-file-actions"><Button size="sm" variant="outline" disabled={fileBusy} onClick={() => void fileAction(selectedRecord, false)}><FileSpreadsheet size={14} />打开 Excel</Button><Button size="sm" variant="ghost" disabled={fileBusy} onClick={() => void fileAction(selectedRecord, true)}><FolderOpen size={14} />打开所在文件夹</Button></div>
            </>}
            {details.flatMap((item) => item.warnings.map((warning) => <div className="db-notice" key={`${item.record.id}-${warning}`}>{comparing ? `${item.record.sn || item.record.fileName}：` : ""}{warning}</div>))}
            {fileMessage && <div className="db-notice" role="status">{fileMessage}</div>}
            {!comparing && <div className="db-tabs" role="tablist" aria-label="详情视图">{[["overview", "曲线概览"], ["points", "测试点"], ["info", "测试信息"]].map(([id, label]) => <button type="button" role="tab" aria-selected={tab === id} key={id} onClick={() => setTab(id)}>{label}</button>)}</div>}
            {!comparing && tab !== "info" && detail && <div className="db-point-controls"><label>选择电流点<select aria-label="选择电流点" value={pointRow ?? ""} onChange={(event) => setPointRow(Number(event.target.value))}>{detail.points.map((point) => <option key={point.rowId} value={point.rowId}>{numberText(point.currentA, 2)} A · 第 {point.rowId} 行</option>)}</select></label><small>与测试点表和图中标记联动</small></div>}
            {(comparing || tab === "overview") && <>
              {!comparing && <div className="db-metrics"><Metric label={`${numberText(currentPoint?.currentA, 2)} A 时功率`} value={numberText(currentPoint?.powerW)} unit="W" accent /><Metric label="工作电压" value={numberText(currentPoint?.voltageV)} unit="V" /><Metric label="电光效率" value={numberText(currentPoint?.efficiencyPercent, 2)} unit="%" /></div>}
              <div className="db-chart-title"><BarChart3 size={16} /><strong>功率—电流曲线</strong><span>原始测试点</span></div>
              {comparing && <div className="db-chart-legend">{details.map((item, index) => <span key={item.record.id} className={`db-series-label series-${index}`}>{recordLabel(item.record)}</span>)}</div>}
              <div className="db-chart">{chartRows.length ? <CanvasLineChart data={chartRows} xKey="currentA" lines={chartLines} ariaLabel={comparing ? "两次测试功率对比曲线" : "历史功率电流曲线"} xLabel="电流 (A)" yLabel="功率 (W)" annotations={annotations} preserveAllPoints /> : <Empty>没有有效功率测试点</Empty>}</div>
            </>}
            {comparing && <><div className="db-chart-title"><strong>共有电流点差值</strong><span>对比 − 基准</span></div><div className="db-table-scroll"><table className="db-data-table"><thead><tr><th>电流 (A)</th><th>基准功率 (W)</th><th>对比功率 (W)</th><th>差值 (W)</th><th>变化 (%)</th></tr></thead><tbody>{differences.map((row) => <tr key={row.currentA}><td>{numberText(row.currentA, 2)}</td>{row.ambiguous ? <td colSpan={4}>重复电流点，无法唯一匹配</td> : <><td>{numberText(row.basePowerW)}</td><td>{numberText(row.otherPowerW)}</td><td>{numberText(row.deltaW)}</td><td>{numberText(row.deltaPercent, 2)}</td></>}</tr>)}</tbody></table>{!differences.length && <p className="db-table-note">没有共有的有效电流点；曲线保留各自的测量数据。</p>}</div><p className="db-table-note">仅比较共有电流点，不插值。基准功率为 0 时，百分比显示 --。</p></>}
            {!comparing && tab === "points" && detail && <div className="db-table-scroll"><table className="db-data-table"><thead><tr><th>源行</th>{POINT_COLUMNS.map(([key, label, unit]) => <th key={key} title={detail.columnLabels[key] ? `原列名：${detail.columnLabels[key]}` : "文件未记录此列"}>{label} ({unit})</th>)}</tr></thead><tbody>{detail.points.map((point) => <tr key={point.rowId} data-selected={point.rowId === pointRow}><td><button aria-label={`选择第 ${point.rowId} 行`} onClick={() => setPointRow(point.rowId)}>{point.rowId}</button></td>{POINT_COLUMNS.map(([key]) => <td key={key}><button onClick={() => setPointRow(point.rowId)}>{numberText(point[key])}</button></td>)}</tr>)}</tbody></table><p className="db-table-note">-- 表示未记录或无效值。峰值波长对应原文件“中心波长”列，质心波长单独显示。</p></div>}
            {!comparing && tab === "info" && detail && <dl className="db-info"><dt>原始文件</dt><dd>{detail.record.sourcePath}</dd><dt>SN 来源</dt><dd>{detail.record.snSource === "directory" ? "由目录推定" : detail.record.snSource === "metadata" ? "文件元数据" : "未记录"}</dd><dt>时间来源</dt><dd>{detail.record.timeSource === "session" ? "测试开始时间（本地显示）" : "文件修改时间"}</dd>{Object.entries(detail.metadata).map(([key, value]) => <div className="db-info-row" key={key}><dt>{key}</dt><dd>{value || "未记录"}</dd></div>)}</dl>}
          </>}
        </div>
      </article>
    </div>
  </section>
})
