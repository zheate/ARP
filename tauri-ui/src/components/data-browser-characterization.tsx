import { useMemo, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  DATA_METRICS,
  cursorDifference,
  extractSummaryMetrics,
  finiteNumber,
  fitMetricRange,
  numberText,
  slopeExtent,
  type DataDetail,
  type DataMetricKey,
} from "@/lib/data-browser"

type Cursor = "A" | "B"
type CursorState = {
  key: string
  enabled: boolean
  active: Cursor
  A: number | null
  B: number | null
  fitted: boolean
}

export function useCharacterization(detail: DataDetail | null, metric: DataMetricKey) {
  const key = `${detail?.record.id}:${detail?.record.version}`
  const empty: CursorState = { key, enabled: false, active: "A", A: null, B: null, fitted: false }
  const [stored, setStored] = useState<CursorState>(empty)
  const state = stored.key === key ? stored : empty
  const update = (patch: Partial<CursorState>) =>
    setStored((previous) => ({ ...(previous.key === key ? previous : empty), ...patch }))
  const points = useMemo(
    () => detail?.points.filter((p) => finiteNumber(p.currentA)) ?? [],
    [detail],
  )
  const a = points.find((p) => p.rowId === state.A),
    b = points.find((p) => p.rowId === state.B)
  const result = useMemo(
    () =>
      state.enabled && state.fitted
        ? fitMetricRange(points, metric, a?.currentA ?? null, b?.currentA ?? null)
        : null,
    [state.enabled, state.fitted, points, metric, a?.currentA, b?.currentA],
  )
  function select(cursor: Cursor, rowId: number) {
    update({
      [cursor]: rowId,
      fitted: false,
      active: cursor === "A" && state.B === null ? "B" : cursor,
    })
  }
  function selectChartPoint(rowId: number) {
    select(state.A === null ? "A" : state.B === null ? "B" : state.active, rowId)
  }
  const selectedPoints = state.enabled
    ? (
        [
          ["A", a],
          ["B", b],
        ] as const
      ).flatMap(([label, point]) =>
        point && finiteNumber(point[metric])
          ? [{ label, x: point.currentA, y: point[metric] }]
          : [],
      )
    : undefined
  const chartRows = useMemo(
    () =>
      points
        .filter((p) => finiteNumber(p[metric]))
        .map((p) => ({
          currentA: p.currentA,
          series0: p[metric],
          rowId: p.rowId,
          fit:
            result?.fit && p.currentA >= result.fit.xMin && p.currentA <= result.fit.xMax
              ? result.fit.slope * p.currentA + result.fit.intercept
              : null,
        }))
        .sort((a, b) => a.currentA - b.currentA),
    [points, metric, result],
  )
  return {
    state,
    update,
    points,
    a,
    b,
    result,
    select,
    selectChartPoint,
    selectedPoints,
    chartRows,
  }
}

export function Characterization({
  detail,
  metricKey,
  analysis,
}: {
  detail: DataDetail
  metricKey: DataMetricKey
  analysis: ReturnType<typeof useCharacterization>
}) {
  const [open, setOpen] = useState(true)
  const metric = DATA_METRICS.find((m) => m.key === metricKey)!
  const { state, points, a, b, result, select, update } = analysis
  const delta = cursorDifference(a, b, metricKey)
  const maximum = useMemo(() => extractSummaryMetrics(detail.points).maxPowerPoint, [detail])
  const local = useMemo(
    () => (metricKey === "powerW" ? slopeExtent(detail.points, metricKey) : null),
    [detail, metricKey],
  )
  const fit = result?.fit
  const slopeUnit = `${metric.unit}/A`
  return (
    <section className="db-characterization" aria-label="特征分析">
      <div className="db-chart-title">
        <button className="db-section-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
          特征分析 {open ? "▾" : "▸"}
        </button>
        <span>基于原始实测点 · 派生结果标记为计算值</span>
      </div>
      {open && (
        <>
          {metricKey === "powerW" && (
            <dl className="db-analysis-readouts">
              <div>
                <dt>最大实测功率 Pmax</dt>
                <dd>
                  {numberText(maximum?.powerW)} W @ {numberText(maximum?.currentA)} A
                </dd>
              </div>
              <div>
                <dt>局部 dP/dI 最小 / 最大（计算）</dt>
                <dd>
                  {numberText(local?.min, 5)} / {numberText(local?.max, 5)} W/A
                </dd>
              </div>
            </dl>
          )}
          <div className="db-chart-toolbar">
            <label>
              <input
                type="checkbox"
                checked={state.enabled}
                onChange={(e) => update({ enabled: e.target.checked })}
              />
              双游标
            </label>
            {state.enabled && (
              <>
                <label>
                  点击移动
                  <select
                    aria-label="当前激活游标"
                    value={state.active}
                    onChange={(e) => update({ active: e.target.value as Cursor })}
                  >
                    <option value="A">游标 A</option>
                    <option value="B">游标 B</option>
                  </select>
                </label>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!a || !b}
                  onClick={() => update({ fitted: true })}
                >
                  拟合 A–B
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => update({ A: null, B: null, fitted: false, active: "A" })}
                >
                  清除游标
                </Button>
              </>
            )}
          </div>
          {state.enabled && (
            <>
              <div className="db-cursors">
                {(["A", "B"] as const).map((cursor) => {
                  const point = cursor === "A" ? a : b
                  const index = points.findIndex((p) => p.rowId === state[cursor])
                  return (
                    <div className="db-cursor-row" key={cursor}>
                      <label>
                        游标 {cursor}
                        <select
                          aria-label={`游标 ${cursor} 测试点`}
                          value={point?.rowId ?? ""}
                          onFocus={() => update({ active: cursor })}
                          onChange={(e) => select(cursor, Number(e.target.value))}
                        >
                          <option value="" disabled>
                            选择实测点
                          </option>
                          {points.map((p, i) => (
                            <option key={p.rowId} value={p.rowId}>
                              #{i + 1} · {numberText(p.currentA, 6)} A
                            </option>
                          ))}
                        </select>
                      </label>
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={`游标 ${cursor} 上一点`}
                        disabled={index <= 0}
                        onClick={() => select(cursor, points[index - 1].rowId)}
                      >
                        上一点
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={`游标 ${cursor} 下一点`}
                        disabled={index >= points.length - 1 || !points.length}
                        onClick={() => select(cursor, points[index + 1].rowId)}
                      >
                        下一点
                      </Button>
                      <output aria-label={`游标 ${cursor} 读数`}>
                        {point
                          ? `${cursor} · #${index + 1} · ${numberText(point.currentA, 6)} A · ${finiteNumber(point[metricKey]) ? `${numberText(point[metricKey])} ${metric.unit}` : `${metric.label}缺测`}`
                          : `${cursor} 未选择`}
                      </output>
                    </div>
                  )
                })}
              </div>
              <dl className="db-analysis-readouts" aria-label="游标差值">
                <div>
                  <dt>ΔI（B − A）</dt>
                  <dd>{numberText(delta.deltaI, 6)} A</dd>
                </div>
                <div>
                  <dt>Δ{metric.label}（计算）</dt>
                  <dd>
                    {numberText(delta.deltaY)} {metric.unit === "%" ? "百分点" : metric.unit}
                  </dd>
                </div>
                <div>
                  <dt>ΔY / ΔI（计算）</dt>
                  <dd>
                    {numberText(delta.slope, 5)} {slopeUnit}
                  </dd>
                </div>
              </dl>
              {result && !fit && (
                <p role="status" className="db-help">
                  无法拟合：{result.reason}
                </p>
              )}
              {fit && (
                <div className="db-fit-result" aria-label="区域拟合结果" role="status">
                  <p className="db-help">
                    所选电流区间：{numberText(Math.min(a!.currentA, b!.currentA), 6)}–
                    {numberText(Math.max(a!.currentA, b!.currentA), 6)}{" "}
                    A。下方范围为参与拟合的有效实测点范围。
                  </p>
                  <dl className="db-analysis-readouts">
                    <div>
                      <dt>
                        {metricKey === "powerW"
                          ? "区间斜率效率（计算）"
                          : `${metric.label}区间斜率（计算）`}
                      </dt>
                      <dd>
                        {numberText(fit.slope, 5)} {slopeUnit}
                      </dd>
                    </div>
                    <div>
                      <dt>截距（计算）</dt>
                      <dd>
                        {numberText(fit.intercept, 5)} {metric.unit}
                      </dd>
                    </div>
                    <div>
                      <dt>线性拟合 R²</dt>
                      <dd>
                        {fit.rSquared === null ? "未定义（指标恒定）" : numberText(fit.rSquared, 4)}
                      </dd>
                    </div>
                    <div>
                      <dt>拟合范围 / 点数</dt>
                      <dd>
                        {numberText(fit.xMin, 6)}–{numberText(fit.xMax, 6)} A · {fit.pointCount}{" "}
                        个实测点
                      </dd>
                    </div>
                    {metricKey === "voltageV" && (
                      <div>
                        <dt>区域微分电阻估计</dt>
                        <dd>
                          Rd ≈{" "}
                          {numberText(Math.abs(fit.slope) < 1 ? fit.slope * 1000 : fit.slope, 3)}{" "}
                          {Math.abs(fit.slope) < 1 ? "mΩ" : "Ω"}
                        </dd>
                      </div>
                    )}
                  </dl>
                  <p className="db-help">
                    线性模型（计算）：y = {numberText(fit.slope, 5)} I{" "}
                    {fit.intercept < 0 ? "−" : "+"} {numberText(Math.abs(fit.intercept), 5)}；y 单位{" "}
                    {metric.unit}，I 单位 A。图中虚线为线性拟合。
                  </p>
                  {metricKey === "voltageV" && (
                    <p className="db-help">
                      基于所选电流区间 V-I 线性拟合；1 V/A = 1
                      Ω。此值为区域估计，不等同于器件小信号动态电阻。
                    </p>
                  )}
                </div>
              )}
              <p className="db-help">
                先点击实测点设置 A，再设置 B，之后移动激活游标。下拉框和上一点 /
                下一点支持键盘操作，独立于当前测试点。
                {metricKey === "powerW" ? "局部增长率取相邻有效实测点，跳过等电流点。" : ""}
              </p>
            </>
          )}
        </>
      )}
    </section>
  )
}
