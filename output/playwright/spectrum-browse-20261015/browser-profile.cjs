async (page) => {
 await page.unrouteAll({behavior:'wait'});
 await page.route('**/src/lib/data-browser-demo.ts*',route=>route.fulfill({contentType:'text/javascript',path:'output/playwright/spectrum-browse-20261015/browser-fixture.js'}));
 await page.route('**/spectrum-browser-fixture?*',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><html><head><meta charset="UTF-8"><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module">
 await import('/scripts/fixtures/spectrum-browser.tsx');
 </script></body></html>`}));
 await page.setViewportSize({width:1440,height:900});await page.emulateMedia({reducedMotion:'reduce'});
 await page.goto('http://localhost:1420/spectrum-browser-fixture?demo=1&currents=12');
 await page.locator('.db-record-button').first().waitFor();await page.evaluate(()=>document.fonts.ready);
 await page.getByRole('combobox',{name:'曲线内容',exact:true}).selectOption('multiSpectrum');
 const canvas=page.getByRole('img',{name:'原始强度光谱叠加',exact:true});await canvas.waitFor();
 const cdp=await page.context().newCDPSession(page);await cdp.send('Performance.enable');
 const rows=[];
 const stats=async()=>Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(r=>[r.name,r.value]));
 const indices=[...Array.from({length:16},(_,i)=>i),12,13,14,15,12,13,14,15];
 for(let step=0;step<indices.length;step++){
  const index=indices[step],button=page.locator('.db-record-button').nth(index);
  const before=await stats(),started=Date.now();
  await button.click();
  await page.getByRole('combobox',{name:'曲线内容',exact:true}).selectOption('multiSpectrum');
  await page.waitForFunction(index=>document.querySelector(`.db-record-button[aria-current="true"] strong`)?.textContent===`PERF-${String(index).padStart(2,'0')}` && document.querySelector('.db-spectrum-section')?.getAttribute('aria-busy')==='false',index);
  await canvas.waitFor();await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const elapsed=Date.now()-started,after=await stats();
  await cdp.send('HeapProfiler.collectGarbage');const heap=await cdp.send('Runtime.getHeapUsage');
  rows.push({step,index,elapsedMs:elapsed,scriptMs:(after.ScriptDuration-before.ScriptDuration)*1000,heapMiB:heap.usedSize/1048576,fullRequests:await page.evaluate(()=>window.__spectrumRequests.length)});
 }
 const state=await page.evaluate(()=>({current:document.querySelector('.db-record-button[aria-current="true"] strong').textContent,choices:document.querySelectorAll('.db-spectrum-choices input').length,selected:document.querySelectorAll('.db-spectrum-choices input:checked').length,requests:window.__spectrumRequests}));
 const pixels=await canvas.evaluate(c=>c.toDataURL());
 await page.screenshot({path:'output/playwright/spectrum-browse-20261015/browser-before.png'});await cdp.detach();
 return {workload:'actual DataBrowserPage, synthetic transport; 16 records x 12 spectra x 8192 points, 24 switches; GC between timings, no hardware',rows,state,pixels};
}
