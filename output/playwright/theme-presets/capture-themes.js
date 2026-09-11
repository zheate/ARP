async (page) => {
  const settle = async () => {
    await page.evaluate(async () => {
      await Promise.all(document.getAnimations().filter(animation => animation.effect?.getComputedTiming().iterations !== Infinity).map(animation => animation.finished.catch(() => {})));
    });
  };
  await page.reload();
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  await page.locator('label').filter({ hasText: '鸢尾紫' }).click();
  await page.getByRole('radio', { name: '鸢尾紫' }).focus();
  await page.keyboard.press('ArrowRight');
  if (!await page.getByRole('radio', { name: '玫瑰', exact: true }).isChecked()) throw new Error('Radio keyboard navigation');
  await page.locator('label').filter({ hasText: '鸢尾紫' }).click();
  for (const mode of ['dark', 'light']) {
    await page.getByRole('button', { name: '系统设置', exact: true }).click();
    if (await page.getByRole('switch', { name: '切换浅色模式' }).getAttribute('aria-checked') !== String(mode === 'light')) await page.getByRole('switch', { name: '切换浅色模式' }).click();
    await settle();
    await page.screenshot({ path: `output/playwright/theme-presets/settings-purple-${mode}.png`, animations: 'disabled' });
    await page.getByRole('button', { name: '自动测试', exact: true }).click();
    await page.getByRole('img', { name: '功率实时曲线', exact: true }).waitFor();
    await settle();
    await page.screenshot({ path: `output/playwright/theme-presets/automatic-purple-${mode}.png`, animations: 'disabled' });
  }
  const before = await page.getByRole('img', { name: '功率实时曲线', exact: true }).evaluate(canvas => canvas.toDataURL());
  await page.evaluate(() => { document.documentElement.dataset.accent = 'green'; });
  await page.waitForFunction(before => document.querySelector('canvas[aria-label="功率实时曲线"]').toDataURL() !== before, before);
  await page.evaluate(() => { document.documentElement.dataset.accent = 'purple'; });
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  await page.setViewportSize({ width: 620, height: 900 });
  await settle();
  await page.screenshot({ path: 'output/playwright/theme-presets/settings-purple-narrow.png', animations: 'disabled' });
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.getByRole('switch', { name: '切换浅色模式' }).click();
  await settle();
  return ['Keyboard arrows select themes', 'Mounted static canvas redraws on theme change', 'Captured settled settings and automatic charts in both modes'];
}
