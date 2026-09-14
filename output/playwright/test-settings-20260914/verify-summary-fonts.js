async (page) => {
  if (await page.getByRole('button', { name: '停止', exact: true }).isEnabled()) {
    await page.getByRole('button', { name: '停止', exact: true }).click();
    await page.getByRole('button', { name: '测试下一台', exact: true }).click();
  }
  await page.getByRole('button', { name: '功率计信息，点击打开设置', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '功率计设置', exact: true });
  const results = [];
  for (const width of [1440, 900]) {
    await page.setViewportSize({ width, height: 1000 });
    const result = await dialog.locator('.power-meter-profile__summary').evaluate(n => ({
      labels: Array.from(n.querySelectorAll('dt'), e => getComputedStyle(e).fontSize),
      values: Array.from(n.querySelectorAll('dd'), e => getComputedStyle(e).fontSize),
      overflow: n.scrollWidth > n.clientWidth,
    }));
    if (result.labels.some(s => s !== '16px') || result.values.some(s => s !== '18px') || result.overflow) throw new Error(JSON.stringify(result));
    await page.screenshot({ path: 'output/playwright/test-settings-20260914/summary-fonts-' + width + '.png', animations: 'disabled' });
    results.push({width, ...result});
  }
  return results;
}
