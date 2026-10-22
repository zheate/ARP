async (page) => {
  await page.reload();
  await page.getByRole('button', {name:'停止',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).last().click();
  await page.evaluate(() => {
    window.__TAURI_INTERNALS__ = {invoke: async (command,args) => {
      if(command==='bridge_request' && args.method.startsWith('dataBrowser.')) {
        const response=await fetch('http://127.0.0.1:18763',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(args)});
        const data=await response.json(); if(!response.ok) throw new Error(data.error); return data;
      }
      throw new Error('Preview bridge: '+command);
    }};
  });
  await page.getByRole('button',{name:'数据浏览',exact:true}).click();
  await page.getByRole('img',{name:'历史功率电流曲线'}).waitFor();
  await page.screenshot({path:'output/playwright/data-library-20260917/03-overview-final-1440.png'});
  const boxes=page.getByRole('checkbox',{name:/选择对比/});
  for(let i=0;i<5;i++) await boxes.nth(i).check();
  if(!(await boxes.nth(5).isDisabled())) throw new Error('Selection limit failed');
  await page.getByRole('button',{name:'对比曲线',exact:true}).click();
  await page.getByRole('img',{name:'多条测试功率电流曲线'}).waitFor();
  const metrics=[['电压 V','电压'],['电光效率 %','电光效率'],['峰值波长 nm','峰值波长'],['质心波长 nm','质心波长'],['FWHM nm','FWHM'],['PIB %','PIB'],['SMSR dB','SMSR']];
  for(const [tab,label] of metrics) {
    await page.getByRole('tab',{name:tab,exact:true}).click();
    await page.getByRole('img',{name:`多条测试${label}电流曲线`}).waitFor();
    if(await page.locator('.db-data-table tbody tr').count()!==10) throw new Error('Missing comparison rows: '+label);
  }
  if(!(await page.locator('.db-chart-legend').innerText()).includes('未记录此参数')) throw new Error('Missing metric not labelled');
  await page.getByRole('tab',{name:'峰值波长 nm',exact:true}).click();
  const layout=[];
  for(const [width,height] of [[1440,1000],[1280,800],[1920,1080]]) {
    await page.setViewportSize({width,height});
    await page.screenshot({path:`output/playwright/data-library-20260917/04-comparison-${width}.png`});
    layout.push(await page.locator('.db-workspace').evaluate(el=>({width:window.innerWidth,columns:getComputedStyle(el).gridTemplateColumns,overflow:el.scrollWidth>el.clientWidth,edges:[...el.children].map(x=>x.getBoundingClientRect().top)})));
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.evaluate(()=>{document.documentElement.dataset.theme='light';document.documentElement.classList.remove('dark')});
  await page.screenshot({path:'output/playwright/data-library-20260917/05-comparison-light.png'});
  await page.evaluate(()=>{document.documentElement.dataset.theme='dark';document.documentElement.classList.add('dark')});
  await page.getByRole('textbox',{name:'搜索 SN',exact:true}).fill('H6B560569');
  await page.getByRole('button',{name:'清除筛选',exact:true}).waitFor();
  await page.waitForFunction(()=>document.querySelectorAll('.db-record-item').length===1);
  if(await page.locator('.db-selected-item').count()!==5) throw new Error('Filtering lost selection');
  await page.getByRole('button',{name:'清除筛选',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('.db-record-item').length===50);
  await page.getByRole('button',{name:'下一页',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('.db-record-item').length===5);
  if(await page.locator('.db-selected-item').count()!==5) throw new Error('Paging lost selection');
  await page.getByRole('button',{name:'上一页',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('.db-record-item').length===50);
  await page.locator('.db-comparison-context summary').click();
  await page.getByRole('button',{name:'设为基准',exact:true}).first().click();
  await page.waitForFunction(()=>document.querySelector('.db-compare-card strong')?.textContent.includes('H6B560570'));
  await page.locator('.db-comparison-context summary').click();
  await page.getByRole('button',{name:'展开详情',exact:true}).click();
  if(await page.getByRole('complementary',{name:'测试记录列表'}).isVisible()) throw new Error('Expand failed');
  await page.getByRole('button',{name:'收起详情',exact:true}).click();
  return {layout,metricsChecked:8,comparisonCount:5,filterPersistence:true,paginationPersistence:true,baselineSwitch:true};
}
