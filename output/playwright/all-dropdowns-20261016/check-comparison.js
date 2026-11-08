async (page) => {
  const checks=[];const check=(name,pass,detail)=>{checks.push({name,pass,detail});if(!pass)throw Error(name+': '+JSON.stringify(detail));};
  await page.getByRole('button',{name:'Excel 文件',exact:true}).click();
  const status=page.getByRole('combobox',{name:'记录状态筛选',exact:true});
  await status.click();check('Excel status options',await page.getByRole('option').count()>=2,await page.getByRole('option').allTextContents());
  await page.getByRole('option',{name:'读取失败',exact:true}).click();check('error status selection',(await status.innerText()).includes('读取失败'));
  await page.getByText('未找到测试记录',{exact:true}).waitFor();check('empty results remain usable',await status.isEnabled());
  await status.click();await page.getByRole('option',{name:'全部状态',exact:true}).click();
  await page.getByRole('button',{name:'测试数据库',exact:true}).click();
  await page.locator('.db-records').getByRole('checkbox').nth(0).check();await page.locator('.db-records').getByRole('checkbox').nth(1).check();
  await page.getByRole('button',{name:'开始比较',exact:true}).click();
  const baseline=page.getByRole('combobox',{name:'基准记录',exact:true});await baseline.click();const labels=await page.getByRole('option').allTextContents();
  await page.getByRole('option').last().click();check('baseline selection',(await baseline.innerText()).includes(labels.at(-1)));
  const point=page.getByRole('combobox',{name:'当前比较电流',exact:true});await point.click();await page.getByRole('option').first().click();check('zero index preserved',(await point.innerText()).includes('#1'));
  for(const [width,height] of [[1920,1080],[1366,768]]){
    await page.setViewportSize({width,height});await baseline.click();
    const b=await page.getByRole('listbox').boundingBox();check(`${width}-baseline-visible`,b.y>=0&&b.y+b.height<=height&&b.x+b.width<=width,b);
    await page.screenshot({path:`output/playwright/all-dropdowns-20261016/data-compare-dark-${width}.png`});await page.keyboard.press('Escape');
    check(`${width}-no-overflow`,await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  }
  await page.getByRole('button',{name:'系统设置',exact:true}).click();await page.getByRole('button',{name:'浅色',exact:true}).click();await page.getByRole('button',{name:'数据浏览',exact:true}).click();
  await baseline.click();await page.screenshot({path:'output/playwright/all-dropdowns-20261016/data-compare-light-1366.png'});await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'系统设置',exact:true}).click();await page.getByRole('button',{name:'深色',exact:true}).click();await page.getByRole('button',{name:'自动测试',exact:true}).click();
  return checks;
}
