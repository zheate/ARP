async (page) => {
  const results = [];
  for (const [width, height, density, theme] of [
    [1180, 800, 'comfortable', 'dark'],
    [1440, 900, 'comfortable', 'light'],
    [1920, 1080, 'comfortable', 'dark'],
    [2048, 1063, 'distant', 'dark'],
    [1000, 900, 'comfortable', 'light'],
  ]) {
    await page.setViewportSize({ width, height });
    await page.evaluate(({ density, theme }) => {
      const key = 'power-test.appearance.v1';
      localStorage.setItem(key, JSON.stringify({ ...JSON.parse(localStorage.getItem(key) || '{}'), density }));
      localStorage.setItem('power-test.theme', theme);
    }, { density, theme });
    await page.reload();
    await page.getByRole('button', { name: '手动配置', exact: true }).click();
    await page.evaluate(() => document.fonts.ready);
    // Display-only long failure text, matching the user's reported state.
    await page.locator('.manual-device-card--acquisition').nth(1).locator('.manual-device-card__identity').evaluate(el => {
      el.textContent = '识别失败：未找到 OceanDirect.dll：/Users/zh/Documents/ARP/assets/libs/ocean_direct/OceanDirect.dll';
    });
    const cards = await page.locator('.manual-device-card').evaluateAll(cards => cards.map(card => {
      const body = card.querySelector('.manual-device-card__body');
      const footer = card.querySelector('.manual-device-card__footer');
      const rect = card.getBoundingClientRect();
      const content = card.querySelector('.manual-device-card__content');
      const blocks = [...content.children].flatMap(el => el === body ? [...body.children] : [el]);
      const rects = blocks.map(el => el.getBoundingClientRect());
      const gaps = rects.slice(1).map((r, i) => r.top - rects[i].bottom);
      return {
        title: card.querySelector('[data-slot=card-title]').textContent,
        top: rect.top, height: rect.height,
        buttonTop: footer.querySelector('button').getBoundingClientRect().top,
        gaps,
        overflow: card.scrollWidth > card.clientWidth,
      };
    }));
    const pair = cards.slice(1);
    if (cards.some(c => c.overflow)) throw new Error(JSON.stringify({ width, cards }));
    if (pair.some(c => Math.max(...c.gaps) - Math.min(...c.gaps) > 0.1 || Math.min(...c.gaps) < 8)) throw new Error(JSON.stringify({ width, cards }));
    if (width > 1100 && (new Set(cards.map(c => c.top)).size !== 1 || new Set(cards.map(c => c.height)).size !== 1 || new Set(cards.map(c => c.buttonTop)).size !== 1)) {
      throw new Error(JSON.stringify({ width, cards }));
    }
    await page.locator('.manual-device-grid').screenshot({ path: `output/github-publish-all-20261016/even-${width}-${theme}.png` });
    for (const name of ['功率计', '光谱仪']) {
      const header = page.getByRole('button', { name, exact: true });
      await header.focus();
      await page.keyboard.press('Enter');
      await page.getByRole('dialog').waitFor();
      await page.keyboard.press('Escape');
      await page.getByRole('dialog').waitFor({ state: 'hidden' });
    }
    results.push({ width, height, density, theme, cards, keyboardSettings: 'passed' });
  }
  return results;
}
