import { chromium as original } from '/Users/zh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs';
export const chromium = new Proxy(original, { get(target, key) {
  if (key === 'launch') return options => target.launch({ ...options, executablePath: '/Users/zh/Library/Caches/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-mac-arm64/chrome-headless-shell' });
  const value = Reflect.get(target, key);
  return typeof value === 'function' ? value.bind(target) : value;
}});
