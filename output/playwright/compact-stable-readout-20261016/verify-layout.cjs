async (page) => {
  const errors=[]; page.on('pageerror', e=>errors.push(e.message));
  await page.reload();
  await page.locator('.power-chart-readout__current-unit').waitFor();
  await page.addScriptTag({path:'/Users/zh/Documents/test/ARP/output/playwright/compact-stable-readout-20261016/power-unit-pages.js'});
  await page.evaluate(()=>document.fonts.ready);
  const results=[];
  for(const mode of ['automatic','manual']) for(const width of [1180,1440,1920]) {
    await page.setViewportSize({width,height:900});
    for(const dark of [false,true]) for(const density of ['compact','comfortable','distant']) {
      await page.evaluate(({dark,density})=>{document.documentElement.classList.toggle('dark',dark);document.documentElement.dataset.density=density;},{dark,density});
      const samples=[];
      for(const [power,current,state] of [[0,0,'idle'],[9.999,9.99,'waiting_stable'],[10,10,'waiting_stable'],[100,14,'waiting_stable'],[9999.999,99.99,'waiting_stable'],[1000,9.9,'waiting_stable'],[0.001,99.9,'waiting_stable'],[null,null,'idle']]) {
        await page.evaluate(args=>window.renderPowerUnitsCase(...args),[mode,power,current,state]);
        for(const delay of [0,20,100]) {
          if(delay) await page.waitForTimeout(delay);
          samples.push(await page.locator('#power-units-qa .power-chart-readout').evaluate(el=>{
            const rect=n=>n.getBoundingClientRect();const find=s=>el.querySelector(s);
            const p=find('.automatic-metric-card__number .numeric-readout__flow');
            const c=find('.automatic-metric-card__current .numeric-readout__flow');
            const w=rect(find('.power-chart-readout__power-unit')),a=rect(find('.power-chart-readout__current-unit'));
            const chart=rect(el.closest('.automatic-power-chart'));
            const decimal=f=>f?.shadowRoot.querySelector('[part~="decimal"]');
            const digits=f=>f?Array.from(f.shadowRoot.querySelectorAll('[part~="digit"]')).map(n=>rect(n).x):null;
            const px=p?rect(p):null,cx=c?rect(c):null;
            return {w:w.x,a:a.x,y:rect(el).y,powerRight:px?.right??null,currentRight:cx?.right??null,powerDecimal:decimal(p)?rect(decimal(p)).x:null,currentDecimal:decimal(c)?rect(decimal(c)).x:null,currentDigits:digits(c),gap:cx?cx.left-w.right:null,currentUnitGap:cx?a.left-cx.right:null,inside:(!px||px.left>=chart.left)&&a.right<=chart.right,overlap:(px&&px.right>w.left)||(cx&&cx.right>a.left),overflow:document.documentElement.scrollWidth>innerWidth,animated:p?.animated??false};
          }));
        }
      }
      const span=key=>{const v=samples.map(s=>s[key]).filter(v=>v!==null);return Math.max(...v)-Math.min(...v);};
      const digitSamples=samples.filter(s=>s.currentDigits);
      const digitShift=Math.max(...Array.from({length:4},(_,i)=>Math.max(...digitSamples.map(s=>s.currentDigits[i]))-Math.min(...digitSamples.map(s=>s.currentDigits[i]))));
      const result={mode,width,dark,density,samples:samples.length,unitShift:Math.max(span('w'),span('a')),numberShift:Math.max(span('powerRight'),span('currentRight'),span('powerDecimal'),span('currentDecimal'),digitShift),verticalShift:span('y'),maxGap:Math.max(...samples.map(s=>s.gap??0)),maxUnitGap:Math.max(...samples.map(s=>s.currentUnitGap??0)),inside:samples.every(s=>s.inside),overlap:samples.some(s=>s.overlap),overflow:samples.some(s=>s.overflow),animated:samples.some(s=>s.animated)};
      results.push(result);
    }
  }
  const failures=results.filter(r=>r.unitShift>0.5||r.numberShift>0.5||r.verticalShift>0.5||r.maxGap>5||r.maxUnitGap>5||!r.inside||r.overlap||r.overflow);
  await page.evaluate(r=>window.compactStableResults=r,{results,failures,errors});
  for(const mode of ['automatic','manual']) {
    await page.setViewportSize({width:1440,height:900});
    await page.evaluate(mode=>{document.documentElement.classList.add('dark');document.documentElement.dataset.density='comfortable';window.renderPowerUnitsCase(mode,0,14,'idle');},mode);
    await page.locator('#power-units-qa .power-chart-readout').screenshot({path:`output/playwright/compact-stable-readout-20261016/${mode}-readout.png`,animations:'disabled'});
    await page.locator('#power-units-qa .automatic-power-chart').screenshot({path:`output/playwright/compact-stable-readout-20261016/${mode}-chart.png`,animations:'disabled'});
  }
  if(failures.length||errors.length) throw new Error(JSON.stringify({failures,errors}));
  return {cases:results.length,samples:results.reduce((n,r)=>n+r.samples,0),maxUnitShift:Math.max(...results.map(r=>r.unitShift)),maxNumberShift:Math.max(...results.map(r=>r.numberShift)),maxGap:Math.max(...results.map(r=>r.maxGap)),animatedCases:results.filter(r=>r.animated).length,errors};
}
