async (page) => {
  if (!await page.evaluate(() => Boolean(window.__birdProfile))) await page.addInitScript(() => {
    window.__birdProfile = { commits: 0, raf: 0, clears: {} };
    const raf = window.requestAnimationFrame;
    window.requestAnimationFrame = function (callback) {
      window.__birdProfile.raf++;
      return raf.call(window, callback);
    };
    const clear = CanvasRenderingContext2D.prototype.clearRect;
    CanvasRenderingContext2D.prototype.clearRect = function (...args) {
      const key = this.canvas.getAttribute('aria-label') || (this.canvas.classList.contains('chart-flappy-bird__frames') ? 'bird-sprite' : 'other');
      window.__birdProfile.clears[key] = (window.__birdProfile.clears[key] || 0) + 1;
      return clear.apply(this, args);
    };
    window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = {
      supportsFiber: true, renderers: new Map(),
      inject(renderer) { this.renderers.set(1, renderer); return 1; },
      onCommitFiberRoot() { window.__birdProfile.commits++; },
      onCommitFiberUnmount() {}, onPostCommitFiberRoot() {},
    };
  });
  const session = await page.context().newCDPSession(page);
  await session.send('Performance.enable');
  const metrics = async () => Object.fromEntries((await session.send('Performance.getMetrics')).metrics.map(({ name, value }) => [name, value]));
  const measure = async (label, duration) => {
    await page.evaluate(() => { window.__birdProfile = { commits: 0, raf: 0, clears: {} }; });
    const before = await metrics();
    await page.waitForTimeout(duration);
    const after = await metrics();
    const counts = await page.evaluate(() => window.__birdProfile);
    return { label, duration, counts, scriptMs: (after.ScriptDuration - before.ScriptDuration) * 1000, taskMs: (after.TaskDuration - before.TaskDuration) * 1000, layoutMs: (after.LayoutDuration - before.LayoutDuration) * 1000, styleMs: (after.RecalcStyleDuration - before.RecalcStyleDuration) * 1000 };
  };
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('http://localhost:1420/?demo=1');
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  await page.getByRole('combobox', { name: '自动测试功率实时曲线末端样式', exact: true }).selectOption('flappy-bird');
  await page.getByRole('button', { name: '自动测试', exact: true }).click();
  await page.waitForTimeout(600);
  const idle = await measure('static-demo', 5000);
  await page.goto('http://localhost:1420/?demo=1&run=1');
  await page.getByRole('button', { name: '开始', exact: true }).click();
  await page.waitForTimeout(600);
  const live = await measure('live-demo', 6000);
  await page.getByRole('button', { name: '停止', exact: true }).click();
  await session.detach();
  return { idle, live };
}
