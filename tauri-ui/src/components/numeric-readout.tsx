import { memo, useLayoutEffect, useRef } from "react"
import { useAppearance } from "@/hooks/use-appearance"
import { changedDigitIndexes } from "@/lib/appearance"

export const NumericReadout = memo(function NumericReadout({ value }: { value: string }) {
  const { appearance } = useAppearance()
  const previous = useRef(value)
  const elements = useRef<Array<HTMLSpanElement | null>>([])

  useLayoutEffect(() => {
    const changed = changedDigitIndexes(previous.current, value)
    previous.current = value
    if (!appearance.highlightChanges || changed.length === 0) return

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    const animations: Animation[] = []
    for (const index of changed) {
      const element = elements.current[index]
      if (!element) continue
      const style = getComputedStyle(element)
      // Keep the readout's hue and continuous shape while making the changed digit easier to spot.
      const highlight = {
        color: `color-mix(in srgb, ${style.color} 45%, ${style.getPropertyValue("--foreground").trim()} 55%)`,
        textShadow: `0 0 0.16em color-mix(in srgb, ${style.color} 45%, transparent)`,
      }
      const keyframes = reducedMotion
        ? [highlight, highlight]
        : [
            { ...highlight, offset: 0 },
            { ...highlight, offset: 0.45, easing: "ease-out" },
            { color: style.color, textShadow: style.textShadow, offset: 1 },
          ]
      animations.push(element.animate(keyframes, { duration: 700 }))
    }
    return () => animations.forEach((animation) => animation.cancel())
  }, [appearance.highlightChanges, value])

  return <span className="numeric-readout" aria-label={value}>
    {[...value].map((character, index) => <span aria-hidden="true" className={/\d/.test(character) ? "numeric-readout__digit" : undefined} key={index} ref={(element) => { elements.current[index] = element }}>{character}</span>)}
  </span>
})
