export const RECENT_DIRECTORIES_KEY = "power-test.recent-output-directories.v1"

export function recentDirectories(stored: unknown, current = ""): string[] {
  const candidates = [current, ...(Array.isArray(stored) ? stored : [])]
  const seen = new Set<string>()
  return candidates.filter((path): path is string => {
    if (typeof path !== "string" || !path.trim()) return false
    const identity = path.trim().replace(/\//g, "\\").replace(/\\+$/, "").toLowerCase()
    if (seen.has(identity)) return false
    seen.add(identity)
    return true
  }).slice(0, 6)
}

export function readRecentDirectories(): string[] {
  try { return recentDirectories(JSON.parse(localStorage.getItem(RECENT_DIRECTORIES_KEY) ?? "null")) }
  catch { return [] }
}
