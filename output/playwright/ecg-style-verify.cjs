async (page) => {
  const results = [];
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  const marker = page.getByRole('combobox', { name: '自动测试功率实时曲线末端样式' });
  await marker.selectOption('ecg');
  for (const mode of [{ theme: 'dark', label: '深色' }, { theme: 'light', label: '浅色' }]) {
    await page.getByRole('button', { name: mode.label, exact: true }).click();
    await page.getByRole('img', { name: '外观示例功率曲线', exact: true }).screenshot({ path: `output/playwright/ecg-preview-${mode.theme}.png` });
    results.push({ theme: mode.theme, selected: await marker.inputValue(), saved: await page.evaluate(() => localStorage.getItem('power-test.latest-point-marker')) });
    await page.getByRole('button', { name: '自动测试', exact: true }).click();
    await page.waitForFunction(() => {
      const canvas = document.querySelector('canvas[aria-label="功率实时曲线"]');
      if (!canvas || !canvas.width || !canvas.height) return false;
      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      let drawn = 0;
      for (let index = 3; index < pixels.length; index += 4) if (pixels[index] > 0 && ++drawn > 500) return true;
      return false;
    });
    await page.getByRole('img', { name: '功率实时曲线', exact: true }).screenshot({ path: `output/playwright/ecg-live-${mode.theme}.png`, animations: 'disabled' });
    await page.getByRole('button', { name: '系统设置', exact: true }).click();
  }
  await page.reload();
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  const restored = await marker.inputValue();
  if (restored !== 'ecg' || results.some(result => result.selected !== 'ecg' || result.saved !== 'ecg')) throw new Error(JSON.stringify({ results, restored }));
  await page.getByRole('button', { name: '深色', exact: true }).click();
  await page.getByRole('combobox', { name: '自动测试功率实时曲线线型', exact: true }).selectOption('dashed');
  await page.getByRole('img', { name: '外观示例功率曲线', exact: true }).screenshot({ path: 'output/playwright/ecg-preview-dashed.png' });
  await page.getByRole('combobox', { name: '自动测试功率实时曲线线型', exact: true }).selectOption('solid');
  await page.getByRole('button', { name: '自动测试', exact: true }).click();
  return { results, restored };
}
