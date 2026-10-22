// From repo root: playwright-cli run-code --filename tauri-ui/scripts/verify-flappy-bird-sync.cjs
// Gate real chart draws to catch DOM markers moving before their Canvas curve.
async (page) => {
  await page.addInitScript(() => {
    const nativeFrame = window.requestAnimationFrame.bind(window);
    const nativeCancel = window.cancelAnimationFrame.bind(window);
    const pending = new Map();
    let nextId = -1;
    const probe = window.__birdSync = { hold: false, pending, draws: 0, endpoints: {} };
    window.requestAnimationFrame = callback => {
      if (!probe.hold) return nativeFrame(callback);
      const id = nextId--;
      pending.set(id, callback);
      return id;
    };
    window.cancelAnimationFrame = id => { pending.delete(id); nativeCancel(id); };
    probe.release = () => new Promise(resolve => nativeFrame(time => {
      probe.hold = false;
      const callbacks = [...pending.values()];
      pending.clear();
      callbacks.forEach(callback => callback(time));
      resolve();
    }));
    const proto = CanvasRenderingContext2D.prototype;
    const begin = proto.beginPath;
    proto.beginPath = function (...args) { this.__lastEndpoint = null; return begin.apply(this, args); };
    for (const name of ['moveTo', 'lineTo', 'bezierCurveTo']) {
      const original = proto[name];
      proto[name] = function (...args) { this.__lastEndpoint = args.slice(-2); return original.apply(this, args); };
    }
    const clear = proto.clearRect;
    proto.clearRect = function (...args) {
      if (this.canvas.getAttribute('aria-label') === '小鸟同步验证') { probe.draws++; probe.endpoints = {}; }
      return clear.apply(this, args);
    };
    const stroke = proto.stroke;
    proto.stroke = function (...args) {
      if (this.canvas.getAttribute('aria-label') === '小鸟同步验证' && this.__lastEndpoint && ['#00a884', '#b05cff'].includes(this.strokeStyle)) {
        probe.endpoints[this.strokeStyle] = this.__lastEndpoint;
      }
      return stroke.apply(this, args);
    };
    addEventListener('error', event => { probe.error = event.message; });
  });
  await page.route('**/bird-sync-fixture?*', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><head><meta charset="utf-8"><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/scripts/fixtures/flappy-bird-sync.tsx"></script></body></html>' }));
  await page.setViewportSize({ width: 1180, height: 720 });
  await page.goto('http://localhost:1420/bird-sync-fixture?demo=1');
  await page.getByRole('img', { name: '小鸟同步验证', exact: true }).waitFor();
  await page.evaluate(() => document.fonts.ready);
  const settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await settle();
  const read = () => page.evaluate(() => ({
    draws: window.__birdSync.draws,
    endpoints: window.__birdSync.endpoints,
    birds: [...document.querySelectorAll('.chart-flappy-bird')].filter(el => getComputedStyle(el).visibility !== 'hidden').map(el => {
      const transform = new DOMMatrix(getComputedStyle(el).transform);
      return [transform.m41 + parseFloat(el.style.width) / 2, transform.m42 + parseFloat(el.style.height) / 2];
    }),
    error: window.__birdSync.error,
  }));
  const aligned = (state, count = 1) => {
    if (state.error) throw Error(state.error);
    const endpoints = Object.values(state.endpoints);
    if (state.birds.length !== count || endpoints.length !== count) throw Error(`Wrong endpoint count: ${JSON.stringify(state)}`);
    state.birds.forEach((bird, i) => {
      if (bird.some((value, axis) => Math.abs(value - endpoints[i][axis]) > 0.001)) throw Error(`Bird detached from curve: ${JSON.stringify(state)}`);
    });
  };
  const results = [];
  const heldUpdate = async (name, patches, count = 1) => {
    const before = await read();
    await page.evaluate(patches => {
      window.__birdSync.hold = true;
      patches.forEach(patch => window.birdSyncFixture.render(patch));
    }, patches);
    await page.waitForFunction(() => window.__birdSync.pending.size > 0);
    const held = await read();
    if (held.draws !== before.draws || JSON.stringify(held.birds) !== JSON.stringify(before.birds)) throw Error(`Marker moved before Canvas draw (${name}): ${JSON.stringify({ before, held })}`);
    await page.evaluate(() => window.__birdSync.release());
    const after = await read();
    if (after.draws !== before.draws + 1) throw Error(`Expected one coalesced draw (${name}): ${JSON.stringify({ before, after })}`);
    aligned(after, count);
    results.push({ name, draws: after.draws - before.draws, birds: after.birds });
  };
  await heldUpdate('new sample', [{ data: [{ time: 0, power: 1, voltage: 20 }, { time: 4.123, power: 6.789, voltage: 73.21 }] }]);
  await heldUpdate('coalesced samples', [
    { data: [{ time: 0, power: 1, voltage: 20 }, { time: 5.31, power: 4.27, voltage: 63.17 }] },
    { data: [{ time: 0, power: 1, voltage: 20 }, { time: 6.37, power: 5.43, voltage: 67.19 }] },
  ]);
  await heldUpdate('scrolling axis', [{ xDomain: [2, 12] }]);
  await heldUpdate('empty data', [{ data: [] }], 0);
  await heldUpdate('restored data', [{ data: [{ time: 2.1, power: 1.3, voltage: 11.9 }, { time: 7.731, power: 3.337, voltage: 72.93 }] }]);
  await page.evaluate(() => window.birdSyncFixture.render({ rightAxis: true }));
  await settle();
  aligned(await read(), 2);
  await heldUpdate('two axes', [{ data: [{ time: 2.1, power: 1.3, voltage: 11.9 }, { time: 8.731, power: 4.337, voltage: 82.93 }] }], 2);
  const renderChecks = [];
  for (const theme of ['light', 'dark']) for (const width of [1180, 1440]) {
    await page.setViewportSize({ width, height: 720 });
    await page.evaluate(theme => { document.documentElement.dataset.theme = theme; }, theme);
    await settle();
    const state = await read();
    aligned(state, 2);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    if (overflow) throw Error('Chart has horizontal overflow');
    await page.screenshot({ path: `output/playwright/flappy-bird-sync-20261016/${theme}-${width}.png` });
    renderChecks.push({ theme, width, birds: state.birds });
  }
  await page.evaluate(() => window.birdSyncFixture.render({ style: 'point' }));
  await settle();
  if (await page.locator('.chart-flappy-bird').count()) throw Error('Switch to point left a bird behind');
  await page.evaluate(() => window.birdSyncFixture.render({ style: 'flappy-bird' }));
  await settle();
  aligned(await read(), 2);
  const playback = await page.locator('.chart-flappy-bird__frames').first().evaluate(el => ({ duration: getComputedStyle(el).animationDuration, timing: getComputedStyle(el).animationTimingFunction }));
  if (playback.duration !== '0.4s' || !['steps(4)', 'steps(4, end)'].includes(playback.timing)) throw Error(`Wing cadence changed: ${JSON.stringify(playback)}`);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const reduced = await page.locator('.chart-flappy-bird__frames').first().evaluate(el => getComputedStyle(el).animationName);
  if (reduced !== 'none') throw Error('Reduced motion no longer stops flapping');
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  const hidden = await page.evaluate(() => {
    const probe = window.__birdSync;
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    try {
      probe.hold = true;
      const before = probe.draws;
      window.birdSyncFixture.render({ data: [{ time: 2.1, power: 1.3, voltage: 11.9 }, { time: 9.131, power: 7.137, voltage: 91.23 }] });
      return { draws: probe.draws - before, pending: probe.pending.size };
    } finally { delete document.visibilityState; probe.hold = false; }
  });
  if (hidden.draws !== 1 || hidden.pending !== 0) throw Error(`Hidden draw no longer synchronous: ${JSON.stringify(hidden)}`);
  aligned(await read(), 2);
  await page.evaluate(() => { window.__birdSync.hold = true; window.birdSyncFixture.render({ xDomain: [3, 13] }); });
  await page.waitForFunction(() => window.__birdSync.pending.size > 0);
  await page.evaluate(() => window.birdSyncFixture.unmount());
  const pendingAfterUnmount = await page.evaluate(() => window.__birdSync.pending.size);
  if (pendingAfterUnmount !== 0) throw Error('Unmount left a queued draw');
  await page.evaluate(() => window.__birdSync.release());
  return { results, renderChecks, playback, reducedMotion: reduced, hidden, pendingAfterUnmount };
}
