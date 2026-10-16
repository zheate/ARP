/** Production-CSS browser QA. Fake ports only; never imports Tauri's native host. */
import {build} from 'esbuild'
import {mkdir,readFile,readdir,writeFile,copyFile} from 'node:fs/promises'
import {resolve,isAbsolute} from 'node:path'
import {pathToFileURL} from 'node:url'
import assert from 'node:assert/strict'
const target=new URL(process.env.ARP_MOTION_QA_URL??'http://127.0.0.1:1438/?demo=1')
if(!['127.0.0.1','localhost','[::1]'].includes(target.hostname)||target.searchParams.get('demo')!=='1')throw Error('Only loopback demo QA is allowed')
if(!process.env.ARP_MOTION_BASELINE_CSS)throw Error('Provide ARP_MOTION_BASELINE_CSS from the pre-change build')
const modulePath=process.env.ARP_PLAYWRIGHT_MODULE??'playwright'
const {chromium}=await import(isAbsolute(modulePath)?pathToFileURL(modulePath).href:modulePath)
const out=resolve(process.env.ARP_MOTION_QA_OUTPUT??'../output/playwright/operation-motion');await mkdir(out,{recursive:true});await mkdir('dist/__motion-qa',{recursive:true})
await build({entryPoints:['scripts/fixtures/operation-motion.tsx'],outfile:'dist/__motion-qa/fixture.js',bundle:true,format:'esm',platform:'browser',tsconfig:'tsconfig.json',loader:{'.css':'empty'},define:{'process.env.NODE_ENV':'"production"'}})
const css=(await readdir('dist/assets')).find(n=>/^index-.*\.css$/.test(n))
await copyFile(process.env.ARP_MOTION_BASELINE_CSS,'dist/__motion-qa/baseline.css')
for(const mode of ['baseline','enabled'])await writeFile(`dist/__motion-qa/${mode}.html`,`<!doctype html><html class="dark" lang="zh-CN"><head><meta charset="utf-8"><link rel="icon" href="data:,"><link rel="stylesheet" href="${mode==='baseline'?'/__motion-qa/baseline.css':'/assets/'+css}"></head><body><div id="root"></div><script type="module" src="/__motion-qa/fixture.js"></script></body></html>`)
const report={browserOnly:true,hardwareConnected:false,checks:[],errors:[],replays:{},performance:[]}
const check=(name,value,detail)=>{report.checks.push({name,passed:!!value,detail});if(!value)throw Error(name+' '+JSON.stringify(detail))}
const browser=await chromium.launch({headless:true,...(process.env.ARP_CHROMIUM_EXECUTABLE?{executablePath:process.env.ARP_CHROMIUM_EXECUTABLE}:{})})
try{
 const page=await browser.newPage({viewport:{width:1440,height:900}});page.setDefaultTimeout(7000)
 page.on('pageerror',e=>report.errors.push(e.message))
 const go=async(mode)=>{await page.goto(new URL(`/__motion-qa/${mode}.html?demo=1`,target).href);await page.waitForFunction(()=>window.__motionQA);await page.evaluate(()=>document.fonts.ready)}
 const styles=async(selector)=>page.locator(selector).evaluate(e=>{let s=getComputedStyle(e);return{animation:s.animationName,duration:s.animationDuration,transition:s.transitionDuration,opacity:s.opacity,transform:s.transform}})
 const dist=values=>{let v=values.slice().sort((a,b)=>a-b);return{count:v.length,medianMs:v[Math.floor(v.length/2)],p95Ms:v[Math.min(v.length-1,Math.floor(v.length*.95))],maxMs:v.at(-1)}}
 for(const mode of ['baseline','enabled']){
  await go(mode)
  const start=page.getByRole('button',{name:'开始',exact:true}),stop=page.getByRole('button',{name:'停止',exact:true})
  check(mode+'-initial-disabled',await stop.isDisabled()&&await page.locator('#disabled-command').isDisabled())
  const responses=[];
  for(let i=0;i<4;i++)for(const button of [start,stop]){await page.waitForFunction(id=>!document.querySelector(id).disabled,button===start?'.automatic-test-start-btn':'.automatic-test-control-card button:last-child');const response=await button.evaluate(e=>{let n=window.__motionQA.commands.length,t=performance.now();e.click();return {ms:performance.now()-t,count:window.__motionQA.commands.length-n}});check(mode+'-callback-same-task-'+i+'-'+(button===start?'start':'stop'),response.count===1,response);responses.push(response.ms)}
  report.performance.push({mode,callback:dist(responses)});
  check(mode+'-command-order',await page.evaluate(()=>window.__motionQA.commands.map(c=>c.method).join(','))===Array(4).fill('automatic.start,automatic.end').join(','))
  if(mode==='enabled')check('command-feedback-plm-150ms',(await styles('.automatic-test-start-btn')).transition==='0.15s')
  for(const kind of ['powerSupply','powerMeter','spectrometer']){
   await page.locator('#open-'+kind).click();const dialog=page.locator('.device-settings-dialog');await dialog.waitFor()
   check(mode+'-'+kind+'-inert',await page.locator('#root').evaluate(e=>e.inert))
   await dialog.evaluate(e=>Promise.all(e.getAnimations().map(a=>a.finished)));
   if(kind==='powerMeter'){const identify=dialog.getByRole('button',{name:'识别',exact:true}).first();const before=await identify.boundingBox();await identify.click();await page.waitForFunction(()=>!document.querySelector('.device-detect-button').disabled);await identify.evaluate(e=>Promise.all(e.getAnimations().map(a=>a.finished)));const after=await identify.boundingBox();check(mode+'-identify-stable',before.width===after.width&&before.height===after.height&&before.x===after.x&&before.y===after.y,{before,after})}
   const combo=dialog.locator('[role="combobox"]:enabled').first();await combo.click();await dialog.locator('[role="listbox"]').waitFor()
   if(mode==='enabled')check(kind+'-menu-one-animation',(await styles('.native-select__list')).animation==='operation-menu-enter')
   await page.keyboard.press('Escape');check(mode+'-'+kind+'-escape-menu',await dialog.isVisible()&&await dialog.locator('[role="listbox"]').count()===0)
   await page.keyboard.press('Escape');await dialog.waitFor({state:'detached'});await page.waitForTimeout(25)
   check(mode+'-'+kind+'-focus-restored',await page.locator('#open-'+kind).evaluate(e=>e===document.activeElement))
  }
  for(let i=0;i<=100;i++){
   await page.evaluate(i=>window.__motionQA.advance(i),i)
   if(i%20===5){await page.locator('#open-powerMeter').click();await page.keyboard.press('Escape')}
   await page.evaluate(()=>new Promise(requestAnimationFrame))
  }
  await page.locator('#save-records').click()
  const result=await page.evaluate(()=>window.__motionQA.result());report.replays[mode]=result
  const timing=await page.evaluate(()=>window.__motionQA.timings.map(t=>t.renderMs));report.performance.push({mode,render:dist(timing)})
 }
 assert.deepEqual(report.replays.baseline,report.replays.enabled);check('raw-samples-revisions-verdict-saved-bytes-command-sequence-equal',true)
 await writeFile(resolve(out,'replay-baseline.json'),JSON.stringify(report.replays.baseline))
 await writeFile(resolve(out,'replay-enabled.json'),JSON.stringify(report.replays.enabled));delete report.replays
 for(const [width,height]of [[1920,1080],[1366,768],[960,540]])for(const theme of ['dark','light'])for(const reduced of [false,true]){
  await page.setViewportSize({width,height});await page.emulateMedia({reducedMotion:reduced?'reduce':'no-preference'});await go('enabled')
  await page.evaluate(theme=>{document.documentElement.classList.toggle('dark',theme==='dark');document.documentElement.dataset.theme=theme},theme)
  const key=`${width}x${height}-${theme}-${reduced?'reduced':'motion'}`
  const button=page.locator('#open-powerSupply');await button.focus();const before=await button.boundingBox();await page.keyboard.press('Enter');const dialog=page.locator('.device-settings-dialog');await dialog.waitFor()
  if(reduced)check(key+'-no-animation',(await styles('.device-settings-dialog')).animation==='none')
  else check(key+'-single-140ms',(await styles('.device-settings-dialog')).animation==='operation-dialog-enter'&&(await styles('.device-settings-dialog')).duration==='0.14s')
  await page.waitForTimeout(160)
  const box=await dialog.boundingBox();check(key+'-bounded',box.x>=0&&box.y>=0&&box.x+box.width<=width+1&&box.y+box.height<=height+1,box)
  const tabs=dialog.locator('button:enabled,input:enabled,[tabindex="0"]').filter({visible:true});await tabs.last().focus();await page.keyboard.press('Tab');check(key+'-focus-contained',await dialog.evaluate(e=>e.contains(document.activeElement)))
  await page.screenshot({path:resolve(out,key+'.png')});await page.keyboard.press('Escape');await dialog.waitFor({state:'detached'});await page.waitForTimeout(25)
  const after=await button.boundingBox();check(key+'-target-stable',before.x===after.x&&before.y===after.y&&before.width===after.width&&before.height===after.height)
  await page.locator('#radix-trigger').click();await page.getByRole('option',{name:'选项二',exact:true}).waitFor();await page.keyboard.press('Escape');await page.getByRole('option',{name:'选项二',exact:true}).waitFor({state:'detached'})
  await page.waitForFunction(()=>document.activeElement?.id==='radix-trigger');check(key+'-radix-focus-restored',await page.locator('#radix-trigger').evaluate(e=>e===document.activeElement))
  for(const safety of ['protection','shutdown']){await page.locator('#'+safety).click();const alert=page.getByRole('alertdialog');await alert.waitFor();const s=await styles('[role="alertdialog"]');check(key+'-'+safety+'-immediate',s.animation==='none'&&s.opacity==='1'&&s.transform==='none',s);if(safety==='protection')await alert.getByRole('button',{name:'确认并关闭',exact:true}).click();else await page.keyboard.press('Escape');await alert.waitFor({state:'detached'})}
 }
 // Actual App uses its original key/page/hook lifecycle in both stylesheet modes.
 const baselineCss=await readFile(process.env.ARP_MOTION_BASELINE_CSS,'utf8');let baselineNavigation=false;
 await page.route('**/assets/index-*.css',route=>baselineNavigation?route.fulfill({contentType:'text/css',body:baselineCss}):route.continue());
 for(const mode of ['baseline','enabled']){
  baselineNavigation=mode==='baseline';await page.emulateMedia({reducedMotion:'no-preference'});await page.setViewportSize({width:1440,height:900});await page.goto(target.href);await page.locator('.app-nav-item').last().waitFor();
  await page.getByRole('button',{name:'急停',exact:true}).click();await page.waitForFunction(()=>!document.querySelectorAll('.app-nav-item')[1].disabled);await page.keyboard.press('Escape');await page.waitForTimeout(40);
  const nav=page.locator('.app-nav-item:not(:disabled)');const latencies=[];
  for(let round=0;round<3;round++)for(let i=0;i<await nav.count();i++){
   const result=await nav.nth(i).evaluate(e=>{const start=performance.now();e.click();return new Promise(resolve=>requestAnimationFrame(()=>resolve({ms:performance.now()-start,current:e.getAttribute('aria-current')})))})
   check(`navigation-${mode}-${round}-${i}-current-next-frame`,result.current==='page',result);latencies.push(result.ms)
  }
  report.performance.push({mode:mode+'-actual-app-navigation',nextFrame:dist(latencies)})
 }
 check('no-waiting-page-entrance',await page.locator('.page-enter').evaluate(e=>[...e.children].every(c=>getComputedStyle(c).animationName==='none')))
 await page.screenshot({path:resolve(out,'actual-app-navigation.png')});check('no-browser-errors',report.errors.length===0,report.errors)
}finally{await browser.close();await writeFile(resolve(out,'results.json'),JSON.stringify(report,null,2))}
console.log(JSON.stringify({checks:report.checks.length,failed:report.checks.filter(c=>!c.passed),performance:report.performance,errors:report.errors},null,2))
