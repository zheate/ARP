async (page) => {
  await page.route('**/src/lib/demo-snapshot.ts*', async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, body: (await response.text()) + `
      DEMO_SNAPSHOT.backend.notices = [];
      DEMO_SNAPSHOT.configuration.sn = "";
      Object.assign(DEMO_SNAPSHOT.automaticTest, { state: "idle", canStart: false, canEnd: false,
        readiness: { ready: false, blockers: [{ code: "missing_sn", message: "请填写产品 SN" }] }, settingsError: "请填写产品 SN" });
      DEMO_SNAPSHOT.safety.controlOwner = "manual";
      DEMO_SNAPSHOT.safety.manualControlsAllowed = true;
      DEMO_SNAPSHOT.devices.spectrometer.running = false;
      DEMO_SNAPSHOT.devices.spectrometer.connected = false;
      DEMO_SNAPSHOT.devices.powerSupply.activeCurrentA = 0;
      DEMO_SNAPSHOT.devices.powerSupply.outputCurrentA = 0;
    ` });
  });
  await page.route('**/src/hooks/use-backend-snapshot.ts*', async (route) => {
    const response = await route.fetch();
    let body = await response.text();
    const anchor = 'if (method === "automatic.start") {';
    if (!body.includes(anchor)) throw new Error('Command fixture anchor changed');
    body = body.replace(anchor, `if (method === "automatic.start") {
      window.__snStartPayload = params;
      return publishDemo({ ...base, configuration: { ...base.configuration, ...params } });
    }
    ${anchor}`);
    await route.fulfill({ response, body });
  });
  await page.goto('http://localhost:1420/?demo=1');
}
