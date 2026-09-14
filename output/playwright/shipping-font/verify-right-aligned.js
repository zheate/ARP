async (page) => {
  const results = [];
  for (const width of [1440, 1280]) {
    await page.setViewportSize({width, height:1000});
    const grid = page.locator('.shipping-report-parameters');
    await grid.scrollIntoViewIfNeeded();
    results.push(await page.evaluate(() => {
      const selector = '.shipping-report-parameter-row > input[data-slot="input"]';
      const inputs = [...document.querySelectorAll(selector)];
      if (!inputs.length || inputs.some(el => getComputedStyle(el).textAlign !== 'right')) throw new Error('Parameter alignment failed');
      const elements = [...document.querySelectorAll('.shipping-report-parameter-row, .shipping-report-parameter-row > *')];
      const boxes = () => elements.map(el => {const r=el.getBoundingClientRect(); return [r.x,r.y,r.width,r.height]});
      const after = boxes();
      const style = document.createElement('style');
      style.textContent = selector + '{text-align:left !important}';
      document.head.appendChild(style);
      const before = boxes();
      style.remove();
      if (JSON.stringify(before)!==JSON.stringify(after)) throw new Error('Layout moved');
      return {width:innerWidth, rightAlignedInputs:inputs.length, otherPositionsUnchanged:true};
    }));
    await grid.screenshot({path:`output/playwright/shipping-font/right-aligned-${width}.png`});
  }
  return results;
}
