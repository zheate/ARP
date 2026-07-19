import { useCallback, useEffect, useState } from "react"

import { fetchBackendSnapshot, sendBackendCommand, type BackendSnapshot } from "@/lib/backend"

const REFRESH_INTERVAL_MS = 1000

export function useBackendSnapshot() {
  const [snapshot, setSnapshot] = useState<BackendSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [commandPending, setCommandPending] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const nextSnapshot = await fetchBackendSnapshot()
      setSnapshot(nextSnapshot)
      setError(null)
    } catch (reason) {
      setSnapshot(null)
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setLoading(false)
    }
  }, [])

  const command = useCallback(async (method: string, params: Record<string, unknown> = {}) => {
    setCommandPending(true)
    try {
      const nextSnapshot = await sendBackendCommand(method, params)
      setSnapshot(nextSnapshot)
      setError(null)
      return nextSnapshot
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason)
      setError(message)
      throw reason
    } finally {
      setCommandPending(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
    const interval = window.setInterval(() => void refresh(), REFRESH_INTERVAL_MS)
    return () => window.clearInterval(interval)
  }, [refresh])

  return { snapshot, error, loading, commandPending, refresh, command }
}
