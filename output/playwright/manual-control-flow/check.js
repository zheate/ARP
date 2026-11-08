async (page) => {
  await page.addInitScript(() => {
    if (new URL(location.href).searchParams.get('qa') !== 'manual-flow') return;
    localStorage.setItem('power-test.theme', 'light');
    const listeners = new Set();
    const subscriptions = new Map();
    const callbacks = new Map();
    let callbackId = 0;
    const qa = window.__manualQa = { sends: [], generation: 0, holdApply: false, holdConfigure: false, holdStop: false };
    const ready = import('/src/lib/demo-snapshot.ts').then(({ DEMO_SNAPSHOT }) => {
      const state = structuredClone(DEMO_SNAPSHOT);
      const kind = new URL(location.href).searchParams.get('kind') || 'tdk';
      state.backend.runtimeId = `qa-${kind}`;
      state.backend.notices = [];
      state.configuration.powerSupplyKind = kind;
      state.configuration.ch341SelectedLocationPath = 'PCIROOT(0)#USB(1)';
      state.configuration.setCurrentA = 8;
      state.automaticTest = { ...state.automaticTest, state: 'idle', detail: '模拟控制器验证', canStart: false, canEnd: false, terminalOutcome: null };
      state.devices.powerSupply = { ...state.devices.powerSupply, outputEnabled: false, activeCurrentA: 0, outputCurrentA: 0 };
      state.manualTest = { pendingCount: 0, exportStatus: 'idle', exportPath: '', exportError: '', capture: { state: 'idle', detail: '施加电流后等待功率稳定', currentA: null, remainingS: null } };
      state.safety = { ...state.safety, manualControlsAllowed: true, controlOwner: 'manual', shutdown: { requested: false, confirmed: false, measuredCurrentA: null, outputOffConfirmed: false, reason: '' } };
      qa.state = state;
      qa.publish = () => {
        for (const subscriptionId of subscriptions.values()) {
          for (const listener of listeners) listener({ data: { __arpSnapshotStream: subscriptionId, message: { snapshot: structuredClone(qa.state) } } });
        }
      };
    });
    window.chrome = window.chrome || {};
    window.chrome.webview = { addEventListener: (_, listener) => listeners.add(listener), removeEventListener: (_, listener) => listeners.delete(listener) };
    window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} };
    window.__TAURI_INTERNALS__ = {
      metadata: { currentWindow: { label: 'main' }, currentWebview: { label: 'main' } },
      transformCallback: (fn) => { callbacks.set(++callbackId, fn); return callbackId; },
      unregisterCallback: (id) => callbacks.delete(id),
      invoke: async (name, args = {}) => {
        if (name === 'license_get_status') return { valid: true, reason: 'valid', machineHash: 'QA', machineCode: 'QA', licensePath: '' };
        if (name === 'bridge_subscribe') {
          await ready;
          subscriptions.set(1, args.subscriptionId);
          queueMicrotask(qa.publish);
          return 1;
        }
        if (name === 'bridge_unsubscribe') return;
        if (name !== 'bridge_request') return 1;
        await ready;
        const { method, params = {} } = args;
        qa.sends.push({ method, params: structuredClone(params) });
        const state = qa.state;
        const captured = qa.generation;
        if (method === 'app.configure') {
          if (qa.holdConfigure) await new Promise((resolve) => qa.releaseConfigure = resolve);
          if (captured !== qa.generation) throw new Error('命令已因安全停止失效');
          state.configuration = { ...state.configuration, ...params };
        }
        if (method === 'powerSupply.setOutput') {
          state.devices.powerSupply.outputEnabled = params.enabled;
          state.devices.powerSupply.activeCurrentA = 0;
          state.configuration.setCurrentA = 0;
        }
        if (method === 'powerSupply.setCurrent') {
          state.manualTest.currentCommand = { pending: true, requestedCurrentA: params.currentA, remainingS: 1.1, stopping: false };
          qa.publish();
          const late = structuredClone(state);
          if (qa.holdApply) await new Promise((resolve) => qa.releaseApply = resolve);
          if (captured !== qa.generation) {
            if (qa.returnLateResponse) {
              late.devices.powerSupply.activeCurrentA = params.currentA;
              return late;
            }
            throw new Error('命令已因安全停止失效');
          }
          state.devices.powerSupply.activeCurrentA = params.currentA;
          state.devices.powerSupply.outputCurrentA = params.currentA;
          state.configuration.setCurrentA = params.currentA;
          state.manualTest.currentCommand.pending = false;
          state.manualTest.capture = { state: 'waiting_stable', detail: '等待功率稳定', currentA: params.currentA, remainingS: null };
          state.safety.shutdown.requested = false;
        }
        if (method === 'powerSupply.safeRampDown') {
          qa.generation++;
          state.manualTest.currentCommand = { pending: false, requestedCurrentA: null, remainingS: 0, stopping: true };
          qa.publish();
          if (qa.holdStop) await new Promise((resolve) => qa.releaseStop = resolve);
          state.devices.powerSupply.activeCurrentA = 0;
          state.devices.powerSupply.outputCurrentA = 0;
          state.devices.powerSupply.outputEnabled = false;
          state.configuration.setCurrentA = 0;
          state.manualTest.currentCommand.stopping = false;
          state.safety.shutdown = { requested: true, confirmed: !qa.failStop, measuredCurrentA: qa.failStop ? 0.2 : 0, outputOffConfirmed: !qa.failStop, reason: qa.failStop ? '无法确认输出电流归零' : '电源输出已确认关闭' };
          state.manualTest.capture = { state: 'cancelled', detail: '安全下电，未完成的测试点已取消', currentA: null, remainingS: null };
          qa.publish();
          if (qa.failStop) throw new Error('无法确认输出电流归零');
        }
        qa.publish();
        return structuredClone(state);
      },
    };
  });

  const require = (condition, message) => { if (!condition) throw new Error(message); };
  const output = '/Users/zh/Documents/test/ARP/output/playwright/manual-control-flow';
  const results = [];
  for (const kind of ['tdk', 'ch341']) {
    await page.goto(`http://localhost:1420/?page=manual&qa=manual-flow&kind=${kind}`);
    const target = page.getByRole('textbox', { name: '目标电流 (A)' });
    await target.waitFor();
    await page.waitForFunction(() => document.querySelector('#manual-psu-current')?.value === '8');
    const apply = page.getByRole('button', { name: '施加电流', exact: true });
    const stop = page.getByRole('button', { name: '安全下电', exact: true });
    await target.fill('12');
    if (kind === 'tdk') {
      require(await apply.isDisabled(), 'TDK must enable output before applying current');
      await page.getByRole('button', { name: '开启输出', exact: true }).click();
      await page.waitForFunction(() => window.__manualQa.state.devices.powerSupply.outputEnabled);
      require(await target.inputValue() === '12', 'TDK zero confirmation must preserve operator target');
    }
    await page.evaluate(() => window.__manualQa.holdApply = true);
    await apply.click();
    await page.waitForFunction(() => !!window.__manualQa.releaseApply);
    require(await apply.isDisabled(), 'Pending current command must reject duplicate clicks');
    require(await stop.isEnabled(), 'Safe down must remain enabled during a pending command');
    require(await page.getByText('等待 1.1 秒后施加 12 A', { exact: true }).isVisible(), 'Waiting command countdown must be visible');
    // Keep the same DOM in place while down is pending, then verify its result.
    await page.evaluate(() => { window.__manualQa.holdStop = true; window.__manualQa.returnLateResponse = true; });
    await stop.click();
    await page.getByRole('button', { name: '下电中…', exact: true }).waitFor();
    await page.evaluate(() => window.__manualQa.releaseStop());
    await page.getByText('已确认安全下电', { exact: true }).waitFor();
    await page.evaluate(() => window.__manualQa.releaseApply());
    await apply.waitFor();
    await page.waitForFunction(() => !document.querySelector('.app-titlebar__pending'));
    require(await target.inputValue() === '12', 'Safe down must preserve operator target');
    require(await page.getByText('已下发 0 A', { exact: true }).isVisible(), 'A delayed apply response must not restore the old current');
    require(await page.evaluate(() => window.__manualQa.state.devices.powerMeter.running && window.__manualQa.state.devices.powerSupply.connected), 'Safe down must keep acquisition and connection');
    require(await page.evaluate(() => window.__manualQa.sends.filter(x => x.method === 'powerSupply.setCurrent').length) === 1, 'Only one apply request is allowed');
    results.push(`${kind}: target retained, duplicate blocked, priority stop, late response rejected`);

    await page.evaluate(() => { window.__manualQa.holdApply = false; window.__manualQa.holdStop = false; });
    if (kind === 'tdk') await page.getByRole('button', { name: '开启输出', exact: true }).click();
    await apply.click();
    await page.waitForFunction(() => window.__manualQa.state.devices.powerSupply.activeCurrentA === 12);
    for (const [state, detail, remainingS] of [
      ['waiting_voltage', '功率已稳定，等待读取电压', 5],
      ['reading_voltage', '正在读取输出电压', null],
      ['validating', '正在校验光谱并暂存测试点', null],
      ['recorded', '已暂存 12 A 测试点，可保存', null],
      ['failed', '测试点未暂存：光谱饱和', null],
    ]) {
      await page.evaluate(({state, detail, remainingS}) => {
        window.__manualQa.state.manualTest.capture = { state, detail, currentA: 12, remainingS };
        window.__manualQa.publish();
      }, {state, detail, remainingS});
      await page.getByRole('status').filter({ hasText: detail }).waitFor();
    }
    results.push(`${kind}: capture phases update through memoized page`);
    await page.evaluate(() => {
      window.__manualQa.state.manualTest.capture = { state: 'recorded', detail: '已暂存 12 A 测试点，可保存', currentA: 12, remainingS: null };
      window.__manualQa.state.manualTest.exportStatus = 'completed';
      window.__manualQa.publish();
    });
    await page.getByText('测试点已保存', { exact: true }).waitFor();
    // Verify an ordinary setting save cannot finish and send a new apply after down.
    await page.getByRole('textbox', { name: '测试站别' }).fill('QA dirty');
    await page.evaluate(() => window.__manualQa.holdConfigure = true);
    const appliesBefore = await page.evaluate(() => window.__manualQa.sends.filter(x => x.method === 'powerSupply.setCurrent').length);
    await apply.click();
    await page.waitForFunction(() => !!window.__manualQa.releaseConfigure);
    await stop.click();
    await page.getByText('已确认安全下电', { exact: true }).waitFor();
    await page.evaluate(() => window.__manualQa.releaseConfigure());
    await page.waitForFunction(() => !document.querySelector('.app-titlebar__pending'));
    require(await page.evaluate(() => window.__manualQa.sends.filter(x => x.method === 'powerSupply.setCurrent').length) === appliesBefore, 'A stop during configure must prevent subsequent apply');
    results.push(`${kind}: stop during configuration prevents a later current write`);

    await page.evaluate(() => { window.__manualQa.holdConfigure = false; window.__manualQa.failStop = true; });
    await stop.click();
    await page.getByRole('status').filter({ hasText: '下电未确认：无法确认输出电流归零' }).waitFor();
    require(await stop.isEnabled(), 'A failed safe down must allow retry');
    await page.evaluate(() => window.__manualQa.failStop = false);
    await stop.click();
    await page.getByText('已确认安全下电', { exact: true }).waitFor();
    results.push(`${kind}: failed shutdown stays unconfirmed and can be retried`);

    for (const theme of ['light', 'dark']) {
      await page.evaluate((theme) => { document.documentElement.classList.toggle('dark', theme === 'dark'); document.documentElement.dataset.theme = theme; }, theme);
      for (const [width, height] of [[1440,900], [1180,720], [760,600]]) {
        await page.setViewportSize({width,height});
        await page.evaluate(() => document.fonts.ready);
        const boxes = await page.locator('.manual-device-card').evaluateAll(cards => cards.map(card => {
          const cardRect = card.getBoundingClientRect();
          const footer = card.querySelector('.manual-device-card__footer').getBoundingClientRect();
          return {x:cardRect.x,y:cardRect.y,width:cardRect.width,height:cardRect.height,footerY:footer.y};
        }));
        for (const box of boxes) require(box.width > 0 && box.x + box.width <= width + 1, `Card overflow at ${kind}/${width}`);
        if (boxes.every(box => Math.abs(box.y - boxes[0].y) < 1)) {
          require(boxes.every(box => Math.abs(box.width - boxes[0].width) < 1), 'Cards must have equal width');
          require(boxes.every(box => Math.abs(box.height - boxes[0].height) < 1), 'Cards must have equal height');
          require(boxes.every(box => Math.abs(box.footerY - boxes[0].footerY) < 1), 'Card footer baselines must align');
        }
        require(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Page overflow at ${kind}/${width}`);
        await page.screenshot({path:`${output}/${kind}-${theme}-${width}.png`});
        results.push({kind,theme,width,height,boxes});
      }
    }
  }
  console.log(JSON.stringify(results, null, 2));
  return results;
}
