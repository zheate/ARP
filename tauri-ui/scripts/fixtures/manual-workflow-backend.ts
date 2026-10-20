// Browser-only port. Never opens a device or writes configuration to disk.
import { useSyncExternalStore } from "react"
import { DEMO_RUN_SNAPSHOT } from "../../src/lib/demo-snapshot"
import type { BackendSnapshot } from "../../src/lib/backend"

let snapshot: BackendSnapshot
let view = "manual"
const listeners = new Set<() => void>()
const commands: Array<{ method: string; params: Record<string, unknown> }> = []
const publish = () => { snapshot = { ...snapshot }; listeners.forEach(listener => listener()) }
function reset() {
  snapshot = structuredClone(DEMO_RUN_SNAPSHOT)
  snapshot.devices.powerSupply.connected = false
  snapshot.devices.powerSupply.outputEnabled = false
  snapshot.devices.powerMeter.running = false
  snapshot.devices.spectrometer.running = false
  snapshot.safety.workflowOwner = null
  snapshot.safety.workflowBusy = false
  snapshot.measurements = { power: [], stable: [], agingPower: [], spectrum: { wavelengthNm: [], intensity: [] }, spectrumPeaks: [] }
  publish()
}
reset()
const powerData = () => Array.from({ length: 601 }, (_, index) => ({ elapsedS: 5 + index / 10, powerW: 20 + Math.sin(index / 13) }))
const command = async (method: string, params: Record<string, unknown> = {}) => {
  snapshot = structuredClone(snapshot)
  commands.push({ method, params })
  if (method === "app.configure") snapshot.configuration = { ...snapshot.configuration!, ...params }
  const owner = params.workflow === "automatic" || params.workflow === "manual" ? params.workflow : view === "manual" ? "manual" : "automatic"
  if (["powerMeter.start", "spectrometer.start", "powerSupply.connect", "automatic.start"].includes(method)) {
    snapshot.safety.workflowOwner = owner
    if (method === "powerMeter.start") {
      snapshot.devices.powerMeter.running = true
      snapshot.measurements!.power = powerData()
    }
    if (method === "spectrometer.start") snapshot.devices.spectrometer.running = true
    if (method === "powerSupply.connect") snapshot.devices.powerSupply.connected = true
    if (method === "automatic.start") {
      snapshot.automaticTest.state = "waiting_stable"
      snapshot.safety.manualControlsAllowed = false
    }
  }
  if (method === "powerMeter.stop") snapshot.devices.powerMeter.running = false
  if (method === "spectrometer.stop") snapshot.devices.spectrometer.running = false
  if (method === "powerSupply.disconnect") snapshot.devices.powerSupply.connected = false
  snapshot.safety.workflowBusy = snapshot.devices.powerSupply.connected || snapshot.devices.powerMeter.running || snapshot.devices.spectrometer.running || !["idle", "completed"].includes(snapshot.automaticTest.state)
  publish()
  return snapshot
}
export function useBackendSnapshot(nextView: string) {
  view = nextView
  const current = useSyncExternalStore(listener => { listeners.add(listener); return () => listeners.delete(listener) }, () => snapshot)
  return { snapshot: current, loading: false, error: null, commandPending: false, pendingCommand: null, command, refresh: async () => snapshot }
}
Object.assign(window, { workflowQA: {
  reset, commands,
  activate(owner: "manual" | "automatic", device: "powerMeter" | "spectrometer" | "powerSupply") {
    return command(device === "powerSupply" ? "powerSupply.connect" : `${device}.start`, { workflow: owner })
  },
  pause() { snapshot = structuredClone(snapshot); snapshot.automaticTest.state = "paused"; snapshot.safety.workflowOwner = "automatic"; snapshot.safety.workflowBusy = true; publish() },
  advance() {
    snapshot = { ...snapshot }
    const power = snapshot.measurements!.power
    const elapsedS = (power.at(-1)?.elapsedS ?? 65) + 0.2
    snapshot.measurements = { ...snapshot.measurements!, power: [...power.slice(-600), { elapsedS, powerW: 20 + Math.sin((elapsedS - 5) * 10 / 13) }] }
    snapshot.devices = { ...snapshot.devices, powerMeter: { ...snapshot.devices.powerMeter, powerW: snapshot.measurements.power.at(-1)!.powerW } }
    publish()
  },
  get data() { return snapshot.measurements },
} })
