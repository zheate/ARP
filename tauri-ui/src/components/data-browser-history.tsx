import { CanvasLineChart } from "@/components/canvas-line-chart"
import { TestConditionDiff } from "@/components/data-browser-conditions"
import { Button } from "@/components/ui/button"
import { sendBackendRequest } from "@/lib/backend"
import {
DATA_METRICS,
MAX_COMPARISONS,
SERIES_COLORS,
commonMeasuredCurrents,
firstLatestChange,
historyMetricRows,
numberText,
sameSnHistory,
timeText,
validTestTime,
type DataDetail,
type DataRecord,
type HistoryMetric
} from "@/lib/data-browser"
import { useEffect, useMemo, useState } from "react"

export function useSnHistory(sn: string, active: boolean, refresh: number) {
  const cache = useMemo(() => new Map<string, DataRecord[]>(), [refresh])
  const [state, setState] = useState<{
    sn: string
    records: DataRecord[]
    busy: boolean
    error: string
  }>({ sn: "", records: [], busy: false, error: "" })
  useEffect(() => {
    let cancelled = false
    if (!active || !sn) return
    const cached = cache.get(sn)
    if (cached) {
      setState({ sn, records: cached, busy: false, error: "" })
      return
    }
    setState({ sn, records: [], busy: true, error: "" })
    void (async () => {
      try {
        // The existing endpoint searches substrings. Page summaries only, then require exact SN equality.
        const params = { source: "database", sn, pageSize: 200, refresh: false }
        const first = await sendBackendRequest("dataBrowser.list", { ...params, page: 1 })
        const records = [...first.records]
        for (let page = 2; page <= first.pageCount; page++) {
          if (cancelled) return
          records.push(
            ...(await sendBackendRequest("dataBrowser.list", { ...params, page }))
              .records,
          )
        }
        if (cancelled) return
        const exact = sameSnHistory(records, sn)
        cache.set(sn, exact)
        while (cache.size > 50) cache.delete(cache.keys().next().value!)
        setState({ sn, records: exact, busy: false, error: "" })
      } catch (error) {
        if (!cancelled)
          setState({
            sn,
            records: [],
            busy: false,
            error: error instanceof Error ? error.message : String(error),
          })
      }
    })()
    return () => {
      cancelled = true
    }
  }, [sn, active, cache])
  return state.sn === sn && active ? state : { sn, records: [], busy: false, error: "" }
}

export function ProductHistory({
  records,
  load,
  onBack,
  onView,
  chosen,
  onToggle,
  onCompare,
}: {
  records: DataRecord[]
  load: (record: DataRecord) => Promise<DataDetail>
  onBack: () => void
  onView: (record: DataRecord) => void
  chosen: string[]
  onToggle: (record: DataRecord) => void
  onCompare: () => void
}) {
  const sn = records[0]?.sn ?? ""
  const [loaded, setLoaded] = useState<DataDetail[]>([])
  const [busy, setBusy] = useState(true)
  const [errors, setErrors] = useState<string[]>([])
  const [retry, setRetry] = useState(0)
  const [metric, setMetric] = useState<HistoryMetric>("maxPower")
  const [selectedCurrent, setSelectedCurrent] = useState<number | null>(null)
  useEffect(() => {
    let cancelled = false
    setBusy(true)
    setErrors([])
    setLoaded([])
    void (async () => {
      const details: DataDetail[] = [],
        failures: string[] = []
      // Bound simultaneous reads. No spectra; share the existing versioned detail cache.
      for (let start = 0; start < records.length; start += 3) {
        if (cancelled) return
        const batch = records.slice(start, start + 3)
        const results = await Promise.allSettled(batch.map((record) => load(record)))
        results.forEach((result, index) => {
          if (result.status === "fulfilled") details.push(result.value)
          else
            failures.push(
              `${timeText(batch[index].timeMs)} (${batch[index].id})：${String(result.reason)}`,
            )
        })
      }
      if (!cancelled) {
        setLoaded(details)
        setErrors(failures)
        setBusy(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [records, load, retry])
  const complete = !busy && !errors.length && loaded.length === records.length
  const currents = useMemo(
    () => (complete ? commonMeasuredCurrents(loaded) : []),
    [complete, loaded],
  )
  const current =
    selectedCurrent !== null && currents.includes(selectedCurrent)
      ? selectedCurrent
      : (currents[currents.length - 1] ?? null)
  const rows = useMemo(
    () => historyMetricRows(loaded, sn, metric, current),
    [loaded, sn, metric, current],
  )
  const definition = DATA_METRICS.find((m) => m.key === metric) ?? DATA_METRICS[0]
  const change = useMemo(() => firstLatestChange(rows, metric), [rows, metric])
  const chartRows = useMemo(
    () =>
      rows
        .filter((row) => validTestTime(row.record.timeMs))
        .map((row) => ({ time: row.record.timeMs, value: row.value })),
    [rows],
  )
  const times = useMemo(() => [...new Set(chartRows.map((r) => r.time))], [chartRows])
  const ticks = times.filter(
    (_, i) => i === 0 || i === times.length - 1 || i === Math.floor(times.length / 2),
  )
  const values = chartRows.flatMap((r) => (r.value === null ? [] : [r.value]))
  const low = values.length ? Math.min(...values) : 0,
    high = values.length ? Math.max(...values) : 1
  const padding = Math.max((high - low) * 0.12, definition.unit === "nm" ? 0.02 : 0.2)
  const datedRecords = records.filter((r) => validTestTime(r.timeMs))
  const endpoints =
    complete && change.first && change.latest && change.first.record.id !== change.latest.record.id
      ? [
          loaded.find((d) => d.record.id === change.first!.record.id)!,
          loaded.find((d) => d.record.id === change.latest!.record.id)!,
        ]
      : []
  const summaryMetrics: HistoryMetric[] =
    metric === "maxPower"
      ? ["maxPower", ...(current === null ? [] : DATA_METRICS.map((m) => m.key))]
      : DATA_METRICS.map((m) => m.key)
  return (
    <section className="db-history" aria-label="产品履历">
      <div className="db-chart-title">
        <Button size="sm" variant="ghost" onClick={onBack}>
          ← 返回记录
        </Button>
        <strong>
          {sn} · 产品履历 · {records.length} 次测试
        </strong>
        <Button size="sm" variant="outline" disabled={chosen.length < 2} onClick={onCompare}>
          比较所选 {chosen.length} 条
        </Button>
      </div>
      <p className="db-help">
        测试数据库 · 精确相同 SN · 时间升序 · {timeText(datedRecords[0]?.timeMs)} 至{" "}
        {timeText(datedRecords[datedRecords.length - 1]?.timeMs)}。
        {records.length - datedRecords.length} 条时间未记录，保留在时间线末尾，不参与时间趋势及首次
        / 最新。
      </p>
      {busy && <p role="status">正在按需读取 {records.length} 次测试的实测点…</p>}
      {!!errors.length && (
        <div role="alert" className="db-notice">
          部分履历读取失败，共同电流、趋势和首次 / 最新暂不可计算。
          <Button size="sm" variant="outline" onClick={() => setRetry((n) => n + 1)}>
            重试履历
          </Button>
          <ul>
            {errors.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="db-chart-toolbar">
        <label>
          履历指标
          <select
            aria-label="履历指标"
            value={metric}
            onChange={(e) => setMetric(e.target.value as HistoryMetric)}
          >
            <option value="maxPower">最大实测功率</option>
            {DATA_METRICS.map((m) => (
              <option key={m.key} value={m.key}>
                {m.label} @ 共同电流 ({m.unit})
              </option>
            ))}
          </select>
        </label>
        <label>
          比较电流
          <select
            aria-label="履历比较电流"
            value={current ?? ""}
            disabled={!currents.length}
            onChange={(e) => setSelectedCurrent(Number(e.target.value))}
          >
            {!currents.length && <option value="">无共同实测电流</option>}
            {currents.map((value) => (
              <option key={value} value={value}>
                {numberText(value, 6)} A
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="db-help">
        共同电流按 6
        位小数匹配，不插值；重复电流点显示为不可比较。最大功率可能来自不同电流，请同时核对对应电流。FWHM
        为半高全宽，PIB 为带内功率占比，SMSR 为边模抑制比。
      </p>
      <div className="db-chart-title">
        <strong>
          {metric === "maxPower"
            ? "最大实测功率"
            : `${definition.label} @ ${numberText(current, 6)} A`}
          随测试时间变化
        </strong>
        <span>{definition.unit}</span>
      </div>
      <div className="db-chart db-history-chart">
        {complete && values.length && (metric === "maxPower" || current !== null) ? (
          <CanvasLineChart
            data={chartRows}
            xKey="time"
            xDomain={
              times.length > 1
                ? [times[0], times[times.length - 1]]
                : [times[0] - 1000, times[0] + 1000]
            }
            xTicks={ticks}
            xValueFormatter={(time) => timeText(time).slice(5, 16)}
            yDomain={[low - padding, high + padding]}
            lines={[
              {
                key: "value",
                label: metric === "maxPower" ? "最大实测功率" : definition.label,
                color: SERIES_COLORS[0],
                showPoints: true,
                pointSize: 6,
                interpolation: "linear",
              },
            ]}
            ariaLabel="产品履历时间趋势"
            yLabel={`${definition.label} (${definition.unit})`}
            preserveAllPoints
          />
        ) : (
          <div className="db-empty">{busy ? "读取履历中…" : "没有可绘制的有效时间与指标数据"}</div>
        )}
        <span className="db-axis-label">测试时间（详见时间线来源）</span>
      </div>
      {complete && (
        <>
          <div className="db-chart-title">
            <strong>首次 / 最新（计算变化）</strong>
            <span>最早 / 最晚有效时间，不跳过端点缺测</span>
          </div>
          <p className="db-help">
            首次 {timeText(change.first?.record.timeMs)} / 最新{" "}
            {timeText(change.latest?.record.timeMs)}；文件修改时间会在下方逐条注明。
          </p>
          <div className="db-table-scroll">
            <table className="db-data-table">
              <thead>
                <tr>
                  <th>指标</th>
                  <th>首次</th>
                  <th>最新</th>
                  <th>Δ 最新 − 首次</th>
                  <th>相对变化</th>
                </tr>
              </thead>
              <tbody>
                {summaryMetrics.map((key) => {
                  const m = DATA_METRICS.find((item) => item.key === key) ?? DATA_METRICS[0]
                  const c = firstLatestChange(historyMetricRows(loaded, sn, key, current), key)
                  return (
                    <tr key={key}>
                      <td>
                        {key === "maxPower"
                          ? "最大实测功率"
                          : `${m.label} @ ${numberText(current, 6)} A`}
                      </td>
                      <td>
                        {numberText(c.first?.value)} {m.unit}
                      </td>
                      <td>
                        {numberText(c.latest?.value)} {m.unit}
                      </td>
                      <td>
                        {numberText(c.delta)} {m.unit === "%" ? "百分点" : m.unit}
                      </td>
                      <td>
                        {c.relative === null
                          ? "不适用 / 不可计算"
                          : `${numberText(c.relative, 2)}%`}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
      <div className="db-chart-title">
        <strong>测试时间线</strong>
        <span>时间相同时按会话 ID、记录 ID 稳定排序</span>
      </div>
      <div className="db-table-scroll">
        <table className="db-data-table db-history-table">
          <thead>
            <tr>
              <th>比较 / 测试时间</th>
              <th>类型 / 测试站 / 温度</th>
              <th>点数</th>
              <th>最大功率 @ 电流</th>
              <th>最大测试电流</th>
              <th>当前履历指标</th>
            </tr>
          </thead>
          <tbody>
            {records.map((record) => {
              const row = rows.find((r) => r.record.id === record.id)
              return (
                <tr key={record.id}>
                  <td>
                    <label>
                      <input
                        type="checkbox"
                        aria-label={`履历加入对比 ${record.id}`}
                        checked={chosen.includes(record.id)}
                        disabled={
                          record.readStatus === "error" ||
                          !record.pointCount ||
                          (!chosen.includes(record.id) && chosen.length >= MAX_COMPARISONS)
                        }
                        onChange={() => onToggle(record)}
                      />
                      <button onClick={() => onView(record)}>{timeText(record.timeMs)}</button>
                    </label>
                    <small>
                      {record.timeSource === "modified" ? "文件修改时间" : "会话时间"} ·{" "}
                      {record.sessionId || record.id}
                    </small>
                  </td>
                  <td>
                    {record.testMode === "automatic"
                      ? "自动测试"
                      : record.testMode === "manual"
                        ? "手动测试"
                        : record.testMode || "类型未记录"}
                    <small>
                      {record.station || "测试站未记录"} · {record.temperature || "温度未记录"}
                    </small>
                  </td>
                  <td>{record.pointCount}</td>
                  <td>
                    {numberText(row?.maxPowerPoint?.powerW)} W @{" "}
                    {numberText(row?.maxPowerPoint?.currentA)} A
                  </td>
                  <td>{numberText(row?.maximumCurrentA)} A</td>
                  <td>
                    {numberText(row?.value)} {definition.unit}
                    <small>{row?.status ?? (busy ? "读取中" : "读取失败")}</small>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {endpoints.length === 2 && (
        <>
          <p className="db-help">
            首次 / 最新测试条件变化，基准为首次。选择 2–5 条记录可在比较模式中逐列核对。
          </p>
          <TestConditionDiff details={endpoints} baselineRecordId={endpoints[0].record.id} />
        </>
      )}
    </section>
  )
}
