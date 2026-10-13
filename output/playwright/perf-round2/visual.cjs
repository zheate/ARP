async (page) => {
  const result=[];
  for(const theme of ['dark','light']) for(const width of [1440,1180]) {
    await page.setViewportSize({width,height:width===1440?960:720});
    await page.goto('http://localhost:1420/performance-fixture?demo=1');
    await page.evaluate(theme=>{localStorage.setItem('power-test.theme',theme);document.documentElement.dataset.theme=theme},theme);
    await page.reload();
    await page.getByRole('img',{name:'光谱曲线',exact:true}).waitFor();
    await page.evaluate(()=>document.fonts.ready);await page.waitForTimeout(700);await page.mouse.move(10,10);
    await page.screenshot({path:`output/playwright/perf-round2/visual-${theme}-${width}.png`});
    result.push(await page.evaluate(({theme,width})=>({theme,width,overflow:document.documentElement.scrollWidth>innerWidth,canvases:[...document.querySelectorAll('canvas[aria-label]')].map(c=>({label:c.getAttribute('aria-label'),width:c.width,height:c.height,data:c.toDataURL()}))}),{theme,width}));
  }
  return result;
}
