import {chromium} from './playwright-local.mjs';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const browser=await chromium.launch({headless:true});const checks=[];
try {
for(const [width,height] of [[1366,768],[960,540]]) {
 const page=await browser.newPage({viewport:{width,height}});
 for(const fixture of ['manual','manual-ch341']) {
  await page.goto(`http://127.0.0.1:1420/__qa/index.html?fixture=${fixture}`,{waitUntil:'networkidle'});
  const inputs=await page.locator('.manual-power-parameters input').evaluateAll(es=>es.map(e=>{const s=getComputedStyle(e),r=e.getBoundingClientRect(),c=e.closest('.manual-device-card').getBoundingClientRect();return {id:e.id,font:s.fontSize,align:s.textAlign,fits:r.width>0&&r.left>=c.left&&r.right<=c.right}}));
  assert.ok(inputs.length>0&&inputs.every(e=>e.font==='20px'&&e.align==='right'&&e.fits));
  if(fixture==='manual') {const voltage=page.locator('#manual-psu-voltage');await voltage.fill('12.75');assert.equal(await voltage.inputValue(),'12.75');}
  await page.getByTitle('点击进入电源设置').first().click();
  const dialog=page.getByRole('dialog');await dialog.waitFor();
  const labels=['读取输出电压','读取输出电流','读取输入电压','读取模块温度'];
  for(const label of labels) assert.equal(await dialog.getByRole('button',{name:label,exact:true}).count(),fixture==='manual-ch341'?1:0);
  await page.screenshot({path:`output/playwright/github-publish-20261021/${width}-${fixture}-settings.png`});
  await page.keyboard.press('Escape');assert.equal(await page.getByRole('dialog').count(),0);
  checks.push({width,height,fixture,inputs,settingsButtons:fixture==='manual-ch341'?labels:[],escapeClosed:true});
 }
 await page.close();
}
await writeFile('output/github-publish-20261021/manual-focused.json',JSON.stringify({checks,passed:true},null,2));console.log('4 focused browser scenarios passed');
} finally {await browser.close();}
