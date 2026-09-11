async (page) => {
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/src/hooks/use-backend-snapshot.ts*', async (route) => {
    const response = await route.fetch();
    let body = await response.text();
    const marker = 'const [snapshot, setSnapshot] = useState(null);';
    if (!body.includes(marker)) throw new Error('QA snapshot hook marker missing');
    body = body.replace(marker, marker + '\nwindow.__qaSetSnapshot = setSnapshot;');
    body += '\nDEMO_SNAPSHOT.devices.spectrometer = { state: "disconnected", label: "光谱仪", detail: "未连接", running: false, quality: "invalid" };\nDEMO_SNAPSHOT.devices.powerMeter.powerW = 0.006;\nDEMO_SNAPSHOT.automaticTest.currentA = 0;\n';
    await route.fulfill({ response, body });
  });
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto('http://127.0.0.1:1437/?demo=1');
  const reading = page.locator('.automatic-power-chart__reading');
  await reading.waitFor();
  await page.waitForFunction(() => document.querySelector('.automatic-power-chart__reading .automatic-metric-card__number')?.textContent === '0.006');
  const initial = await reading.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const chart = element.parentElement.getBoundingClientRect();
    const style = getComputedStyle(element);
    return {
      text: element.textContent,
      border: style.borderTopWidth,
      background: style.backgroundColor,
      pointerEvents: style.pointerEvents,
      rightOffset: chart.right - rect.right,
      topOffset: rect.top - chart.top,
      color: getComputedStyle(element.querySelector('.automatic-metric-card__number')).color,
    };
  });
  if (initial.border !== '0px' || initial.background !== 'rgba(0, 0, 0, 0)') throw new Error('Reading should have no border or background');
  if (initial.color !== 'rgb(255, 122, 26)') throw new Error('Power color differs from manual card');
  if (initial.topOffset !== 16 || initial.rightOffset !== 16 || initial.pointerEvents !== 'none') throw new Error('Reading placement or chart pointer handling incorrect');
  for (const state of ['is-on', 'is-off', 'is-on']) {
    await page.waitForFunction((state) => document.querySelector('.automatic-power-chart__reading .automatic-metric-card__recording-dot')?.classList.contains(state), state);
  }
  await page.screenshot({ path: 'output/playwright/power-only-live-reading/wide.png' });
  for (const [powerW, quality, expected] of [[12.345, 'live', '12.345'], [0, 'live', '0.000'], [-0.006, 'live', '-0.006'], [-0.006, 'stale', '--'], [null, 'invalid', '--']]) {
    await page.evaluate(({powerW, quality}) => window.__qaSetSnapshot((previous) => ({
      ...previous, devices: { ...previous.devices, powerMeter: { ...previous.devices.powerMeter, powerW, quality } },
    })), {powerW, quality});
    await page.waitForFunction((expected) => document.querySelector('.automatic-power-chart__reading .automatic-metric-card__number')?.textContent === expected, expected);
  }
  await page.evaluate(() => window.__qaSetSnapshot((previous) => ({ ...previous, devices: { ...previous.devices, powerMeter: { ...previous.devices.powerMeter, running: false } } })));
  await page.waitForFunction(() => !document.querySelector('.automatic-power-chart__reading .automatic-metric-card__recording-dot'));
  await page.evaluate(() => window.__qaSetSnapshot((previous) => ({ ...previous, devices: { ...previous.devices, powerMeter: { ...previous.devices.powerMeter, powerW: 0.006, quality: 'live', running: true } } })));
  await page.waitForFunction(() => document.querySelector('.automatic-power-chart__reading .automatic-metric-card__number')?.textContent === '0.006');
  await page.setViewportSize({ width: 1366, height: 768 });
  const narrow = await reading.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const chart = element.parentElement.getBoundingClientRect();
    return { fits: rect.left >= chart.left && rect.right <= chart.right, height: chart.height, pageOverflow: document.documentElement.scrollWidth > innerWidth };
  });
  if (!narrow.fits || narrow.pageOverflow || narrow.height < 200) throw new Error('Narrow chart layout failed');
  await page.screenshot({ path: 'output/playwright/power-only-live-reading/narrow.png' });
  await page.evaluate(() => window.__qaSetSnapshot((previous) => ({ ...previous, devices: { ...previous.devices, spectrometer: { ...previous.devices.spectrometer, running: true } } })));
  await page.waitForFunction(() => !document.querySelector('.automatic-power-chart__reading'));
  if (await page.locator('.automatic-metric-grid .automatic-metric-card').count() !== 4) throw new Error('Spectrum metrics changed');
  if (errors.length) throw new Error(errors.join('\n'));
  console.log(JSON.stringify({ initial, narrow, checks: ['live value updates without series changes', 'zero and negative values', 'stale and invalid placeholders', 'red recording dot blinks on/off/on', 'recording dot removed when stopped', 'spectrum mode keeps four original cards'], pageErrors: errors }));
}
