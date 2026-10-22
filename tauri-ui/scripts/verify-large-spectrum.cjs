// Run from the repository root with playwright-cli run-code --filename <this file>.
// Uses the actual product page and synthetic data; never a backend or instrument.
async (page) => {
 await page.unrouteAll({behavior: 'wait'});
 await page.route('**/src/lib/data-browser-demo.ts*',route=>route.fulfill({contentType:'text/javascript',path:'tauri-ui/scripts/fixtures/spectrum-browser-data.js'}));
 await page.route('**/spectrum-browser-fixture?*',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><html><head><meta charset="UTF-8"><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module">
 await import('/scripts/fixtures/spectrum-browser.tsx');
 </script></body></html>`}));
 await page.setViewportSize({width:1440,height:900});
 await page.addInitScript(()=>{
  if(window.__overlayProbe)return;
  window.__overlayProbe={lines:0,errors:[]};
  const line=CanvasRenderingContext2D.prototype.lineTo;
  CanvasRenderingContext2D.prototype.lineTo=function(...args){if(this.canvas.getAttribute('aria-label')==='原始强度光谱叠加')window.__overlayProbe.lines++;return line.apply(this,args);};
  addEventListener('error',e=>window.__overlayProbe.errors.push(e.message));
 });
 await page.goto('http://localhost:1420/spectrum-browser-fixture?demo=1&currents=24');
 await page.locator('.db-record-button').first().waitFor();
 await page.getByRole('combobox',{name:'曲线内容',exact:true}).selectOption('multiSpectrum');
 const canvas=page.getByRole('img',{name:'原始强度光谱叠加',exact:true});await canvas.waitFor();
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 const result=await page.evaluate(()=>({choices:document.querySelectorAll('.db-spectrum-choices input').length,selected:document.querySelectorAll('.db-spectrum-choices input:checked').length,...window.__overlayProbe}));
 if(result.errors.length||result.selected!==24||result.lines<24*8191)throw Error(JSON.stringify(result));
 await page.screenshot({path:'output/playwright/large-spectrum-regression.png'});
 return result;
}
