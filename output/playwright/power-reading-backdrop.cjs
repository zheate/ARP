async (page) => {
  await page.route('**/src/lib/demo-snapshot.ts*', async route => {
    const response = await route.fetch();
    const body = await response.text();
    await route.fulfill({ response, body: body + `
      DEMO_SNAPSHOT.devices.spectrometer.running = false;
      DEMO_SNAPSHOT.devices.powerMeter.powerW = 1.238;
      DEMO_SNAPSHOT.devices.powerSupply.activeCurrentA = 0.59;
      DEMO_SNAPSHOT.automaticTest.currentA = 0.59;
      Object.assign(DEMO_SNAPSHOT.automaticTest.stability, { wavelengthRequired: false, holdElapsedS: 3.2, holdRequiredS: 5 });
      DEMO_SNAPSHOT.measurements.power = Array.from({ length: 61 }, (_, elapsedS) => ({ elapsedS, powerW: 0.06 + 1.18 * (1 - Math.exp(-elapsedS / 10)) }));
    ` });
  });
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto('http://127.0.0.1:1420/?demo=1');
  const reading = page.locator('.automatic-power-chart__reading');
  await reading.waitFor();
  const chart = page.locator('.automatic-power-chart');
  const baselineStyle = await page.addStyleTag({ content: '.automatic-power-chart__reading::before { display: none !important; }' });
  const before = { reading: await reading.boundingBox(), chart: await chart.boundingBox() };
  const clip = { x: before.chart.x + before.chart.width - 430, y: before.chart.y, width: 430, height: 250 };
  await page.screenshot({ path: 'output/playwright/power-reading-before.png', clip });
  await baselineStyle.evaluate(element => element.remove());
  const after = { reading: await reading.boundingBox(), chart: await chart.boundingBox() };
  if (JSON.stringify(before) !== JSON.stringify(after)) throw new Error('Backdrop changed layout');
  await page.screenshot({ path: 'output/playwright/power-reading-dark.png', clip });
  const hitTest = await reading.evaluate(element => {
    const rect = element.getBoundingClientRect();
    const target = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
    return { pointerEvents: getComputedStyle(element).pointerEvents, target: target?.tagName, insideReading: element.contains(target), pseudoBackground: getComputedStyle(element, '::before').backgroundColor };
  });
  if (hitTest.insideReading || hitTest.pointerEvents !== 'none') throw new Error('Backdrop blocks chart interaction');
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  await page.getByRole('button', { name: '浅色', exact: true }).click();
  await page.getByRole('button', { name: '自动测试', exact: true }).click();
  await page.screenshot({ path: 'output/playwright/power-reading-light.png', clip });
  const lightBackground = await reading.evaluate(element => getComputedStyle(element, '::before').backgroundColor);
  await page.setViewportSize({ width: 1100, height: 800 });
  const compact = await reading.evaluate(element => ({ width: element.getBoundingClientRect().width, overflow: document.documentElement.scrollWidth > innerWidth }));
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  await page.getByRole('button', { name: '深色', exact: true }).click();
  await page.getByRole('button', { name: '自动测试', exact: true }).click();
  await page.setViewportSize({ width: 1366, height: 900 });
  return { unchangedLayout: true, before, after, hitTest, lightBackground, compact };
}
