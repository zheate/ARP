import { memo, useLayoutEffect, useRef } from "react"
import { useAppearance } from "@/hooks/use-appearance"
import { changedDigitIndexes } from "@/lib/appearance"

export const NumericReadout = memo(function NumericReadout({ value }: { value: string }) {
  const { appearance } = useAppearance()
  const previous = useRef(value)
  const elements = useRef<Array<HTMLSpanElement | null>>([])

  useLayoutEffect(() => {
    if (appearance.highlightChanges && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      for (const index of changedDigitIndexes(previous.current, value)) {
        const element = elements.current[index]
        if (!element) continue
        element.getAnimations().forEach((animation) => animation.cancel())
        const accent = getComputedStyle(element).getPropertyValue("--app-accent-strong").trim()
        element.animate([{ color: accent, opacity: 1 }, { color: "inherit", opacity: 1 }], { duration: 180, easing: "ease-out" })
      }
    }
    previous.current = value
  }, [appearance.highlightChanges, value])

  return <span className="numeric-readout" aria-label={value}>
    {[...value].map((character, index) => <span aria-hidden="true" className={/\d/.test(character) ? "numeric-readout__digit" : undefined} key={index} ref={(element) => { elements.current[index] = element }}>{character}</span>)}
  </span>
})
