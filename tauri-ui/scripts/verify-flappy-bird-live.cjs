// Full demo replay through the real automatic page, including the 10 s boundary.
async (page) => {
  await page.addInitScript(() => {
    localStorage.setItem('power-test.latest-point-marker', 'flappy-bird');
    const probe = window.__birdLive = { draws: 0, costs: [], frames: [], startedAt: 0, errors: [] };
    const proto = CanvasRenderingContext2D.prototype, stroke = proto.stroke, clear = proto.clearRect;
    for (const method of ['moveTo', 'lineTo', 'bezierCurveTo']) {
      const original = proto[method];
      proto[method] = function (...args) { this.__tip = args.slice(-2); return original.apply(this, args); };
    }
    proto.clearRect = function (...args) {
      if (this.canvas.getAttribute('aria-label') === '功率实时曲线') probe.draws++;
      return clear.apply(this, args);
    };
    proto.stroke = function (...args) {
      if (this.canvas.getAttribute('aria-label') === '功率实时曲线' && this.lineWidth > 1.1) probe.tip = this.__tip;
      return stroke.apply(this, args);
    };
    const raf = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = callback => raf(time => {
      const before = probe.draws, started = performance.now();
      callback(time);
      const cost = performance.now() - started;
      if (probe.startedAt && probe.draws !== before) {
        const bird = document.querySelector('.chart-flappy-bird');
        if (!bird || getComputedStyle(bird).visibility !== 'visible') return;
        const rect = bird.getBoundingClientRect(), chart = bird.parentElement.getBoundingClientRect();
        probe.frames.push({ time: (performance.now() - probe.startedAt) / 1000,
          bird: [rect.left - chart.left + rect.width / 2, rect.top - chart.top + rect.height / 2], tip: probe.tip });
        probe.costs.push(cost);
      }
    });
    addEventListener('error', event => probe.errors.push(event.message));
  });
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto('http://localhost:1420/?demo=1&run=1');
  const start = page.getByRole('button', { name: '开始', exact: true });
  await start.waitFor();
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => { window.__birdLive.startedAt = performance.now(); });
  await start.click();
  await page.waitForFunction(() => window.__birdLive.frames.some(frame => frame.time >= 22), null, { timeout: 30000 });
  await page.screenshot({ path: 'output/playwright/flappy-bird-axis-20261016/automatic-after-22s.png' });
  await page.getByRole('button', { name: '停止', exact: true }).click();
  const result = await page.evaluate(() => {
    const p = window.__birdLive;
    const windows = [[2, 9], [11, 20]].map(([from, to]) => {
      const frames = p.frames.filter(frame => frame.time >= from && frame.time < to);
      const costs = p.costs.filter((_, i) => p.frames[i].time >= from && p.frames[i].time < to).sort((a, b) => a - b);
      return { from, to, frames: frames.length, meanDrawMs: costs.reduce((a, b) => a + b, 0) / costs.length, p95DrawMs: costs[Math.floor(costs.length * 0.95)],
        maxMarkerError: Math.max(...frames.flatMap(frame => frame.bird.map((v, axis) => Math.abs(v - frame.tip[axis])))) };
    });
    return { windows, errors: p.errors, totalFrames: p.frames.length };
  });
  if (result.errors.length || result.windows.some(w => w.frames < 200 || w.maxMarkerError > 0.001)) throw Error(`Live replay failed: ${JSON.stringify(result)}`);
  return result;
}
