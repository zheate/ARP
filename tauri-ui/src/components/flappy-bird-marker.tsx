import { memo, useImperativeHandle, useLayoutEffect, useRef, type Ref } from "react"
import "./flappy-bird-marker.css"

const WING_OFFSETS = [0, -3, 0, 3] as const
const FRAME_COUNT = WING_OFFSETS.length
let cachedSprite: { size: number; ratio: number; canvas: HTMLCanvasElement } | null = null

function drawBird(context: CanvasRenderingContext2D, x: number, size: number, wingOffset: number): void {
  context.save()
  context.translate(x, size / 3)
  context.scale(size / 24, size / 24)
  context.translate(-12, -8)

  context.fillStyle = "#543b2b"
  context.fillRect(7, 0, 9, 1)
  context.fillRect(5, 1, 13, 1)
  context.fillRect(3, 2, 16, 2)
  context.fillRect(2, 4, 18, 2)
  context.fillRect(1, 6, 19, 6)
  context.fillRect(3, 12, 17, 2)
  context.fillRect(5, 14, 13, 1)
  context.fillRect(7, 15, 9, 1)

  context.fillStyle = "#f9d43b"
  context.fillRect(7, 1, 9, 1)
  context.fillRect(5, 2, 13, 1)
  context.fillRect(4, 3, 15, 3)
  context.fillRect(3, 6, 16, 6)
  context.fillRect(4, 12, 13, 2)
  context.fillRect(7, 14, 9, 1)
  context.fillStyle = "#edaa2e"
  context.fillRect(3, 10, 13, 2)
  context.fillRect(4, 12, 13, 2)
  context.fillRect(7, 14, 9, 1)

  context.fillStyle = "#543b2b"
  context.fillRect(12, 2, 5, 1)
  context.fillRect(11, 3, 8, 2)
  context.fillRect(11, 5, 9, 4)
  context.fillRect(13, 9, 6, 1)
  context.fillStyle = "#fffdf2"
  context.fillRect(12, 3, 5, 1)
  context.fillRect(12, 4, 6, 4)
  context.fillRect(14, 8, 5, 1)
  context.fillStyle = "#29221d"
  context.fillRect(16, 4, 2, 4)

  context.save()
  context.translate(0, wingOffset)
  context.fillStyle = "#543b2b"
  context.fillRect(2, 6, 5, 1)
  context.fillRect(1, 7, 7, 4)
  context.fillRect(2, 11, 5, 1)
  context.fillStyle = "#fff2b3"
  context.fillRect(2, 7, 4, 1)
  context.fillRect(2, 8, 5, 2)
  context.fillStyle = "#e7bc54"
  context.fillRect(2, 10, 5, 1)
  context.restore()

  context.fillStyle = "#543b2b"
  context.fillRect(17, 8, 6, 1)
  context.fillRect(16, 9, 8, 4)
  context.fillRect(18, 13, 5, 1)
  context.fillStyle = "#ff9b3d"
  context.fillRect(17, 9, 6, 1)
  context.fillRect(17, 10, 6, 1)
  context.fillStyle = "#f16c34"
  context.fillRect(18, 12, 5, 1)
  context.restore()
}

function birdSprite(size: number, ratio: number): HTMLCanvasElement {
  if (cachedSprite?.size === size && cachedSprite.ratio === ratio) return cachedSprite.canvas
  const canvas = document.createElement("canvas")
  canvas.width = Math.round(size * FRAME_COUNT * ratio)
  canvas.height = Math.round(size * 2 / 3 * ratio)
  const context = canvas.getContext("2d")
  if (context) {
    context.scale(ratio, ratio)
    WING_OFFSETS.forEach((wingOffset, index) => drawBird(context, size * (index + 0.5), size, wingOffset))
  }
  // Keep only one small strip, including across view switches. Samples and
  // animation frames never regenerate it or redraw the measurement canvas.
  cachedSprite = { size, ratio, canvas }
  return canvas
}

export const FlappyBirdMarker = memo(function FlappyBirdMarker({ size, ref }: { size: number; ref?: Ref<HTMLDivElement> }) {
  const markerRef = useRef<HTMLDivElement>(null)
  const spriteRef = useRef<HTMLCanvasElement>(null)
  useImperativeHandle(ref, () => markerRef.current!, [])

  useLayoutEffect(() => {
    const marker = markerRef.current
    const canvas = spriteRef.current
    if (!marker || !canvas) return
    const ratio = Math.min(Math.max(window.devicePixelRatio || 1, 1), 2)
    const sprite = birdSprite(size, ratio)
    canvas.width = sprite.width
    canvas.height = sprite.height
    canvas.getContext("2d")?.drawImage(sprite, 0, 0)

    let visible = true
    const updatePlayback = () => {
      canvas.style.animationPlayState = visible && !document.hidden ? "running" : "paused"
    }
    const observer = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting
      updatePlayback()
    })
    observer.observe(marker)
    document.addEventListener("visibilitychange", updatePlayback)
    updatePlayback()
    return () => {
      observer.disconnect()
      document.removeEventListener("visibilitychange", updatePlayback)
    }
  }, [size])

  return <div
    aria-hidden="true"
    className="chart-flappy-bird"
    ref={markerRef}
    // The chart draw callback owns position and visibility, including first draw.
    style={{ visibility: "hidden", width: size, height: size * 2 / 3 }}
  >
    <canvas className="chart-flappy-bird__frames" ref={spriteRef} style={{ width: size * FRAME_COUNT, height: size * 2 / 3 }} />
  </div>
})
