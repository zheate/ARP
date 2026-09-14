import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { createRequire } from "node:module"
import { fileURLToPath } from "node:url"
import test from "node:test"
import { createElement as h } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { build } from "esbuild"

const root = fileURLToPath(new URL("..", import.meta.url))
const result = await build({
  stdin: { contents: 'export { Field } from "./src/components/field"; export { Input } from "./src/components/ui/input"; export { NativeSelect } from "./src/components/native-select"; export { PdPage } from "./src/App"; export { DEMO_SNAPSHOT } from "./src/lib/demo-snapshot";', resolveDir: root },
  tsconfig: `${root}/tsconfig.json`, bundle: true, write: false, platform: "node", format: "cjs",
  loader: { ".css": "empty" }, external: ["react", "react-dom", "react-dom/*"],
  plugins: [{ name: "expose-pd-for-render-test", setup(builder) {
    builder.onLoad({ filter: /App\.tsx$/ }, async ({ path }) => ({ contents: `${await readFile(path, "utf8")}\nexport { PdPage };`, loader: "tsx" }))
  } }],
})
const module = { exports: {} }
new Function("require", "module", "exports", result.outputFiles[0].text)(createRequire(import.meta.url), module, module.exports)
const { Field, Input, NativeSelect, PdPage, DEMO_SNAPSHOT } = module.exports
const noop = () => {}

test("composite form labels reach inputs and local comboboxes without duplicate IDs", () => {
  for (const control of [h(Input), h(NativeSelect, { value: "dev", onChange: noop }, h("option", { value: "dev" }, "仪器"))]) {
    const html = renderToStaticMarkup(h(Field, { label: "控制器" }, control))
    const label = html.match(/<label[^>]*id="([^"]+)"/)
    assert.ok(label)
    assert.ok(html.includes(`aria-labelledby="${label[1]}"`))
    assert.equal((html.match(new RegExp(`id="${label[1]}"`, "g")) ?? []).length, 1)
  }
})
test("NativeSelect supports its existing ariaLabel and standard aria-label callers", () => {
  for (const props of [{ ariaLabel: "旧标签" }, { "aria-label": "脚本选择" }]) {
    const html = renderToStaticMarkup(h(NativeSelect, { ...props, value: "standard", onChange: noop }, h("option", { value: "standard" }, "标准测试")))
    assert.ok(html.includes(`aria-label="${Object.values(props)[0]}"`))
  }
})
test("PD acquisition parameters are locked during a run but stop remains outside that fieldset", () => {
  const snapshot = structuredClone(DEMO_SNAPSHOT)
  snapshot.pd = { devices: [], channels: [], ranges: [], points: [], ...snapshot.pd, state: "running" }
  const html = renderToStaticMarkup(h(PdPage, { snapshot, active: true, pending: false, run: noop }))
  const fieldset = html.search(/<fieldset\b[^>]*\bdisabled=""/)
  assert.ok(fieldset > 0)
  const stop = html.match(/<button[^>]*>[^<]*(?:<svg[\s\S]*?<\/svg>)?停止并保存<\/button>/)
  assert.ok(stop, "existing stop action must remain rendered")
  assert.ok(!stop[0].includes('disabled=""'))
  assert.ok(html.indexOf(stop[0]) < fieldset)
})
