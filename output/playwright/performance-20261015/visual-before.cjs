async (page) => {
 await page.emulateMedia({reducedMotion:'reduce'});
 const results=[];
 for(const theme of ['dark','light']) for(const width of [1440,1180]) for(const name of ['automatic','manual','settings','data']) {
  await page.setViewportSize({width,height:width===1440?900:720});
  await page.goto(`http://localhost:1420/?demo=1&page=${name}`);
  await page.evaluate(theme=>{localStorage.setItem('power-test.theme',theme);document.documentElement.dataset.theme=theme;},theme);
  await page.reload(); await page.evaluate(()=>document.fonts.ready);
  if(name==='data') await page.getByText('数据浏览',{exact:true}).first().waitFor();
  else if(name==='settings') await page.getByRole('heading',{name:'系统设置 / 外观'}).waitFor();
  else await page.getByRole('img',{name:'光谱曲线',exact:true}).waitFor();
  await page.waitForTimeout(350);
  const key=`${name}-${theme}-${width}`;
  await page.screenshot({path:`output/playwright/performance-20261015/before-${key}.png`});
  const state=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,canvases:[...document.querySelectorAll('canvas[aria-label]')].map(c=>({name:c.getAttribute('aria-label'),pixels:c.toDataURL()}))}));
  results.push({key,...state});
 }
 return results;
}
