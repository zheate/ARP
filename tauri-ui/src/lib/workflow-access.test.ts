import assert from "node:assert/strict"
import test from "node:test"
import { DEMO_SNAPSHOT } from "./demo-snapshot.ts"
import { snapshotForWorkflow, workflowBlocked } from "./workflow-access.ts"

test("each occupied device locks the opposite page, including a paused automatic run", () => {
  for (const owner of ["manual", "automatic"] as const) {
    const snapshot = structuredClone(DEMO_SNAPSHOT)
    snapshot.automaticTest.state = "idle"
    snapshot.safety.workflowOwner = owner
    snapshot.safety.workflowBusy = true
    assert.equal(workflowBlocked(snapshot, owner), false)
    assert.equal(workflowBlocked(snapshot, owner === "manual" ? "automatic" : "manual"), true)
    snapshot.safety.workflowBusy = false
    assert.equal(workflowBlocked(snapshot, "manual"), false)
    assert.equal(workflowBlocked(snapshot, "automatic"), false)
  }
  const snapshot = structuredClone(DEMO_SNAPSHOT)
  snapshot.automaticTest.state = "paused"
  assert.equal(workflowBlocked(snapshot, "manual"), true)
  assert.equal(workflowBlocked(null, "automatic"), true)
})

test("other workflow has no curves or reference; shared source data stays unchanged", () => {
  const snapshot = structuredClone(DEMO_SNAPSHOT)
  snapshot.safety.workflowOwner = "manual"
  const source = JSON.stringify(snapshot)
  const manual = snapshotForWorkflow(snapshot, "manual")!
  assert.equal(manual.measurements, snapshot.measurements)
  assert.equal(manual.automaticTest.reference, undefined)
  const automatic = snapshotForWorkflow(snapshot, "automatic")!
  assert.equal(automatic.measurements!.power.length, 0)
  assert.equal(automatic.measurements!.stable.length, 0)
  assert.equal(automatic.measurements!.spectrum.wavelengthNm.length, 0)
  assert.equal(automatic.automaticTest.reference, undefined)
  assert.equal(JSON.stringify(snapshot), source)
})
