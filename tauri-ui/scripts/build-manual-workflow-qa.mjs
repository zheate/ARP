import { build } from "esbuild"
import { readFile } from "node:fs/promises"
import path from "node:path"

const out = path.resolve("../output/playwright/manual-workflow-20261020")
const frozen = new Map([
  [path.resolve("src/App.tsx"), "App.tsx"],
  [path.resolve("src/features/manual/page.tsx"), "manual-page.tsx"],
  [path.resolve("src/features/charts/realtime.tsx"), "realtime.tsx"],
])
for (const mode of ["baseline", "fixed"]) {
  await build({
    entryPoints: ["scripts/fixtures/manual-workflow.tsx"], outfile: `${out}/${mode}.js`,
    bundle: true, format: "esm", platform: "browser", tsconfig: "tsconfig.json",
    define: { "process.env.NODE_ENV": '"production"' }, loader: { ".woff2": "file" },
    plugins: [{ name: "isolated-workflow-ports", setup(builder) {
      builder.onResolve({ filter: /hooks\/use-backend-snapshot$/ }, () => ({ path: path.resolve("scripts/fixtures/manual-workflow-backend.ts") }))
      if (mode === "baseline") builder.onLoad({ filter: /\.tsx$/ }, async args => {
        const source = frozen.get(args.path)
        if (source) return { contents: await readFile(`${out}/baseline/${source}`, "utf8"), loader: "tsx", resolveDir: path.dirname(args.path) }
      })
    } }],
  })
}
