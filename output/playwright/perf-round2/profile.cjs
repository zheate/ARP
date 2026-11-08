async (page) => {
  await page.addInitScript(() => {
    if (window.__perf) return;
    window.__perf = { clears: {}, gaps: [], reads: 0, commits: 0 };
    const clear = CanvasRenderingContext2D.prototype.clearRect;
    CanvasRenderingContext2D.prototype.clearRect = function (...args) {
      const label = this.canvas.getAttribute('aria-label');
      if (label) window.__perf.clears[label] = (window.__perf.clears[label] || 0) + 1;
      return clear.apply(this, args);
    };
    const rect = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = function (...args) { window.__perf.reads++; return rect.apply(this, args); };
    window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = { supportsFiber: true, renderers: new Map(), inject(r) { this.renderers.set(1,r); return 1; }, onCommitFiberRoot() { window.__perf.commits++; }, onCommitFiberUnmount() {}, onPostCommitFiberRoot() {} };
    let last;
    function frame(t) { if (last) window.__perf.gaps.push(t-last); last=t; requestAnimationFrame(frame); }
    requestAnimationFrame(frame);
  });
  await page.route('**/performance-fixture?*', route => route.fulfill({ contentType: 'text/html', body: `<!DOCTYPE html><html><head><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module">
    import { DEMO_SNAPSHOT, resetDemoRun } from '/src/lib/demo-snapshot.ts';
    const measurements = DEMO_SNAPSHOT.measurements;
    Object.assign(DEMO_SNAPSHOT, resetDemoRun(DEMO_SNAPSHOT));
    DEMO_SNAPSHOT.measurements = measurements;
    DEMO_SNAPSHOT.devices.spectrometer.running = true;
    measurements.power = Array.from({length: 601}, (_,i) => ({elapsedS:i/10,powerW:20+Math.sin(i/13)}));
    const x = Array.from({length:8192},(_,i)=>972+i*8/8191);
    measurements.spectrum = {wavelengthNm:x, intensity:x.map(w=>100+12000*Math.exp(-(((w-976.2)/.4)**2)))};
    await import('/src/main.tsx');
    </script></body></html>` }));
  const session = await page.context().newCDPSession(page);
  await session.send('Performance.enable');
  await session.send('Emulation.setCPUThrottlingRate', {rate:4});
  const metrics = async () => Object.fromEntries((await session.send('Performance.getMetrics')).metrics.map(({name,value})=>[name,value]));
  const reset = () => page.evaluate(() => { window.__perf = {clears:{},gaps:[],reads:0,commits:0}; });
  const finish = async (before) => {
    const after=await metrics();
    const counts=await page.evaluate(()=>window.__perf);
    const gaps=counts.gaps.sort((a,b)=>a-b); delete counts.gaps;
    return {...counts, frameP95Ms:gaps[Math.floor(gaps.length*.95)],frameMaxMs:gaps.at(-1),framesOver34ms:gaps.filter(x=>x>34).length,
      scriptMs:(after.ScriptDuration-before.ScriptDuration)*1000,taskMs:(after.TaskDuration-before.TaskDuration)*1000,layoutMs:(after.LayoutDuration-before.LayoutDuration)*1000,styleMs:(after.RecalcStyleDuration-before.RecalcStyleDuration)*1000};
  };
  await page.setViewportSize({width:1440,height:960});
  await page.goto('http://localhost:1420/performance-fixture?demo=1');
  await page.getByRole('img',{name:'光谱曲线',exact:true}).waitFor();
  await page.waitForTimeout(700);
  await reset(); let before=await metrics();
  const power=await page.getByRole('img',{name:'功率实时曲线',exact:true}).boundingBox();
  for(let i=0;i<60;i++){ await page.mouse.move(power.x+60+(power.width-90)*i/60,power.y+power.height/2); await page.waitForTimeout(16); }
  await page.mouse.move(10,10); await page.waitForTimeout(100);
  const hover=await finish(before);
  await reset(); before=await metrics();
  const switches=[];
  for(let round=0;round<2;round++) for(const name of ['手动配置','PD 采集','系统设置','自动测试']) {
    const elapsed=await page.getByRole('button',{name,exact:true}).evaluate(async button=>{const t=performance.now();button.click();await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));return performance.now()-t;});
    switches.push({name,elapsedMs:elapsed}); await page.waitForTimeout(350);
  }
  const navigation=await finish(before);
  await page.mouse.move(10,10); await page.screenshot({path:'output/playwright/perf-round2/current.png'});
  await session.send('Profiler.enable'); await session.send('Profiler.start');
  await page.getByRole('button',{name:'手动配置',exact:true}).click();
  await page.getByRole('button',{name:'自动测试',exact:true}).click();
  await page.waitForTimeout(400);
  const {profile}=await session.send('Profiler.stop');
  const nodes=new Map(profile.nodes.map(n=>[n.id,n.callFrame]));const totals={};
  profile.samples?.forEach((id,i)=>{const f=nodes.get(id);const key=f.functionName+' '+f.url.split('/').at(-1);totals[key]=(totals[key]||0)+(profile.timeDeltas[i]||0)/1000;});
  const hot=Object.entries(totals).filter(([key])=>!key.startsWith('(idle)')).sort((a,b)=>b[1]-a[1]).slice(0,18);
  await session.send('Emulation.setCPUThrottlingRate',{rate:1});await session.detach();
  return {workload:'production React, full app, synthetic 8192-point spectrum and 601 power samples, 4x CPU throttle, 1440x960',hover,navigation,switches,hot};
}
