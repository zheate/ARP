// Real component fixtures, compiled only into a disposable QA build. No Tauri App,
// native IPC, acquisition, file export or real safety command is started here.
import { readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { resolve, isAbsolute } from 'node:path';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const url = new URL(process.env.ARP_UI_QA_URL ?? 'http://127.0.0.1:1420/?demo=1');
if (!['127.0.0.1','localhost','[::1]'].includes(url.hostname) || url.searchParams.get('demo') !== '1') throw new Error('Browser demo only.');
const out = resolve(process.env.ARP_DIALOG_QA_OUTPUT ?? '../qa-evidence/dialogs');
await mkdir(out,{recursive:true});
const names = ['PowerProtectionDialog','AutomaticPauseDialog','AutomaticCompletionDialog','ShutdownFailureDialog','PowerMeterProfileDialog'];
const fixture = `
import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {${names.join(',')}} from './src/App';
import {SaveScriptDialog} from './src/components/save-script-dialog';
import {DEMO_SNAPSHOT} from './src/lib/demo-snapshot';
const kind=new URLSearchParams(location.search).get('case');
const long='模拟通信异常：'+('长设备名-976nm-LD-DAQ-0123456789 '.repeat(8)+'\\n').repeat(8);
window.__qaCalls=[];
function Harness(){
 const [open,setOpen]=useState(false);
 const close=()=>setOpen(false);
 const record=(name)=>{window.__qaCalls.push(name);};
 const snapshot=structuredClone(DEMO_SNAPSHOT);
 let dialog=null;
 if(kind==='protection')dialog=<PowerProtectionDialog message={long} onClose={close}/>;
 if(kind==='pause')dialog=<AutomaticPauseDialog auto={{...snapshot.automaticTest,state:'paused',pauseKind:'spectrum_saturated',pauseReason:long,pauseActions:['adjust_spectrum','retry_point','safe_end']}} integrationTimeUs={10000} pending={false} onAction={record}/>;
 if(kind==='completion')dialog=<AutomaticCompletionDialog title='测试已停止，但下电状态未确认' message={long} onClose={close} outcome='shutdown_unconfirmed' shutdown={{confirmed:false}} totalPoints={6} completedPoints={3} sn={'ARP-'+('976-LD-'.repeat(20))} exportStatus='failed' exportError={long} canNextDevice={false} canRetryExport={true} onNextDevice={()=>record('next')} onRetryExport={()=>record('retry-export')} onChooseExportDirectory={()=>record('directory')}/>;
 if(kind==='shutdown'||kind==='shutdown-pending')dialog=<ShutdownFailureDialog message={long} shutdownConfirmed={false} pending={kind==='shutdown-pending'} onCancel={close} onRetry={()=>record('retry')} onForceExit={()=>record('force')}/>;
 if(kind==='profile')dialog=<PowerMeterProfileDialog open onClose={close} snapshot={snapshot} config={snapshot.configuration} active pending={false} run={async(method)=>record(method)}/>;
 if(kind==='save-script')dialog=<SaveScriptDialog onClose={close} onSave={async()=>{record('save');await new Promise(r=>setTimeout(r,500));throw new Error('模拟保存失败：目录只读');}}/>;
 return <><button id='launcher' onClick={()=>setOpen(true)}>打开测试弹窗</button><button onClick={()=>record('background')}>后台操作</button>{open&&dialog}</>;
}
createRoot(document.getElementById('root')).render(<Harness/>);
`;
const bundle=resolve('dist/arp-dialog-fixture.js'), html=resolve('dist/arp-dialog-fixture.html');
await build({stdin:{contents:fixture,resolveDir:process.cwd(),loader:'tsx'},tsconfig:'tsconfig.json',bundle:true,format:'esm',platform:'browser',outfile:bundle,loader:{'.css':'empty'},define:{'process.env.NODE_ENV':'"production"','import.meta.env.DEV':'false'},plugins:[{name:'expose-dialogs-in-test-only',setup(b){b.onLoad({filter:/App\.tsx$/},async({path})=>({contents:(await readFile(path,'utf8'))+'\nexport { '+names.join(',')+' };',loader:'tsx'}));}}]});
const index=await readFile('dist/index.html','utf8');
const styles=[...index.matchAll(/<link\b[^>]*rel="stylesheet"[^>]*>/g)].map(m=>m[0]).join('\n');
if(!styles)throw new Error('Production CSS link missing');
await writeFile(html,'<!doctype html><html lang="zh-CN" data-theme="dark"><head><meta charset="utf-8">'+styles+'</head><body><div id="root"></div><script type="module" src="/arp-dialog-fixture.js"></script></body></html>');
const modulePath=process.env.ARP_PLAYWRIGHT_MODULE??'playwright';
const {chromium}=await import(isAbsolute(modulePath)?pathToFileURL(modulePath).href:modulePath);
const browser=await chromium.launch({headless:true});
const result={browserOnly:true,nativeWindowsVerified:false,checks:[],errors:[],captures:[]};
const check=(name,passed,details)=>result.checks.push({name,passed:Boolean(passed),details});
try{
 for(const [width,height,dpr] of [[1920,1080,1],[1366,768,1],[960,540,2]]){
  const context=await browser.newContext({viewport:{width,height},deviceScaleFactor:dpr,locale:'zh-CN'});
  const page=await context.newPage();
  page.on('pageerror',e=>result.errors.push(e.message));
  page.on('console',e=>{if(e.type()==='error')result.errors.push(e.text());});
  for(const kind of ['protection','pause','completion','shutdown','shutdown-pending','profile','save-script']){
   const key=width+'x'+height+'-dpr'+dpr+'-'+kind;
   await page.goto(new URL('/arp-dialog-fixture.html?case='+kind,url).href,{waitUntil:'networkidle'});
   await page.locator('#launcher').click();
   const dialog=page.locator('[role="dialog"],[role="alertdialog"]').last();await dialog.waitFor();await page.waitForTimeout(250);await page.evaluate(()=>document.fonts.ready);
   const bounds=await dialog.boundingBox();
   check(key+'-bounds',bounds&&bounds.x>=-1&&bounds.y>=-1&&bounds.x+bounds.width<=width+1&&bounds.y+bounds.height<=height+1,bounds);
   const controls=dialog.locator('button:enabled:not([tabindex="-1"]),input:enabled:not([tabindex="-1"])').filter({visible:true});
   if(await controls.count()){
    await controls.last().focus();await page.keyboard.press('Tab');check(key+'-tab-wrap',await controls.first().evaluate(e=>e===document.activeElement));
    await controls.first().focus();await page.keyboard.press('Shift+Tab');check(key+'-reverse-wrap',await controls.last().evaluate(e=>e===document.activeElement));
   }
   check(key+'-inert-background',await page.locator('#root').evaluate(e=>e.inert));
   const footer=dialog.locator(':scope > div').last();const fb=await footer.boundingBox();
   check(key+'-footer-visible',fb&&fb.y>=0&&fb.y+fb.height<=height+1,fb);
   if(kind==='protection'){
    check(key+'-no-unverified-shutdown-claim',!(await dialog.innerText()).includes('设备已进入断电保护状态'));
    await page.keyboard.press('Escape');check(key+'-explicit-ack-required',await dialog.isVisible());
   }
   if(kind==='pause'){await page.keyboard.press('Escape');check(key+'-pause-not-dismissed',await dialog.isVisible());}
   if(kind==='shutdown'){
    const force=dialog.getByRole('button',{name:'确认并强制退出',exact:true});check(key+'-force-disabled',await force.isDisabled());
    await dialog.getByRole('checkbox').check();check(key+'-physical-confirmation-unlocks',await force.isEnabled());
   }
   if(kind==='shutdown-pending')check(key+'-pending-controls-disabled',await controls.count()===0);
   if(kind==='save-script'){
    await dialog.getByRole('button',{name:'保存脚本',exact:true}).click();check(key+'-empty-validation',await dialog.getByRole('alert').innerText()==='请输入脚本名称');
    await dialog.getByLabel('脚本名称',{exact:true}).fill('QA test');await dialog.getByRole('button',{name:'保存脚本',exact:true}).click();
    check(key+'-saving-disabled',await dialog.getByRole('button',{name:'保存中…',exact:true}).isDisabled());
    await page.keyboard.press('Escape');check(key+'-cannot-close-while-saving',await dialog.isVisible());
    await dialog.getByRole('alert').filter({hasText:'模拟保存失败'}).waitFor();
    check(key+'-save-error-retains-input',await dialog.getByLabel('脚本名称',{exact:true}).inputValue()==='QA test');
   }
   check(key+'-no-background-action',!(await page.evaluate(()=>window.__qaCalls)).includes('background'));
   await page.screenshot({path:resolve(out,key+'.png')});result.captures.push(key);
   await writeFile(resolve(out,'results.json'),JSON.stringify(result,null,2));
  }
  await context.close();
 }
}finally{
 await browser.close();await rm(bundle,{force:true});await rm(html,{force:true});await writeFile(resolve(out,'results.json'),JSON.stringify(result,null,2));
}
console.log(JSON.stringify({captures:result.captures.length,checks:result.checks.length,failures:result.checks.filter(c=>!c.passed),errors:result.errors},null,2));
if(result.errors.length||result.checks.some(c=>!c.passed))process.exitCode=1;
