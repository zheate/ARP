// Run through playwright-cli run-code --filename. All hardware ports are fake.
async (page) => {
  const out = 'E:/工作/长光华芯/软件/泵驱一体/auto_run_Lpowe2/output/playwright/manual-workflow-20261020';
  const report = { browserOnly: true, hardwareConnected: false, checks: [], replay: {}, errors: [] };
  const check = (name, value, detail) => { report.checks.push({ name, passed: !!value, detail }); if (!value) throw Error(name + ': ' + JSON.stringify(detail)); };
  page.on('pageerror', error => report.errors.push(error.message));
  await page.addInitScript(() => {
    localStorage.setItem('power-test.latest-point-marker', 'dinosaur');
    localStorage.setItem('power-test.theme', 'light');
    const probe = window.__manualDraws = { draws: 0, times: [], costs: [], latestCost: 0 };
    const clear = CanvasRenderingContext2D.prototype.clearRect;
    CanvasRenderingContext2D.prototype.clearRect = function (...args) {
      if (this.canvas.getAttribute('aria-label') === '功率实时曲线') { probe.draws++; probe.times.push(performance.now()); }
      return clear.apply(this, args);
    };
    const raf = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = callback => raf(time => {
      const before = probe.draws, started = performance.now();
      callback(time);
      if (probe.draws !== before) probe.costs.push(performance.now() - started);
    });
  });
  await page.route('**/workflow-fixture?*', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html data-theme="light"><head><meta charset="utf-8"><link rel="icon" href="data:,"><link rel="stylesheet" href="/workflow-qa.css"></head><body><div id="root"></div><script type="module">import "/src/index.css"; import "/src/workbench-appearance.css"; import "/src/operation-motion.css"; import "/workflow-qa.js";</script></body></html>' }));
  const open = async (mode, currentPage = 'manual') => {
    await page.unroute('**/workflow-qa.js');
    await page.unroute('**/workflow-qa.css');
    await page.route('**/workflow-qa.js', route => route.fulfill({ path: `${out}/${mode}.js`, contentType: 'text/javascript' }));
    await page.route('**/workflow-qa.css', route => route.fulfill({ path: `${out}/${mode}.css`, contentType: 'text/css' }));
    await page.goto(`http://localhost:1420/workflow-fixture?demo=1&page=${currentPage}`);
    await page.waitForFunction(() => window.workflowQA && document.querySelector('[data-page]'));
    await page.evaluate(() => document.fonts.ready);
  };
  const nav = name => page.locator('.app-shell__sidebar').getByRole('button', { name, exact: true });
  await page.setViewportSize({ width: 1440, height: 960 });
  for (const mode of ['baseline', 'fixed']) {
    await open(mode);
    await page.evaluate(() => window.workflowQA.activate('manual', 'powerMeter'));
    await page.waitForFunction(() => document.querySelector('canvas[aria-label="功率实时曲线"]'));
    await page.waitForTimeout(500);
    const raw = await page.evaluate(() => {
      window.__manualDraws.draws = 0; window.__manualDraws.times = []; window.__manualDraws.costs = [];
      let count = 0;
      return new Promise(resolve => {
        const timer = setInterval(() => {
          window.workflowQA.advance();
          if (++count === 20) { clearInterval(timer); setTimeout(() => resolve(JSON.stringify(window.workflowQA.data)), 350); }
        }, 200);
      });
    });
    const metrics = await page.evaluate(() => {
      const p = window.__manualDraws, costs = p.costs.toSorted((a, b) => a - b);
      const marker = document.querySelector('[data-marker-style="dinosaur"]');
      return { draws: p.draws, markerVisible: !!marker && getComputedStyle(marker).visibility === 'visible',
        p95DrawCallbackMs: costs[Math.floor(costs.length * .95)] ?? null,
        intermediateFrames: p.times.slice(1).filter((time, i) => time - p.times[i] < 100).length,
        points: window.workflowQA.data.power.length };
    });
    report.replay[mode] = { ...metrics, raw };
    await page.screenshot({ path: `${out}/${mode}-manual.png` });
  }
  check('same-complete-source-data', report.replay.baseline.raw === report.replay.fixed.raw);
  check('manual-dragon-visible', report.replay.fixed.markerVisible, report.replay.fixed);
  check('manual-intermediate-frames', report.replay.fixed.intermediateFrames > 60, report.replay.fixed);
  delete report.replay.baseline.raw; delete report.replay.fixed.raw;
  for (const owner of ['manual', 'automatic']) {
    const other = owner === 'manual' ? '自动测试' : '手动配置';
    for (const device of ['powerSupply', 'powerMeter', 'spectrometer']) {
      await open('fixed', owner);
      await page.evaluate(({ owner, device }) => window.workflowQA.activate(owner, device), { owner, device });
      check(`${owner}-${device}-locks-other-page`, await nav(other).isDisabled());
      await page.keyboard.press(owner === 'manual' ? 'Control+1' : 'Control+2');
      check(`${owner}-${device}-blocks-shortcut`, await page.locator('[data-page]').getAttribute('data-page') === owner);
      await nav('系统设置').click();
      check(`${owner}-${device}-lock-survives-settings`, await nav(other).isDisabled());
      await page.evaluate(() => window.workflowQA.reset());
      check(`${owner}-${device}-unlocks-when-stopped`, await nav(other).isEnabled());
    }
  }
  await open('fixed', 'automatic');
  await page.evaluate(() => window.workflowQA.pause());
  check('automatic-pause-retains-lock', await nav('手动配置').isDisabled());
  // Load a mismatched view directly: curves must remain empty even if a
  // navigation shortcut is bypassed or an old page is restored on reload.
  for (const owner of ['manual', 'automatic']) {
    await open('fixed', owner === 'manual' ? 'automatic' : 'manual');
    await page.evaluate(owner => window.workflowQA.activate(owner, 'powerMeter'), owner);
    await page.waitForTimeout(100);
    check(`${owner}-other-page-has-no-power-canvas`, await page.locator('canvas[aria-label="功率实时曲线"]').count() === 0);
    check(`${owner}-other-page-has-no-efficiency-canvas`, await page.locator('canvas[aria-label="功率 / 效率曲线"]').count() === 0);
  }
  await open('fixed');
  await page.evaluate(() => window.workflowQA.activate('manual', 'powerMeter'));
  await nav('系统设置').click();
  await page.getByRole('combobox', { name: '功率实时曲线末端样式', exact: true }).click();
  await page.getByRole('option', { name: '小蜜蜂', exact: true }).click();
  await nav('手动配置').click();
  await page.waitForFunction(() => getComputedStyle(document.querySelector('[data-marker-style="bee"]')).visibility === 'visible');
  check('appearance-change-propagates-to-manual', true);
  for (const size of [{ width: 1180, height: 720 }, { width: 2048, height: 1063 }]) {
    await page.setViewportSize(size);
    await page.waitForTimeout(200);
    const layout = await page.evaluate(() => {
      const cards = [...document.querySelectorAll('.manual-device-card')].map(e => { const r = e.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, width: r.width }; });
      return { cards, overflow: document.documentElement.scrollWidth > innerWidth };
    });
    check(`cards-align-${size.width}`, layout.cards.length === 3 && Math.max(...layout.cards.map(c => c.top)) - Math.min(...layout.cards.map(c => c.top)) < 1 && Math.max(...layout.cards.map(c => c.bottom)) - Math.min(...layout.cards.map(c => c.bottom)) < 1 && !layout.overflow, layout);
    await page.screenshot({ path: `${out}/manual-${size.width}.png` });
  }
  check('no-browser-errors', report.errors.length === 0, report.errors);
  await page.evaluate(value => { window.__workflowReport = value }, report);
  return report;
}
