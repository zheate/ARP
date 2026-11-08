async (page) => {
  const stage = 'after';
  const out = '/Users/zh/Documents/test/ARP/output/playwright/manual-input-numbers-20261020';
  const report = { stage, scenarios: [], errors: [] };
  page.on('pageerror', error => report.errors.push(error.message));
  for (const theme of ['light', 'dark']) {
    for (const width of [1440, 1180]) {
      await page.setViewportSize({ width, height: 960 });
      await page.goto('http://localhost:1420/?demo=1&run=1&page=manual');
      await page.evaluate(theme => localStorage.setItem('power-test.theme', theme), theme);
      await page.reload();
      await page.getByRole('button', { name: '开启输出', exact: true }).click();
      const voltage = page.locator('#manual-psu-voltage');
      const current = page.locator('#manual-psu-current');
      await voltage.fill('22');
      await current.fill('2');
      await current.press('Tab');
      await page.evaluate(() => document.fonts.ready);
      const geometry = await page.evaluate(() => {
        const rect = el => {
          const r = el.getBoundingClientRect();
          return { x: r.x, y: r.y, width: r.width, height: r.height };
        };
        const card = document.querySelector('.manual-device-card--power');
        const inputs = [...card.querySelectorAll('.manual-power-parameters input')].map(el => {
          const css = getComputedStyle(el);
          return { id: el.id, value: el.value, rect: rect(el), fontSize: css.fontSize,
            lineHeight: css.lineHeight, textAlign: css.textAlign, paddingRight: css.paddingRight,
            paddingLeft: css.paddingLeft, borderWidth: css.borderWidth,
            arrows: [...el.parentElement.querySelectorAll('button')].map(rect) };
        });
        return { theme: document.documentElement.dataset.theme, card: rect(card), inputs,
          buttons: [...card.querySelectorAll('.manual-power-actions button')].map(rect),
          readouts: rect(card.querySelector('.manual-power-readouts')),
          overflow: document.documentElement.scrollWidth > innerWidth };
      });
      await page.locator('.manual-device-card--power').screenshot({ path: `${out}/${stage}-${theme}-${width}.png` });
      report.scenarios.push({ width, ...geometry });
    }
  }
  return report;
}
