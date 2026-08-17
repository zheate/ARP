import { getCurrentWebview } from "@tauri-apps/api/webview";

const STORAGE_KEY = "power-test.webview-zoom-factor";
const DEFAULT_ZOOM = 1;
const MIN_ZOOM = 0.2;
const MAX_ZOOM = 5;
const ZOOM_STEP = 0.2;
const WHEEL_STEP_THRESHOLD = 50;

function normalizedZoom(value: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.round(value * 10) / 10));
}

function readStoredZoom(): number {
  try {
    const value = Number(window.localStorage.getItem(STORAGE_KEY));
    return Number.isFinite(value) && value >= MIN_ZOOM && value <= MAX_ZOOM
      ? normalizedZoom(value)
      : DEFAULT_ZOOM;
  } catch {
    return DEFAULT_ZOOM;
  }
}

function writeStoredZoom(value: number): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, String(value));
  } catch {
    // Keep zoom controls working even if WebView storage is unavailable.
  }
}

export function installPersistentZoom(): void {
  if (!("__TAURI_INTERNALS__" in window)) return;

  const webview = getCurrentWebview();
  let zoom = readStoredZoom();
  let wheelDelta = 0;

  const applyZoom = (nextZoom: number): void => {
    zoom = normalizedZoom(nextZoom);
    writeStoredZoom(zoom);
    void webview.setZoom(zoom).catch((error: unknown) => {
      console.error("无法应用页面缩放", error);
    });
  };

  applyZoom(zoom);

  window.addEventListener("keydown", (event) => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey) return;

    if (event.code === "Digit0" || event.code === "Numpad0") {
      event.preventDefault();
      applyZoom(DEFAULT_ZOOM);
      return;
    }

    if (event.code === "Equal" || event.code === "NumpadAdd") {
      event.preventDefault();
      applyZoom(zoom + ZOOM_STEP);
      return;
    }

    if (event.code === "Minus" || event.code === "NumpadSubtract") {
      event.preventDefault();
      applyZoom(zoom - ZOOM_STEP);
    }
  }, true);

  window.addEventListener("wheel", (event) => {
    if (!(event.ctrlKey || event.metaKey)) return;

    event.preventDefault();
    const deltaScale = event.deltaMode === event.DOM_DELTA_LINE
      ? 16
      : event.deltaMode === event.DOM_DELTA_PAGE
        ? window.innerHeight
        : 1;
    wheelDelta += event.deltaY * deltaScale;
    if (Math.abs(wheelDelta) < WHEEL_STEP_THRESHOLD) return;

    applyZoom(zoom + (wheelDelta < 0 ? ZOOM_STEP : -ZOOM_STEP));
    wheelDelta = 0;
  }, { capture: true, passive: false });
}
