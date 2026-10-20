import { getCurrentWindow } from "@tauri-apps/api/window"
import { useCallback, useEffect, useRef, useState } from "react"
import { acquirePresentationFullscreen } from "@/lib/presentation-fullscreen"

function enterFullscreen() {
  if ("__TAURI_INTERNALS__" in window) {
    const appWindow = getCurrentWindow()
    return acquirePresentationFullscreen({
      isFullscreen: () => appWindow.isFullscreen(),
      enter: () => appWindow.setFullscreen(true),
      exit: () => appWindow.setFullscreen(false),
    })
  }
  // Request immediately while the button's user activation is still available.
  const wasFullscreen = Boolean(document.fullscreenElement)
  const request = wasFullscreen ? Promise.resolve() : document.documentElement.requestFullscreen()
  return request.then(() => async () => {
    if (!wasFullscreen && document.fullscreenElement) await document.exitFullscreen()
  })
}

export function usePowerCouplingMode(onError: (message: string) => void, needsAttention: boolean) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const wanted = useRef(false)
  const pending = useRef(false)
  const mounted = useRef(true)
  const release = useRef<(() => Promise<void>) | null>(null)
  const report = useRef(onError)
  const blocked = useRef(needsAttention)
  report.current = onError
  blocked.current = needsAttention

  const close = useCallback(() => {
    wanted.current = false
    setOpen(false)
    const restore = release.current
    if (!restore) return
    release.current = null
    pending.current = true
    setBusy(true)
    void restore().catch((error: unknown) => {
      report.current(error instanceof Error ? error.message : String(error))
    }).finally(() => {
      pending.current = false
      if (mounted.current) setBusy(false)
    })
  }, [])

  const enter = useCallback(() => {
    if (wanted.current || pending.current || blocked.current) return
    wanted.current = true
    pending.current = true
    setBusy(true)
    let entering: Promise<() => Promise<void>>
    try { entering = enterFullscreen() }
    catch (error) { entering = Promise.reject(error) }
    setOpen(true)
    void entering.then(async (restore) => {
      if (!wanted.current || !mounted.current) await restore()
      else release.current = restore
    }).catch((error: unknown) => {
      wanted.current = false
      if (mounted.current) {
        setOpen(false)
        report.current(error instanceof Error ? error.message : String(error))
      }
    }).finally(() => {
      pending.current = false
      if (mounted.current) setBusy(false)
    })
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!wanted.current || event.key !== "Escape") return
      event.preventDefault()
      event.stopImmediatePropagation()
      close()
    }
    const onFullscreenChange = () => {
      if (!("__TAURI_INTERNALS__" in window) && !document.fullscreenElement && wanted.current) close()
    }
    mounted.current = true
    window.addEventListener("keydown", onKey, true)
    document.addEventListener("fullscreenchange", onFullscreenChange)
    return () => {
      mounted.current = false
      wanted.current = false
      window.removeEventListener("keydown", onKey, true)
      document.removeEventListener("fullscreenchange", onFullscreenChange)
      const restore = release.current
      release.current = null
      if (restore) void restore().catch(() => undefined)
    }
  }, [close])

  useEffect(() => { if (needsAttention && wanted.current) close() }, [close, needsAttention])
  return { open, busy, enter, close }
}
