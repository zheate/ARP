import assert from "node:assert/strict"
import { createRequire } from "node:module"
import { fileURLToPath } from "node:url"
import test from "node:test"
import { build } from "esbuild"

const root = fileURLToPath(new URL("..", import.meta.url))
const result = await build({
  stdin: { contents: 'export { couplingPowerText } from "./src/components/power-coupling-view"; export { DEMO_SNAPSHOT } from "./src/lib/demo-snapshot";', resolveDir: root },
  tsconfig: `${root}/tsconfig.json`, bundle: true, write: false, platform: "node", format: "cjs",
  loader: { ".css": "empty" }, external: ["react", "react-dom", "react-dom/*"],
})
const module = { exports: {} }
new Function("require", "module", "exports", result.outputFiles[0].text)(createRequire(import.meta.url), module, module.exports)
const { couplingPowerText, DEMO_SNAPSHOT } = module.exports

test("coupling shows valid live power without modifying full-precision source samples", () => {
  for (const [value, expected] of [[0, "0.000"], [0.12345678912345, "0.123"], [1300.1239, "1300.124"], [-0.01234, "-0.012"]]) {
    const snapshot = structuredClone(DEMO_SNAPSHOT)
    snapshot.devices.powerMeter.powerW = value
    const before = JSON.stringify(snapshot)
    assert.equal(couplingPowerText(snapshot), expected)
    assert.equal(JSON.stringify(snapshot), before)
  }
})

test("stopped, stale, missing and non-finite measurements never masquerade as live power", () => {
  assert.equal(couplingPowerText(null), "--")
  for (const changes of [{ running: false }, { quality: "stale" }, { quality: "unavailable" }, { quality: "saturated" }, { powerW: NaN }, { powerW: Infinity }, { powerW: null }]) {
    const snapshot = structuredClone(DEMO_SNAPSHOT)
    Object.assign(snapshot.devices.powerMeter, changes)
    assert.equal(couplingPowerText(snapshot), "--")
  }
  const snapshot = structuredClone(DEMO_SNAPSHOT)
  snapshot.backend.connected = false
  assert.equal(couplingPowerText(snapshot), "--")
})
