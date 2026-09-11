async (page) => {
  await page.route('**/src/lib/demo-snapshot.ts*', async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, body: (await response.text()) + `
      DEMO_SNAPSHOT.backend.notices = [];
      DEMO_SNAPSHOT.devices.spectrometer.running = false;
      DEMO_SNAPSHOT.devices.spectrometer.connected = false;
      DEMO_SNAPSHOT.devices.powerMeter.powerW = 0.038;
      DEMO_SNAPSHOT.devices.powerSupply.outputCurrentA = 0;
    ` });
  });
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto('http://localhost:1420/?demo=1');
  await page.locator('.automatic-power-chart__reading').waitFor();
  const result = [];
  for (const width of [1366, 1920]) {
    await page.setViewportSize({ width, height: 900 });
    const geometry = await page.locator('.automatic-power-chart__reading').evaluate((reading) => {
      const title = reading.querySelector('.automatic-metric-card__title').getBoundingClientRect();
      const value = reading.querySelector('.automatic-metric-card__value').getBoundingClientRect();
      const chart = reading.parentElement.getBoundingClientRect();
      return { titleRight: title.right, valueRight: value.right, rightInset: chart.right - value.right,
        top: getComputedStyle(reading).top, alignItems: getComputedStyle(reading).alignItems,
        insideChart: title.top >= chart.top && value.bottom <= chart.bottom && value.left >= chart.left };
    });
    if (Math.abs(geometry.titleRight - geometry.valueRight) > 1 || !geometry.insideChart || geometry.alignItems !== 'flex-end') throw new Error(JSON.stringify(geometry));
    result.push({ width, ...geometry });
  }
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.locator('.automatic-power-chart').screenshot({ path: 'output/playwright/power-reading-alignment.png' });
  return result;
}
