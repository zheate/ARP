async (page) => {
  const results = [];
  for (const [width,height,density,theme] of [[1180,720,'comfortable','dark'],[1440,900,'comfortable','light'],[2048,1063,'distant','dark']]) {
    await page.setViewportSize({width,height});
    await page.evaluate(({density,theme})=>{
      const k='power-test.appearance.v1';
      localStorage.setItem(k,JSON.stringify({...JSON.parse(localStorage.getItem(k)||'{}'),density}));
      localStorage.setItem('power-test.theme',theme);
    },{density,theme});
    await page.reload();
    await page.locator('.power-chart-readout__current-unit').waitFor();
    await page.evaluate(()=>document.fonts.ready);
    const inspect = () => page.locator('.power-chart-readout').evaluate(el=>{
      const rect=s=>el.querySelector(s).getBoundingClientRect();
      const u=rect('.power-chart-readout__power-unit');
      const current=el.querySelector('.automatic-metric-card__current .numeric-readout');
      const c=(current.querySelector('.numeric-readout__flow') || current).getBoundingClientRect();
      const n=rect('.automatic-metric-card__number .numeric-readout');
      const chart=el.closest('.automatic-power-chart').getBoundingClientRect();
      const a=rect('.power-chart-readout__current-unit');
      return {gap:c.left-u.right,noOverlap:n.right<=u.left && c.right<=a.left,inChart:n.left>=chart.left && a.right<=chart.right};
    });
    const actual=await inspect();
    if(actual.gap!==4 || !actual.noOverlap || !actual.inChart) throw new Error(JSON.stringify(actual));
    await page.locator('.automatic-power-chart').screenshot({path:`output/playwright/power-current-gap-20261016/after-${width}-${theme}.png`});
    // Render-only stress fixture: replace displayed numeric content, leaving production CSS intact.
    for (const [power,current] of [['0.000','14'],['1234.567','123.45'],['—','—']]) {
      await page.locator('.power-chart-readout').evaluate((el,{power,current})=>{
        el.querySelector('.automatic-metric-card__number .numeric-readout').textContent=power;
        el.querySelector('.automatic-metric-card__current .numeric-readout').textContent=current;
      },{power,current});
      const fixture=await inspect();
      if(fixture.gap!==4 || !fixture.noOverlap || !fixture.inChart) throw new Error(JSON.stringify({width,power,current,...fixture}));
      results.push({width,height,density,theme,power,current,...fixture});
      if(current==='14') await page.locator('.power-chart-readout').screenshot({path:`output/playwright/power-current-gap-20261016/fixture-14A-${width}-${theme}.png`});
    }
  }
  return results;
}
