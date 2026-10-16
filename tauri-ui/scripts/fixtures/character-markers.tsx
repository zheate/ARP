// Product chart regression fixture. Synthetic rows only; no bridge or devices.
import { createRoot } from "react-dom/client"
import { flushSync } from "react-dom"
import { CanvasLineChart, type CanvasChartLine } from "../../src/components/canvas-line-chart"
import { LATEST_POINT_MARKER_OPTIONS, type LatestPointMarkerStyle } from "../../src/lib/appearance"

const prototype = CanvasRenderingContext2D.prototype as CanvasRenderingContext2D & Record<string, any>
for (const method of ["moveTo", "lineTo", "bezierCurveTo"] as const) {
  const original = prototype[method]
  prototype[method] = function (this: any, ...args: number[]) {
    this.__qaEndpoint = args.slice(-2)
    return original.apply(this, args)
  } as any
}
const originalStroke = prototype.stroke
prototype.stroke = function (this: any, ...args: any[]) {
  if (this.canvas.getAttribute("aria-label") === "角色末端验证" && this.strokeStyle === "#00a884") {
    this.canvas.__qaEndpoint = this.__qaEndpoint
  }
  return originalStroke.apply(this, args)
} as any

const initialData = [{ time: 0, power: 2, voltage: 20 }, { time: 10, power: 10, voltage: 100 }]
let state = {
  style: "bee" as LatestPointMarkerStyle,
  data: initialData as Array<{ time: number; power: number | null; voltage: number | null }>,
  domain: [0, 10] as [number, number],
  monotone: true,
  rightAxis: false,
  showPoints: false,
  hidden: false,
}
const root = createRoot(document.getElementById("root")!)
function render(patch: Partial<typeof state> = {}) {
  state = { ...state, ...patch }
  const lines: CanvasChartLine[] = [{ key: "power", label: "功率", color: "#00a884", showLatestPoint: true,
    latestPointStyle: state.style, animateLatestPoint: true, interpolation: state.monotone ? "monotone" : "linear", showPoints: state.showPoints },
  ...(state.rightAxis ? [{ key: "voltage", label: "电压", color: "#b05cff", yAxisId: "right" as const, showLatestPoint: true,
    latestPointStyle: state.style, animateLatestPoint: true, interpolation: state.monotone ? "monotone" as const : "linear" as const }] : [])]
  flushSync(() => root.render(<main style={{ padding: 24 }}>
    <h1>曲线末端角色验证</h1>
    <label>末端样式 <select aria-label="验证末端样式" value={state.style} onChange={(e) => render({ style: e.target.value as LatestPointMarkerStyle })}>
      {LATEST_POINT_MARKER_OPTIONS.map((option) => <option value={option.id} key={option.id}>{option.label}</option>)}
    </select></label>
    <button onClick={() => render({ data: [] })}>清空数据</button>
    <button onClick={() => render({ data: initialData, domain: [0, 10], hidden: false })}>恢复数据</button>
    <button onClick={() => render({ hidden: !state.hidden })}>显示／隐藏图表</button>
    <div style={{ height: 340, display: state.hidden ? "none" : undefined }}>
      <CanvasLineChart data={state.data} xKey="time" lines={lines} xDomain={state.domain} yDomain={[0, 10]}
        rightYDomain={[0, 100]} tightInsets ariaLabel="角色末端验证" preserveAllPoints />
    </div>
  </main>))
}
Object.assign(window, { characterMarkerFixture: {
  render,
  get data() { return state.data },
  reset: () => render({ data: initialData, domain: [0, 10], hidden: false, rightAxis: false, showPoints: false }),
} })
render()
