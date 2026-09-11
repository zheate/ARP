async (page) => {
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  const labels = await page.getByRole('radio').evaluateAll(inputs => inputs.map(input => input.closest('label').textContent.trim()));
  const results = [];
  for (const mode of ['dark', 'light']) {
    for (const label of labels) {
      await page.getByRole('button', { name: '系统设置', exact: true }).click();
      const toggle = page.getByRole('switch', { name: '切换浅色模式' });
      if (await toggle.getAttribute('aria-checked') !== String(mode === 'light')) await toggle.click();
      await page.locator('label').filter({ hasText: label }).click();
      await page.getByRole('button', { name: '自动测试', exact: true }).click();
      await page.getByRole('img', { name: '光谱曲线', exact: true }).waitFor();
      await page.waitForFunction(() => {
        const canvas = document.querySelector('canvas[aria-label="光谱曲线"]');
        if (!canvas?.width || !canvas.height) return false;
        const color = getComputedStyle(document.documentElement).getPropertyValue('--chart-1').trim();
        const rgb = color.slice(1).match(/../g).map(v => parseInt(v, 16));
        const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        let count = 0;
        for (let i = 0; i < pixels.length; i += 4) {
          if (pixels[i + 3] > 128 && rgb.every((v, channel) => Math.abs(pixels[i + channel] - v) <= 2)) count++;
        }
        // Peak markers alone have fewer than 100 opaque pixels.
        return count > 500;
      });
      results.push(`${mode}/${label}: spectrum curve follows theme`);
      if (label === '鸢尾紫') await page.screenshot({ path: `output/playwright/theme-presets/spectrum-purple-${mode}.png`, animations: 'disabled' });
    }
  }
  return results;
}
