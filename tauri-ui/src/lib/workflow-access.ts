import type { BackendSnapshot } from "./backend"

export type TestWorkflow = "manual" | "automatic"

export function workflowBlocked(snapshot: BackendSnapshot | null, target: TestWorkflow): boolean {
  if (!snapshot) return true
  const automaticActive = !["idle", "completed"].includes(snapshot.automaticTest.state)
  if (target === "manual" && automaticActive) return true
  const owner = snapshot.safety.workflowOwner ?? (automaticActive ? "automatic" : null)
  const busy = snapshot.safety.workflowBusy ?? Boolean(
    snapshot.devices.powerSupply.connected || snapshot.devices.powerSupply.outputEnabled
    || snapshot.devices.powerMeter.running || snapshot.devices.spectrometer.running,
  )
  return busy && owner !== null && owner !== target
}

export function snapshotForWorkflow(snapshot: BackendSnapshot | null, workflow: TestWorkflow): BackendSnapshot | null {
  if (!snapshot) return snapshot
  if (!snapshot.safety.workflowOwner || snapshot.safety.workflowOwner === workflow) {
    return workflow === "manual" && snapshot.automaticTest.reference
      ? { ...snapshot, automaticTest: { ...snapshot.automaticTest, reference: undefined } }
      : snapshot
  }
  return {
    ...snapshot,
    automaticTest: { ...snapshot.automaticTest, reference: undefined },
    measurements: { power: [], stable: [], agingPower: [], spectrum: { wavelengthNm: [], intensity: [] }, spectrumPeaks: [] },
  }
}
