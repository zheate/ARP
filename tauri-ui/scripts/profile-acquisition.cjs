// Opt-in browser diagnostic; run via playwright-cli run-code --filename.
// Only localhost fixture responses are instrumented. Product files stay intact.
async (page) => {
  const directory = '/Users/zh/Documents/test/ARP/output/playwright/acquisition-timing-20261015';
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.unrouteAll({behavior: 'wait'});
  await page.addInitScript(() => {
    if (window.__arpAcquisitionTimingInstalled) return;
    window.__arpAcquisitionTimingInstalled = true;
    window.__timing = {frame: 0, draws: [], merges: [], gaps: [], enabled: true};
    let previous;
    const tick = now => {
      if (previous !== undefined) window.__timing.gaps.push(now - previous);
      previous = now;
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  await page.route('**/src/hooks/use-backend-snapshot.ts*', async route => {
    const response = await route.fetch();
    const original = await response.text();
    const needle = 'return { snapshot, error:';
    if (!original.includes(needle)) throw new Error('Snapshot diagnostic seam not found');
    await route.fulfill({response, body: original.replace(needle,
      'window.__publishTimingSnapshot = (next) => { snapshotRef.current = next; setSnapshot(next); }; ' + needle)});
  });
  await page.route('**/src/components/canvas-line-chart.tsx*', async route => {
    const response = await route.fetch();
    const original = await response.text();
    const pattern = /drawBaseChart\(canvas, container, args\.size,[^;]+;/;
    if (!pattern.test(original)) throw new Error('Canvas diagnostic seam not found');
    await route.fulfill({response, body: original.replace(pattern, call => `
      const timingStart = performance.now();
      ${call}
      if (window.__timing.enabled) window.__timing.draws.push({
        frame: window.__timing.frame, label: canvas.getAttribute('aria-label'),
        ms: performance.now() - timingStart, finished: performance.now()
      });
    `)});
  });
  await page.route('**/acquisition-timing?*', route => route.fulfill({
    contentType: 'text/html', body: `<!doctype html><html><head><meta charset="utf-8"><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module">
      import {DEMO_SNAPSHOT, resetDemoRun} from '/src/lib/demo-snapshot.ts';
      import {mergeBackendSnapshot, decodeBackendSnapshotPatch} from '/src/lib/backend.ts';
      const size = Number(new URLSearchParams(location.search).get('points'));
      Object.assign(DEMO_SNAPSHOT, resetDemoRun(DEMO_SNAPSHOT));
      DEMO_SNAPSHOT.devices.spectrometer.running = true;
      let current = DEMO_SNAPSHOT;
      const wavelengthNm = Array.from({length:size}, (_,i) => 950+i*60/(size-1));
      DEMO_SNAPSHOT.measurements = {...DEMO_SNAPSHOT.measurements,
        power:Array.from({length:1200},(_,i)=>({elapsedS:i/10,powerW:3.5})),
        spectrum:{wavelengthNm,intensity:wavelengthNm.map(w=>75+40000*Math.exp(-0.5*((w-976.1)/0.32)**2))}};
      let number = 100;
      window.__advanceTiming = () => {
        number++;
        const intensity = wavelengthNm.map(w => 75+(40000+number)*Math.exp(-0.5*((w-976.1)/0.32)**2)+4000*Math.exp(-0.5*((w-977.3)/0.20)**2));
        const startX = number/10;
        const base = current.measurements?.power?.length ? current.measurements.power : Array.from({length:1200},(_,i)=>({elapsedS:startX+i/10,powerW:3.5}));
        const end = base.at(-1).elapsedS + 0.1;
        const {measurements, pd, seriesPatches, ...metadata} = current;
        const patch = {...metadata, measurements:{spectrum:{wavelengthNm,intensity}},
          devices: {...current.devices, powerMeter:{...current.devices.powerMeter,powerW:3.5+number/10000}},
          seriesPatches:{power:{startX:base.length>=1200?base[1].elapsedS:base[0].elapsedS,points:[{elapsedS:end,powerW:3.5+Math.sin(number/13)/100}]}},
          seriesRevisions:{...current.seriesRevisions,power:number,spectrum:number}};
        // Prepare bytes before the receive timestamp: backend JSON generation
        // is measured separately in Python and must not inflate this sample.
        const encoded = JSON.stringify(patch);
        const started = performance.now();
        const received = JSON.parse(encoded);
        const parsed = performance.now();
        const decoded = decodeBackendSnapshotPatch(received);
        const validated = performance.now();
        current = mergeBackendSnapshot(current, decoded);
        const merged = performance.now();
        window.__timing.frame = number;
        window.__publishTimingSnapshot(current);
        const dispatched = performance.now();
        return {frame:number, started, parseMs:parsed-started,validationMs:validated-parsed,mergeMs:merged-validated,dispatchMs:dispatched-merged,
          spectrumPoints:current.measurements.spectrum.intensity.length,powerPoints:current.measurements.power.length};
      };
      await import('/src/main.tsx');
    </script></body></html>`
  }));
  await page.setViewportSize({width:1440,height:900});
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Performance.enable');
  const metric = async () => Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(m=>[m.name,m.value]));
  const results = [];
  const dist = values => {
    const v = values.slice().sort((a,b)=>a-b);
    return {count:v.length, median_ms:v[Math.floor(v.length/2)],p95_ms:v[Math.min(v.length-1,Math.floor(v.length*.95))],max_ms:v.at(-1)};
  };
  for (const [points, throttle] of [[3648,1],[8192,1],[8192,4]]) {
    await cdp.send('Emulation.setCPUThrottlingRate',{rate:throttle});
    await page.goto('http://localhost:1422/acquisition-timing?demo=1&points='+points);
    await page.getByRole('img',{name:'光谱曲线',exact:true}).waitFor();
    await page.evaluate(()=>document.fonts.ready);
    await page.waitForFunction(()=>typeof window.__publishTimingSnapshot==='function');
    // Prime the exact input scale before taking any measurement.
    await page.evaluate(()=>window.__advanceTiming());
    await page.waitForTimeout(300);
    for (let run=0;run<3;run++) {
      await page.evaluate(()=>{window.__timing.draws=[];window.__timing.gaps=[];});
      const before = await metric();
      const frames = await page.evaluate(async()=>{
        const frames=[];
        for(let i=0;i<40;i++) {
          const frame=window.__advanceTiming();
          // Verify both actual base draws completed for this input; this is
          // Canvas command completion, not GPU presentation or screen latency.
          const deadline=performance.now()+2000;
          while(!['功率实时曲线','光谱曲线'].every(label=>window.__timing.draws.some(d=>d.frame===frame.frame&&d.label===label))) {
            if(performance.now()>deadline)throw new Error('Missing chart frame '+frame.frame);
            await new Promise(resolve=>requestAnimationFrame(resolve));
          }
          const drawings=window.__timing.draws.filter(d=>d.frame===frame.frame);
          frame.drawDoneMs=Math.max(...drawings.map(d=>d.finished))-frame.started;
          frames.push(frame);
          await new Promise(resolve=>setTimeout(resolve,Math.max(0,100-(performance.now()-frame.started))));
        }
        return frames;
      });
      const after = await metric();
      const sample = await page.evaluate(()=>({draws:window.__timing.draws,gaps:window.__timing.gaps}));
      if (frames.some(f=>f.spectrumPoints!==points || f.powerPoints!==1200)) throw new Error('Dropped input samples');
      const labels = [...new Set(sample.draws.map(d=>d.label))];
      results.push({points,throttle,run:run+1,frames:frames.length,
        parse:dist(frames.map(f=>f.parseMs)),merge:dist(frames.map(f=>f.mergeMs)),
        validation:dist(frames.map(f=>f.validationMs)),
        receive_interval:dist(frames.slice(1).map((f,i)=>f.started-frames[i].started)),
        dispatch:dist(frames.map(f=>f.dispatchMs)),receive_to_canvas_commands:dist(frames.map(f=>f.drawDoneMs)),
        canvas: Object.fromEntries(labels.map(label=>[label,dist(sample.draws.filter(d=>d.label===label).map(d=>d.ms))])),
        raf_gap:dist(sample.gaps),gaps_over_34ms:sample.gaps.filter(g=>g>34).length,
        script_total_ms:(after.ScriptDuration-before.ScriptDuration)*1000,
        task_total_ms:(after.TaskDuration-before.TaskDuration)*1000,
        layout_total_ms:(after.LayoutDuration-before.LayoutDuration)*1000,
        raw_frames:frames, raw_draws:sample.draws});
    }
  }
  await cdp.send('Emulation.setCPUThrottlingRate',{rate:1});
  await cdp.detach();
  await page.screenshot({path:directory+'/final.png'});
  if(errors.length)throw new Error(errors.join('\n'));
  const report={scope:'actual app, production React, synthetic incremental power/full-spectrum patches every 100ms; Chromium, not WebView2; no hardware',
    viewport:{width:1440,height:900},results,errors};
  const download = page.waitForEvent('download');
  await page.evaluate(report => {
    const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)+'\n'],{type:'application/json'}));
    const anchor=document.createElement('a');anchor.href=url;anchor.download='browser.json';anchor.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  },report);
  await (await download).saveAs(directory+'/browser.json');
  return {report:directory+'/browser.json',results:results.map(({raw_frames,raw_draws,...result})=>result)};
}
