async (page) => {
  await page.setViewportSize({ width: 1600, height: 1100 });
  const card = page.locator('[data-slot="card"][aria-label="功率 / 效率"]');
  const legend = card.locator('[aria-label="图例"]');
  const chart = page.getByRole('img', { name: '功率 / 效率曲线', exact: true });
  const results = [];
  for (const mode of ['dark', 'light']) {
    // Keep the mounted chart while changing appearance to check live legend colors.
    await page.evaluate((mode) => {
      document.documentElement.dataset.theme = mode;
      document.documentElement.classList.toggle('dark', mode === 'dark');
    }, mode);
    await legend.waitFor();
    const details = await card.evaluate(el => {
      const legend = el.querySelector('[aria-label="图例"]');
      const canvas = el.querySelector('canvas[role="img"]');
      const frame = legend.getBoundingClientRect();
      const plot = canvas.getBoundingClientRect();
      return {
        labels: [...legend.children].map(item => item.textContent),
        colors: [...legend.querySelectorAll('svg line')].map(line => getComputedStyle(line).stroke),
        dashes: [...legend.querySelectorAll('svg line')].map(line => line.getAttribute('stroke-dasharray')),
        circles: legend.querySelectorAll('svg circle').length,
        x: frame.x - plot.x, y: frame.y - plot.y,
        inside: frame.right < plot.right && frame.bottom < plot.bottom,
        pointerEvents: getComputedStyle(legend.parentElement).pointerEvents,
      };
    });
    if (details.labels.join('|') !== '功率 (W)|效率 (%)|历史参考') throw new Error(JSON.stringify(details));
    if (details.circles !== 2 || details.dashes[2] !== '4 4') throw new Error('Legend symbols differ from curves');
    if (details.x < 52 || details.x > 90 || details.y < 8 || details.y > 40 || !details.inside) throw new Error(`Legend position: ${JSON.stringify(details)}`);
    if (details.pointerEvents !== 'none') throw new Error('Legend intercepts pointer events');
    const expected = mode === 'dark' ? ['rgb(255, 122, 26)', 'rgb(245, 197, 107)'] : ['rgb(182, 83, 16)', 'rgb(138, 104, 24)'];
    if (details.colors[0] !== expected[0] || details.colors[1] !== expected[1]) throw new Error(`Legend theme mismatch ${JSON.stringify(details)}`);
    await page.mouse.move(2, 2);
    await card.screenshot({ path: `output/playwright/efficiency-legend-${mode}.png` });
    const box = await chart.boundingBox();
    await chart.hover({ position: { x: box.width * 0.4, y: box.height * 0.55 } });
    await page.getByRole('tooltip').waitFor();
    if (!await legend.isVisible()) throw new Error('Legend disappeared during hover');
    results.push(`${mode}: upper-left legend, all three labels, line symbols, theme colors and hover passed`);
  }
  await page.setViewportSize({ width: 1100, height: 850 });
  const narrow = await legend.boundingBox();
  const narrowChart = await chart.boundingBox();
  if (narrow.x + narrow.width > narrowChart.x + narrowChart.width || narrow.y + narrow.height > narrowChart.y + narrowChart.height) throw new Error('Legend does not fit at 1100px');
  results.push('1100px viewport: legend fits inside plot');
  return results;
}
