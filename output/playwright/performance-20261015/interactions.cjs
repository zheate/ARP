async (page) => {
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto('http://localhost:1420/?demo=1&page=settings');
 await page.getByRole('heading',{name:'系统设置 / 外观'}).waitFor();
 return {buttons:await page.getByRole('button').allTextContents(),selects:await page.getByRole('combobox').allTextContents(),errors};
}
