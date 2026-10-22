import assert from "node:assert/strict"
import { createRequire } from "node:module"
import { fileURLToPath } from "node:url"
import test from "node:test"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { build } from "esbuild"

const root = fileURLToPath(new URL("..", import.meta.url))
const result = await build({
  stdin: { contents: 'export { ManualPage } from "./src/features/manual/page"; export { PowerSupplySettingsForm, PowerMeterProfileEditor } from "./src/features/devices/settings"; export { DEMO_SNAPSHOT } from "./src/lib/demo-snapshot";', resolveDir: root },
  tsconfig: `${root}/tsconfig.json`, bundle: true, write: false, platform: "node", format: "cjs",
  loader: { ".css": "empty" }, external: ["react", "react-dom", "react-dom/*"],
})
const module = { exports: {} }
new Function("require", "module", "exports", result.outputFiles[0].text)(createRequire(import.meta.url), module, module.exports)
const { ManualPage, PowerSupplySettingsForm, PowerMeterProfileEditor, DEMO_SNAPSHOT } = module.exports
const noop = () => {}

test("inline coefficient selection preserves acquisition, identity and unsaved-point locks", () => {
  for (const state of ["ready", "running", "detecting", "unsaved", "automatic", "pending", "inactive", "otherProbe"]) {
    const snapshot = structuredClone(DEMO_SNAPSHOT)
    snapshot.automaticTest.state = state === "automatic" ? "waiting_stable" : "idle"
    Object.assign(snapshot.devices.powerMeter, { running: state === "running", detecting: state === "detecting" })
    snapshot.manualTest = { ...snapshot.manualTest, pendingCount: state === "unsaved" ? 1 : 0 }
    const config = { ...snapshot.configuration, ...(state === "otherProbe" ? { powerMeterResource: "ASRL99::INSTR" } : {}) }
    const html = renderToStaticMarkup(createElement(PowerMeterProfileEditor, { snapshot, config, active: state !== "inactive", pending: state === "pending", run: noop, onManage: noop }))
    const selector = html.match(/<button\b([^>]*aria-label="手动测试系数方案"[^>]*)>/)
    assert.ok(selector)
    assert.equal(/\sdisabled(?:=|\s|$)/.test(selector[1]), state !== "ready", state)
    if (state === "otherProbe") assert.doesNotMatch(html, /976nm 生产光路/)
  }
})

test("manual setpoints use ordinary editable text without a fixed-format overlay", () => {
  for (const value of [0, 1, 1.2, 12, 0.123456]) {
    const snapshot = structuredClone(DEMO_SNAPSHOT)
    snapshot.configuration.setCurrentA = value
    snapshot.safety.manualControlsAllowed = true
    const html = renderToStaticMarkup(createElement(ManualPage, { snapshot, config: snapshot.configuration, active: true, pending: false, run: noop, update: noop }))
    assert.match(html, new RegExp(`id="manual-psu-current"[^>]*value="${value}"`))
    assert.doesNotMatch(html, /number-input__formatted-value|data-fixed-display|manual-power-digit/)
  }
})

test("manual power readouts use output feedback and never substitute input voltage or target current", () => {
  for (const field of ["outputVoltageV", "outputCurrentA"]) {
    for (const [connected, measured, voltageDisplay, currentDisplay] of [[true, 0, "0", "0.0"], [true, 12.34, "12.34", "12.3"], [true, 65, "65", "65.0"], [true, 12.36, "12.36", "12.4"], [true, null, "--", "--"], [true, NaN, "--", "--"], [false, 12.34, "--", "--"]]) {
      const expected = field === "outputCurrentA" ? currentDisplay : voltageDisplay
      const snapshot = structuredClone(DEMO_SNAPSHOT)
      snapshot.configuration.powerSupplyKind = "tdk"
      snapshot.configuration.setCurrentA = 8
      snapshot.devices.powerSupply = { ...snapshot.devices.powerSupply, connected, inputVoltageV: 48, activeCurrentA: 8, [field]: measured }
      snapshot.safety.manualControlsAllowed = true
      const html = renderToStaticMarkup(createElement(ManualPage, { snapshot, config: snapshot.configuration, active: true, pending: false, run: noop, update: noop }))
      const readout = html.match(new RegExp(`data-measurement="${field}" data-available="(true|false)"[^>]*>\\s*<span class="numeric-readout" aria-label="([^"]*)"`, "s"))
      assert.ok(readout, "the feedback readout must be present")
      assert.equal(readout[2], expected, `${field}, connected=${connected}, feedback=${measured}`)
      assert.equal(readout[1], String(expected !== "--"))
      assert.ok(!html.includes("当前电流："), "the duplicate current readout must be removed")
    }
  }
})

test("unconfirmed shutdown exposes small residual currents without rounding them to zero", () => {
  for (const current of [0.02, 0.015, 0.009, 0]) {
    const snapshot = structuredClone(DEMO_SNAPSHOT)
    snapshot.configuration.powerSupplyKind = "ru"
    snapshot.safety.manualControlsAllowed = true
    Object.assign(snapshot.safety.shutdown, { requested: true, confirmed: false, outputOffConfirmed: true })
    Object.assign(snapshot.devices.powerSupply, { connected: true, outputEnabled: false, outputCurrentA: current })
    const html = renderToStaticMarkup(createElement(ManualPage, { snapshot, config: snapshot.configuration, active: true, pending: false, run: noop, update: noop }))
    const readout = html.match(/data-measurement="outputCurrentA" data-available="true"[^>]*>\s*<span class="numeric-readout" aria-label="([^"]*)"/s)
    assert.ok(readout)
    assert.equal(readout[1], current.toFixed(3))
    assert.match(html, /manual-power-measurement--unconfirmed/)
  }
})

test("quick current application advertises its shortcut and shares button availability", () => {
  for (const state of ["ready", "disconnected", "outputOff", "pending", "detecting", "automatic", "stopping", "inactive"]) {
    const snapshot = structuredClone(DEMO_SNAPSHOT)
    snapshot.configuration.powerSupplyKind = "tdk"
    snapshot.devices.powerSupply = {
      ...snapshot.devices.powerSupply, connected: state !== "disconnected",
      outputEnabled: state !== "outputOff", detecting: state === "detecting",
    }
    snapshot.safety.manualControlsAllowed = state !== "automatic"
    snapshot.manualTest = { ...snapshot.manualTest, currentCommand: { pending: false, requestedCurrentA: null, remainingS: 0, stopping: state === "stopping" } }
    const html = renderToStaticMarkup(createElement(ManualPage, {
      snapshot, config: snapshot.configuration, active: state !== "inactive", pending: state === "pending", run: noop, update: noop,
    }))
    assert.equal((html.match(/aria-keyshortcuts="Control\+Enter"/g) ?? []).length, 1)
    const button = html.match(/<button\b([^>]*)>(?:(?!<\/button>).)*?设置电流(?:<\/span>)?<\/button>/s)
    assert.ok(button, "the apply-current button must be present")
    assert.equal(/\sdisabled(?:=|\s|$)/.test(button[1]), !["ready", "pending"].includes(state), state)
    assert.equal(/aria-disabled="true"/.test(button[1]), state !== "ready", `${state} command guard`)
  }
})

test("manual voltage uses the selected controller's configuration and keeps output-off adjustment available", () => {
  for (const kind of ["ru", "tdk", "ch341"]) {
    for (const state of ["ready", "outputOff", "disconnected", "pending", "detecting", "automatic", "stopping", "inactive", "shutdownUnconfirmed"]) {
      const snapshot = structuredClone(DEMO_SNAPSHOT)
      Object.assign(snapshot.configuration, { powerSupplyKind: kind, ruVoltageV: 32.7, tdkVoltageV: 24.3 })
      Object.assign(snapshot.devices.powerSupply, { connected: state !== "disconnected", outputEnabled: state !== "outputOff", detecting: state === "detecting" })
      snapshot.safety.manualControlsAllowed = state !== "automatic"
      Object.assign(snapshot.safety.shutdown, { requested: state === "shutdownUnconfirmed", confirmed: false })
      snapshot.manualTest = { ...snapshot.manualTest, currentCommand: { stopping: state === "stopping" } }
      const html = renderToStaticMarkup(createElement(ManualPage, { snapshot, config: snapshot.configuration, active: state !== "inactive", pending: state === "pending", run: noop, update: noop }))
      const input = html.match(/<input\b([^>]*id="manual-psu-voltage"[^>]*)>/)
      const button = html.match(/<button\b([^>]*)>(?:(?!<\/button>).)*?设置电压(?:<\/span>)?<\/button>/s)
      if (kind === "ch341") {
        assert.equal(input, null)
        assert.equal(button, null)
        continue
      }
      assert.ok(input && button, "serial supplies expose voltage on the main card")
      assert.match(input[1], new RegExp(`value="${kind === "ru" ? "32.7" : "24.3"}"`))
      const blocked = !["ready", "outputOff", "pending"].includes(state) && !(kind === "tdk" && state === "shutdownUnconfirmed")
      assert.equal(/\sdisabled(?:=|\s|$)/.test(input[1]), blocked, `${kind}/${state} voltage input`)
      assert.equal(/\sdisabled(?:=|\s|$)/.test(button[1]), blocked, `${kind}/${state}`)
    }
  }
})

test("voltage moves out of manual hardware settings while automatic voltage controls remain", () => {
  for (const kind of ["ru", "tdk"]) {
    const snapshot = structuredClone(DEMO_SNAPSHOT)
    snapshot.configuration.powerSupplyKind = kind
    const props = { snapshot, config: snapshot.configuration, update: noop, active: true, pending: false, run: noop }
    const manual = renderToStaticMarkup(createElement(PowerSupplySettingsForm, { ...props, mode: "manual" }))
    const automatic = renderToStaticMarkup(createElement(PowerSupplySettingsForm, { ...props, mode: "auto" }))
    assert.doesNotMatch(manual, /输出电压 \(V\)|>设置电压</)
    assert.match(automatic, /输出电压 \(V\)/)
    assert.match(automatic, />设置电压</)
  }
})
