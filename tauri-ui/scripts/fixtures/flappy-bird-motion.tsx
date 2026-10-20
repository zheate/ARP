// Display-only fixture: real power/spectrum components, no backend or devices.
import { createRoot } from "react-dom/client"
import { flushSync } from "react-dom"
import { PowerRealtimeChart, SpectrumRealtimeChart } from "../../src/features/charts/realtime"
import { DEMO_SNAPSHOT } from "../../src/lib/demo-snapshot"
import "../../src/index.css"
import "../../src/workbench-appearance.css"
import type { LatestPointMarkerStyle } from "../../src/lib/appearance"

const root = createRoot(document.getElementById("root")!)
const wavelengthNm = Array.from({ length: 8192 }, (_, index) => 970 + index * 12 / 8191)
const spectrum = { wavelengthNm, intensity: wavelengthNm.map(x => 100 + 12000 * Math.exp(-(((x - 976.2) / 0.4) ** 2))) }
let elapsed = 60
let intervalMs = 100
let timer: ReturnType<typeof setInterval> | undefined
let frames = 0
let markerStyle: LatestPointMarkerStyle = "flappy-bird"
let power = Array.from({ length: 601 }, (_, index) => ({ elapsedS: index / 10, powerW: 20 + Math.sin(index / 13) }))
function render() {
  const snapshot = { ...DEMO_SNAPSHOT, measurements: { ...DEMO_SNAPSHOT.measurements!, power, spectrum } }
  flushSync(() => root.render(
    <div style={{ height: "100vh", padding: 16, display: "grid", gridTemplateRows: "1fr 1fr", gap: 16 }}>
      <PowerRealtimeChart snapshot={snapshot} stretch latestPointMarkerStyle={markerStyle} />
      <SpectrumRealtimeChart snapshot={snapshot} stretch />
    </div>,
  ))
}
const fixture = {
  setStyle(style: LatestPointMarkerStyle) { markerStyle = style; render() },
  advance() {
    elapsed += intervalMs / 1000
    power = [...power.slice(1), { elapsedS: elapsed, powerW: 20 + Math.sin(elapsed * 10 / 13) }]
    frames++
    render()
  },
  start(cadence: number) { fixture.stop(); intervalMs = cadence; timer = setInterval(fixture.advance, cadence) },
  stop() { clearInterval(timer); timer = undefined },
  get frames() { return frames },
  get data() { return power },
}
Object.assign(window, { birdMotionFixture: fixture })
render()
