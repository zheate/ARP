async (page) => {
  await page.route('**/feet-fixture', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><head><link rel="icon" href="data:,"><link rel="stylesheet" href="/src/index.css"></head><body><div id="root"></div><script type="module" src="/scripts/fixtures/character-markers.tsx"></script></body></html>' }));
  await page.goto('http://localhost:1420/feet-fixture');
  await page.getByRole('combobox', { name: '验证末端样式' }).selectOption('dinosaur');
  await page.evaluate(() => window.characterMarkerFixture.render({ data: [{time:0,power:5,voltage:50}, {time:5,power:5,voltage:50}] }));
  await page.waitForSelector('.chart-character-marker[data-sprite-ready="true"]');
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const read = () => page.evaluate(() => {
    const marker = document.querySelector('.chart-character-marker');
    const body = marker.querySelector('.chart-character-marker__body');
    const sprite = body.querySelector('canvas');
    const chart = document.querySelector('canvas[aria-label="角色末端验证"]');
    const endpoint = chart.__qaEndpoint;
    const markerRect = marker.getBoundingClientRect();
    const bodyRect = body.getBoundingClientRect();
    const chartRect = chart.getBoundingClientRect();
    const image = sprite.getContext('2d').getImageData(0,0,sprite.width,sprite.height);
    const feet = [];
    for (let frame=0;frame<4;frame++) {
      let bottom = 0;
      for(let y=0;y<sprite.height;y++) for(let x=Math.floor(sprite.height*.4);x<sprite.height*.9;x++) {
        if(image.data[(y*sprite.width+frame*sprite.height+x)*4+3]>128) bottom=Math.max(bottom,y+1);
      }
      feet.push({frame, bottom, fraction:bottom/sprite.height, error: bodyRect.top-chartRect.top+bottom/sprite.height*bodyRect.height-endpoint[1]});
    }
    const fallback = marker.querySelector('.chart-character-marker__fallback');
    const saved = marker.dataset.spriteReady;
    delete marker.dataset.spriteReady;
    const fallbackRect = fallback.getBoundingClientRect();
    const fallbackError = fallbackRect.top+fallbackRect.height/2-chartRect.top-endpoint[1];
    marker.dataset.spriteReady = saved;
    return { endpoint, markerTop:markerRect.top-chartRect.top, bodyTop:bodyRect.top-chartRect.top, feet, fallbackError, clipped:markerRect.top<chartRect.top || markerRect.right>chartRect.right+.1 };
  });
  const results=[];
  for(const width of [960,1600]) for(const topEdge of [false,true]) {
    await page.setViewportSize({width,height:900});
    await page.evaluate(top => window.characterMarkerFixture.render({ data: [{time:0,power:top?10:5,voltage:50}, {time:top?10:5,power:top?10:5,voltage:50}] }),topEdge);
    await page.evaluate(() => new Promise(resolve => setTimeout(resolve,250)));
    const result=await read();
    if(result.feet.some(foot => Math.abs(foot.error)>1)) throw Error('Dragon feet are not on the curve: '+JSON.stringify(result.feet));
    if(result.clipped || Math.abs(result.fallbackError)>.1) throw Error('Clipped sprite or displaced fallback: '+JSON.stringify(result));
    await page.screenshot({ path:`output/playwright/dragon-feet-20261016/fixture-${width}-${topEdge?'top':'flat'}.png` });
    results.push({width,topEdge,...result});
  }
  await page.getByRole('button',{name:'清空数据'}).click();
  await page.waitForFunction(() => getComputedStyle(document.querySelector('.chart-character-marker')).visibility==='hidden');
  await page.getByRole('button',{name:'恢复数据'}).click();
  await page.waitForFunction(() => getComputedStyle(document.querySelector('.chart-character-marker')).visibility==='visible');
  return {checks:results,emptyAndRestore:true};
}
