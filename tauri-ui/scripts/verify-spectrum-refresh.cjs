// playwright-cli run-code --filename. Open the fixture with ?mode=baseline/fixed.
// Use real production chart components, complete spectra, and no hardware.
async (page) => {
  const mode = new URL(page.url()).searchParams.get('mode') || 'fixed';
  await page.addInitScript(() => {
    if (window.__spectrumProbe) return;
    window.__spectrumProbe = { draws: 0, errors: [], yTicks: [] };
    const clear = CanvasRenderingContext2D.prototype.clearRect;
    CanvasRenderingContext2D.prototype.clearRect = function (...args) {
      if (this.canvas.getAttribute('aria-label') === '实时光谱曲线') {
        window.__spectrumProbe.draws++;
        window.__spectrumProbe.yTicks = [];
      }
      return clear.apply(this, args);
    };
    const text = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function (...args) {
      if (this.canvas.getAttribute('aria-label') === '实时光谱曲线' && args[1] < 60) window.__spectrumProbe.yTicks.push(String(args[0]));
      return text.apply(this, args);
    };
    addEventListener('error', event => window.__spectrumProbe.errors.push(event.message));
  });
  await page.setViewportSize({ width: 1180, height: 720 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.reload();
  const canvas = page.getByRole('img', { name: '实时光谱曲线', exact: true });
  await canvas.waitFor();
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(250);
  const before = await canvas.evaluate(c => c.toDataURL());
  await page.evaluate(() => { window.__spectrumProbe.draws = 0; });
  for (let i = 0; i < 30; i++) {
    await page.evaluate(() => window.spectrumRefreshFixture.tick());
    await page.waitForTimeout(25);
  }
  const metricsOnly = await page.evaluate(() => ({ ...window.__spectrumProbe, metric: document.querySelector('[data-testid="metric"]').textContent }));
  const unchangedPixels = before === await canvas.evaluate(c => c.toDataURL());
  await page.evaluate(() => { window.__spectrumProbe.draws = 0; });
  const arrivals = await page.evaluate(async () => {
    const latencies = [];
    for (let i = 0; i < 10; i++) {
      const before = window.__spectrumProbe.draws;
      const started = performance.now();
      window.spectrumRefreshFixture.advance();
      await new Promise((resolve, reject) => {
        function check() {
          if (window.__spectrumProbe.draws > before) return resolve();
          if (performance.now() - started > 1000) return reject(Error('New spectrum was not drawn'));
          requestAnimationFrame(check);
        }
        requestAnimationFrame(check);
      });
      latencies.push(performance.now() - started);
    }
    return { draws: window.__spectrumProbe.draws, frame: window.spectrumRefreshFixture.frame, latencies, data: window.spectrumRefreshFixture.data };
  });
  const finalPixels = await canvas.evaluate(c => c.toDataURL());
  await page.evaluate(() => window.spectrumRefreshFixture.scale(0.1));
  await page.waitForTimeout(60);
  const initialTicks = await page.evaluate(() => JSON.stringify(window.__spectrumProbe.yTicks));
  await page.evaluate(() => { for (let i = 0; i < 30; i++) window.spectrumRefreshFixture.tick(); });
  await page.waitForTimeout(60);
  const statusTicks = await page.evaluate(() => JSON.stringify(window.__spectrumProbe.yTicks));
  for (let i = 0; i < 4; i++) {
    await page.evaluate(() => window.spectrumRefreshFixture.advance());
    await page.waitForTimeout(35);
  }
  const fifthFrameTicks = await page.evaluate(() => JSON.stringify(window.__spectrumProbe.yTicks));
  await page.evaluate(() => window.spectrumRefreshFixture.advance());
  await page.waitForTimeout(60);
  const sixthFrameTicks = await page.evaluate(() => JSON.stringify(window.__spectrumProbe.yTicks));
  const axisUsesSpectrumFrames = initialTicks !== '[]' && initialTicks === statusTicks && initialTicks === fifthFrameTicks && initialTicks !== sixthFrameTicks;
  await page.screenshot({ path: `output/playwright/spectrum-response-20261022/${mode}-1180.png` });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.waitForTimeout(120);
  await page.screenshot({ path: `output/playwright/spectrum-response-20261022/${mode}-1920.png` });
  const widePixels = await canvas.evaluate(c => c.toDataURL());
  await page.evaluate(() => window.spectrumRefreshFixture.smoothing(false));
  await page.waitForTimeout(100);
  const smoothingWorks = widePixels !== await canvas.evaluate(c => c.toDataURL());
  const beforeWindow = await canvas.evaluate(c => c.toDataURL());
  await page.evaluate(() => window.spectrumRefreshFixture.window(975, 3));
  await page.waitForTimeout(100);
  const windowWorks = beforeWindow !== await canvas.evaluate(c => c.toDataURL());
  await page.evaluate(() => window.spectrumRefreshFixture.clear());
  const cleared = await canvas.count() === 0;
  await page.evaluate(() => window.spectrumRefreshFixture.advance());
  await canvas.waitFor();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  const report = { mode, metricsOnly, unchangedPixels, arrivals, finalPixels, axisUsesSpectrumFrames, smoothingWorks, windowWorks, cleared, overflow };
  const download = page.waitForEvent('download');
  await page.evaluate(({ mode, report }) => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(report)], { type: 'application/json' }));
    a.download = `${mode}-refresh.json`; a.click();
  }, { mode, report });
  await (await download).saveAs(`output/playwright/spectrum-response-20261022/${mode}-refresh.json`);
  if (metricsOnly.draws !== 0 || metricsOnly.metric !== '30' || !unchangedPixels || arrivals.frame !== 10 || arrivals.draws !== 10 || !axisUsesSpectrumFrames || !smoothingWorks || !windowWorks || !cleared || overflow || metricsOnly.errors.length) {
    throw Error(`Spectrum regression: ${JSON.stringify({ ...report, arrivals: { ...arrivals, data: undefined }, finalPixels: undefined })}`);
  }
  return { metricsOnly, unchangedPixels, arrivals: { ...arrivals, data: '8192 complete samples saved' }, axisUsesSpectrumFrames, smoothingWorks, windowWorks, cleared, overflow };
}
