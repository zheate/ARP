export type MeasurementVerdict = { label: string; tone: "success" | "danger" | "muted" }

/** Presentation only: no rounding, normalization, filtering or acquisition decisions. */
export function measurementVerdict(value: number | null | undefined, quality: string | undefined, passes: boolean): MeasurementVerdict {
  if (quality === "stale") return { label: "数据过期", tone: "muted" }
  if (quality === "invalid" || typeof value === "number" && !Number.isFinite(value)) return { label: "数据无效", tone: "muted" }
  if (quality !== "live" || value == null) return { label: "无实时数据", tone: "muted" }
  return passes ? { label: "达标", tone: "success" } : { label: "超限", tone: "danger" }
}
