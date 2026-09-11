async (page) => {
  let scenario = { state: 'waiting_stable', elapsedS: 6.3, powerStable: false };
  await page.route('**/src/lib/demo-snapshot.ts*', async route => {
    const response = await route.fetch();
    const patch = {
      state: scenario.state, currentA: 0.6, currentIndex: 0, currents: [0.6, 1, 2],
      stability: {
        powerStable: scenario.powerStable, elapsedS: scenario.elapsedS, requiredS: 10,
        spanW: 0.05, spanLimitW: 0.1, percent: 2, limitPercent: 0.2,
        quality: 'live', hasReading: true, wavelengthRequired: false, wavelengthStable: false,
      },
    };
    await route.fulfill({ response, body: (await response.text()) + '\nObject.assign(DEMO_SNAPSHOT.automaticTest, ' + JSON.stringify(patch) + ');' });
  });
  const results = [];
  for (const width of [1920, 1366, 1024, 820]) {
    await page.setViewportSize({ width, height: 900 });
    await page.reload();
    await page.locator('.automatic-phase-track__stability').waitFor();
    await page.locator('.automatic-phase-track__stability-time').filter({ hasText: '6.3 / 10 秒' }).waitFor();
    const layout = await page.evaluate(() => {
      const header = document.querySelector('.app-titlebar');
      const track = document.querySelector('.automatic-phase-track');
      const status = document.querySelector('.automatic-phase-track__stability');
      const actions = document.querySelector('.app-titlebar__actions');
      const brand = document.querySelector('.app-titlebar__brand');
      const rect = el => ({ left: el.getBoundingClientRect().left, right: el.getBoundingClientRect().right });
      return { text: status.textContent, overflow: header.scrollWidth > header.clientWidth, track: rect(track), status: rect(status), actions: rect(actions), brand: rect(brand) };
    });
    if (layout.overflow || layout.status.right > layout.actions.left || layout.track.left < layout.brand.right) throw new Error(JSON.stringify({ width, layout }));
    results.push({ width, ...layout });
    if (width === 1366) await page.locator('.app-titlebar').screenshot({ path: 'output/playwright/stability-status-1366.png' });
  }
  scenario = { state: 'waiting_voltage', elapsedS: 10, powerStable: true };
  await page.reload();
  await page.locator('.automatic-phase-track__stability').filter({ hasText: '已稳定' }).waitFor();
  results.push({ stableText: await page.locator('.automatic-phase-track__stability').innerText() });
  return results;
}
