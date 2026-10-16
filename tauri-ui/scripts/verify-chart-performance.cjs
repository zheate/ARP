// Run from the repository root with playwright-cli run-code --filename <this file>.
// Uses only a browser fixture and the product chart components; no Tauri/hardware.
async (page) => {
  await page.addInitScript(() => {
    if (window.__chartRegression) return;
    window.__chartRegression = { baseDraws: 0, rectReads: 0 };
    const clear = CanvasRenderingContext2D.prototype.clearRect;
    CanvasRenderingContext2D.prototype.clearRect = function (...args) {
      if (this.canvas.hasAttribute('aria-label')) window.__chartRegression.baseDraws++;
      return clear.apply(this, args);
    };
    const rect = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = function (...args) {
      if (this.firstElementChild?.matches('canvas[aria-label]')) window.__chartRegression.rectReads++;
      return rect.apply(this, args);
    };
  });
  await page.setViewportSize({ width: 1180, height: 720 });
  await page.goto('http://localhost:1420/scripts/fixtures/chart-performance.html');
  const power = page.getByRole('img', { name: '功率实时曲线', exact: true });
  await power.waitFor();
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(300);
  const setup = await page.evaluate(() => window.__chartRegression);
  if (setup.rectReads !== 0) throw new Error(`Chart mount forced ${setup.rectReads} rectangle reads`);
  const box = await power.boundingBox();
  const original = await power.evaluate(canvas => canvas.toDataURL());
  await page.evaluate(() => { window.__chartRegression.baseDraws = 0; });
  for (let index = 0; index < 30; index++) {
    await page.mouse.move(box.x + 60 + (box.width - 95) * index / 30, box.y + box.height / 2);
    await page.waitForTimeout(18);
  }
  const hovered = await power.evaluate(canvas => ({
    base: canvas.toDataURL(),
    text: canvas.parentElement.textContent,
    overlayVisible: [...canvas.nextElementSibling.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data].some(value => value !== 0),
  }));
  if (hovered.base !== original || !hovered.overlayVisible || !hovered.text.includes('实时功率')) throw new Error('Hover changed base pixels or lost tooltip/crosshair');
  await page.mouse.move(2, 2); await page.waitForTimeout(80);
  const hoverDraws = await page.evaluate(() => window.__chartRegression.baseDraws);
  if (hoverDraws !== 0) throw new Error(`Hover unnecessarily repainted the base ${hoverDraws} times`);
  const clearOverlay = await power.evaluate(canvas => [...canvas.nextElementSibling.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data].every(value => value === 0));
  if (!clearOverlay) throw new Error('Pointer leave did not clear the crosshair');
  await page.evaluate(() => { window.__chartRegression.baseDraws = 0; window.chartPerformanceFixture.advance(); });
  await page.waitForTimeout(250);
  const updateDraws = await page.evaluate(() => window.__chartRegression.baseDraws);
  if (updateDraws !== 2) throw new Error(`Expected one redraw per changed chart, got ${updateDraws}`);
  const hiddenDraws = await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    try {
      window.__chartRegression.baseDraws = 0;
      window.chartPerformanceFixture.advance();
      return window.__chartRegression.baseDraws;
    } finally { delete document.visibilityState; }
  });
  if (hiddenDraws !== 2) throw new Error('Hidden-window updates no longer draw synchronously');
  const session = await page.context().newCDPSession(page);
  const sizes = [];
  for (const ratio of [1, 1.5, 2]) {
    // Change dimensions in the same CDP call; setViewportSize would reset DPR.
    await session.send('Emulation.setDeviceMetricsOverride', { width: 1200 + ratio * 40, height: 760, deviceScaleFactor: ratio, mobile: false });
    await page.waitForTimeout(150);
    sizes.push(await power.evaluate(canvas => {
      const box = canvas.getBoundingClientRect();
      const ratio = Math.min(Math.max(devicePixelRatio, 1), 2);
      return { ratio, correct: canvas.width === Math.round(Math.round(box.width) * ratio) && canvas.height === Math.round(Math.round(box.height) * ratio) };
    }));
  }
  if (sizes.some((size, index) => !size.correct || size.ratio !== [1, 1.5, 2][index])) throw new Error('Canvas backing size does not match its CSS size and DPR');
  await session.send('Emulation.clearDeviceMetricsOverride'); await session.detach();
  const beforeTheme = await power.evaluate(canvas => canvas.toDataURL());
  await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });
  await page.waitForTimeout(150);
  const afterTheme = await power.evaluate(canvas => canvas.toDataURL());
  if (beforeTheme === afterTheme) throw new Error('Theme change did not repaint canvas');
  return { mountRectReads: setup.rectReads, hoverBaseDraws: hoverDraws, changedFrameDraws: updateDraws, hiddenFrameDraws: hiddenDraws, tooltipAndCrosshair: true, pointerLeaveClears: true, sizes, themeRepaint: true };
}
