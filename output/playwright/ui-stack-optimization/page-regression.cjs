async (page) => {
  await page.goto('http://127.0.0.1:1432/?demo=1&run=1');
  await page.setViewportSize({width:1440,height:900});
  // Force a real production chunk load failure before any report prefetch.
  let block=true;
  await page.route('**/report-*.js',route=>block?route.abort('failed'):route.continue());
  await page.getByRole('button',{name:'出货报告',exact:true}).evaluate(el=>el.click());
  await page.getByRole('button',{name:'重试加载',exact:true}).waitFor();
  if(!await page.getByRole('button',{name:'急停',exact:true}).isEnabled())throw new Error('Page failure blocked global safety control');
  block=false;
  await page.getByRole('button',{name:'重试加载',exact:true}).click();
  await page.getByRole('textbox',{name:'Excel 测试数据路径',exact:true}).waitFor();
  await page.unroute('**/report-*.js');
  await page.getByRole('button',{name:'数据浏览',exact:true}).click();
  const search=page.getByPlaceholder('搜索产品 SN…');
  await search.fill('DEMO-LD-003');await page.waitForTimeout(500);
  await page.getByRole('button',{name:'自动测试',exact:true}).click();
  await page.getByRole('button',{name:'数据浏览',exact:true}).click();
  if(await search.inputValue()!=='DEMO-LD-003')throw new Error('Data page lost search after navigation');
  await page.getByRole('button',{name:'自动测试',exact:true}).click();
  await page.getByRole('textbox',{name:'产品 SN',exact:true}).fill('QA-UNSAVED');
  await page.getByRole('button',{name:'手动配置',exact:true}).click();
  if(await page.getByRole('textbox',{name:'产品 SN',exact:true}).inputValue()!=='QA-UNSAVED')throw new Error('Configuration draft lost on page change');
  await page.getByRole('button',{name:'自动测试',exact:true}).click();
  await page.getByRole('button',{name:'添加',exact:true}).click();
  await page.getByRole('dialog').waitFor();await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'编辑',exact:true}).click();
  await page.getByRole('dialog').waitFor();await page.keyboard.press('Escape');
  await page.goto('http://127.0.0.1:1432/?demo=1&run=1&page=data');
  await page.getByPlaceholder('搜索产品 SN…').waitFor();
  return {status:'PASS', checks:['production import failure and retry','global emergency control remains enabled','data filter survives navigation','unsaved configuration survives navigation','extracted setup add/edit callbacks','direct data-page entry']};
}
