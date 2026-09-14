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
async function capture(page, name) {
  await page.evaluate(() => document.fonts.ready);
  const geometry = await page.evaluate(() => {
    const rect = e => { const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}; };
    const shown = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
    const description = e => ({text:(e.getAttribute('aria-label')||e.textContent||'').trim().slice(0,150),rect:rect(e)});
    const dialogs = [...document.querySelectorAll('[role="dialog"],[role="alertdialog"]')].filter(shown);
    return { viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio}, overflow:document.documentElement.scrollWidth>innerWidth+1,
      dialogs:dialogs.map(description), charts:[...document.querySelectorAll('canvas')].filter(shown).map(description),
      readouts:[...document.querySelectorAll('.automatic-metric-card__value')].map(description),
      controls:[...document.querySelectorAll('button,input,[role="combobox"]')].filter(shown).map(e=>({...description(e),disabled:e.matches(':disabled'),name:e.getAttribute('aria-label'),title:e.getAttribute('title')})),
      text:document.body.innerText };
  });
  await page.screenshot({ path:resolve(out,`${name}.png`) });
  await writeFile(resolve(out,`${name}.html`),await page.content());
  results.captures.push({name,geometry});
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
  }
  // A separate unchanged mid-run fixture deliberately locks manual/report navigation.
  await page.goto(url.href,{waitUntil:'networkidle'});
  await capture(page,`${key}-automatic-running`);
  results.interactions.push({name:`${key}-running-lock`,passed:await page.getByRole('button',{name:'手动配置',exact:true}).isDisabled() && await page.getByRole('button',{name:'出货报告',exact:true}).isDisabled()});
  if(dpr===1 && width!==2560 || dpr===2){
   await page.goto(idle.href,{waitUntil:'networkidle'});
   for(let index=0;index<3;index++){
    const launcher=page.locator('.device-rail-item').nth(index);
    await launcher.click();
    const dialog=page.locator('[role="dialog"]').last();
    await dialog.waitFor();
    await page.waitForTimeout(250);
    await capture(page,`${key}-device-dialog-${index}`);
    const enabled=dialog.locator('button:enabled:not([tabindex="-1"]),input:enabled:not([tabindex="-1"])');
    await enabled.last().focus();await page.keyboard.press('Tab');
    const trapped=await dialog.evaluate(e=>e.contains(document.activeElement));
    results.interactions.push({name:`${key}-dialog-${index}-tab-contained`,passed:trapped});
    const combo=dialog.getByRole('combobox').filter({visible:true});
    for(let n=0;n<await combo.count();n++){
     if(await combo.nth(n).isDisabled())continue;
     await combo.nth(n).click();
     const list=dialog.getByRole('listbox');
     if(await list.count()){
      await capture(page,`${key}-device-dialog-${index}-select`);
      await page.keyboard.press('Escape');
      results.interactions.push({name:`${key}-dialog-${index}-escape-select`,passed:await dialog.isVisible() && await list.count()===0});
     }
     break;
    }
    const detect=dialog.getByRole('button',{name:/识别/}).first();
    if(await detect.count() && await detect.isEnabled()){
     const footer=dialog.locator('.device-settings-dialog__footer');
     const before=await footer.boundingBox();await detect.click();await page.waitForTimeout(100);
     const after=await footer.boundingBox();
     results.interactions.push({name:`${key}-dialog-${index}-detect-stability`,passed:before && after && Math.abs(before.y-after.y)<1 && Math.abs(before.height-after.height)<1 && await footer.getByText('保存设置',{exact:true}).count()===1});
    }
    await page.keyboard.press('Escape');
    if(await dialog.count())await dialog.getByRole('button',{name:'关闭设置弹窗'}).click();
    results.interactions.push({name:`${key}-dialog-${index}-focus-restored`,passed:await launcher.evaluate(e=>e===document.activeElement)});
   }
  }
  await writeFile(resolve(out,'results.json'),JSON.stringify(results,null,2));
  await context.close();
 }
} finally { await browser.close();await writeFile(resolve(out,'results.json'),JSON.stringify(results,null,2)); }
console.log(JSON.stringify({captures:results.captures.length,errors:results.errors,interactions:results.interactions},null,2));
if(process.env.ARP_UI_QA_ASSERT==='1' && (results.errors.length || results.interactions.some(i=>!i.passed) || results.captures.some(c=>c.geometry.overflow)))process.exitCode=1;
