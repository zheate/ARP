async (page) => {
  const fixture = {"sourcePath": "C:/preview/HAU5A0028.xlsx", "schemaVersion": "1", "sn": "HAU5A0028", "productName": "150W 450nm Fiber Coupling Module", "testMode": "automatic", "singlePoint": false, "currents": [20], "points": [{"currentA": 20, "voltageV": 29.91, "powerW": 316.8, "efficiencyPercent": 52.96, "peakWavelengthNm": 974.9552155814577, "fwhmNm": 0.748, "slopeEfficiencyWPerA": 15.839}], "spectrumComplete": true, "spectrumWavelengthRange": {"minimum": 970, "maximum": 980}, "allowedReportTypes": ["spectrum", "pole"], "compatibility": {"kind": "verified_success", "status": "success", "message": "", "requiresLegacyConfirmation": false}, "fieldDefinitions": {"spectrum": [{"key": "power", "label": "Output Power (CW)", "unit": "W", "side": "left", "measuredKey": "powerW"}, {"key": "operatingCurrent", "label": "Operating Current", "unit": "A", "side": "left", "measuredKey": "currentA"}, {"key": "centerWavelength", "label": "Center Wavelength", "unit": "nm", "side": "left", "measuredKey": "peakWavelengthNm"}, {"key": "fwhm", "label": "Spectral Width (FWHM)", "unit": "nm", "side": "left", "measuredKey": "fwhmNm"}, {"key": "wavelengthCurrentCoefficient", "label": "Wavelength-Current Coefficient", "unit": "nm/A", "side": "left", "measuredKey": "wavelengthCurrentCoefficientNmPerA"}, {"key": "coolingTemperature", "label": "Cooling Temperature", "unit": "°C", "side": "left"}, {"key": "powerFluctuation", "label": "Power Fluctuation", "unit": "%", "side": "left"}, {"key": "thresholdCurrent", "label": "Threshold Current", "unit": "A", "side": "right"}, {"key": "operatingVoltage", "label": "Operating Voltage", "unit": "V", "side": "right", "measuredKey": "voltageV"}, {"key": "slopeEfficiency", "label": "Slope Efficiency", "unit": "W/A", "side": "right", "measuredKey": "slopeEfficiencyWPerA"}, {"key": "electroOpticalEfficiency", "label": "E-O efficiency", "unit": "%", "side": "right", "measuredKey": "efficiencyPercent"}, {"key": "na017Energy", "label": "NA 0.17 Encircled Energy", "unit": "%", "side": "right"}, {"key": "fiberCoreDiameter", "label": "Fiber Core Diameter", "unit": "μm", "side": "right"}, {"key": "fiberNa", "label": "Fiber NA", "unit": "", "side": "right"}], "pole": [{"key": "power", "label": "Output Power", "unit": "W", "side": "single", "measuredKey": "powerW"}, {"key": "operatingCurrent", "label": "Operating Current", "unit": "A", "side": "single", "measuredKey": "currentA"}, {"key": "operatingVoltage", "label": "Operating Voltage", "unit": "V", "side": "single", "measuredKey": "voltageV"}, {"key": "slopeEfficiency", "label": "Slope Efficiency", "unit": "W/A", "side": "single", "measuredKey": "slopeEfficiencyWPerA"}, {"key": "electroOpticalEfficiency", "label": "E-O efficiency", "unit": "%", "side": "single", "measuredKey": "efficiencyPercent"}, {"key": "coolingTemperature", "label": "Cooling Temperature", "unit": "°C", "side": "single"}]}, "preferences": {"spectrum": {"selectedFields": ["power", "operatingCurrent", "centerWavelength", "fwhm", "wavelengthCurrentCoefficient", "coolingTemperature", "powerFluctuation", "thresholdCurrent", "operatingVoltage", "slopeEfficiency", "electroOpticalEfficiency", "na017Energy", "fiberCoreDiameter", "fiberNa"], "manualValues": {"coolingTemperature": "25", "na017Energy": "95.5"}, "customFields": [], "reportLanguage": "zh"}, "pole": {"selectedFields": ["power", "operatingCurrent", "operatingVoltage", "slopeEfficiency", "electroOpticalEfficiency", "coolingTemperature"], "manualValues": {"coolingTemperature": "25", "na017Energy": "95.5"}, "customFields": [], "reportLanguage": "zh"}}};
  await page.unroute('**/src/lib/backend.ts*');
  await page.route('**/src/lib/backend.ts*', async route => {
    const response = await route.fetch();
    const source = await response.text();
    const needle = 'export async function sendBackendRequest(method, params = {}) {';
    if (!source.includes(needle)) throw new Error('Backend mock injection point missing');
    const mock = `const fixture = ${JSON.stringify(fixture)}; if (method === 'shippingReport.inspect') return fixture; if (method === 'shippingReport.preview') return {pages: []}; if (method === 'shippingReport.batchInspect') return {rootPath:'C:/preview',items:[{...fixture,status:'ready',modifiedAtMs:Date.now()}],unionCurrents:[20],commonCurrents:[20],productNames:[fixture.productName],allReadySupportSpectrum:true,preferences:fixture.preferences};`;
    await route.fulfill({response, body: source.replace(needle, needle + mock)});
  });
  await page.addInitScript(() => {localStorage.setItem('power-test.theme','light'); localStorage.setItem('power-test.accent-theme','rose');});
  await page.goto('http://localhost:1420/?demo=1&run=1');
  await page.getByRole('button', {name:'出货报告', exact:true}).click();
  await page.getByRole('textbox', {name:'Excel 测试数据路径', exact:true}).fill(fixture.sourcePath);
  await page.getByRole('textbox', {name:'本地 PDF 保存文件夹', exact:true}).fill('C:/preview');
  await page.getByRole('button', {name:'读取并配置报告', exact:true}).click();
  await page.getByText('输出功率（CW）', {exact:true}).waitFor();

  const results = [];
  const check = async (mode) => {
    const section = page.locator('div').filter({has:page.getByRole('heading',{name:'光谱横轴',exact:true})}).filter({has:page.getByText('下限 (nm)',{exact:true})});
    const heading=page.getByRole('heading',{name:'光谱横轴',exact:true});
    const block=heading.locator('..');
    const inputs=block.locator('input');
    const values=await inputs.evaluateAll(elements=>elements.map(el=>el.value));
    if(JSON.stringify(values)!==JSON.stringify(['959.96','989.96'])) throw new Error(JSON.stringify(values));
    await block.scrollIntoViewIfNeeded();
    await block.screenshot({path:`output/playwright/shipping-font/wavelength-rounded-${mode}.png`});
    results.push({mode,values});
  };
  await page.setViewportSize({width:1440,height:1000});
  await check('single');
  await page.getByRole('button',{name:'批量生成',exact:true}).click();
  await page.getByRole('textbox',{name:'Excel 批量根目录',exact:true}).fill('C:/preview');
  await page.getByRole('button',{name:'扫描并预检',exact:true}).click();
  await page.getByRole('heading',{name:'光谱横轴',exact:true}).waitFor();
  await check('batch');
  return results;
}
