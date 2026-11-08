import { memo, useImperativeHandle, useLayoutEffect, useRef, type Ref } from "react"
import { useMarkerPlayback } from "./use-marker-playback"
import "./character-sprite-marker.css"

type CharacterStyle = "bee" | "dinosaur" | "green-dinosaur"
export const DINOSAUR_FOOT_BASELINE = 305 / 310

export const CharacterSpriteMarker = memo(function CharacterSpriteMarker({ style, size, ref }: {
  style: CharacterStyle
  size: number
  ref?: Ref<HTMLDivElement>
}) {
  const markerRef = useRef<HTMLDivElement>(null)
  useImperativeHandle(ref, () => markerRef.current!, [])
  useMarkerPlayback(markerRef)
  useLayoutEffect(() => {
    const marker = markerRef.current
    if (!marker) return
    marker.dataset.vectorReady = "false"
    const images = [...marker.querySelectorAll("img")]
    const ready = () => {
      if (images.every(image => image.complete && image.naturalWidth > 0)) marker.dataset.vectorReady = "true"
    }
    images.forEach(image => image.addEventListener("load", ready))
    ready()
    return () => images.forEach(image => image.removeEventListener("load", ready))
  }, [style])
  const framesStyle = { width: size * 4, height: size, animationDuration: style === "bee" ? "0.4s" : "0.5s", animationPlayState: "paused" }

  return <div aria-hidden="true" className="chart-character-marker" data-marker-style={style} ref={markerRef}
    style={{ visibility: "hidden", width: size, height: size }}>
    <span className="chart-character-marker__fallback" style={style === "bee" ? undefined : { top: `${DINOSAUR_FOOT_BASELINE * 100}%` }} />
    <div className="chart-character-marker__body">
      <div className="chart-character-marker__frames" data-marker-animation style={framesStyle}>
        <img key={style} src={`/chart-markers/${style}-vector.svg`} width="100%" height="100%" alt="" draggable={false} />
      </div>
    </div>
    {style === "dinosaur" && <div className="chart-character-marker__flame" data-marker-animation
      style={{ transformOrigin: `${size * 35 / 310}px ${size * 169 / 310}px`, animationPlayState: "paused" }}>
      <div className="chart-character-marker__frames" data-marker-animation style={framesStyle}>
        <img src="/chart-markers/dinosaur-flame-vector.svg" width="100%" height="100%" alt="" draggable={false} />
      </div>
    </div>}
  </div>
})
