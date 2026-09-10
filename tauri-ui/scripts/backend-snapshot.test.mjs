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
