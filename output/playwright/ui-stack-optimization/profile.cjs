async (page) => {
  await page.addInitScript(() => {
    window.__profile = { renders:{}, clears:{}, gaps:[] };
    window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = {supportsFiber:true, renderers:new Map(), inject(r){this.renderers.set(1,r);return 1;}, onCommitFiberRoot(id,root){
      const walk = f => {if(!f)return; const name=f.type?.displayName || f.type?.name || f.type?.type?.name; if(name && (f.flags & 1))window.__profile.renders[name]=(window.__profile.renders[name]||0)+1;if(!f.alternate || f.child!==f.alternate.child)walk(f.child);walk(f.sibling);};walk(root.current);
    },onCommitFiberUnmount(){},onPostCommitFiberRoot(){}};
    const clear=CanvasRenderingContext2D.prototype.clearRect;
    CanvasRenderingContext2D.prototype.clearRect=function(...a){const name=this.canvas.getAttribute('aria-label');if(name)window.__profile.clears[name]=(window.__profile.clears[name]||0)+1;return clear.apply(this,a);};
    let last;const frame=t=>{if(last)window.__profile.gaps.push(t-last);last=t;requestAnimationFrame(frame);};requestAnimationFrame(frame);
  });
  // Instrument only this browser fixture. Product hook and scheduling are unchanged.
  await page.route('**/src/hooks/use-backend-snapshot.ts*',async route=>{
    const response=await route.fetch();const original=await response.text();
    const needle='return { snapshot, error:';
    if(!original.includes(needle))throw new Error('Missing snapshot instrumentation seam');
    await route.fulfill({response,body:original.replace(needle,'window.__publishSnapshot = (next) => { snapshotRef.current = next; setSnapshot(next); }; '+needle)});
  });
  await page.route('**/stack-performance?*',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><html><head><meta charset="UTF-8"><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module">
    import {DEMO_SNAPSHOT,resetDemoRun} from '/src/lib/demo-snapshot.ts';
    import {mergeBackendSnapshot} from '/src/lib/backend.ts';
    const wavelengthNm=Array.from({length:8192},(_,i)=>972+i*8/8191);
    Object.assign(DEMO_SNAPSHOT,resetDemoRun(DEMO_SNAPSHOT));
    DEMO_SNAPSHOT.devices.spectrometer.running=true;
    DEMO_SNAPSHOT.measurements.spectrum={wavelengthNm,intensity:wavelengthNm.map(w=>100+12000*Math.exp(-(((w-976.2)/.4)**2)))};
    let current=DEMO_SNAPSHOT,frame=0;
    window.__advance=()=>{frame++;const patch=structuredClone(DEMO_SNAPSHOT);patch.devices.powerMeter.powerW=20+frame/100;patch.devices.powerMeter.ageMs=frame;patch.measurements.power=Array.from({length:601},(_,i)=>({elapsedS:frame/10+i/10,powerW:20+Math.sin((i+frame)/13)}));patch.measurements.spectrum.intensity=wavelengthNm.map(w=>100+(12000+frame)*Math.exp(-(((w-976.2)/.4)**2)));patch.seriesRevisions={...patch.seriesRevisions,power:frame+100,spectrum:frame+100};current=mergeBackendSnapshot(current,patch);window.__publishSnapshot(current);};
    window.__fixtureState=()=>({frame,points:current.measurements.spectrum.wavelengthNm.length,power:current.devices.powerMeter.powerW});
    await import('/src/main.tsx');
  </script></body></html>`}));
  await page.setViewportSize({width:1440,height:900});
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.goto('http://127.0.0.1:1420/stack-performance?demo=1');
  await page.getByRole('img',{name:'光谱曲线',exact:true}).waitFor();
  await page.evaluate(()=>document.fonts.ready);await page.waitForTimeout(500);
  const cdp=await page.context().newCDPSession(page);
  await cdp.send('Performance.enable');await cdp.send('Emulation.setCPUThrottlingRate',{rate:4});
  const metrics=async()=>Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(x=>[x.name,x.value]));
  const results=[];
  for(let run=0;run<3;run++){
    await page.evaluate(()=>window.__profile={renders:{},clears:{},gaps:[]});
    const before=await metrics();
    await page.evaluate(async()=>{for(let i=0;i<30;i++){const t=performance.now();window.__advance();await new Promise(r=>setTimeout(r,Math.max(0,100-(performance.now()-t))));}});
    const after=await metrics();const p=await page.evaluate(()=>window.__profile);const gaps=p.gaps.sort((a,b)=>a-b);delete p.gaps;
    results.push({...p,frameP95:gaps[Math.floor(gaps.length*.95)],longFrames:gaps.filter(x=>x>34).length,scriptMs:(after.ScriptDuration-before.ScriptDuration)*1000,taskMs:(after.TaskDuration-before.TaskDuration)*1000,layoutMs:(after.LayoutDuration-before.LayoutDuration)*1000});
  }
  const state=await page.evaluate(()=>window.__fixtureState());
  const spectrum=await page.getByRole('img',{name:'光谱曲线',exact:true}).evaluate(el=>el.toDataURL());
  await cdp.send('Emulation.setCPUThrottlingRate',{rate:1});await cdp.detach();
  return {workload:'actual app; injected snapshots; 8192 full spectrum points; 100 ms; 3 x 30 frames; Chromium 4x CPU',results,state,spectrum};
}
