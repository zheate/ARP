import { memo, useImperativeHandle, useLayoutEffect, useRef, type Ref } from "react"
import { useMarkerPlayback } from "./use-marker-playback"
import "./flappy-bird-marker.css"

export const FlappyBirdMarker = memo(function FlappyBirdMarker({ size, ref }: { size: number; ref?: Ref<HTMLDivElement> }) {
  const markerRef = useRef<HTMLDivElement>(null)
  const imageRef = useRef<HTMLImageElement>(null)
  useImperativeHandle(ref, () => markerRef.current!, [])
  useMarkerPlayback(markerRef)
  useLayoutEffect(() => {
    const marker = markerRef.current, image = imageRef.current
    if (!marker || !image) return
    marker.dataset.vectorReady = "false"
    const ready = () => {
      if (image.complete && image.naturalWidth > 0) marker.dataset.vectorReady = "true"
    }
    image.addEventListener("load", ready)
    ready()
    return () => image.removeEventListener("load", ready)
  }, [])

  return <div aria-hidden="true" className="chart-flappy-bird" ref={markerRef}
    style={{ visibility: "hidden", width: size, height: size * 2 / 3 }}>
    <span className="chart-flappy-bird__fallback" />
    <div className="chart-flappy-bird__frames" data-marker-animation style={{ width: size * 4, height: size * 2 / 3, animationPlayState: "paused" }}>
      <img ref={imageRef} src="/chart-markers/flappy-bird-vector.svg" width="100%" height="100%" alt="" draggable={false} />
    </div>
  </div>
})
