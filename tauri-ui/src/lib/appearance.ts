export const ACCENT_THEMES = [
  { id: "orange", label: "经典橙" },
  { id: "blue", label: "极光蓝" },
  { id: "green", label: "森林绿" },
  { id: "purple", label: "鸢尾紫" },
  { id: "rose", label: "玫瑰" },
  { id: "graphite", label: "石墨" },
] as const

export type AccentTheme = typeof ACCENT_THEMES[number]["id"]
export type Theme = "dark" | "light"
export type CurveLineStyle = "solid" | "dashed" | "dotted"
export type LatestPointMarkerStyle = "point" | "paper-plane" | "paw" | "comet" | "ecg" | "flappy-bird"
export type ChartCanvasStyle = "clean" | "fine" | "engineering"
export type InterfaceDensity = "compact" | "comfortable" | "distant"

export const CHART_CANVASES = [
  { id: "clean", label: "纯净" },
  { id: "fine", label: "细网格" },
  { id: "engineering", label: "工程坐标纸" },
] as const

export const INTERFACE_DENSITIES = [
  { id: "compact", label: "紧凑", description: "更多内容同屏" },
  { id: "comfortable", label: "舒展", description: "更宽松的间距" },
  { id: "distant", label: "远看", description: "放大关键读数" },
] as const

export interface AppearancePreferences {
  canvas: ChartCanvasStyle
  gridOpacity: number
  lineWidth: number
  trailEnabled: boolean
  trailSeconds: number
  density: InterfaceDensity
  highlightChanges: boolean
}

export const APPEARANCE_STORAGE_KEY = "power-test.appearance.v1"
export const DEFAULT_APPEARANCE: Readonly<AppearancePreferences> = {
  canvas: "fine",
  gridOpacity: 0.3,
  lineWidth: 1.8,
  trailEnabled: true,
  trailSeconds: 5,
  density: "comfortable",
  highlightChanges: false,
}

function boundedNumber(value: unknown, fallback: number, minimum: number, maximum: number): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.min(maximum, Math.max(minimum, value))
    : fallback
}

export function normalizeAppearance(value: unknown): AppearancePreferences {
  const candidate = value && typeof value === "object" && !Array.isArray(value)
    ? value as Partial<AppearancePreferences>
    : {}
  return {
    canvas: CHART_CANVASES.some((item) => item.id === candidate.canvas) ? candidate.canvas! : DEFAULT_APPEARANCE.canvas,
    density: INTERFACE_DENSITIES.some((item) => item.id === candidate.density) ? candidate.density! : DEFAULT_APPEARANCE.density,
    gridOpacity: boundedNumber(candidate.gridOpacity, DEFAULT_APPEARANCE.gridOpacity, 0.1, 0.8),
    lineWidth: boundedNumber(candidate.lineWidth, DEFAULT_APPEARANCE.lineWidth, 1, 4),
    trailSeconds: boundedNumber(candidate.trailSeconds, DEFAULT_APPEARANCE.trailSeconds, 1, 15),
    trailEnabled: typeof candidate.trailEnabled === "boolean" ? candidate.trailEnabled : DEFAULT_APPEARANCE.trailEnabled,
    highlightChanges: typeof candidate.highlightChanges === "boolean" ? candidate.highlightChanges : DEFAULT_APPEARANCE.highlightChanges,
  }
}

export function readAppearance(): AppearancePreferences {
  try {
    return normalizeAppearance(JSON.parse(window.localStorage.getItem(APPEARANCE_STORAGE_KEY) ?? "null"))
  } catch {
    return { ...DEFAULT_APPEARANCE }
  }
}

// Anchor the tail to the latest finite sample, never wall time or array length.
// This also keeps the same visual window after pausing or switching views.
export function chartTrailBoundary(latestX: number | null, seconds: number, domain: [number, number]): number | null {
  if (latestX === null || !Number.isFinite(latestX) || !Number.isFinite(seconds) || seconds <= 0 || domain[1] <= domain[0]) return null
  return Math.max(domain[0], Math.min(domain[1], latestX - seconds))
}

export function changedDigitIndexes(previous: string, current: string): number[] {
  if (previous.length !== current.length || !/^\d/.test(previous)) return []
  return [...current].flatMap((character, index) => /\d/.test(character) && character !== previous[index] ? [index] : [])
}
