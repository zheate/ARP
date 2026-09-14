/** Browser-only evidence capture. Never starts a Tauri sidecar or a hardware command. */
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve, isAbsolute } from 'node:path';
import { pathToFileURL } from 'node:url';

const target = new URL(process.env.ARP_UI_QA_URL ?? 'http://127.0.0.1:1420/?demo=1');
if (!['127.0.0.1', 'localhost', '[::1]'].includes(target.hostname) || target.searchParams.get('demo') !== '1') {
  throw new Error('UI QA is restricted to a loopback browser demo with ?demo=1.');
}
const modulePath = process.env.ARP_PLAYWRIGHT_MODULE ?? 'playwright';
const { chromium } = await import(isAbsolute(modulePath) ? pathToFileURL(modulePath).href : modulePath);
const output = resolve(process.env.ARP_UI_QA_OUTPUT ?? '../qa-evidence/browser');
await mkdir(output, { recursive: true });
const pages = [
  ['automatic', '自动测试'], ['manual', '手动配置'], ['pd', 'PD 采集'],
  ['data', '数据浏览'], ['shipping', '出货报告'], ['settings', '系统设置'],
];
// DPR and logical viewport are recorded separately. This is not native Windows DPI acceptance.
const configurations = [
  { name: '1920x1080', width: 1920, height: 1080, dpr: 1 },
  { name: '1366x768', width: 1366, height: 768, dpr: 1 },
  { name: '2560x1440', width: 2560, height: 1440, dpr: 1 },
  { name: '1920-at-125', width: 1536, height: 864, dpr: 1.25 },
  { name: '1920-at-150', width: 1280, height: 720, dpr: 1.5 },
  { name: '1920-at-200', width: 960, height: 540, dpr: 2 },
];
const results = { target: target.href, browserOnly: true, configurations, captures: [] };
const browser = await chromium.launch({ headless: true });
try {
  for (const configuration of configurations) {
    const context = await browser.newContext({
      viewport: { width: configuration.width, height: configuration.height },
      deviceScaleFactor: configuration.dpr, colorScheme: 'dark', locale: 'zh-CN',
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push({ type: 'pageerror', message: error.message }));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push({ type: 'console', message: message.text() });
    });
    await page.goto(target.href, { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: /自动测试/ }).first().waitFor({ timeout: 15000 });
    await page.evaluate(() => document.fonts.ready);
    for (const [id, label] of pages) {
      const filename = `${configuration.name}-${id}`;
      const errorStart = errors.length;
      let navigationError = null;
      try {
        await page.getByRole('button', { name: new RegExp(label) }).first().click({ timeout: 5000 });
        await page.waitForTimeout(350);
      } catch (error) {
        navigationError = String(error);
      }
      const geometry = await page.evaluate(() => {
        const rectangle = (element) => {
          const r = element.getBoundingClientRect();
          return { x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, bottom: r.bottom };
        };
        const visible = (element) => {
          const r = element.getBoundingClientRect();
          const css = getComputedStyle(element);
          return r.width > 0 && r.height > 0 && css.visibility !== 'hidden' && css.display !== 'none';
        };
        const descriptor = (element) => ({
          tag: element.tagName, class: typeof element.className === 'string' ? element.className : '',
          text: (element.getAttribute('aria-label') || element.textContent || '').trim().slice(0, 160),
          title: element.getAttribute('title'), rect: rectangle(element),
        });
        const controls = [...document.querySelectorAll('button,input,select,[role="combobox"],[role="tab"]')].filter(visible);
        return {
          viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio },
          documentWidth: document.documentElement.scrollWidth,
          horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
          controls: controls.map((element) => ({ ...descriptor(element), disabled: element.matches(':disabled') || element.getAttribute('aria-disabled') === 'true' })),
          outsideViewport: controls.filter((element) => {
            const r = element.getBoundingClientRect();
            return r.x < -1 || r.right > innerWidth + 1 || r.y < -1 || r.bottom > innerHeight + 1;
          }).map(descriptor),
          charts: [...document.querySelectorAll('canvas')].filter(visible).map(descriptor),
          dialogs: [...document.querySelectorAll('[role="dialog"],[role="alertdialog"]')].filter(visible).map(descriptor),
          text: document.body.innerText,
        };
      });
      await page.screenshot({ path: resolve(output, `${filename}.png`), fullPage: false });
      if (configuration.dpr === 1 && configuration.width !== 2560) {
        await writeFile(resolve(output, `${filename}.html`), await page.content());
        await page.keyboard.press('Tab');
        geometry.focus = await page.evaluate(() => {
          const element = document.activeElement;
          const css = getComputedStyle(element);
          const r = element.getBoundingClientRect();
          return { tag: element.tagName, text: element.getAttribute('aria-label') || element.textContent, outline: css.outline, boxShadow: css.boxShadow, focusVisible: element.matches(':focus-visible'), x: r.x, y: r.y, width: r.width, height: r.height };
        });
        await page.screenshot({ path: resolve(output, `${filename}-focus.png`), fullPage: false });
      }
      results.captures.push({ configuration: configuration.name, page: id, navigationError, errors: errors.slice(errorStart), geometry });
      await writeFile(resolve(output, 'results.json'), JSON.stringify(results, null, 2));
    }
    results.captures.push({ configuration: configuration.name, startupErrors: errors });
    await context.close();
  }
} finally {
  await browser.close();
  await writeFile(resolve(output, 'results.json'), JSON.stringify(results, null, 2));
}
console.log(JSON.stringify(results.captures.map(({ configuration, page, navigationError, errors, geometry }) => ({ configuration, page, navigationError, errors, horizontalOverflow: geometry?.horizontalOverflow, outsideViewport: geometry?.outsideViewport.length })), null, 2));
