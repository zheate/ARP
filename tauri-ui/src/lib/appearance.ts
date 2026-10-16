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
export const LATEST_POINT_MARKER_OPTIONS = [
  { id: "point", label: "圆点" },
  { id: "paper-plane", label: "纸飞机" },
  { id: "paw", label: "爪印" },
  { id: "comet", label: "彗星" },
  { id: "ecg", label: "心电图" },
  { id: "flappy-bird", label: "Flappy Bird 小鸟" },
  { id: "bee", label: "小蜜蜂" },
  { id: "green-dinosaur", label: "小恐龙" },
  { id: "dinosaur", label: "小火龙" },
] as const
export type LatestPointMarkerStyle = typeof LATEST_POINT_MARKER_OPTIONS[number]["id"]
export type AnimatedLatestPointMarkerStyle = "flappy-bird" | "bee" | "dinosaur" | "green-dinosaur"

export function normalizeLatestPointMarkerStyle(value: unknown): LatestPointMarkerStyle {
  return LATEST_POINT_MARKER_OPTIONS.find((option) => option.id === value)?.id ?? "point"
}

export function isAnimatedLatestPointMarker(style: LatestPointMarkerStyle | undefined): style is AnimatedLatestPointMarkerStyle {
  return style === "flappy-bird" || style === "bee" || style === "dinosaur" || style === "green-dinosaur"
}

export function latestPointMarkerSize(style: LatestPointMarkerStyle | undefined): number {
  return style === "bee" || style === "dinosaur" || style === "green-dinosaur" ? 32 : 24
}
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
  highlightChanges: boolean // Retain the stored key for the numeric animation switch.
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
