async (page) => {
  const results=[];
  await page.evaluate(()=>{document.documentElement.dataset.theme='light'});
  for(const width of [1280,1440,1920]) {
    await page.setViewportSize({width,height:1100});
    for(const density of ['compact','comfortable','distant']) {
      await page.evaluate(density=>document.documentElement.dataset.density=density,density);
      const card=page.locator('.automatic-metric-grid .automatic-metric-card--power');
      await card.scrollIntoViewIfNeeded();
      const result=await card.evaluate(el=>{
        const value=el.querySelector('.automatic-metric-card__value');
        const title=el.querySelector('.automatic-metric-card__title');
        const box=el.getBoundingClientRect(), v=value.getBoundingClientRect();
        const before=[...document.querySelectorAll('.automatic-metric-card')].map(el=>el.getBoundingClientRect().toJSON());
        const statusBefore=title.getBoundingClientRect().toJSON();
        value.style.transform='none';
        const original=[...document.querySelectorAll('.automatic-metric-card')].map(el=>el.getBoundingClientRect().toJSON());
        value.style.removeProperty('transform');
        if(JSON.stringify(before)!==JSON.stringify(original)) throw new Error('Card dimensions moved');
        if(JSON.stringify(statusBefore)!==JSON.stringify(title.getBoundingClientRect().toJSON())) throw new Error('Status moved');
        const textRects=node=>{const walker=document.createTreeWalker(node,NodeFilter.SHOW_TEXT);const boxes=[];while(walker.nextNode()){if(!walker.currentNode.textContent.trim())continue;const range=document.createRange();range.selectNodeContents(walker.currentNode);boxes.push(...range.getClientRects());}return boxes;};
        const statusRects=textRects(title), valueRects=textRects(value);
        const overlaps=statusRects.some(a=>valueRects.some(b=>Math.min(a.right,b.right)>Math.max(a.left,b.left) && Math.min(a.bottom,b.bottom)>Math.max(a.top,b.top)));
        return {width:innerWidth,density:document.documentElement.dataset.density,cardHeight:box.height,centerError:Math.abs(v.y+v.height/2-box.y-box.height/2),overlaps};
      });
      results.push(result);
      await card.screenshot({path:`output/playwright/power-center/${width}-${density}.png`});
    }
  }
  return results;
}
