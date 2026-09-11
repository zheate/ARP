import assert from "node:assert/strict"
import test from "node:test"
import { DEMO_RUN_SNAPSHOT, DEMO_SNAPSHOT, resetDemoRun } from "./demo-snapshot.ts"
import { DEMO_DWELL_S, DEMO_RUN_DURATION_S, demoRunSnapshot } from "./demo-run.ts"

test("runnable preview starts at zero with no residual results or automatic control owner", () => {
  for (const base of [DEMO_SNAPSHOT, demoRunSnapshot(DEMO_RUN_SNAPSHOT, DEMO_RUN_DURATION_S)]) {
    const idle = resetDemoRun(base)
    assert.equal(idle.automaticTest.state, "idle")
    assert.equal(idle.automaticTest.canStart, true)
    assert.equal(idle.automaticTest.currentDwellElapsedS, null)
    assert.equal(idle.automaticTest.savedPointCount, 0)
    assert.equal(idle.automaticTest.runContext, null)
    assert.equal(idle.devices.powerSupply.outputEnabled, false)
    assert.equal(idle.devices.powerSupply.outputCurrentA, 0)
    assert.equal(idle.devices.powerMeter.stable, false)
    assert.equal(idle.devices.powerMeter.stableElapsedS, null)
    assert.equal(idle.safety.controlOwner, "manual")
    assert.deepEqual(idle.measurements?.stable, [])
  }
})

test("hold uses real elapsed seconds and records each point exactly once after its hold", () => {
  const firstHold = 2.6
  for (const seconds of [0, 1, 3, DEMO_DWELL_S - 0.01]) {
    const snapshot = demoRunSnapshot(DEMO_RUN_SNAPSHOT, firstHold + seconds)
    assert.equal(snapshot.automaticTest.state, "waiting_voltage")
    assert.equal(snapshot.automaticTest.currentDwellS, DEMO_DWELL_S)
    assert.equal(snapshot.automaticTest.currentDwellElapsedS, Math.floor(seconds))
    assert.equal(snapshot.devices.powerMeter.stableElapsedS, Math.floor(seconds))
    assert.equal(snapshot.automaticTest.savedPointCount, 0)
  }
  const recorded = demoRunSnapshot(DEMO_RUN_SNAPSHOT, firstHold + DEMO_DWELL_S + 0.01)
  assert.equal(recorded.automaticTest.savedPointCount, 1)
  assert.equal(recorded.automaticTest.state, "saving_point")
  const final = demoRunSnapshot(DEMO_RUN_SNAPSHOT, DEMO_RUN_DURATION_S)
  assert.equal(final.automaticTest.savedPointCount, 3)
  assert.equal(final.automaticTest.exportedPointCount, 3)
  assert.deepEqual(final.measurements?.stable.map((point) => point.currentA), [2, 6, 10])
  assert.equal(final.devices.powerSupply.outputCurrentA, 0)
  assert.equal(final.devices.powerMeter.stableElapsedS, null)
  assert.equal(final.automaticTest.currentDwellElapsedS, null)
  // Slow or repeated refreshes must produce the same outcome without accumulating writes.
  assert.deepEqual(demoRunSnapshot(DEMO_RUN_SNAPSHOT, DEMO_RUN_DURATION_S + 100), final)
  assert.equal(DEMO_RUN_SNAPSHOT.automaticTest.savedPointCount, 0)
})
