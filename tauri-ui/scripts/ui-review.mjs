import { mkdir, writeFile } from 'node:fs/promises';
import { resolve, isAbsolute } from 'node:path';
import { pathToFileURL } from 'node:url';

const url = new URL(process.env.ARP_UI_QA_URL ?? 'http://127.0.0.1:1420/?demo=1');
if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || url.searchParams.get('demo') !== '1') throw new Error('Only loopback browser demos are permitted.');
const modulePath = process.env.ARP_PLAYWRIGHT_MODULE ?? 'playwright';
const { chromium } = await import(isAbsolute(modulePath) ? pathToFileURL(modulePath).href : modulePath);
const out = resolve(process.env.ARP_UI_QA_OUTPUT ?? '../qa-evidence/review');
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true });
const results = { browserOnly: true, nativeWindowsVerified: false, captures: [], interactions: [], errors: [] };
const configurations = [[1920,1080,1],[1366,768,1],[2560,1440,1],[1536,864,1.25],[1280,720,1.5],[960,540,2]];
const pages = [['automatic','自动测试'],['manual','手动配置'],['pd','PD 采集'],['data','数据浏览'],['shipping','出货报告'],['settings','系统设置']];
const check = (name, passed, details) => results.interactions.push({name, passed: Boolean(passed), ...(details === undefined ? {} : {details})});
async function capture(page, name) {
  await page.evaluate(() => document.fonts.ready);
  const geometry = await page.evaluate(() => {
    const rect = e => { const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}; };
    const shown = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
    const description = e => ({text:(e.getAttribute('aria-label')||e.textContent||'').trim().slice(0,150),rect:rect(e)});
    const dialogs = [...document.querySelectorAll('[role="dialog"],[role="alertdialog"]')].filter(shown);
    return { viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio}, overflow:document.documentElement.scrollWidth>innerWidth+1,
      dialogs:dialogs.map(description), charts:[...document.querySelectorAll('canvas')].filter(shown).map(description),
      readouts:[...document.querySelectorAll('.automatic-metric-card__value')].map(e=>({...description(e),overflow:e.scrollWidth>e.clientWidth+1})),
      controls:[...document.querySelectorAll('button,input,[role="combobox"]')].filter(shown).map(e=>({...description(e),disabled:e.matches(':disabled'),name:e.getAttribute('aria-label'),title:e.getAttribute('title')})),
      text:document.body.innerText };
  });
  await page.screenshot({ path:resolve(out,`${name}.png`) });
  await writeFile(resolve(out,`${name}.html`),await page.content());
  results.captures.push({name,geometry});
  for(const dialog of geometry.dialogs) check(`${name}-dialog-in-viewport`,dialog.rect.y>=-1 && dialog.rect.bottom<=geometry.viewport.height+1 && dialog.rect.x>=-1 && dialog.rect.right<=geometry.viewport.width+1,dialog.rect);
  return geometry;
}
async function checkFocus(page, dialog, name) {
  const enabled=dialog.locator('button:enabled:not([tabindex="-1"]),input:enabled:not([tabindex="-1"])').filter({visible:true});
  await enabled.last().focus();await page.keyboard.press('Tab');
  check(`${name}-tab-contained`,await dialog.evaluate(e=>e.contains(document.activeElement)));
  await enabled.first().focus();await page.keyboard.press('Shift+Tab');
  check(`${name}-shift-tab-contained`,await dialog.evaluate(e=>e.contains(document.activeElement)));
  check(`${name}-background-inert`,await page.locator('#root').evaluate(e=>e.inert));
  const focus=await page.evaluate(()=>{const e=document.activeElement;const s=getComputedStyle(e);return {visible:e.matches(':focus-visible'),outlineWidth:s.outlineWidth,outlineStyle:s.outlineStyle,boxShadow:s.boxShadow};});
  check(`${name}-visible-focus`,focus.visible && (focus.outlineStyle!=='none' && parseFloat(focus.outlineWidth)>0 || focus.boxShadow!=='none'),focus);
}
try {
 for (const [width,height,dpr] of configurations) {
  const context = await browser.newContext({viewport:{width,height},deviceScaleFactor:dpr,locale:'zh-CN',colorScheme:'dark'});
  const page=await context.newPage();
  page.on('pageerror', e=>results.errors.push({width,height,dpr,type:'pageerror',message:e.message}));
  page.on('console',e=>{if(e.type()==='error')results.errors.push({width,height,dpr,type:'console',message:e.text()});});
  const idle=new URL(url);idle.searchParams.set('run','1');
  await page.goto(idle.href,{waitUntil:'networkidle'});
  const key=`${width}x${height}-dpr${dpr}`;
  for(const [id,label] of pages){
   await page.getByRole('button',{name:label,exact:true}).click();
   await page.waitForTimeout(250);
   await capture(page,`${key}-${id}-idle`);
   if(id==='pd'){
    const action=await page.locator('.pd-acquisition-actions').boundingBox();
    check(`${key}-pd-action-visible`,action && action.y>=0 && action.y+action.height<=height,action);
    const fields=await page.locator('.pd-settings-grid .pd-inline-field').evaluateAll(elements=>elements.map(e=>{
      const r=e.getBoundingClientRect();const label=e.querySelector('label');const lr=label?.getBoundingClientRect();
      return {label:label?.textContent,x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,labelRight:lr?.right};
    }));
    const overlap=fields.some((a,i)=>fields.some((b,j)=>j>i && Math.min(a.right,b.right)-Math.max(a.x,b.x)>1 && Math.min(a.bottom,b.bottom)-Math.max(a.y,b.y)>1));
    check(`${key}-pd-fields-fit-without-overlap`,fields.length>=6 && fields.every(f=>f.width>=160 && f.labelRight<=f.right+1) && !overlap,fields);
   }
  }
  // The unchanged mid-run fixture deliberately locks manual/report navigation.
  await page.goto(url.href,{waitUntil:'networkidle'});
  const live=await capture(page,`${key}-automatic-running`);
  check(`${key}-running-lock`,await page.getByRole('button',{name:'手动配置',exact:true}).isDisabled() && await page.getByRole('button',{name:'出货报告',exact:true}).isDisabled());
  check(`${key}-readouts-no-overflow`,live.readouts.every(r=>!r.overflow),live.readouts);
  if(dpr===1){
   const spectrum=live.charts.find(c=>c.text==='实时光谱曲线');
   check(`${key}-spectrum-visible`,spectrum && spectrum.rect.y>=0 && spectrum.rect.bottom<=height+1 && spectrum.rect.height>=(height>=1000?320:180),spectrum?.rect);
  }
  // Synthetic DOM text stress tests geometry only, never backend measurement data.
  // Preserve the page fixture's original value range. Thousand-unit transitions
  // are exercised through the real React readout in ui-state-review.mjs.
  const stress=await page.evaluate(()=>[...document.querySelectorAll('.automatic-metric-card__number')].map(e=>{
    const readout=e.querySelector('.numeric-readout');if(!readout)return {passed:false};
    const original=[...readout.childNodes];const container=e.parentElement;const unit=container.querySelector('.automatic-metric-card__unit');const initial=container.getBoundingClientRect();const samples=[];
    // The power chart reserves a right-aligned number column; spectrum sidebar
    // metrics start at the left edge. Standalone metric cards remain centered.
    const alignment=e.closest('.automatic-power-chart__reading')?'right':e.closest('.spectrum-metric-list')?'left':'center';
    try{
     for(const value of ['9.999','10.000','999.999','--','-12.345']){
      readout.textContent=value;const r=e.getBoundingClientRect();const bounds=container.getBoundingClientRect();const u=unit?.getBoundingClientRect();
      const range=document.createRange();range.selectNodeContents(readout);const ink=range.getBoundingClientRect();
      const aligned=alignment==='right'?Math.abs(ink.right-r.right)<1:alignment==='left'?Math.abs(ink.x-bounds.x)<1:Math.abs((ink.x+ink.width/2)-(bounds.x+bounds.width/2))<1;
      samples.push({value,alignment,number:{x:r.x,right:r.right},ink:{x:ink.x,right:ink.right},container:{x:bounds.x,right:bounds.right},unit:u&&{x:u.x,right:u.right},passed:aligned && Math.abs(bounds.x-initial.x)<1 && Math.abs(bounds.width-initial.width)<1 && ink.width>0 && ink.height>0 && r.height>0 && r.x>=bounds.x-1 && ink.x>=r.x-1 && ink.right<=r.right+1 && u && u.width>0 && u.height>0 && u.x>=r.right && u.right<=bounds.right+1 && container.scrollWidth<=container.clientWidth+1});
     }
    }finally{readout.replaceChildren(...original);}
    return {passed:samples.every(s=>s.passed),samples};
  }));
  check(`${key}-synthetic-numeric-layout`,stress.length===4 && stress.every(s=>s.passed),stress);
  if(dpr===1 && width!==2560 || dpr===2){
   await page.goto(idle.href,{waitUntil:'networkidle'});
   for(let index=0;index<3;index++){
    const launcher=page.locator('.device-rail-item').nth(index);
    await launcher.click();
    const dialog=page.locator('[role="dialog"]').last();
    await dialog.waitFor();await page.waitForTimeout(250);
    await capture(page,`${key}-device-dialog-${index}`);
    await checkFocus(page,dialog,`${key}-dialog-${index}`);
    const combo=dialog.getByRole('combobox').filter({visible:true});
    for(let n=0;n<await combo.count();n++){
     if(await combo.nth(n).isDisabled())continue;
     await combo.nth(n).click();
     const list=dialog.getByRole('listbox');
     if(await list.count()){
      if(n===0)await capture(page,`${key}-device-dialog-${index}-select`);
      const bounds=await list.boundingBox();
      check(`${key}-dialog-${index}-select-${n}-viewport`,bounds && bounds.height>24 && bounds.y>=0 && bounds.y+bounds.height<=height+1,bounds);
      await page.keyboard.press('Escape');
      check(`${key}-dialog-${index}-escape-select-${n}`,await dialog.isVisible() && await list.count()===0);
     }
    }
    const detect=dialog.getByRole('button',{name:/识别/}).first();
    if(await detect.count() && await detect.isEnabled()){
     const footer=dialog.locator('.device-settings-dialog__footer');
     const before=await footer.boundingBox();await detect.click();await page.waitForTimeout(100);
     const after=await footer.boundingBox();
     check(`${key}-dialog-${index}-detect-stability`,before && after && Math.abs(before.y-after.y)<1 && Math.abs(before.height-after.height)<1 && await footer.getByText('保存设置',{exact:true}).count()===1);
    }
    await page.keyboard.press('Escape');
    if(await dialog.count())await dialog.getByRole('button',{name:'关闭设置弹窗'}).click();
    await page.waitForTimeout(80);
    check(`${key}-dialog-${index}-focus-restored`,await launcher.evaluate(e=>e===document.activeElement));
   }
   if(dpr===1){
    for(const [name,launcher] of [['specifications',page.getByRole('button',{name:'编辑脚本',exact:true})],['save-script',page.getByRole('button',{name:'添加脚本',exact:true})]]){
     await launcher.click();const dialog=page.getByRole('dialog').last();await dialog.waitFor();await page.waitForTimeout(250);
     await capture(page,`${key}-${name}`);await checkFocus(page,dialog,`${key}-${name}`);
     await page.keyboard.press('Escape');await page.waitForTimeout(80);
     check(`${key}-${name}-escape-close`,await page.getByRole('dialog').count()===0);
    }
   }
  }
  if(width===1366 && dpr===1){
   await page.evaluate(()=>localStorage.setItem('power-test.theme','light'));
   await page.goto(idle.href,{waitUntil:'networkidle'});
   await capture(page,`${key}-automatic-light`);
   await page.locator('.device-rail-item').first().click();const dialog=page.getByRole('dialog').last();await dialog.waitFor();await page.waitForTimeout(250);
   await checkFocus(page,dialog,`${key}-light-dialog`);await capture(page,`${key}-dialog-light-focus`);
  }
  await writeFile(resolve(out,'results.json'),JSON.stringify(results,null,2));await context.close();
 }
} finally { await browser.close();await writeFile(resolve(out,'results.json'),JSON.stringify(results,null,2)); }
console.log(JSON.stringify({captures:results.captures.length,errors:results.errors,interactions:results.interactions},null,2));
if(process.env.ARP_UI_QA_ASSERT==='1' && (results.errors.length || results.interactions.some(i=>!i.passed) || results.captures.some(c=>c.geometry.overflow)))process.exitCode=1;
