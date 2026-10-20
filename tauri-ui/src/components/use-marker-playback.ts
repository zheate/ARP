import { useLayoutEffect, type RefObject } from "react"

/** Pause decorative CSS animation when its measured endpoint is not visible. */
export function useMarkerPlayback(markerRef: RefObject<HTMLDivElement | null>) {
  useLayoutEffect(() => {
    const marker = markerRef.current
    if (!marker) return
    let intersecting = false
    const update = () => {
      const state = intersecting && !document.hidden && marker.style.visibility !== "hidden" && marker.dataset.vectorReady !== "false" ? "running" : "paused"
      marker.querySelectorAll<HTMLElement>("[data-marker-animation]").forEach((element) => {
        element.style.animationPlayState = state
      })
    }
    const intersection = new IntersectionObserver(([entry]) => {
      intersecting = entry.isIntersecting
      update()
    })
    const visibility = new MutationObserver(update)
    intersection.observe(marker)
    visibility.observe(marker, { attributes: true, attributeFilter: ["style", "data-vector-ready"] })
    document.addEventListener("visibilitychange", update)
    update()
    return () => {
      intersection.disconnect()
      visibility.disconnect()
      document.removeEventListener("visibilitychange", update)
    }
  }, [markerRef])
}
