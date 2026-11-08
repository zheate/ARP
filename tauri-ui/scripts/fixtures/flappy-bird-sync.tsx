// Browser regression fixture. Uses the product chart without a backend or devices.
import { createRoot } from "react-dom/client"
import { flushSync } from "react-dom"
import { CanvasLineChart, type CanvasChartLine } from "../../src/components/canvas-line-chart"
import "../../src/index.css"

const root = createRoot(document.getElementById("root")!)
let state = {
  data: [{ time: 0, power: 1, voltage: 20 }, { time: 3.141, power: 2.718, voltage: 61.337 }] as Array<{ time: number; power: number | null; voltage: number | null }>,
  xDomain: [0, 10] as [number, number],
  xTicks: undefined as number[] | undefined,
  style: "flappy-bird" as CanvasChartLine["latestPointStyle"],
  rightAxis: false,
  smooth: false,
  monotone: false,
  yDomain: [0, 10] as [number, number],
}

function render(patch: Partial<typeof state> = {}) {
  state = { ...state, ...patch }
  const lines: CanvasChartLine[] = [
    { key: "power", label: "功率", color: "#00a884", showLatestPoint: true, latestPointStyle: state.style, animateLatestPoint: state.smooth, interpolation: state.monotone ? "monotone" : "linear" },
    ...(state.rightAxis ? [{ key: "voltage", label: "电压", color: "#b05cff", yAxisId: "right" as const, showLatestPoint: true, latestPointStyle: state.style, animateLatestPoint: state.smooth, interpolation: state.monotone ? "monotone" as const : "linear" as const }] : []),
  ]
  flushSync(() => root.render(
    <div style={{ padding: 32, height: 400 }}>
      <CanvasLineChart data={state.data} xKey="time" lines={lines} xDomain={state.xDomain} xTicks={state.xTicks} yDomain={state.yDomain} rightYDomain={[0, 100]} ariaLabel="小鸟同步验证" />
    </div>,
  ))
}

Object.assign(window, { birdSyncFixture: { render, get data() { return state.data }, unmount: () => flushSync(() => root.render(null)) } })
render()
