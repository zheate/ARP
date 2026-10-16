import assert from "node:assert/strict"
import test from "node:test"
import { fileURLToPath } from "node:url"
import { build } from "esbuild"

// Bundle the actual merge entry point so the same aliases/types as the UI are
// resolved, without starting a WebView or calling a Tauri command.
const bundled = await build({
  entryPoints: [fileURLToPath(new URL("../src/lib/backend.ts", import.meta.url))],
  tsconfig: fileURLToPath(new URL("../tsconfig.json", import.meta.url)),
  bundle: true, write: false, format: "esm", platform: "node",
})
const { mergeBackendSnapshot } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`)

test("measurement updates retain equivalent configuration and script identities", () => {
  const first = { ...snapshot(1, [0]), configuration: { sn: "A", customCurrentPoints: [{ currentA: 1, dwellS: 2 }] }, testScripts: [{ id: "standard", name: "标准", powerSupplyKinds: ["tdk"] }] }
  const previous = mergeBackendSnapshot(null, first)
  const patch = { ...structuredClone(first), ...snapshot(2, [0, 1]) }
  const next = mergeBackendSnapshot(previous, patch)
  assert.equal(next.configuration, previous.configuration)
  assert.equal(next.testScripts, previous.testScripts)
  assert.deepEqual(next.measurements.power, patch.measurements.power)
  assert.deepEqual(next.measurements.spectrum.intensity, [2])
})

test("static-section sharing never hides edits, removals, or a backend restart", () => {
  const previous = mergeBackendSnapshot(null, { ...snapshot(5, [0]), configuration: { sn: "A", customCurrentPoints: [{ currentA: 1, dwellS: 2 }] }, testScripts: [{ id: "standard", name: "标准" }] })
  const edited = { ...snapshot(6, [1]), configuration: { sn: "A", customCurrentPoints: [{ currentA: 1, dwellS: 3 }] }, testScripts: [{ id: "standard", name: "新名称" }] }
  const next = mergeBackendSnapshot(previous, edited)
  assert.equal(next.configuration, edited.configuration)
  assert.equal(next.testScripts, edited.testScripts)
  const removed = mergeBackendSnapshot(previous, snapshot(7, []))
  assert.equal(removed.configuration, undefined)
  assert.equal(removed.testScripts, undefined)
  const restarted = structuredClone(previous)
  restarted.backend.runtimeId = "runtime-2"
  const fresh = mergeBackendSnapshot(previous, restarted)
  assert.equal(fresh.configuration, restarted.configuration)
  assert.notEqual(fresh.configuration, previous.configuration)
})

test("every full-spectrum sample survives consecutive measurement snapshots", () => {
  let previous = null
  for (let frame = 1; frame <= 30; frame++) {
    const patch = snapshot(frame, [frame / 10])
    const wavelengthNm = Array.from({ length: 8192 }, (_, i) => 970 + i / 1024)
    const intensity = wavelengthNm.map((_, i) => i + frame / 100)
    patch.measurements.spectrum = { wavelengthNm, intensity }
    previous = mergeBackendSnapshot(previous, patch)
    assert.deepEqual(previous.measurements.spectrum.wavelengthNm, wavelengthNm)
    assert.deepEqual(previous.measurements.spectrum.intensity, intensity)
  }
})

function snapshot(revision, times) {
  return {
    capturedAt: "2026-09-08T00:00:00Z",
    backend: { mode: "active", runtimeId: "runtime-1" },
    seriesRevisions: { power: revision, stable: revision, aging: revision, spectrum: revision, pd: revision },
    measurements: {
      power: times.map((elapsedS) => ({ elapsedS, powerW: elapsedS })),
      stable: [], agingPower: [], spectrumPeaks: [],
      spectrum: { wavelengthNm: [976], intensity: [revision], gridRevision: 1 },
    },
    pd: { state: "running", points: times.map((elapsedS) => ({ elapsedS, value: elapsedS })) },
  }
}

function delta(revision, times) {
  const result = snapshot(revision, times)
  result.seriesPatches = {
    power: { startX: 10, points: result.measurements.power },
    pd: { startX: 10, points: result.pd.points },
  }
  delete result.measurements
  delete result.pd.points
  return result
}

test("actual snapshot merge handles full command histories followed by overlapping stream deltas", () => {
  let state = mergeBackendSnapshot(null, snapshot(10, [10]))
  state = mergeBackendSnapshot(state, snapshot(11, [10, 11]))
  state = mergeBackendSnapshot(state, delta(12, [11, 12]))
  assert.deepEqual(state.measurements.power.map((point) => point.elapsedS), [10, 11, 12])
  assert.deepEqual(state.pd.points.map((point) => point.elapsedS), [10, 11, 12])
  assert.equal(state.seriesRevisions.power, 12)
})

test("actual snapshot merge retains reset histories and revisions against delayed messages", () => {
  let state = mergeBackendSnapshot(null, snapshot(20, [10, 11]))
  state = mergeBackendSnapshot(state, snapshot(21, []))
  state = mergeBackendSnapshot(state, delta(20, [11, 12]))
  assert.deepEqual(state.measurements.power, [])
  assert.deepEqual(state.pd.points, [])
  assert.equal(state.seriesRevisions.power, 21)
  assert.equal(state.seriesRevisions.pd, 21)
  assert.deepEqual(state.measurements.spectrum.intensity, [21])
})

test("actual snapshot merge replaces histories after a Python runtime restart with lower revisions", () => {
  const previous = mergeBackendSnapshot(null, snapshot(100, [100]))
  const restarted = snapshot(1, [0])
  restarted.backend.runtimeId = "runtime-2"
  const next = mergeBackendSnapshot(previous, restarted)
  assert.equal(next.backend.runtimeId, "runtime-2")
  assert.equal(next.seriesRevisions.power, 1)
  assert.deepEqual(next.measurements.power.map((point) => point.elapsedS), [0])
  assert.deepEqual(next.pd.points.map((point) => point.elapsedS), [0])
})
