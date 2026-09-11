async (page) => {
  await page.unrouteAll({ behavior: 'wait' });
  await page.route('**/src/lib/demo-snapshot.ts*', async (route) => {
    const response = await route.fetch();
    const body = await response.text();
    await route.fulfill({ response, body: body + `
      DEMO_SNAPSHOT.backend.notices = [];
      DEMO_SNAPSHOT.automaticTest.state = "idle";
      DEMO_SNAPSHOT.safety.controlOwner = "manual";
      DEMO_SNAPSHOT.safety.manualControlsAllowed = true;
      DEMO_SNAPSHOT.devices.powerSupply.activeCurrentA = 0;
      DEMO_SNAPSHOT.devices.powerSupply.outputCurrentA = 0;
      DEMO_SNAPSHOT.devices.powerSupply.outputEnabled = false;
    ` });
  });
  await page.route('**/src/hooks/use-backend-snapshot.ts*', async (route) => {
    const response = await route.fetch();
    let body = await response.text();
    // Model the asynchronous gap in the real native subscription. Keep the
    // actual view reset, notification state, timers and page components.
    if (!body.includes('if (DEMO_PREVIEW_ENABLED) {') || !body.includes('const base = demoSnapshotRef.current;')) throw new Error('Hook fixture anchors changed');
    body = body.replace('if (DEMO_PREVIEW_ENABLED) {', 'if (DEMO_PREVIEW_ENABLED) { await new Promise(resolve => setTimeout(resolve, 150));');
    body = body.replace('const base = demoSnapshotRef.current;', `const base = demoSnapshotRef.current;
      if (method === "powerSupply.read") {
        const next = { ...base, backend: { ...base.backend, notices: [...base.backend.notices, {
          id: crypto.randomUUID(), level: "info", title: "输出电压", message: "输出电压：12.00 V"
        }] } };
        demoSnapshotRef.current = next;
        snapshotRef.current = next;
        setSnapshot(next);
        return next;
      }
    `);
    await route.fulfill({ response, body });
  });
  await page.route('**/src/App.tsx*', async (route) => {
    const response = await route.fetch();
    let body = await response.text();
    if (!body.includes('if (!hasNotificationSnapshot) return;')) throw new Error('Fix guard not found');
    body = body.replace('if (!hasNotificationSnapshot) return;', 'if (!hasNotificationSnapshot && !new URLSearchParams(location.search).has("testOriginal")) return;');
    await route.fulfill({ response, body });
  });
  await page.goto('http://localhost:1420/?demo=1&page=manual&testOriginal=1');
}
