async (page) => {
  await page.setViewportSize({ width: 1600, height: 1100 });
  const results = [];
  for (const mode of ['dark', 'light']) {
    await page.evaluate(mode => {
      document.documentElement.dataset.theme = mode;
      document.documentElement.classList.toggle('dark', mode === 'dark');
    }, mode);
    await page.mouse.move(2, 2);
    await page.locator('[data-slot="card"][aria-label="功率 / 效率"]').screenshot({ path: `output/playwright/legend-backdrop-${mode}.png` });
    results.push(await page.evaluate(() => {
      const legend = document.querySelector('.chart-inset-legend');
      const styles = getComputedStyle(legend);
      const right = getComputedStyle(legend, '::before');
      return { mode: document.documentElement.dataset.theme, border: styles.borderWidth, shadow: styles.boxShadow,
        background: right.backgroundColor, blur: right.filter,
        labelColorMatches: getComputedStyle(legend.firstElementChild).color === getComputedStyle(document.querySelector('.automatic-metric-card__title')).color,
        pointerEvents: styles.pointerEvents };
    }));
  }
  return results;
}
