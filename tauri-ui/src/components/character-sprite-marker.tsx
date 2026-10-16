import { memo, useImperativeHandle, useLayoutEffect, useRef, type Ref } from "react"
import "./character-sprite-marker.css"

type CharacterStyle = "bee" | "dinosaur"
type FrameRect = readonly [x: number, y: number, width: number, height: number]
const FRAME_COUNT = 4
const SHEET_SIZE = 1254

// Keep the approved source images intact. Equal-size display windows align the
// eyes across frames; the bee's source cells also exclude neighbouring sprites.
const CHARACTERS: Record<CharacterStyle, {
  source: string
  fps: number
  frames: readonly FrameRect[]
  cells?: readonly FrameRect[]
}> = {
  bee: {
    source: "/chart-markers/bee-flap-four-frames-97043142efa1.png",
    fps: 10,
    frames: [[32, 72, 600, 600], [614, 80, 600, 600], [25, 671, 600, 600], [614, 672, 600, 600]],
    cells: [[0, 0, 627, 627], [627, 0, 627, 627], [0, 627, 627, 627], [627, 627, 627, 627]],
  },
  dinosaur: {
    source: "/chart-markers/dino-walk-four-frames-9f95d27db3c1.png",
    fps: 8,
    frames: [[8, 496, 310, 310], [320, 496, 310, 310], [636, 496, 310, 310], [947, 496, 310, 310]],
  },
}

const spriteCache = new Map<string, Promise<HTMLCanvasElement>>()

function characterSprite(style: CharacterStyle, size: number, ratio: number): Promise<HTMLCanvasElement> {
  const key = `${style}:${size}:${ratio}`
  const cached = spriteCache.get(key)
  if (cached) return cached
  const character = CHARACTERS[style]
  const sprite = new Promise<HTMLCanvasElement>((resolve, reject) => {
    const image = new Image()
    image.onload = () => {
      const canvas = document.createElement("canvas")
      const framePixels = Math.round(size * ratio)
      canvas.width = framePixels * FRAME_COUNT
      canvas.height = framePixels
      const context = canvas.getContext("2d")
      if (!context) { reject(new Error("Character sprite canvas is unavailable")); return }
      character.frames.forEach(([x, y, width, height], index) => {
        context.save()
        context.beginPath()
        context.rect(index * framePixels, 0, framePixels, framePixels)
        context.clip()
        context.translate(index * framePixels, 0)
        context.scale(framePixels / width, framePixels / height)
        context.translate(-x, -y)
        const cell = character.cells?.[index]
        if (cell) {
          context.beginPath()
          context.rect(...cell)
          context.clip()
        }
        context.drawImage(image, 0, 0, SHEET_SIZE, SHEET_SIZE)
        context.restore()
      })
      resolve(canvas)
    }
    image.onerror = () => reject(new Error(`Failed to load ${style} chart sprite`))
    image.src = character.source
  })
  spriteCache.set(key, sprite)
  void sprite.catch(() => spriteCache.delete(key))
  return sprite
}

export const CharacterSpriteMarker = memo(function CharacterSpriteMarker({ style, size, ref }: {
  style: CharacterStyle
  size: number
  ref?: Ref<HTMLDivElement>
}) {
  const markerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  useImperativeHandle(ref, () => markerRef.current!, [])

  useLayoutEffect(() => {
    const marker = markerRef.current
    const canvas = canvasRef.current
    if (!marker || !canvas) return
    let cancelled = false
    let loaded = false
    let intersecting = false
    delete marker.dataset.spriteReady
    const updatePlayback = () => {
      canvas.style.animationPlayState = loaded && intersecting && !document.hidden && marker.style.visibility !== "hidden" ? "running" : "paused"
    }
    updatePlayback()
    const intersection = new IntersectionObserver(([entry]) => {
      intersecting = entry.isIntersecting
      updatePlayback()
    })
    intersection.observe(marker)
    const visibility = new MutationObserver(updatePlayback)
    visibility.observe(marker, { attributes: true, attributeFilter: ["style"] })
    document.addEventListener("visibilitychange", updatePlayback)
    const ratio = Math.min(Math.max(window.devicePixelRatio || 1, 1), 2)
    void characterSprite(style, size, ratio).then((sprite) => {
      if (cancelled) return
      canvas.width = sprite.width
      canvas.height = sprite.height
      const context = canvas.getContext("2d")
      if (!context) return
      context.drawImage(sprite, 0, 0)
      marker.dataset.spriteReady = "true"
      loaded = true
      updatePlayback()
    }).catch(() => {
      // A missing asset falls back to a dot at the same measured endpoint.
      if (!cancelled) updatePlayback()
    })
    return () => {
      cancelled = true
      intersection.disconnect()
      visibility.disconnect()
      document.removeEventListener("visibilitychange", updatePlayback)
    }
  }, [style, size])

  return <div aria-hidden="true" className="chart-character-marker" data-marker-style={style} ref={markerRef}
    style={{ visibility: "hidden", width: size, height: size }}>
    <span className="chart-character-marker__fallback" />
    <canvas className="chart-character-marker__frames" ref={canvasRef}
      style={{ width: size * FRAME_COUNT, height: size, animationDuration: `${FRAME_COUNT / CHARACTERS[style].fps}s`, animationPlayState: "paused" }} />
  </div>
})
