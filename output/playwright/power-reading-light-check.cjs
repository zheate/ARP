async (page) => {
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  await page.getByRole('button', { name: '浅色', exact: true }).click();
  await page.getByRole('button', { name: '自动测试', exact: true }).click();
  await page.locator('.automatic-power-chart').screenshot({ path: 'output/playwright/power-reading-light-card.png' });
  const box = await page.locator('.automatic-power-chart').boundingBox();
  await page.screenshot({ path: 'output/playwright/power-reading-light.png', clip: { x: box.x + box.width - 430, y: box.y, width: 430, height: 250 } });
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  await page.getByRole('button', { name: '深色', exact: true }).click();
  await page.getByRole('button', { name: '自动测试', exact: true }).click();
  return { lightScreenshot: 'output/playwright/power-reading-light.png' };
}
