// Browser-only performance fixture. Never imported by the product entry point.
import { createRoot } from "react-dom/client"
import { flushSync } from "react-dom"
import { PowerRealtimeChart, SpectrumRealtimeChart } from "../../src/features/charts/realtime"
import { DEMO_SNAPSHOT } from "../../src/lib/demo-snapshot"
import "../../src/index.css"
import "../../src/workbench-appearance.css"

const root = createRoot(document.getElementById("root")!)
const wavelengthNm = Array.from({ length: 8192 }, (_, index) => 970 + index * 12 / 8191)
let frame = 0
function render() {
  const snapshot = {
    ...DEMO_SNAPSHOT,
    measurements: {
      ...DEMO_SNAPSHOT.measurements!,
      power: Array.from({ length: 601 }, (_, index) => ({ elapsedS: frame / 5 + index / 10, powerW: 20 + Math.sin((index + frame) / 13) })),
      spectrum: { wavelengthNm, intensity: wavelengthNm.map((x) => 100 + (12000 + frame) * Math.exp(-(((x - 976.2) / 0.4) ** 2))) },
    },
  }
  flushSync(() => root.render(
    <div style={{ height: "100vh", padding: 16, display: "grid", gridTemplateRows: "1fr 1fr", gap: 16 }}>
      <PowerRealtimeChart snapshot={snapshot} automatic stretch />
      <SpectrumRealtimeChart snapshot={snapshot} stretch />
    </div>,
  ))
}
Object.assign(window, { chartPerformanceFixture: { advance() { frame++; render() }, get frame() { return frame } } })
render()
