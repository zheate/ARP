async (page) => {
  const results = [];
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  for (const mode of ['automatic', 'manual']) {
    for (const width of [1180, 1440, 1920]) {
      await page.setViewportSize({ width, height: 900 });
      for (const dark of [false, true]) {
        await page.evaluate((value) => document.documentElement.classList.toggle('dark', value), dark);
        for (const density of ['compact', 'comfortable', 'distant']) {
          await page.evaluate((value) => { document.documentElement.dataset.density = value; }, density);
          const samples = [];
          for (const [power, current, state] of [
            [0, 0, 'idle'], [9.999, 9.9, 'waiting_stable'], [10, 10, 'waiting_stable'],
            [100, 99.99, 'waiting_stable'], [9999.999, 100.01, 'waiting_stable'], [null, null, 'idle'],
          ]) {
            await page.evaluate((args) => window.renderPowerUnitsCase(...args), [mode, power, current, state]);
            for (const delay of [0, 16, 40, 80]) {
              if (delay) await page.waitForTimeout(delay);
              samples.push(await page.evaluate(async () => {
                await document.fonts.ready;
                const reading = document.querySelector('#power-units-qa .power-chart-readout');
                const chart = reading.closest('.automatic-power-chart');
                const power = reading.querySelector('.automatic-metric-card__number .numeric-readout');
                const current = reading.querySelector('.automatic-metric-card__current .numeric-readout');
                const w = reading.querySelector('.power-chart-readout__power-unit');
                const a = reading.querySelector('.power-chart-readout__current-unit');
                const powerText = power.lastElementChild;
                const currentText = current.lastElementChild;
                const flow = power.querySelector('number-flow-react');
                const decimal = flow?.shadowRoot?.querySelector('[part~="decimal"]');
                const box = (element) => element.getBoundingClientRect();
                return {
                  x: box(reading).x, y: box(reading).y,
                  powerX: box(power).x, powerRight: box(powerText).right,
                  currentX: box(current).x, currentRight: box(currentText).right,
                  w: box(w).x, a: box(a).x,
                  decimalX: decimal ? box(decimal).x : null,
                  animated: flow ? flow.animated : null,
                  top: box(reading).top - box(chart).top,
                  inside: box(reading).x >= box(chart).x && box(a).right <= box(chart).right && box(reading).bottom <= box(chart).bottom,
                  overlap: box(powerText).right > box(w).left || box(currentText).right > box(a).left,
                  overflow: document.documentElement.scrollWidth > innerWidth,
                };
              }));
            }
          }
          const span = (key) => {
            const values = samples.map((sample) => sample[key]).filter((value) => value !== null);
            return Math.max(...values) - Math.min(...values);
          };
          results.push({
            mode, width, dark, density, samples: samples.length,
            shift: Math.max(...['x', 'y', 'powerX', 'powerRight', 'currentX', 'currentRight', 'w', 'a', 'decimalX'].map(span)),
            unitAShift: span('a'), unitWShift: span('w'), powerDecimalShift: span('decimalX'),
            top: samples[0].top, animated: samples.some((sample) => sample.animated === true),
            inside: samples.every((sample) => sample.inside), overlap: samples.some((sample) => sample.overlap),
            overflow: samples.some((sample) => sample.overflow),
          });
        }
      }
    }
  }
  await page.evaluate((value) => { window.unitQaResults = value; }, { results, errors });
  await page.setViewportSize({ width: 1440, height: 900 });
  for (const mode of ['automatic', 'manual']) {
    await page.evaluate((value) => {
      document.documentElement.classList.add('dark');
      document.documentElement.dataset.density = 'compact';
      window.renderPowerUnitsCase(value, 0.86, 8);
    }, mode);
    await page.waitForTimeout(150);
    await page.locator('#power-units-qa .automatic-power-chart').screenshot({ path: `/Users/zh/Documents/test/ARP/output/playwright/power-units-fixed-20261015/${mode}-after.png` });
  }
}
