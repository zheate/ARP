async (page) => {
  const checks=[];
  await page.keyboard.press('Escape');
  await page.getByRole('tab',{name:'曲线分析',exact:true}).click();
  const filters=page.getByRole('button',{name:'数据源与更多筛选',exact:true});
  if(await filters.getAttribute('aria-expanded')==='true')await filters.click();
  function check(name,pass,detail){checks.push({name,pass,detail});if(!pass)throw Error(name+': '+JSON.stringify(detail));}
  async function choose(name,text){const trigger=page.getByRole('combobox',{name,exact:true});await trigger.click();await page.getByRole('option',{name:text,exact:true}).click();await page.getByRole('listbox').waitFor({state:'hidden'});await page.waitForFunction(name=>document.activeElement?.getAttribute('aria-label')===name,name);check(name+' selection',(await trigger.innerText()).includes(text));}
  await page.getByRole('combobox',{name:'曲线内容',exact:true}).click();
  check('fragment options preserved',await page.getByRole('option').count()===4,await page.getByRole('option').allTextContents());
  await page.getByRole('option',{name:'多电流光谱',exact:true}).click();
  await choose('曲线内容','指标曲线');
  await choose('分析指标','FWHM (nm)');
  await choose('当前测试点','#10 · 20.000 A');
  check('numeric point updates',await page.getByRole('slider',{name:'实测点位置'}).inputValue()==='9');
  await page.getByRole('checkbox',{name:'双游标',exact:true}).uncheck();await page.getByRole('checkbox',{name:'双游标',exact:true}).check();await page.getByRole('button',{name:'清除游标',exact:true}).click();
  await choose('当前激活游标','游标 B');
  await choose('游标 A 测试点','#1 · 2.000000 A');
  check('cursor auto advance preserved',(await page.getByRole('combobox',{name:'当前激活游标',exact:true}).innerText()).includes('游标 B')); 
  await choose('游标 B 测试点','#10 · 20.000000 A');
  await page.getByRole('button',{name:'拟合 A–B',exact:true}).click();
  await page.getByRole('button',{name:'履历 · 5 次测试',exact:true}).click();
  await page.getByRole('combobox',{name:'履历指标',exact:true}).waitFor();
  await choose('履历指标','功率 @ 共同电流 (W)');
  await choose('履历比较电流','20.000000 A');
  await page.getByRole('button',{name:'数据源与更多筛选',exact:true}).click();
  await choose('测试站别筛选','封盖后');
  check('implicit station values preserved',(await page.locator('.db-records').innerText()).length>0);
  await choose('测试站别筛选','全部站别');
  await choose('日期范围','最近 7 天');
  await choose('日期范围','全部时间');
  await page.getByRole('combobox',{name:'测试类型筛选',exact:true}).click();
  check('test modes preserved',await page.getByRole('option').count()===4,await page.getByRole('option').allTextContents());
  await page.keyboard.press('Escape');
  await page.screenshot({path:'output/playwright/all-dropdowns-20261016/data-history-dark-1920.png'});
  return checks;
}
