// Deterministic browser checks against the product chart. No backend/devices.
async (page, markerStyle = 'flappy-bird', outputFolder = 'output/playwright/flappy-bird-smooth-20261016') => {
  await page.addInitScript(() => {
    const now = performance.now.bind(performance);
    const raf = requestAnimationFrame.bind(window), cancel = cancelAnimationFrame.bind(window);
    const probe = window.__birdMotion = { hold: false, time: 0, pending: new Map(), draws: 0, endpoints: {}, directions: {} };
    let id = -1;
    performance.now = () => probe.hold ? probe.time : now();
    window.requestAnimationFrame = callback => {
      if (!probe.hold) return raf(callback);
      const next = id--; probe.pending.set(next, callback); return next;
    };
    window.cancelAnimationFrame = id => { probe.pending.delete(id); cancel(id); };
    probe.begin = () => { probe.time = now() + 1000; probe.hold = true; };
    probe.step = ms => {
      probe.time += ms;
      const callbacks = [...probe.pending.values()]; probe.pending.clear();
      callbacks.forEach(callback => callback(probe.time));
    };
    const proto = CanvasRenderingContext2D.prototype, begin = proto.beginPath, clear = proto.clearRect, stroke = proto.stroke;
    proto.beginPath = function (...args) { this.__endpoint = null; this.__direction = null; return begin.apply(this, args); };
    for (const method of ['moveTo', 'lineTo', 'bezierCurveTo']) {
      const original = proto[method];
      proto[method] = function (...args) {
        const end = args.slice(-2), control = method === 'bezierCurveTo' ? args.slice(2,4) : this.__endpoint;
        if (method !== 'moveTo' && control && Math.hypot(end[0]-control[0],end[1]-control[1]) > 1e-8) this.__direction = Math.atan2(end[1]-control[1],end[0]-control[0]);
        else if (method !== 'moveTo') this.__direction = null; // A zero-length prefix has no visible tangent.
        this.__endpoint = end; return original.apply(this, args);
      };
    }
    proto.clearRect = function (...args) {
      if (this.canvas.getAttribute('aria-label') === '小鸟同步验证') { probe.draws++; probe.endpoints = {}; probe.directions = {}; }
      return clear.apply(this, args);
    };
    proto.stroke = function (...args) {
      if (this.canvas.getAttribute('aria-label') === '小鸟同步验证' && this.__endpoint && ['#00a884', '#b05cff'].includes(this.strokeStyle)) {
        probe.endpoints[this.strokeStyle] = this.__endpoint;
        probe.directions[this.strokeStyle] = this.__direction;
      }
      return stroke.apply(this, args);
    };
  });
  await page.route('**/bird-smooth-fixture?*', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html data-theme="dark"><head><meta charset="utf-8"><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/scripts/fixtures/flappy-bird-sync.tsx"></script></body></html>' }));
  await page.setViewportSize({ width: 1440, height: 720 });
  await page.goto('http://localhost:1420/bird-smooth-fixture?demo=1');
  await page.getByRole('img', { name: '小鸟同步验证', exact: true }).waitFor();
  await page.evaluate(() => document.fonts.ready);
  const settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await settle();
  await page.evaluate(style => window.birdSyncFixture.render({ smooth: true, rightAxis: true, style }), markerStyle);
  await settle();
  await page.waitForFunction(() => [...document.querySelectorAll('.chart-character-marker,.chart-flappy-bird')].every(marker => marker.dataset.vectorReady === 'true'));
  const read = () => page.evaluate(() => ({ draws: window.__birdMotion.draws, pending: window.__birdMotion.pending.size, endpoints: Object.values(window.__birdMotion.endpoints),
    directions: Object.values(window.__birdMotion.directions),
    angles: [...document.querySelectorAll('.chart-flappy-bird,.chart-character-marker,[data-static-marker]')].filter(el => getComputedStyle(el).visibility === 'visible').map(el => el.dataset.staticMarker === 'paper-plane' ? parseFloat(el.style.getPropertyValue('--marker-angle')) : null),
    birds: [...document.querySelectorAll('.chart-flappy-bird,.chart-character-marker,[data-static-marker]')].filter(el => getComputedStyle(el).visibility === 'visible').map(el => {
      const rect = el.getBoundingClientRect(), container = el.parentElement.getBoundingClientRect();
      const anchorY = ['dinosaur', 'green-dinosaur'].includes(el.dataset.markerStyle) ? 305 / 310 : .5;
      return [rect.left - container.left + rect.width / 2, rect.top - container.top + rect.height * anchorY];
    }), data: JSON.stringify(window.birdSyncFixture.data) }));
  const aligned = state => {
    if (state.birds.length !== state.endpoints.length) throw Error(`Marker count differs: ${JSON.stringify(state)}`);
    state.birds.forEach((bird, i) => bird.forEach((value, axis) => {
      if (Math.abs(value - state.endpoints[i][axis]) > 0.001) throw Error(`Marker detached at an animation frame: ${JSON.stringify(state)}`);
    }));
    state.angles.forEach((angle, i) => {
      if (angle !== null && state.directions[i] !== null && Math.abs(angle-state.directions[i]) > 1e-7) throw Error(`Plane does not follow curve tangent: ${JSON.stringify(state)}`);
    });
  };
  await page.evaluate(() => window.__birdMotion.begin());
  const step = async ms => { await page.evaluate(ms => window.__birdMotion.step(ms), ms); const state = await read(); aligned(state); return state; };
  const initial = [{ time: 0, power: 1, voltage: 20 }, { time: 3.141, power: 2.718, voltage: 61.337 }];
  let data = initial;
  const results = [];
  const transition = async (name, point, patch = {}) => {
    const before = await read(); data = [...data, point];
    await page.evaluate(({ data, patch }) => {
      window.__birdMotion.time += 200;
      window.birdSyncFixture.render({ ...patch, data: Object.freeze(data.map(row => Object.freeze(row))) });
    }, { data, patch });
    const held = await read();
    if (held.draws !== before.draws || JSON.stringify(held.birds) !== JSON.stringify(before.birds)) throw Error(`${name}: moved before drawing`);
    const frames = [];
    for (const ms of [0, 50, 50, 50, 50]) frames.push(await step(ms));
    if (new Set(frames.map(frame => JSON.stringify(frame.birds))).size < 4) throw Error(`${name}: no intermediate positions`);
    if (frames.some(frame => frame.data !== held.data)) throw Error(`${name}: source samples changed`);
    if (frames[4].pending !== 0) throw Error(`${name}: animation did not stop at the latest sample`);
    const stopped = await step(500);
    if (stopped.draws !== frames[4].draws) throw Error(`${name}: continued drawing after catch-up`);
    results.push({ name, positions: frames.map(frame => frame.birds), sourceUnchanged: true, idleStopped: true });
  };
  await transition('linear', { time: 5.123, power: 6.789, voltage: 73.21 });
  await transition('monotone + scrolling/time and power scales', { time: 6.731, power: 3.337, voltage: 82.93 }, { monotone: true, xDomain: [1, 11], yDomain: [0, 14] });
  // Real power axes have explicit ticks and their upper bound equals the latest
  // sample after 10 s. A padded generic axis misses this exact failure mode.
  const checkRightEdge = async (name, previousTime, nextTime, minimum) => {
    const oldPower = 2, nextPower = 8;
    await page.evaluate(({ previousTime, minimum, oldPower }) => {
      window.birdSyncFixture.render({
        data: [{ time: minimum, power: 1, voltage: 10 }, { time: previousTime, power: oldPower, voltage: 20 }],
        xDomain: [minimum, previousTime], xTicks: [minimum, previousTime],
        monotone: false, yDomain: [0, 10],
      });
      window.__birdMotion.step(500);
    }, { previousTime, minimum, oldPower });
    const nextMinimum = previousTime >= 60 ? nextTime - 60 : minimum;
    await page.evaluate(({ nextTime, nextPower, nextMinimum }) => {
      window.__birdMotion.time += 200;
      window.birdSyncFixture.render({ data: [...window.birdSyncFixture.data, { time: nextTime, power: nextPower, voltage: 80 }],
        xDomain: [nextMinimum, nextTime], xTicks: [nextMinimum, nextTime] });
    }, { nextTime, nextPower, nextMinimum });
    const frames = [];
    for (const ms of [0, 50, 50, 50, 50]) frames.push(await step(ms));
    const firstY = frames[0].birds[0][1], lastY = frames[4].birds[0][1];
    // With a fixed y scale and a linear segment, 50% time must display 50% y.
    const expectedHalfY = (firstY + lastY) / 2;
    if (Math.abs(frames[2].birds[0][1] - expectedHalfY) > 0.001) throw Error(`${name}: endpoint stalled until axis catch-up: ${JSON.stringify(frames.map(frame => frame.birds))}`);
    results.push({ name, positions: frames.map(frame => frame.birds), midpointIsHalfway: true });
  };
  await checkRightEdge('10-second expanding axis', 10, 10.2, 0);
  await checkRightEdge('60-second scrolling axis', 60, 60.2, 0);
  // Restore the original sequence before the burst checks below.
  await page.evaluate(data => {
    window.birdSyncFixture.render({ data, xDomain: [1, 11], xTicks: undefined, monotone: true, yDomain: [0, 14] });
    window.__birdMotion.step(500);
  }, data);
  await page.evaluate(() => {
    window.__birdMotion.time += 100;
    window.birdSyncFixture.render({ data: [...window.birdSyncFixture.data, { time: 7.731, power: 4.337, voltage: 86.93 }] });
  });
  await step(50);
  await page.screenshot({ path: `${outputFolder}/mid-animation.png` });
  await page.evaluate(() => window.birdSyncFixture.render({ data: [...window.birdSyncFixture.data, { time: 8.531, power: 5.337, voltage: 91.23 }] }));
  const burst = [];
  for (const ms of [0, 20, 20, 20]) burst.push(await step(ms));
  if (burst[3].pending !== 0) throw Error('Burst did not catch up in its bounded transition');
  results.push({ name: 'burst during animation', positions: burst.map(frame => frame.birds) });
  await page.evaluate(() => {
    window.__birdMotion.time += 200;
    window.birdSyncFixture.render({ data: [...window.birdSyncFixture.data, { time: 9.131, power: 7.137, voltage: 94.23 }] });
  });
  await step(40);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.waitForFunction(() => window.__birdMotion.pending.size === 0, null, { polling: 20 });
  aligned(await read());
  const animationSelector = '.chart-flappy-bird__frames,.chart-character-marker__frames,.chart-character-marker__flame';
  const reduced = await page.locator(animationSelector).evaluateAll(elements => elements.map(el => getComputedStyle(el).animationName));
  if (reduced.some(name => name !== 'none')) throw Error('Reduced motion did not stop decorative animation');
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.evaluate(() => {
    window.__birdMotion.time += 200;
    window.birdSyncFixture.render({ data: [...window.birdSyncFixture.data, { time: 9.631, power: 6.137, voltage: 93.23 }] });
  });
  await step(40);
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  const hidden = await read(); aligned(hidden);
  if (hidden.pending) throw Error('Hidden page left an active animation');
  if (await page.locator(animationSelector).evaluateAll(elements => elements.some(el => el.style.animationPlayState !== 'paused'))) throw Error('Hidden decorative animation kept running');
  await page.evaluate(() => { delete document.visibilityState; delete document.hidden; document.dispatchEvent(new Event('visibilitychange')); window.__birdMotion.hold = false; });
  for (const theme of ['light', 'dark']) for (const width of [1180, 1440]) {
    await page.setViewportSize({ width, height: 720 });
    await page.evaluate(theme => { document.documentElement.dataset.theme = theme; }, theme);
    await settle(); aligned(await read());
    await page.screenshot({ path: `${outputFolder}/${theme}-${width}.png` });
  }
  const frameSelector = '.chart-flappy-bird__frames,.chart-character-marker__frames';
  let wing = null;
  if (await page.locator(frameSelector).count()) {
    wing = await page.locator(frameSelector).first().evaluate(el => ({ duration: getComputedStyle(el).animationDuration, timing: getComputedStyle(el).animationTimingFunction }));
    const duration = markerStyle === 'flappy-bird' || markerStyle === 'bee' ? '0.4s' : '0.5s';
    if (wing.duration !== duration || wing.timing !== 'steps(4)') throw Error(`Character cadence changed: ${JSON.stringify(wing)}`);
    await page.waitForFunction(selector => [...document.querySelectorAll(selector)].every(el => el.style.animationPlayState === 'running'), frameSelector);
    const frameTransform = await page.locator(frameSelector).first().evaluate(el => getComputedStyle(el).transform);
    await page.waitForFunction(({selector, frameTransform}) => getComputedStyle(document.querySelector(selector)).transform !== frameTransform, {selector:frameSelector, frameTransform});
  }
  await page.evaluate(() => { window.__birdMotion.begin(); window.birdSyncFixture.render({ data: [] }); window.__birdMotion.step(0); });
  const empty = await read();
  if (empty.birds.length || empty.pending) throw Error('Empty data left a visible bird or animation');
  await page.evaluate(() => { window.birdSyncFixture.render({ data: [{ time: 0, power: 1, voltage: 20 }, { time: 2, power: 3, voltage: 40 }] }); window.__birdMotion.step(0); });
  const reset = await read(); aligned(reset);
  if (reset.pending) throw Error('New session animated from the old one');
  await page.evaluate(() => {
    window.__birdMotion.time += 200;
    window.birdSyncFixture.render({data:[...window.birdSyncFixture.data,{time:2.8,power:5.3,voltage:63.13}]});
    window.__birdMotion.step(20);
  });
  const switchedStyles=[];
  for (const style of ['paper-plane','paw','flappy-bird','bee','green-dinosaur','dinosaur','point',markerStyle]) {
    const source=(await read()).data;
    await page.evaluate(style => { window.birdSyncFixture.render({style}); window.__birdMotion.step(20); },style);
    await page.waitForFunction(() => [...document.querySelectorAll('.chart-character-marker,.chart-flappy-bird')].every(marker => marker.dataset.vectorReady === 'true'), null, {polling:20});
    const state=await read(); aligned(state);
    if(state.data!==source || state.birds.length!==2) throw Error(`Switching to ${style} changed source data or markers`);
    switchedStyles.push(style);
  }
  await page.evaluate(() => {
    window.__birdMotion.time += 100;
    window.birdSyncFixture.render({ data: [...window.birdSyncFixture.data, { time: 3, power: 4, voltage: 50 }] });
    window.birdSyncFixture.unmount();
  });
  if ((await read()).pending) throw Error('Unmount left an animation callback');
  return { markerStyle, results, switchedStyles, tangentChecked: true, reducedMotion: reduced, hiddenStopped: true, emptyAndReset: true, unmountCancelled: true, wing };
}
