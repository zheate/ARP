// Deterministic display comparison with the real PowerRealtimeChart, 601 power
// points, an 8192-pixel spectrum, and 100 ms sample arrivals. No hardware.
async (page, markerStyle = 'point', requireSmooth = true) => {
  await page.addInitScript(() => {
    const now = performance.now.bind(performance), raf = requestAnimationFrame.bind(window), cancel = cancelAnimationFrame.bind(window);
    const probe = window.__pointFollow = { hold: false, time: 0, pending: new Map(), draws: 0, endpoint: null };
    let id = -1;
    performance.now = () => probe.hold ? probe.time : now();
    window.requestAnimationFrame = callback => {
      if (!probe.hold) return raf(callback);
      const key = id--; probe.pending.set(key, callback); return key;
    };
    window.cancelAnimationFrame = key => { probe.pending.delete(key); cancel(key); };
    probe.step = ms => {
      probe.time += ms;
      const callbacks = [...probe.pending.values()]; probe.pending.clear();
      callbacks.forEach(callback => callback(probe.time));
    };
    const proto = CanvasRenderingContext2D.prototype, begin = proto.beginPath, clear = proto.clearRect, stroke = proto.stroke;
    proto.beginPath = function(...args) { this.__pointEndpoint = null; return begin.apply(this,args); };
    for (const method of ['moveTo','lineTo','bezierCurveTo']) {
      const original = proto[method];
      proto[method] = function(...args) { this.__pointEndpoint = args.slice(-2); return original.apply(this,args); };
    }
    proto.clearRect = function(...args) {
      if (this.canvas.getAttribute('aria-label') === '功率实时曲线') { probe.draws++; probe.endpoint = null; }
      return clear.apply(this,args);
    };
    proto.stroke = function(...args) {
      if (this.canvas.getAttribute('aria-label') === '功率实时曲线' && this.__pointEndpoint) probe.endpoint = this.__pointEndpoint;
      return stroke.apply(this,args);
    };
  });
  await page.route('**/point-follow-fixture', route => route.fulfill({ contentType:'text/html', body:'<!doctype html><html data-theme="dark"><head><meta charset="utf-8"><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/scripts/fixtures/flappy-bird-motion.tsx"></script></body></html>' }));
  await page.setViewportSize({width:1440,height:900});
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.goto('http://localhost:1420/point-follow-fixture');
  await page.getByRole('img',{name:'功率实时曲线',exact:true}).waitFor();
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(style => window.birdMotionFixture.setStyle(style), markerStyle);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.waitForFunction(() => [...document.querySelectorAll('.chart-character-marker,.chart-flappy-bird')].every(marker => marker.dataset.vectorReady === 'true'));
  await page.evaluate(() => { const p=window.__pointFollow; p.time=performance.now()+1000;p.hold=true; });
  const read = () => page.evaluate(() => {
    const p=window.__pointFollow, marker=document.querySelector('[data-static-marker],.chart-flappy-bird,.chart-character-marker');
    const matrix=new DOMMatrix(marker.style.transform);
    const foot = ['dinosaur','green-dinosaur'].includes(marker.dataset.markerStyle) ? 305/310 : .5;
    return {position:[matrix.m41+parseFloat(marker.style.width)/2,matrix.m42+parseFloat(marker.style.height)*foot],
      endpoint:p.endpoint,draws:p.draws,pending:p.pending.size,data:JSON.stringify(window.birdMotionFixture.data)};
  });
  const results=[];
  for(let arrival=0;arrival<3;arrival++) {
    await page.evaluate(() => { window.__pointFollow.time+=100;window.birdMotionFixture.advance(); });
    const expectedData=await page.evaluate(() => JSON.stringify(window.birdMotionFixture.data));
    const frames=[];
    for(const ms of [0,25,25,25,25,100]) {
      await page.evaluate(ms => window.__pointFollow.step(ms),ms);
      const state=await read();
      if(state.data!==expectedData) throw Error('Display animation changed full-precision source data');
      if(state.position.some((v,i)=>Math.abs(v-state.endpoint[i])>0.001)) throw Error(`Point detached from curve: ${JSON.stringify(state)}`);
      const {data,...frame}=state;frames.push(frame);
    }
    if(frames.at(-1).pending) throw Error('Display failed to catch up');
    results.push({arrival,distinctPositions:new Set(frames.map(f=>JSON.stringify(f.position))).size,frames});
  }
  const stopped=await read();
  await page.evaluate(() => window.__pointFollow.step(1000));
  const idle=await read();
  if(idle.draws!==stopped.draws) throw Error('Idle point kept redrawing');
  await page.screenshot({path:`output/playwright/other-markers-20261020/${markerStyle}.png`});
  if(requireSmooth && results.some(result=>result.distinctPositions<4)) throw Error(`${markerStyle} jumps directly to the received sample`);
  return {markerStyle,environment:'Chromium, production React, 1440x900; 100 ms-spaced data, each appended sample observed at 0/25/50/75/100/200 ms; no device timing or FPS measurement',results,
    sourceUnchanged:true,idleStopped:true,sourceData:JSON.parse(idle.data)};
}
