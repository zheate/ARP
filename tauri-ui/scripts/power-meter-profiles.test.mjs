import assert from "node:assert/strict"
import { createRequire } from "node:module"
import { fileURLToPath } from "node:url"
import test from "node:test"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { build } from "esbuild"

const root = fileURLToPath(new URL("..", import.meta.url))
const built = await build({
  stdin: { contents: 'export { PowerMeterProfileEditor } from "./src/features/devices/settings"; export { DEMO_SNAPSHOT } from "./src/lib/demo-snapshot"; export { runDemoPowerMeterProfileCommand } from "./src/hooks/use-backend-snapshot";', resolveDir: root },
  tsconfig: `${root}/tsconfig.json`, bundle: true, write: false, platform: "node", format: "cjs",
  loader: { ".css": "empty" }, external: ["react", "react-dom", "react-dom/*"],
})
const module = { exports: {} }
new Function("require", "module", "exports", built.outputFiles[0].text)(createRequire(import.meta.url), module, module.exports)
const { PowerMeterProfileEditor, DEMO_SNAPSHOT, runDemoPowerMeterProfileCommand } = module.exports
const snapshot = () => structuredClone(DEMO_SNAPSHOT)
const remove = (base, profile, params = {}) => runDemoPowerMeterProfileCommand(base, "powerMeter.profile.delete", {
  driverKind: base.devices.powerMeter.coefficientLibrary.driverKind,
  serialNumber: base.devices.powerMeter.coefficientLibrary.serialNumber,
  profileId: profile.id, expectedVersion: profile.versions.at(-1).version, ...params,
})

test("demo deletion switches profile and resource gain without modifying the original snapshot", () => {
  const base = snapshot()
  const original = structuredClone(base)
  const library = base.devices.powerMeter.coefficientLibrary
  const selected = library.profiles.find(p => p.id === library.selectedProfileId)
  const next = library.profiles.find(p => p.id !== selected.id)
  const result = remove(base, selected)
  assert.deepEqual(base, original)
  assert.equal(result.configuration.powerMeterProfileId, next.id)
  assert.equal(result.configuration.softwareGain, next.versions.at(-1).softwareGain)
  const resource = result.devices.powerMeter.resourceProfiles.find(p => p.serialNumber === library.serialNumber)
  assert.equal(resource.softwareGain, result.configuration.softwareGain)
  assert.deepEqual(resource.coefficientLibrary, result.devices.powerMeter.coefficientLibrary)
})

test("demo deletion of an unselected profile preserves the active profile", () => {
  const base = snapshot()
  const library = base.devices.powerMeter.coefficientLibrary
  const unselected = library.profiles.find(p => p.id !== library.selectedProfileId)
  const result = remove(base, unselected)
  assert.equal(result.configuration.powerMeterProfileId, library.selectedProfileId)
  assert.equal(result.configuration.softwareGain, base.configuration.softwareGain)
})

test("demo deletion of the last profile returns an empty unbound library", () => {
  let base = snapshot()
  while (base.devices.powerMeter.coefficientLibrary.profiles.length) {
    base = remove(base, base.devices.powerMeter.coefficientLibrary.profiles[0])
  }
  assert.deepEqual(base.devices.powerMeter.coefficientLibrary.profiles, [])
  assert.equal(base.configuration.powerMeterProfileId, "")
  assert.equal(base.configuration.powerMeterProfileVersion, 0)
  assert.equal(base.configuration.powerMeterProfileName, "未绑定方案")
  assert.equal(base.configuration.powerMeterProfileWavelengthPending, false)
})

test("demo deletion rejects stale revisions and changed probe identities", () => {
  const base = snapshot()
  const profile = base.devices.powerMeter.coefficientLibrary.profiles[0]
  assert.throws(() => remove(base, profile, { expectedVersion: 0 }), /方案已被修改/)
  assert.throws(() => remove(base, profile, { serialNumber: "OTHER" }), /探头已切换/)
})

test("delete action respects acquisition, discovery, automatic, identity and unsaved-point locks", () => {
  for (const state of ["ready", "running", "detecting", "automatic", "unsaved", "inactive", "unknownProbe", "empty"]) {
    const base = snapshot()
    base.automaticTest.state = state === "automatic" ? "waiting_stable" : "idle"
    Object.assign(base.devices.powerMeter, { running: state === "running", detecting: state === "detecting" })
    base.manualTest = { ...base.manualTest, pendingCount: state === "unsaved" ? 1 : 0 }
    if (state === "empty") {
      const empty = { ...base.devices.powerMeter.coefficientLibrary, profiles: [], selectedProfileId: "", selectedVersion: 0 }
      base.devices.powerMeter.coefficientLibrary = empty
      base.devices.powerMeter.resourceProfiles = base.devices.powerMeter.resourceProfiles.map((item) => item.serialNumber === empty.serialNumber ? { ...item, coefficientLibrary: empty } : item)
    }
    const config = { ...base.configuration, ...(state === "unknownProbe" ? { powerMeterResource: "ASRL99::INSTR" } : {}) }
    const html = renderToStaticMarkup(createElement(PowerMeterProfileEditor, { snapshot: base, config, active: state !== "inactive", pending: false, run: async () => {} }))
    const action = [...html.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)].find((match) => match[2].includes("删除方案"))
    assert.ok(action, state)
    assert.equal(/\sdisabled(?:=|\s|$)/.test(action[1]), state !== "ready", state)
    assert.doesNotMatch(html, /删除方案确认/)
  }
})
