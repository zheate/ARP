async (page) => {
 await page.unrouteAll({behavior:'wait'});
 await page.emulateMedia({reducedMotion:'reduce'});
 const results=[];
 for(const theme of ['dark','light'])for(const width of [1440,1180])for(const name of ['automatic','manual','settings','data']){
  const pair=[];
  for(const port of [1422,1420]){
   await page.setViewportSize({width,height:width===1440?900:720});
   await page.goto(`http://localhost:${port}/?demo=1&page=${name}`);
   await page.evaluate(theme=>localStorage.setItem('power-test.theme',theme),theme);await page.reload();
   await page.evaluate(()=>document.fonts.ready);
   await page.getByRole('button',{name:'急停',exact:true}).waitFor();
   if(name==='settings')await page.getByRole('heading',{name:'系统设置 / 外观'}).waitFor();
   if(name==='data')await page.getByRole('region',{name:'数据浏览',exact:true}).waitFor();
   await page.mouse.move(2,70);await page.waitForTimeout(350);
   pair.push(await page.evaluate(()=>[...document.querySelectorAll('[data-slot="card"], [aria-label="测试设备与控制"], .appearance-settings__controls, .appearance-settings__preview')].filter(e=>e.getBoundingClientRect().width).map(e=>{
    const r=e.getBoundingClientRect();return [e.getAttribute('aria-label')||e.getAttribute('data-slot')||e.className,...['x','y','width','height'].map(k=>Math.round(r[k]*100)/100)];
   })));
  }
  const equal=JSON.stringify(pair[0])===JSON.stringify(pair[1]);
  results.push({theme,width,page:name,equal,count:pair[1].length,...(!equal?{before:pair[0],after:pair[1]}:{})});
 }
 if(results.some(r=>!r.equal))throw new Error(JSON.stringify(results));
 return results;
}
