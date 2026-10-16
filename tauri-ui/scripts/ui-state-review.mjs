/** Render real presentation components with inert callbacks. No native process or hardware. */
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { resolve, isAbsolute } from 'node:path';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const base = new URL(process.env.ARP_UI_QA_URL ?? 'http://127.0.0.1:1420/?demo=1');
if (!['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname) || base.searchParams.get('demo') !== '1') throw new Error('Only loopback browser demos are permitted.');
const out = resolve(process.env.ARP_UI_QA_OUTPUT ?? '../qa-evidence/review');
const fixtureDir = resolve('dist/__arp_ui_qa__');
await mkdir(fixtureDir, { recursive: true });
await mkdir(out, { recursive: true });
const index = await readFile('dist/index.html', 'utf8');
const styles = [...index.matchAll(/<link\b[^>]*rel="stylesheet"[^>]*>/g)].map(match => match[0]).join('\n');
if (!styles) throw new Error('Production stylesheet not found; do not validate unstyled fixtures.');
await writeFile(resolve(fixtureDir, 'index.html'), `<!doctype html><html lang="zh-CN" data-theme="dark"><head><meta charset="UTF-8">${styles}</head><body><div id="root"></div><script type="module" src="/__arp_ui_qa__/fixture.js"></script></body></html>`);
const entry = `
import {createElement as h, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {PowerProtectionDialog, AutomaticPauseDialog, ShutdownFailureDialog} from './src/features/automatic/lifecycle-dialogs.tsx';
import {PdPage} from './src/features/pd/page.tsx';
import {AutomaticMetricCard} from './src/features/shared/controls.tsx';
import {DEMO_SNAPSHOT} from './src/lib/demo-snapshot.ts';
const name=new URLSearchParams(location.search).get('fixture');
const message=Array.from({length:70},(_,i)=>'诊断 '+(i+1)+'：请检查仪器连接、实体电源面板与测试记录。 C:\\\\test-records\\\\long-device-id-01234567890123456789').join('\\n');
const noop=()=>undefined;
const snapshot=structuredClone(DEMO_SNAPSHOT);
snapshot.pd={devices:[],channels:[],ranges:[],points:[],...snapshot.pd,state:'running',status:'采集中：参数锁定；停止控制保持可用'};
function Fixture(){
 const [open,setOpen]=useState(true);const [value,setValue]=useState('9.999');
 window.__arpQaSetValue=setValue;
 const close=()=>setOpen(false);
 let child;
 if(name==='protection')child=h(PowerProtectionDialog,{message,onClose:close});
 if(name==='shutdown')child=h(ShutdownFailureDialog,{message,shutdownConfirmed:false,pending:false,onCancel:close,onRetry:noop,onForceExit:noop});
 if(name==='pause')child=h(AutomaticPauseDialog,{auto:{...snapshot.automaticTest,pauseKind:'device_error',pauseReason:message,pauseActions:['retry_point','safe_end'],pauseDeadlineAtUtc:null},integrationTimeUs:100,pending:false,onAction:noop});
 if(name==='pd-running')child=h('div',{className:'app-shell',style:{padding:16}},h(PdPage,{snapshot,active:true,pending:false,run:noop}));
 if(name==='readout')child=h('section',{className:'automatic-metric-grid',style:{padding:16,maxWidth:1000}},h(AutomaticMetricCard,{title:'功率',value,unit:'W',valueTone:'primary'}));
 return h('div',null,h('button',{id:'background-action',onClick:noop},'测试背景控件'),open?child:h('p',null,'已关闭'));
}
createRoot(document.getElementById('root')).render(h(Fixture));
`;
await build({stdin:{contents:entry,resolveDir:process.cwd(),loader:'jsx'},outfile:resolve(fixtureDir,'fixture.js'),bundle:true,format:'esm',platform:'browser',tsconfig:'tsconfig.json',loader:{'.css':'empty'},define:{'import.meta.env':'{"DEV":false,"PROD":true}'}});
const modulePath=process.env.ARP_PLAYWRIGHT_MODULE??'playwright';
const {chromium}=await import(isAbsolute(modulePath)?pathToFileURL(modulePath).href:modulePath);
const browser=await chromium.launch({headless:true});
const results={browserOnly:true,fixturesAreSynthetic:true,errors:[],checks:[],captures:[]};
const check=(name,passed,details)=>results.checks.push({name,passed:Boolean(passed),details});
try{
 for(const [width,height,dpr] of [[1920,1080,1],[1366,768,1],[960,540,2]]){
  const context=await browser.newContext({viewport:{width,height},deviceScaleFactor:dpr,locale:'zh-CN',reducedMotion:'reduce'});
  const page=await context.newPage();
  page.on('pageerror',error=>results.errors.push(String(error)));
  page.on('console',msg=>{if(msg.type()==='error')results.errors.push(msg.text());});
  for(const name of ['protection','shutdown','pause','pd-running','readout']){
   const url=new URL('/__arp_ui_qa__/index.html',base);url.searchParams.set('demo','1');url.searchParams.set('fixture',name);
   await page.goto(url.href,{waitUntil:'networkidle'});await page.waitForTimeout(100);await page.evaluate(()=>document.fonts.ready);
   const key=`${width}x${height}-dpr${dpr}-${name}`;
   const dialog=page.locator('[role="alertdialog"]');
   if(await dialog.count()){
    const box=await dialog.boundingBox();check(`${key}-dialog-bounded`,box && box.y>=0 && box.y+box.height<=height+1,box);
    const controls=dialog.locator('button:enabled').filter({visible:true});
    const footer=dialog.locator(':scope > :last-child');const footerBox=await footer.boundingBox();
    check(`${key}-footer-visible`,footerBox && footerBox.y>=0 && footerBox.y+footerBox.height<=height+1,footerBox);
    await controls.last().focus();await page.keyboard.press('Tab');check(`${key}-tab-trapped`,await dialog.evaluate(e=>e.contains(document.activeElement)));
    await controls.first().focus();await page.keyboard.press('Shift+Tab');check(`${key}-reverse-tab-trapped`,await dialog.evaluate(e=>e.contains(document.activeElement)));
    check(`${key}-background-inert`,await page.locator('#root').evaluate(e=>e.inert));
    if(name==='shutdown')check(`${key}-unsafe-exit-disabled`,await dialog.getByRole('button',{name:'确认并强制退出',exact:true}).isDisabled());
    if(name!=='shutdown'){await page.keyboard.press('Escape');check(`${key}-ack-required`,await dialog.isVisible());}
   }
   if(name==='pd-running'){
    const stop=page.getByRole('button',{name:'停止并保存',exact:true});check(`${key}-stop-enabled`,await stop.isEnabled());
    const rect=await stop.boundingBox();check(`${key}-stop-visible`,rect && rect.y>=0 && rect.y+rect.height<=height+1,rect);
    const enabledFields=await page.locator('.pd-settings-grid').evaluate(e=>e.querySelectorAll('input:enabled,button:enabled').length);
    check(`${key}-parameters-disabled`,enabledFields===0,{enabledFields});
   }
   if(name==='readout'){
    const number=page.locator('.automatic-metric-card__number');const unit=page.locator('.automatic-metric-card__unit');
    const before=await number.boundingBox();const unitBefore=await unit.boundingBox();
    for(const value of ['10.000','999.999','1000.000','--','-12.345']){
     await page.evaluate(v=>window.__arpQaSetValue(v),value);await page.waitForTimeout(30);
     const rect=await number.boundingBox();const unitAfter=await unit.boundingBox();
     check(`${key}-geometry-${value}`,before && rect && Math.abs((before.x+before.width/2)-(rect.x+rect.width/2))<1 && unitAfter && unitAfter.x>=rect.x+rect.width,{before,rect,unitBefore,unitAfter});
    }
    check(`${key}-reduced-motion-no-animation`,await number.evaluate(e=>e.getAnimations({subtree:true}).length===0));
   }
   const filename=`${key}.png`;await page.screenshot({path:resolve(out,filename)});results.captures.push(filename);
  }
  await context.close();
 }
}finally{await browser.close();await writeFile(resolve(out,'state-results.json'),JSON.stringify(results,null,2));}
console.log(JSON.stringify(results,null,2));
if(results.errors.length||results.checks.some(c=>!c.passed))process.exitCode=1;
