import { build } from '/Users/zh/Documents/test/ARP/tauri-ui/node_modules/esbuild/lib/main.js'
import { cp } from 'node:fs/promises'
const root = '/Users/zh/Documents/test/ARP/tauri-ui'
await cp(new URL('./fixture/', import.meta.url), `${root}/.automatic-control-qa-20261015`, {recursive:true})
for (const before of [true,false]) {
  await build({absWorkingDir:root,nodePaths:[`${root}/node_modules`],entryPoints:['.automatic-control-qa-20261015/fixture.tsx'],outfile:`.automatic-control-qa-20261015/${before?'before':'after'}.js`,bundle:true,format:'esm',platform:'browser',jsx:'automatic',tsconfig:'tsconfig.json',loader:{'.css':'empty'},define:{'process.env.NODE_ENV':'"production"','import.meta.env':'{"DEV":false,"PROD":true}'},plugins:before?[{name:'before-control',setup(build){build.onResolve({filter:/^@\/features\/shared\/controls$/},()=>({path:'/Users/zh/Documents/test/ARP/tauri-ui/.automatic-control-qa-20261015/before-controls.tsx'}))}}]:[]})
}
