async (page) => {
  await page.setViewportSize({ width: 2048, height: 1063 });
  await page.evaluate(() => {
    const appearance = JSON.parse(localStorage.getItem('power-test.appearance.v1') || '{}');
    localStorage.setItem('power-test.appearance.v1', JSON.stringify({ ...appearance, density: 'distant' }));
  });
  await page.reload();
  await page.getByRole('region', { name: '实时关键指标' }).waitFor();
  await page.locator('.automatic-metric-card--power').screenshot({ path: 'output/playwright/metric-header-card.png', animations: 'disabled' });
  return await page.locator('.automatic-metric-card--power').innerText();
}
