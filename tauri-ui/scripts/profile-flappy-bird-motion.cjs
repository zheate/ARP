// playwright-cli run-code --filename tauri-ui/scripts/profile-flappy-bird-motion.cjs
async (page) => {
  await page.addInitScript(() => {
    if (window.__birdDrawProfile) return;
    const probe = window.__birdDrawProfile = { draws: 0, costs: [], gaps: [], lastFrame: 0, measuring: false };
    const clear = CanvasRenderingContext2D.prototype.clearRect;
    CanvasRenderingContext2D.prototype.clearRect = function (...args) {
      if (this.canvas.getAttribute('aria-label') === '功率实时曲线') probe.draws++;
      return clear.apply(this, args);
    };
    const nativeFrame = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = callback => nativeFrame(time => {
      const before = probe.draws;
      const started = performance.now();
      callback(time);
      if (probe.measuring && probe.draws > before) probe.costs.push(performance.now() - started);
    });
    const observe = time => {
      if (probe.measuring && probe.lastFrame) probe.gaps.push(time - probe.lastFrame);
      probe.lastFrame = time;
      nativeFrame(observe);
    };
    nativeFrame(observe);
  });
  await page.route('**/bird-motion-fixture?*', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html data-theme="dark"><head><meta charset="utf-8"><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/scripts/fixtures/flappy-bird-motion.tsx"></script></body></html>' }));
  await page.setViewportSize({ width: 1440, height: 900 });
  const session = await page.context().newCDPSession(page);
  await session.send('Performance.enable');
  const metrics = async () => Object.fromEntries((await session.send('Performance.getMetrics')).metrics.map(({ name, value }) => [name, value]));
  const results = [];
  for (const cadence of [100, 200]) for (let trial = 0; trial < 3; trial++) {
    await page.goto('http://localhost:1420/bird-motion-fixture?demo=1');
    await page.getByRole('img', { name: '功率实时曲线', exact: true }).waitFor();
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(cadence => window.birdMotionFixture.start(cadence), cadence);
    await page.waitForFunction(() => window.birdMotionFixture.frames >= 5);
    await page.evaluate(() => {
      const p = window.__birdDrawProfile; p.draws = 0; p.costs = []; p.gaps = []; p.measuring = true;
      p.started = performance.now(); p.frames = window.birdMotionFixture.frames;
    });
    const before = await metrics();
    await page.waitForTimeout(4000);
    const after = await metrics();
    const result = await page.evaluate(() => {
      const p = window.__birdDrawProfile; p.measuring = false; window.birdMotionFixture.stop();
      const costs = p.costs.toSorted((a, b) => a - b);
      const gaps = p.gaps.toSorted((a, b) => a - b);
      return { elapsedMs: performance.now() - p.started, samples: window.birdMotionFixture.frames - p.frames,
        draws: p.draws, timedDraws: costs.length, drawTotalMs: costs.reduce((a, b) => a + b, 0),
        drawMeanMs: costs.reduce((a, b) => a + b, 0) / costs.length, drawP95Ms: costs[Math.floor(costs.length * 0.95)], drawMaxMs: costs.at(-1),
        frameMedianMs: gaps[Math.floor(gaps.length * 0.5)], frameP95Ms: gaps[Math.floor(gaps.length * 0.95)], gapsOver25Ms: gaps.filter(x => x > 25).length,
        points: window.birdMotionFixture.data.length, dpr: devicePixelRatio, hidden: document.hidden };
    });
    if (result.hidden || result.points !== 601) throw Error(`Invalid benchmark: ${JSON.stringify(result)}`);
    results.push({ cadence, trial, ...result, taskMs: (after.TaskDuration - before.TaskDuration) * 1000, scriptMs: (after.ScriptDuration - before.ScriptDuration) * 1000 });
  }
  await session.detach();
  return { environment: 'Local headed Chromium, production React runtime, 1440x900, 601 power points plus 8192-pixel spectrum', results };
}
