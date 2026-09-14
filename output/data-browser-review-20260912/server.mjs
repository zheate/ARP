import { createServer } from '../../tauri-ui/node_modules/vite/dist/node/index.js';
import { readFile } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
const execute = promisify(execFile);
const root = '/Users/zh/Documents/test/ARP';
const folder = `${root}/output/data-browser-review-20260912`;
const server = await createServer({
 root: `${root}/tauri-ui`,
 configFile: `${root}/tauri-ui/vite.config.ts`,
 server: {host:'127.0.0.1', port:1436, strictPort:true, fs:{allow:[root]}},
 plugins:[{name:'local-data-browser-review',configureServer(vite){
  vite.middlewares.use(async(req,res,next)=>{
   if(req.url === '/__audit.html'){
    res.setHeader('Content-Type','text/html');
    res.end(await vite.transformIndexHtml(req.url, await readFile(`${folder}/audit.html`,'utf8')));return;
   }
   if(req.url === '/__data' && req.method === 'POST'){
    try {
     let body='';for await(const c of req)body+=c;
     const {stdout}=await execute('/Users/zh/miniconda3/envs/sth_eb314/bin/python',['-c',`import json,sys\nfrom combined_test.data_browser import list_data_records,inspect_data_record\nr=json.loads(sys.argv[1])\nf={'dataBrowser.list':list_data_records,'dataBrowser.inspect':inspect_data_record}[r['method']]\nprint(json.dumps(f(r['params']),ensure_ascii=False))`,body],{cwd:root,maxBuffer:4*1024*1024});
     res.setHeader('Content-Type','application/json');res.end(stdout);
    }catch(e){res.statusCode=400;res.end(JSON.stringify({error:e.stderr||e.message}));}return;
   }
   next();
  });
 }}]
});
await server.listen();server.printUrls();
