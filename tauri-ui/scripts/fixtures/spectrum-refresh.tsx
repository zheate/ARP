// Isolated rendering checks; no bridge or device commands.
import { createRoot } from "react-dom/client"
import { flushSync } from "react-dom"
import { SpectrumRealtimeChart } from "../../src/features/charts/realtime"
import { DEMO_SNAPSHOT } from "../../src/lib/demo-snapshot"
import "../../src/index.css"
import "../../src/workbench-appearance.css"

const root = createRoot(document.getElementById("root")!)
const wavelengthNm = Array.from({ length: 8192 }, (_, index) => 820 + index * 200 / 8191)
let frame = 0
let tick = 0
let smoothing = true
let scale = 1
let centerNm = 976
let halfRangeNm = 25
let spectrum = makeSpectrum()
let peaks = [{ label: "主峰", peakWavelengthNm: 976, peakIntensity: 12100 }]
function makeSpectrum() {
  return { wavelengthNm, intensity: wavelengthNm.map(x => 100 + scale * (12000 + frame * 50) * Math.exp(-(((x - 976 - frame * 0.01) / 0.4) ** 2))) }
}
function render() {
  const snapshot = {
    ...DEMO_SNAPSHOT,
    devices: {
      ...DEMO_SNAPSHOT.devices,
      spectrometer: { ...DEMO_SNAPSHOT.devices.spectrometer, spectrumWindow: { centerNm, halfRangeNm } },
    },
    measurements: { ...DEMO_SNAPSHOT.measurements!, spectrum, spectrumPeaks: peaks },
  }
  flushSync(() => root.render(
    <div className="automatic-test-workspace" style={{ height: "100vh", padding: 16, display: "flex", alignItems: "center" }}>
      <div style={{ width: "100%", height: 300 }}>
        <SpectrumRealtimeChart snapshot={snapshot} smoothing={smoothing} stretch metrics={
          <><div className="spectrum-readout"><span>独立状态更新</span><strong data-testid="metric">{tick}</strong></div></>
        } />
      </div>
    </div>,
  ))
}
Object.assign(window, { spectrumRefreshFixture: {
  tick() { tick++; render() },
  advance() { frame++; spectrum = makeSpectrum(); peaks = [{ label: "主峰", peakWavelengthNm: 976 + frame * 0.01, peakIntensity: 12100 + frame * 50 }]; render() },
  scale(value: number) { scale = value; frame++; spectrum = makeSpectrum(); render() },
  smoothing(value: boolean) { smoothing = value; render() },
  window(center: number, halfRange: number) { centerNm = center; halfRangeNm = halfRange; render() },
  clear() { spectrum = { wavelengthNm: [], intensity: [] }; peaks = []; render() },
  get data() { return spectrum },
  get frame() { return frame },
} })
render()
