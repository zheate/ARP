async (page) => {
  await page.route('**/chart-performance-fixture', route => route.fulfill({contentType:'text/html',body:'<!DOCTYPE html><html data-theme="dark"><head><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/scripts/fixtures/chart-performance.tsx"></script></body></html>'}));
  await page.goto('http://localhost:1420/chart-performance-fixture');
  await page.getByRole('img',{name:'光谱曲线',exact:true}).waitFor();
  await page.evaluate(()=>document.fonts.ready);
  await page.waitForTimeout(400);
  const session=await page.context().newCDPSession(page);
  await session.send('Performance.enable'); await session.send('Emulation.setCPUThrottlingRate',{rate:4});
  const getMetrics=async()=>Object.fromEntries((await session.send('Performance.getMetrics')).metrics.map(x=>[x.name,x.value]));
  await page.evaluate(()=>{window.__perf={clears:{},gaps:[],reads:0,commits:0}});
  const before=await getMetrics();
  const updates=await page.evaluate(async()=>{
    const times=[];
    for(let i=0;i<30;i++) { const t=performance.now();window.chartPerformanceFixture.advance();times.push(performance.now()-t);await new Promise(r=>setTimeout(r,Math.max(0,200-(performance.now()-t)))); }
    return times;
  });
  const after=await getMetrics(), counts=await page.evaluate(()=>window.__perf);
  const gaps=counts.gaps.sort((a,b)=>a-b);delete counts.gaps;
  await page.mouse.move(10,10);
  const image=await page.getByRole('img',{name:'光谱曲线',exact:true}).evaluate(c=>c.toDataURL());
  await session.send('Emulation.setCPUThrottlingRate',{rate:1});await session.detach();
  return {counts,updates,frameP95Ms:gaps[Math.floor(gaps.length*.95)],frameMaxMs:gaps.at(-1),scriptMs:(after.ScriptDuration-before.ScriptDuration)*1000,taskMs:(after.TaskDuration-before.TaskDuration)*1000,layoutMs:(after.LayoutDuration-before.LayoutDuration)*1000,styleMs:(after.RecalcStyleDuration-before.RecalcStyleDuration)*1000,image};
}
