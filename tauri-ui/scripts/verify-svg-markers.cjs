// Run using playwright-cli run-code --filename. Product chart, synthetic data,
// no Python bridge or real hardware.
async (page) => {
  const failures = [];
  page.on('pageerror', error => failures.push(error.message));
  const requests = [];
  page.on('request', request => requests.push(request.url()));
  await page.route('**/svg-marker-fixture?*', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><head><meta charset="utf-8"><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/scripts/fixtures/character-markers.tsx"></script></body></html>' }));
  await page.setViewportSize({ width: 1180, height: 720 });
  await page.goto('http://localhost:1420/svg-marker-fixture?demo=1');
  await page.getByRole('heading', { name: '曲线末端角色验证' }).waitFor();
  await page.evaluate(() => document.fonts.ready);
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  const settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const styles = await page.locator('select option').evaluateAll(options => options.map(option => option.value));
  if (styles.length !== 7 || styles.includes('ecg') || styles.includes('comet')) throw Error('Removed marker style is still offered');
  const results = [];
  const selector = '.chart-character-marker, .chart-flappy-bird, [data-static-marker]';
  for (const width of [1180, 1920]) {
    await page.setViewportSize({ width, height: 900 });
    for (const scale of [1, 2]) for (const style of styles) {
      await page.evaluate(({ style, scale }) => window.characterMarkerFixture.render({ style, displayScale: scale, rightAxis: true }), { style, scale });
      await settle();
      await page.waitForFunction(() => [...document.querySelectorAll('.chart-character-marker,.chart-flappy-bird')].every(marker => marker.dataset.vectorReady === 'true'));
      const result = await page.evaluate(selector => {
        const chart = document.querySelector('canvas[aria-label="角色末端验证"]');
        const box = chart.getBoundingClientRect();
        const markers = [...document.querySelectorAll(selector)];
        const first = markers[0], rect = first.getBoundingClientRect();
        const dinosaur = ['dinosaur', 'green-dinosaur'].includes(first.dataset.markerStyle);
        const point = [rect.left - box.left + rect.width / 2, rect.top - box.top + rect.height * (dinosaur ? 305 / 310 : 0.5)];
        return { count: markers.length, error: point.map((v, i) => Math.abs(v - chart.__qaEndpoint[i])),
          visible: markers.every(marker => getComputedStyle(marker).visibility === 'visible'),
          vectors: markers.every(marker => (marker.querySelector('svg') || marker.querySelector('img[src$=".svg"]')) && !marker.querySelector('canvas,image,img[src$=".png"]')),
          contained: markers.every(marker => { const r = marker.getBoundingClientRect(); return r.left >= box.left && r.right <= box.right && r.top >= box.top && r.bottom <= box.bottom; }),
          data: JSON.stringify(window.characterMarkerFixture.data) };
      }, selector);
      if (result.count !== 2 || result.error.some(value => value > 0.001) || !result.visible || !result.vectors || !result.contained) throw Error(JSON.stringify({ style, scale, width, ...result }));
      results.push({ style, scale, width, aligned: true, contained: true });
    }
  }
  await page.evaluate(() => window.characterMarkerFixture.render({ style: 'dinosaur', rightAxis: false }));
  await settle();
  const animation = page.locator('.chart-character-marker__body [data-marker-animation]');
  await page.waitForFunction(() => [...document.querySelectorAll('[data-marker-animation]')].every(el => getComputedStyle(el).animationPlayState === 'running'));
  const matrix = await animation.evaluate(el => getComputedStyle(el).transform);
  await page.waitForFunction(({ matrix }) => getComputedStyle(document.querySelector('.chart-character-marker__body [data-marker-animation]')).transform !== matrix, { matrix });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  if (await animation.evaluate(el => getComputedStyle(el).animationName) !== 'none') throw Error('Reduced motion is ignored');
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.evaluate(() => window.characterMarkerFixture.render({ hidden: true }));
  await page.waitForFunction(() => [...document.querySelectorAll('[data-marker-animation]')].every(el => getComputedStyle(el).animationPlayState === 'paused'));
  await page.evaluate(() => window.characterMarkerFixture.render({ hidden: false, data: [] }));
  await settle();
  if (await page.locator(selector).evaluateAll(markers => markers.some(marker => getComputedStyle(marker).visibility !== 'hidden'))) throw Error('Empty data left a visible marker');
  await page.evaluate(() => window.characterMarkerFixture.reset());
  await settle();
  await page.evaluate(() => window.characterMarkerFixture.render({ showPoints: true }));
  await settle();
  if (await page.locator(selector).count()) throw Error('All-point mode also drew an endpoint marker');
  await page.route('**/svg-gallery-fixture', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><head><meta charset="utf-8"><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/scripts/fixtures/svg-marker-gallery.tsx"></script></body></html>' }));
  await page.goto('http://localhost:1420/svg-gallery-fixture');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.getByRole('heading', { name: 'SVG 末端光标 · 1× / 2× / 4×' }).waitFor();
  await settle();
  await page.screenshot({ path: 'output/playwright/svg-markers-20261020/gallery.png', fullPage: true });
  await page.evaluate(() => document.body.style.zoom = '2');
  await page.screenshot({ path: 'output/playwright/svg-markers-20261020/gallery-zoom-200.png', fullPage: true });
  if (requests.some(url => /chart-markers\/.*\.png/.test(url))) throw Error('A raster sprite was requested');
  if (failures.length) throw Error(failures.join('\n'));
  return { results, animation: true, reducedMotion: true, hiddenPaused: true, emptyHidden: true, allPointMode: true, noRasterRequests: true, pageErrors: failures };
}
