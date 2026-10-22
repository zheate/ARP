// Production dialogs with isolated example state. No bridge/device access.
import { useState } from "react"
import { createRoot } from "react-dom/client"
import { DeviceSettingsDialog, type DeviceSettingsKind } from "../../src/features/devices/settings"
import { emptyConfig } from "../../src/features/configuration/model"
import { DEMO_SNAPSHOT } from "../../src/lib/demo-snapshot"
import type { AppConfiguration, BackendSnapshot } from "../../src/lib/backend"
import type { RunCommand } from "../../src/lib/command-contract"
import "../../src/index.css"
import "../../src/workbench-appearance.css"

function Fixture() {
  const [kind, setKind] = useState<DeviceSettingsKind | null>(null)
  const [config, setConfig] = useState<AppConfiguration>({ ...emptyConfig, ...DEMO_SNAPSHOT.configuration!, setCurrentA: 0 })
  const [snapshot, setSnapshot] = useState<BackendSnapshot>({ ...DEMO_SNAPSHOT,
    devices: { ...DEMO_SNAPSHOT.devices,
      powerSupply: { ...DEMO_SNAPSHOT.devices.powerSupply, connected: false, outputEnabled: false },
      powerMeter: { ...DEMO_SNAPSHOT.devices.powerMeter, running: false, powerBackground: {
        mode: "shadow", algorithm: "background-dual-window-v1", state: "尚未学习本底", job: null,
        parametersConfirmed: false, model: null, decision: { stable: false, reason: "参数待确认（仅诊断）",
          metrics: null, tolerance_w: null, noise_limit_w: null, guarded_shift_w: null, guarded_drift_w: null },
      } },
      spectrometer: { ...DEMO_SNAPSHOT.devices.spectrometer, running: false },
    }, automaticTest: { ...DEMO_SNAPSHOT.automaticTest, state: "idle" },
  })
  const run: RunCommand = async (method, params) => {
    if (method === "device.refresh") {
      const device = (params as { device: keyof BackendSnapshot["devices"] }).device
      setSnapshot(s => ({ ...s, devices: { ...s.devices, [device]: { ...s.devices[device], detecting: true } } }))
      await new Promise(resolve => setTimeout(resolve, 600))
      setSnapshot(s => ({ ...s, devices: { ...s.devices, [device]: { ...s.devices[device], detecting: false } } }))
    } else if (method === "powerMeter.start" || method === "powerMeter.stop") {
      setSnapshot(s => ({ ...s, devices: { ...s.devices, powerMeter: { ...s.devices.powerMeter, running: method === "powerMeter.start" } } }))
    } else if (method.startsWith("powerMeter.background.")) {
      setSnapshot(s => ({ ...s, devices: { ...s.devices, powerMeter: { ...s.devices.powerMeter,
        powerBackground: { ...s.devices.powerMeter.powerBackground!,
          state: method.endsWith("cancel") ? "本底任务已取消" : "学习本底 0.0 / 180 秒",
          job: method.endsWith("cancel") ? null : "learn" },
      } } }))
    }
  }
  return <main style={{ padding: 24, minHeight: "110vh" }}>
    <p>本地界面验证：示例状态，无真实设备命令。</p>
    <div style={{ display: "flex", gap: 16 }} data-testid="cards">
      {(["powerSupply", "powerMeter", "spectrometer"] as const).map((device, index) => <button style={{ width: 200, height: 100, border: "1px solid #ccc" }} key={device} onClick={() => setKind(device)}>{["电源设置", "功率计设置", "光谱仪设置"][index]}</button>)}
    </div>
    {kind && <DeviceSettingsDialog kind={kind} open onClose={() => setKind(null)} onSave={async () => {}} snapshot={snapshot} config={config}
      update={(key, value) => setConfig(c => ({ ...c, [key]: value }))} active pending={false} run={run} />}
  </main>
}
createRoot(document.getElementById("root")!).render(<Fixture />)
