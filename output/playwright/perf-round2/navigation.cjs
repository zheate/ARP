async (page) => {
  const errors=[];const onError=error=>errors.push(String(error));page.on('pageerror',onError);
  await page.goto('http://localhost:1420/scripts/fixtures/app-performance.html?demo=1');
  await page.getByRole('img',{name:'光谱曲线',exact:true}).waitFor();
  await page.waitForTimeout(500);
  const results=[];
  for(let i=0;i<2;i++) for(const name of ['手动配置','PD 采集','数据浏览','出货报告','系统设置','自动测试']) {
    await page.getByRole('button',{name,exact:true}).click();
    await page.waitForTimeout(400);
    const state=await page.evaluate(()=>({active:document.querySelector('nav button[aria-current="page"]')?.textContent,overflow:document.documentElement.scrollWidth>innerWidth,charts:[...document.querySelectorAll('canvas[aria-label]')].filter(c=>c.getBoundingClientRect().width>0).map(c=>({label:c.getAttribute('aria-label'),width:c.width,height:c.height}))}));
    if(state.active!==name||state.overflow||state.charts.some(c=>c.width<=0||c.height<=0)) throw new Error(JSON.stringify({name,state}));
    if(name==='自动测试'&&state.charts.length!==3) throw new Error('Returning to automatic page lost charts');
    results.push({name,...state});
  }
  page.off('pageerror',onError);
  if(errors.length) throw new Error(errors.join('\n'));
  return {transitions:results.length,results,scriptErrors:errors};
}
