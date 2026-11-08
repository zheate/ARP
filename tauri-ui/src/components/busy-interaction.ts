import type { KeyboardEvent, SyntheticEvent } from "react"

// Temporary requests lock interaction without painting every control as
// unavailable. Keep permanent permission/device locks in native disabled props.
export function busyInteraction(locked: boolean | (() => boolean)) {
  const block = (event: SyntheticEvent) => {
    if (typeof locked === "function" ? locked() : locked) {
      event.preventDefault()
      event.stopPropagation()
    }
  }
  return {
    onClickCapture: block,
    onBeforeInputCapture: block,
    onChangeCapture: block,
    onPasteCapture: block,
    onSubmitCapture: block,
    onKeyDownCapture: (event: KeyboardEvent) => {
      if (event.key !== "Tab" && event.key !== "Escape") block(event)
    },
  }
}
