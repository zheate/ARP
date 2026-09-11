async (page) => {
  const presets = [
    ['orange', '经典橙', '#f5c56b', '#8a6818'],
    ['blue', '极光蓝', '#65c8d0', '#187982'],
    ['green', '森林绿', '#d5c17a', '#80681c'],
    ['purple', '鸢尾紫', '#e9a3bf', '#ab4675'],
    ['rose', '玫瑰', '#bca8eb', '#7853b0'],
    ['graphite', '石墨', '#80bdb5', '#35776e'],
  ];
  const results = [];
  const checkCanvas = async (expected) => {
    await page.waitForFunction((hex) => {
      const canvas = document.querySelector('canvas[aria-label="功率 / 效率曲线"]');
      if (!canvas?.width || !canvas.height) return false;
      const rgb = hex.slice(1).match(/../g).map(v => parseInt(v, 16));
      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      const scale = canvas.width / canvas.getBoundingClientRect().width;
      let curve = 0, rightAxis = 0;
      for (let i = 0; i < pixels.length; i += 4) {
        if (pixels[i + 3] < 128 || !rgb.every((v, c) => Math.abs(pixels[i + c] - v) <= 2)) continue;
        const x = (i / 4) % canvas.width;
        if (x > canvas.width - 48 * scale) rightAxis++;
        else if (x > 52 * scale) curve++;
      }
      return curve > 100 && rightAxis > 10;
    }, expected);
  };
  const checkTooltip = async (expected) => {
    const marker = page.getByRole('tooltip').getByText('效率 (%)', { exact: true }).locator('span');
    const expectedRgb = `rgb(${expected.slice(1).match(/../g).map(v => parseInt(v, 16)).join(', ')})`;
    await marker.waitFor();
    const actual = await marker.evaluate(el => getComputedStyle(el).backgroundColor);
    if (actual !== expectedRgb) throw new Error(`Tooltip ${actual} != ${expectedRgb}`);
  };
  for (const mode of ['dark', 'light']) {
    for (const [id, label, dark, light] of presets) {
      await page.getByRole('button', { name: '系统设置', exact: true }).click();
      await page.getByRole('button', { name: mode === 'dark' ? '深色' : '浅色', exact: true }).click();
      await page.locator('label').filter({ has: page.getByRole('radio', { name: label, exact: true }) }).click();
      const expected = mode === 'dark' ? dark : light;
      await page.waitForFunction(({id, mode, expected}) => {
        const root = document.documentElement;
        const styles = getComputedStyle(root);
        return root.dataset.accent === id && root.dataset.theme === mode
          && styles.getPropertyValue('--chart-efficiency').trim() === expected
          && styles.getPropertyValue('--chart-1').trim() !== expected;
      }, {id, mode, expected});
      await page.getByRole('button', { name: '自动测试', exact: true }).click();
      await checkCanvas(expected);
      const chart = page.getByRole('img', { name: '功率 / 效率曲线', exact: true });
      const box = await chart.boundingBox();
      await chart.hover({ position: { x: box.width * 0.25, y: box.height * 0.5 } });
      await checkTooltip(expected);
      if (id === 'blue' || (id === 'purple' && mode === 'dark')) {
        await page.locator('[data-slot="card"][aria-label="功率 / 效率"]').screenshot({ path: `output/playwright/efficiency-${id}-${mode}.png` });
      }
      results.push(`${mode}/${id}: curve, right axis, tooltip passed (${expected})`);
    }
  }
  // Keep the mounted canvas and hovered point to exercise the existing theme observer.
  await page.evaluate(() => {
    document.documentElement.dataset.accent = 'blue';
    document.documentElement.dataset.theme = 'dark';
    document.documentElement.classList.add('dark');
  });
  await checkCanvas('#65c8d0');
  await checkTooltip('#65c8d0');
  results.push('Mounted canvas and open tooltip update immediately after theme change');
  await page.reload();
  await checkCanvas('#35776e');
  results.push('Reload restores selected light/graphite palette');
  return results;
}
