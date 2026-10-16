import assert from "node:assert/strict"
import { createRequire } from "node:module"
import { fileURLToPath } from "node:url"
import test from "node:test"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { build } from "esbuild"

const root = fileURLToPath(new URL("..", import.meta.url))
const result = await build({
  stdin: { contents: 'export { ManualPage } from "./src/features/manual/page"; export { DEMO_SNAPSHOT } from "./src/lib/demo-snapshot";', resolveDir: root },
  tsconfig: `${root}/tsconfig.json`, bundle: true, write: false, platform: "node", format: "cjs",
  loader: { ".css": "empty" }, external: ["react", "react-dom", "react-dom/*"],
})
const module = { exports: {} }
new Function("require", "module", "exports", result.outputFiles[0].text)(createRequire(import.meta.url), module, module.exports)
const { ManualPage, DEMO_SNAPSHOT } = module.exports
const noop = () => {}

test("manual current display keeps one decimal without changing the editable value", () => {
  for (const [value, displayed] of [[0, "0.0"], [1, "1.0"], [1.2, "1.2"], [12, "12.0"]]) {
    const snapshot = structuredClone(DEMO_SNAPSHOT)
    snapshot.configuration.setCurrentA = value
    snapshot.safety.manualControlsAllowed = true
    const html = renderToStaticMarkup(createElement(ManualPage, { snapshot, config: snapshot.configuration, active: true, pending: false, run: noop, update: noop }))
    assert.match(html, new RegExp(`id="manual-psu-current"[^>]*value="${value}"`))
    assert.match(html, new RegExp(`class="number-input__formatted-value">${displayed.replace('.', '\\.')}<`))
    assert.equal((html.match(/data-fixed-display="true"/g) ?? []).length, 1)
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
    const button = html.match(/<button\b([^>]*)>(?:(?!<\/button>).)*?加电<\/button>/s)
    assert.ok(button, "the apply-current button must be present")
    assert.equal(/\sdisabled(?:=|\s|$)/.test(button[1]), state !== "ready", state)
  }
})
