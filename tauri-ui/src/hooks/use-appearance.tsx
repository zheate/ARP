import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react"
import { APPEARANCE_STORAGE_KEY, DEFAULT_APPEARANCE, normalizeAppearance, readAppearance, type AppearancePreferences } from "@/lib/appearance"

const AppearanceContext = createContext<{
  appearance: AppearancePreferences
  updateAppearance: (patch: Partial<AppearancePreferences>) => void
}>({ appearance: { ...DEFAULT_APPEARANCE }, updateAppearance: () => undefined })

export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [appearance, setAppearance] = useState(readAppearance)
  const updateAppearance = useCallback((patch: Partial<AppearancePreferences>) => {
    setAppearance((current) => normalizeAppearance({ ...current, ...patch }))
  }, [])

  useEffect(() => {
    document.documentElement.dataset.density = appearance.density
    try {
      window.localStorage.setItem(APPEARANCE_STORAGE_KEY, JSON.stringify(appearance))
    } catch {
      // Keep the selected appearance for this session when storage is blocked.
    }
  }, [appearance])

  const value = useMemo(() => ({ appearance, updateAppearance }), [appearance, updateAppearance])
  return <AppearanceContext.Provider value={value}>{children}</AppearanceContext.Provider>
}

export function useAppearance() {
  return useContext(AppearanceContext)
}
