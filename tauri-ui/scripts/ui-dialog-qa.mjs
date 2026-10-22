/** Mount real frontend components with inert callbacks; no Tauri app or hardware. */
import { mkdir, readdir, writeFile } from 'node:fs/promises';
import { resolve, isAbsolute } from 'node:path';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const target = new URL(process.env.ARP_UI_QA_URL ?? 'http://127.0.0.1:1420/?demo=1');
if (!['127.0.0.1','localhost','[::1]'].includes(target.hostname) || target.searchParams.get('demo') !== '1') throw new Error('Only loopback browser QA is allowed.');
const modulePath = process.env.ARP_PLAYWRIGHT_MODULE ?? 'playwright';
const { chromium } = await import(isAbsolute(modulePath) ? pathToFileURL(modulePath).href : modulePath);
const out = resolve('../qa-evidence/dialogs');
await mkdir(out,{recursive:true}); await mkdir('dist/__qa',{recursive:true});
await build({
 stdin:{resolveDir:process.cwd(),loader:'tsx',contents:`
import {useState} from 'react'; import {createRoot} from 'react-dom/client';
import {PowerProtectionDialog,AutomaticPauseDialog,AutomaticCompletionDialog,ShutdownFailureDialog} from './src/features/automatic/lifecycle-dialogs';
import {AutomaticSpecificationsDialog,PowerMeterProfileDialog} from './src/features/devices/settings';
import {PdPage} from './src/features/pd/page';
import {ManualPage} from './src/features/manual/page';
import {SaveScriptDialog} from './src/components/save-script-dialog';
import {DEMO_SNAPSHOT} from './src/lib/demo-snapshot';
const kind=new URLSearchParams(location.search).get('fixture');
const snapshot=structuredClone(DEMO_SNAPSHOT);
snapshot.safety.manualControlsAllowed=true;
snapshot.devices.powerSupply={...snapshot.devices.powerSupply,connected:kind!=='manual-disconnected',outputCurrentA:kind==='manual-missing'?null:0.25,activeCurrentA:1.25};
snapshot.configuration.powerSupplyKind=kind?.startsWith('manual-ch341')?'ch341':'tdk';
// Deliberately distinguish the target, TDK output feedback and CH341 active current.
snapshot.configuration.setCurrentA=8;
if(kind==='manual-zero')snapshot.devices.powerSupply.outputCurrentA=0;
if(kind==='manual-ch341-zero')snapshot.devices.powerSupply.activeCurrentA=0;
if(kind==='manual-ch341-missing')snapshot.devices.powerSupply.activeCurrentA=null;
if(kind==='manual-ch341-disconnected')snapshot.devices.powerSupply.connected=false;
snapshot.pd={devices:['Dev1-Long-NI-DAQ-Device-Name-For-Layout-Validation'],channels:['ai0'],ranges:[{value:10,label:'±10 V'}],state:'running',status:'采集中 · 示例数据',currentValue:1.234,voltage:1.234,sampleCount:6000,mean:1.2,standardDeviation:0.03,rangeText:'1.1 / 1.3',points:Array.from({length:60},(_,i)=>({elapsedS:i,value:1.2+Math.sin(i/6)*0.1})),settings:{device:'Dev1-Long-NI-DAQ-Device-Name-For-Layout-Validation',channel:'ai0',terminal:'DIFF',range:10,sampleRateHz:1000,blockSize:100,scale:1,offset:0,unit:'V',save:true,outputDir:'C:/ARP/QA'}};
const longMessage=('仪器通信中断；请检查电源实体面板并核实数据保存位置。\\n').repeat(20);
const noAction=async()=>undefined;
function Harness(){const[open,setOpen]=useState(false);const close=()=>setOpen(false);
 const common={active:true,pending:false,snapshot,config:snapshot.configuration,update:()=>{},run:noAction,onClose:close};
 let content=null;
 if(open){
  if(kind==='protection')content=<PowerProtectionDialog message={longMessage} onClose={close}/>;
  if(kind==='shutdown'||kind==='shutdown-pending')content=<ShutdownFailureDialog message={longMessage} shutdownConfirmed={false} pending={kind==='shutdown-pending'} onCancel={close} onRetry={noAction} onForceExit={noAction}/>;
  if(kind==='pause')content=<AutomaticPauseDialog auto={{...snapshot.automaticTest,state:'paused',pauseKind:'device_error',pauseReason:longMessage,pauseActions:['retry_point','safe_end'],pauseDeadlineAtUtc:null}} integrationTimeUs={10000} pending={false} onAction={noAction}/>;
  if(kind==='completion')content=<AutomaticCompletionDialog title="测试已停止，但下电状态未确认" message={longMessage} onClose={close} outcome="shutdown_unconfirmed" shutdown={{confirmed:false,reason:'无法读取电源状态'}} totalPoints={20} completedPoints={3} sn="QA-LONG-SERIAL-0000000001" exportStatus="failed" exportError="磁盘只读，Excel 未生成" journalPath="" canNextDevice={false} canRetryExport={true} onNextDevice={noAction} onRetryExport={noAction} onChooseExportDirectory={noAction}/>;
  if(kind==='specifications')content=<AutomaticSpecificationsDialog {...common} onSave={noAction}/>;
  if(kind==='profiles')content=<PowerMeterProfileDialog {...common} open/>;
  if(kind==='save-error'||kind==='save-pending')content=<SaveScriptDialog onClose={close} onSave={async()=>{if(kind==='save-pending')await new Promise(()=>{});throw Error('磁盘只读，脚本尚未保存')}}/>;
 }
 return <><button id="launcher" onClick={()=>setOpen(true)}>打开验证弹窗</button><button id="background">背景操作</button>{kind==='pd-running'?<PdPage snapshot={snapshot} active pending={false} run={noAction}/>:null}{kind?.startsWith('manual')?<ManualPage {...common}/>:null}{content}</>
}
createRoot(document.getElementById('root')).render(<Harness/>);`},
 bundle:true,format:'esm',platform:'browser',outfile:'dist/__qa/fixture.js',tsconfig:'tsconfig.json',loader:{'.css':'empty'},define:{'process.env.NODE_ENV':'"production"'},
});
const styles=(await readdir('dist/assets')).filter(name=>name.endsWith('.css')).map(name=>`<link rel="stylesheet" href="/assets/${name}">`).join('');
await writeFile('dist/__qa/index.html',`<!doctype html><html class="dark" data-theme="dark" lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${styles}</head><body><div id="root"></div><script type="module" src="/__qa/fixture.js"></script></body></html>`);
const results={browserOnly:true,hardwareConnected:false,nativeWindowsVerified:false,checks:[],errors:[],captures:[]};
const check=(name,passed,detail)=>results.checks.push({name,passed:Boolean(passed),detail});
const browser=await chromium.launch({headless:true});
try{
 for(const [width,height,dpr] of [[1920,1080,1],[1366,768,1],[960,540,2]]){
  const context=await browser.newContext({viewport:{width,height},deviceScaleFactor:dpr,locale:'zh-CN'});
  const page=await context.newPage();const key=`${width}x${height}-dpr${dpr}`;
  page.on('pageerror',error=>results.errors.push({key,message:error.message}));
  page.on('console',message=>{if(message.type()==='error')results.errors.push({key,message:message.text()});});
  for(const fixture of ['protection','shutdown','shutdown-pending','pause','completion','specifications','profiles','save-error','save-pending','pd-running','manual','manual-missing','manual-disconnected','manual-ch341','manual-zero','manual-ch341-zero','manual-ch341-missing','manual-ch341-disconnected']){
   const url=new URL(`/__qa/index.html?fixture=${fixture}`,target);
   await page.goto(url.href,{waitUntil:'networkidle'});
   const name=`${key}-${fixture}`;
   if(fixture==='pd-running'){
    check(`${name}-settings-disabled`,await page.locator('fieldset[aria-label="PD 采集参数"] input').evaluateAll(es=>es.length>0&&es.every(e=>e.matches(':disabled'))));
    const stop=page.getByRole('button',{name:'停止并保存'});const r=await stop.boundingBox();
    check(`${name}-stop-visible-enabled`,await stop.isEnabled()&&r.y>=0&&r.y+r.height<=height,r);
    check(`${name}-chart-present`,await page.locator('canvas').count()>0);
   }else if(fixture.startsWith('manual')){
    const expected={manual:'0.25','manual-ch341':'1.25','manual-zero':'0.00','manual-ch341-zero':'0.00','manual-missing':'--','manual-disconnected':'--','manual-ch341-missing':'--','manual-ch341-disconnected':'--'}[fixture];
    const current=page.locator('.automatic-metric-card__current');
    await current.scrollIntoViewIfNeeded();
    // NumericReadout exposes the unpadded semantic value. NumberFlow renders
    // animated digits in a custom element; the unit is a separate visible span.
    const reading=await current.evaluate(e=>{
     const values=e.querySelectorAll('.numeric-readout');const units=e.querySelectorAll('.power-chart-readout__current-unit');
     const number=values[0];const unit=units[0];const rendered=number?.firstElementChild;const digitBounds=rendered?.getBoundingClientRect();const r=e.getBoundingClientRect();const n=number?.getBoundingClientRect();const u=unit?.getBoundingClientRect();
     const shown=el=>el&&el.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
     return {valueCount:values.length,unitCount:units.length,value:number?.getAttribute('aria-label'),unit:unit?.textContent.trim(),placeholder:number?.textContent.trim(),visible:shown(e)&&shown(number)&&shown(rendered)&&shown(unit)&&digitBounds.width>0&&digitBounds.height>0,inViewport:r.x>=0&&r.y>=0&&r.right<=innerWidth+1&&r.bottom<=innerHeight+1,fits:n&&u&&n.width>0&&n.height>0&&u.width>0&&u.height>0&&n.x>=r.x-1&&u.x>=n.right&&u.right<=r.right+1&&e.scrollWidth<=e.clientWidth+1};
    });
    check(`${name}-readback-not-setpoint`,reading.valueCount===1 && reading.value===expected && reading.value!=='8.00' && (expected!=='--'||reading.placeholder==='--'),{expected,...reading});
    check(`${name}-current-unit`,reading.unitCount===1 && reading.unit==='A',reading);
    check(`${name}-current-visible-without-overflow`,reading.visible && reading.inViewport && reading.fits,reading);
   }else{
    await page.locator('#launcher').click();const dialog=page.locator('[role="dialog"],[role="alertdialog"]').last();await dialog.waitFor();await page.waitForTimeout(200);
    const rect=await dialog.boundingBox();check(`${name}-dialog-bounded`,rect.y>=0&&rect.x>=0&&rect.y+rect.height<=height+1&&rect.x+rect.width<=width+1,rect);
    check(`${name}-background-inert`,await page.locator('#root').evaluate(e=>e.inert));
    const enabled=dialog.locator('button:enabled,input:enabled').filter({visible:true});
    if(await enabled.count()){
     await enabled.last().focus();await page.keyboard.press('Tab');check(`${name}-tab-wrap`,await enabled.first().evaluate(e=>e===document.activeElement));
     await enabled.first().focus();await page.keyboard.press('Shift+Tab');check(`${name}-reverse-tab-wrap`,await enabled.last().evaluate(e=>e===document.activeElement));
    }else{await page.keyboard.press('Tab');check(`${name}-pending-focus-contained`,await dialog.evaluate(e=>e.contains(document.activeElement)));}
    const footer=(await dialog.locator('.device-settings-dialog__footer').count())>0?dialog.locator('.device-settings-dialog__footer'):dialog.locator(':scope > div').last();
    const f=await footer.boundingBox();check(`${name}-footer-visible`,f&&f.y>=0&&f.y+f.height<=height+1,f);
    if(fixture==='shutdown'){
     const force=page.getByRole('button',{name:'确认并强制退出'});check(`${name}-force-guarded`,await force.isDisabled());
     await dialog.getByRole('checkbox').check();check(`${name}-explicit-ack-enables-force`,await force.isEnabled());
    }
    if(fixture==='shutdown-pending')check(`${name}-all-actions-disabled`,await dialog.locator('button').evaluateAll(es=>es.every(e=>e.disabled)));
    if(fixture==='protection')check(`${name}-no-unverified-safe-claim`,!(await dialog.innerText()).includes('设备已进入断电保护状态'));
    if(fixture==='completion')check(`${name}-no-unverified-disk-claim`,!(await dialog.innerText()).includes('本地 JSONL 已保存'));
    if(fixture==='pause'){await page.keyboard.press('Escape');check(`${name}-escape-cannot-dismiss`,await dialog.isVisible());}
    if(fixture.startsWith('save-')){
     await dialog.getByRole('textbox').fill('UI QA');await dialog.getByRole('button',{name:'保存脚本'}).click();
     if(fixture==='save-error'){await dialog.getByRole('alert').waitFor();check(`${name}-error-preserves-dialog`,await dialog.isVisible()&&await dialog.getByRole('button',{name:'保存脚本'}).isEnabled());}
     else{check(`${name}-pending-lock`,await dialog.getByRole('textbox').isDisabled());await page.keyboard.press('Escape');check(`${name}-pending-escape-ignored`,await dialog.isVisible());}
    }
   }
   await page.screenshot({path:resolve(out,`${name}.png`)});results.captures.push(name);
   check(`${name}-no-document-overflow`,await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   await writeFile(resolve(out,'results.json'),JSON.stringify(results,null,2));
  }
  await context.close();
 }
}finally{await browser.close();await writeFile(resolve(out,'results.json'),JSON.stringify(results,null,2));}
console.log(JSON.stringify({captures:results.captures.length,checks:results.checks.length,errors:results.errors,failures:results.checks.filter(c=>!c.passed)},null,2));
if(results.errors.length||results.checks.some(c=>!c.passed))process.exitCode=1;
