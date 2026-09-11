async (page) => {
  await page.reload();
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  await page.locator('label').filter({ hasText: '鸢尾紫' }).click();
  for (const mode of ['dark', 'light']) {
    await page.getByRole('button', { name: '系统设置', exact: true }).click();
    const toggle = page.getByRole('switch', { name: '切换浅色模式' });
    if (await toggle.getAttribute('aria-checked') !== String(mode === 'light')) await toggle.click();
    await page.getByRole('button', { name: '自动测试', exact: true }).click();
    await page.getByRole('img', { name: '光谱曲线', exact: true }).waitFor();
    await page.waitForFunction(() => {
      const canvas = document.querySelector('canvas[aria-label="光谱曲线"]');
      const hex = getComputedStyle(document.documentElement).getPropertyValue('--chart-1').trim();
      const rgb = hex.slice(1).match(/../g).map(v => parseInt(v, 16));
      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      let count = 0;
      for (let i = 0; i < pixels.length; i += 4) {
        if (pixels[i + 3] > 128 && rgb.every((v, channel) => Math.abs(pixels[i + channel] - v) <= 2)) count++;
      }
      return count > 10;
    });
    const colorMatches = await page.getByLabel('峰值图例', { exact: true }).evaluate(el => {
      const path = el.querySelectorAll('path')[1];
      const expected = getComputedStyle(document.documentElement).getPropertyValue('--chart-1').trim();
      const rgb = expected.slice(1).match(/../g).map(v => parseInt(v, 16));
      return getComputedStyle(path).fill === `rgb(${rgb.join(', ')})`;
    });
    if (!colorMatches) throw new Error('Peak 2 legend does not match theme');
    await page.screenshot({ path: `output/playwright/theme-presets/automatic-purple-${mode}.png`, animations: 'disabled' });
  }
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  await page.getByRole('switch', { name: '切换浅色模式' }).click();
  await page.screenshot({ path: 'output/playwright/theme-presets/settings-purple-dark.png', animations: 'disabled' });
  return 'Peak 2 canvas marker and SVG legend follow theme in dark and light modes';
}
