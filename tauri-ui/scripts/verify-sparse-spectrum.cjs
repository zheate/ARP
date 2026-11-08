// Playwright CLI run-code from the repository root. Full product chart, synthetic
// spectra, no backend. Count real lookups separately from performance timings.
async (page) => {
  await page.unrouteAll({behavior: 'wait'});
  await page.route('**/src/lib/data-browser-demo.ts*', route => route.fulfill({contentType:'text/javascript',path:'tauri-ui/scripts/fixtures/spectrum-browser-data.js'}));
  await page.route('**/sparse-spectrum-fixture?*', route => route.fulfill({contentType:'text/html',body:'<!doctype html><html><head><meta charset="UTF-8"><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module">await import("/scripts/fixtures/spectrum-browser.tsx");</script></body></html>'}));
  await page.route('**/src/components/canvas-line-chart.tsx*', async route => {
    const response = await route.fetch();
    const source = await response.text();
    const needle = 'valueAt: (index, key) => finiteValue(rows[index]?.[key]),';
    if(!source.includes(needle)) throw Error('The lookup probe must match the actual chart adapter');
    await route.fulfill({response,body:source.replace(needle,'valueAt: (index, key) => { window.__sparseProbe.reads++; return finiteValue(rows[index]?.[key]); },')});
  });
  await page.addInitScript(()=>{
    if(window.__sparseProbe)return;
    window.__sparseProbe={reads:0,lines:0,errors:[]};
    const line=CanvasRenderingContext2D.prototype.lineTo;
    CanvasRenderingContext2D.prototype.lineTo=function(...args){if(this.canvas.getAttribute('aria-label')==='原始强度光谱叠加')window.__sparseProbe.lines++;return line.apply(this,args);};
    addEventListener('error',event=>window.__sparseProbe.errors.push(event.message));
  });
  await page.setViewportSize({width:1440,height:900});
  await page.goto('http://localhost:1420/sparse-spectrum-fixture?demo=1&currents=24');
  await page.locator('.db-record-button').first().waitFor();
  await page.evaluate(()=>document.fonts.ready);
  await page.evaluate(()=>{window.__sparseProbe.reads=0;window.__sparseProbe.lines=0;});
  await page.getByRole('combobox',{name:'曲线内容',exact:true}).selectOption('multiSpectrum');
  const canvas=page.getByRole('img',{name:'原始强度光谱叠加',exact:true});
  await canvas.waitFor();
  await page.waitForFunction(c=>{
    const dpr=Math.min(Math.max(devicePixelRatio||1,1),2);
    return c.width>0&&Math.abs(c.width-c.clientWidth*dpr)<=1&&Math.abs(c.height-c.clientHeight*dpr)<=1;
  },await canvas.elementHandle());
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const result=await page.evaluate(()=>({...window.__sparseProbe,selected:document.querySelectorAll('.db-spectrum-choices input:checked').length}));
  if(result.errors.length||result.selected!==24||result.lines<24*8191)throw Error(`Full spectrum changed: ${JSON.stringify(result)}`);
  if(result.reads>24*8192*20)throw Error(`Sparse spectra still scan empty series cells: ${JSON.stringify(result)}`);
  return result;
}
