async (page) => {
  const presets = [
    ['orange', '经典橙', '#ff7a1a', '#b65310'],
    ['blue', '极光蓝', '#93a8ff', '#475ac7'],
    ['green', '森林绿', '#78c9a4', '#267453'],
    ['purple', '鸢尾紫', '#b79aee', '#8052b5'],
    ['rose', '玫瑰', '#e7a0b0', '#ac4563'],
    ['graphite', '石墨', '#c2c8d0', '#535e6d'],
  ];
  const results = [];
  for (const mode of ['dark', 'light']) {
    await page.getByRole('button', { name: '系统设置', exact: true }).click();
    if (await page.getByRole('switch', { name: '切换浅色模式' }).getAttribute('aria-checked') !== String(mode === 'light')) {
      await page.getByRole('switch', { name: '切换浅色模式' }).click();
    }
    for (const [id, label, dark, light] of presets) {
      await page.getByRole('button', { name: '系统设置', exact: true }).click();
      await page.locator('label').filter({ hasText: label }).click();
      const expected = mode === 'dark' ? dark : light;
      await page.waitForFunction(({ id, mode, expected }) => {
        const root = document.documentElement;
        return root.dataset.accent === id && root.dataset.theme === mode && getComputedStyle(root).getPropertyValue('--primary').trim() === expected;
      }, { id, mode, expected });
      if (!await page.getByRole('radio', { name: label, exact: true }).isChecked()) throw new Error(`${mode}/${id}: radio selection`);
      const stored = await page.evaluate(() => [localStorage.getItem('power-test.theme'), localStorage.getItem('power-test.accent-theme')]);
      if (stored.join('/') !== `${mode}/${id}`) throw new Error(`${mode}/${id}: persistence`);
      if (id === 'purple') await page.screenshot({ path: `output/playwright/theme-presets/settings-purple-${mode}.png` });
      await page.getByRole('button', { name: '自动测试', exact: true }).click();
      await page.getByRole('img', { name: '功率实时曲线', exact: true }).waitFor();
      await page.waitForFunction((hex) => {
        const rgb = hex.slice(1).match(/../g).map(v => parseInt(v, 16));
        return ['功率实时曲线', '功率 / 效率曲线'].every(label => {
          const canvas = document.querySelector(`canvas[aria-label="${label}"]`);
          if (!canvas?.width || !canvas.height) return false;
          const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
          let count = 0;
          for (let i = 0; i < pixels.length; i += 4) {
            if (pixels[i + 3] > 128 && rgb.every((v, channel) => Math.abs(pixels[i + channel] - v) <= 2)) count++;
          }
          return count > 100;
        });
      }, expected);
      const reading = await page.locator('.automatic-metric-card--power .automatic-metric-card__number').evaluate(el => getComputedStyle(el).color);
      const rgb = expected.slice(1).match(/../g).map(v => parseInt(v, 16));
      if (reading !== `rgb(${rgb.join(', ')})`) throw new Error(`${mode}/${id}: live power ${reading}`);
      if (id === 'purple') await page.screenshot({ path: `output/playwright/theme-presets/automatic-purple-${mode}.png` });
      results.push(`${mode}/${id}: selection, storage, live reading and both canvas curves passed`);
    }
  }
  await page.reload();
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  if (!await page.getByRole('radio', { name: '石墨', exact: true }).isChecked()) throw new Error('Reload lost accent');
  if (await page.getByRole('switch', { name: '切换浅色模式' }).getAttribute('aria-checked') !== 'true') throw new Error('Reload lost light mode');
  results.push('Reload preserved light/graphite');
  await page.locator('label').filter({ hasText: '鸢尾紫' }).click();
  await page.setViewportSize({ width: 620, height: 900 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth || [...document.querySelectorAll('.theme-preset')].some(el => el.getBoundingClientRect().right > innerWidth));
  if (overflow) throw new Error('Narrow settings overflow');
  await page.screenshot({ path: 'output/playwright/theme-presets/settings-purple-narrow.png' });
  results.push('620px settings: no horizontal overflow');
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.evaluate(() => { localStorage.setItem('power-test.accent-theme', 'invalid-preset'); });
  await page.reload();
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  if (!await page.getByRole('radio', { name: '经典橙', exact: true }).isChecked()) throw new Error('Invalid preference fallback');
  results.push('Invalid accent preference falls back to classic orange');
  await page.locator('label').filter({ hasText: '鸢尾紫' }).click();
  await page.getByRole('switch', { name: '切换浅色模式' }).click();
  return results;
}
