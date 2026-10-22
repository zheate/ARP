// Matched browser-only workload: status every 100 ms, full spectrum every 400 ms.
async (page) => {
  const mode = new URL(page.url()).searchParams.get('mode') || 'fixed';
  await page.addInitScript(() => {
    if (window.__spectrumProbe) return;
    window.__spectrumProbe = { draws: 0, errors: [] };
    const clear = CanvasRenderingContext2D.prototype.clearRect;
    CanvasRenderingContext2D.prototype.clearRect = function (...args) {
      if (this.canvas.getAttribute('aria-label') === '实时光谱曲线') window.__spectrumProbe.draws++;
      return clear.apply(this, args);
    };
  });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.reload();
  const canvas = page.getByRole('img', { name: '实时光谱曲线', exact: true });
  await canvas.waitFor();
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(250);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Performance.enable');
  const before = (await cdp.send('Performance.getMetrics')).metrics;
  const workload = await page.evaluate(async () => {
    window.__spectrumProbe.draws = 0;
    const started = performance.now();
    const synchronousWorkMs = [];
    for (let i = 1; i <= 120; i++) {
      const begin = performance.now();
      if (i % 4 === 0) window.spectrumRefreshFixture.advance();
      else window.spectrumRefreshFixture.tick();
      synchronousWorkMs.push(performance.now() - begin);
      await new Promise(resolve => setTimeout(resolve, Math.max(0, started + i * 100 - performance.now())));
    }
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    return { durationMs: performance.now() - started, synchronousWorkMs, draws: window.__spectrumProbe.draws, frames: window.spectrumRefreshFixture.frame, errors: window.__spectrumProbe.errors, data: window.spectrumRefreshFixture.data };
  });
  const after = (await cdp.send('Performance.getMetrics')).metrics;
  const finalPixels = await canvas.evaluate(c => c.toDataURL());
  const report = { mode, userAgent: await page.evaluate(() => navigator.userAgent), before, after, workload, finalPixels };
  const download = page.waitForEvent('download');
  await page.evaluate(({ mode, report }) => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(report)], { type: 'application/json' }));
    a.download = `${mode}-workload.json`; a.click();
  }, { mode, report });
  await (await download).saveAs(`output/playwright/spectrum-response-20261022/${mode}-workload.json`);
  await cdp.detach();
  if (workload.frames !== 30 || workload.errors.length) throw Error('Spectrum workload lost a frame or raised an error');
  const task = metrics => metrics.find(m => m.name === 'TaskDuration').value;
  return { mode, durationMs: workload.durationMs, draws: workload.draws, frames: workload.frames, mainThreadTaskSeconds: task(after) - task(before) };
}
