async (page) => {
  const results = [];
  for (const mode of [
    { width: 2048, height: 1063, density: 'distant' },
    { width: 1600, height: 1000, density: 'comfortable' },
    { width: 1280, height: 900, density: 'compact' },
  ]) {
    await page.setViewportSize({ width: mode.width, height: mode.height });
    await page.evaluate(density => {
      const appearance = JSON.parse(localStorage.getItem('power-test.appearance.v1') || '{}');
      localStorage.setItem('power-test.appearance.v1', JSON.stringify({ ...appearance, density }));
    }, mode.density);
    await page.reload();
    await page.getByRole('region', { name: '实时关键指标' }).waitFor();
    await page.evaluate(async () => { await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
    await page.screenshot({ path: `output/playwright/metric-header-${mode.width}.png`, animations: 'disabled' });
    results.push(await page.evaluate(mode => {
      const box = element => { const { x, y, width, height } = element.getBoundingClientRect(); return { x, y, width, height }; };
      const grid = document.querySelector('.automatic-chart-grid');
      const card = grid.querySelector('.automatic-metric-card--power');
      const status = card.querySelector('.power-stability-status');
      const title = card.querySelector('.automatic-metric-card__title');
      return { ...mode, rows: getComputedStyle(grid).gridTemplateRows, grid: box(grid), powerRow: box(grid.firstElementChild), metrics: box(card.parentElement), spectrum: box(grid.lastElementChild), card: box(card), title: box(title), status: box(status), statusInHeader: Boolean(status.closest('.automatic-metric-card__title--with-status')), statusCount: card.querySelectorAll('.power-stability-status').length };
    }, mode));
  }
  return results;
}
