import { useLayoutEffect, useRef, type ReactNode } from "react"
import { createPortal } from "react-dom"

const layers: HTMLElement[] = []
const originalInert = new Map<HTMLElement, boolean>()
const focusableSelector = 'button, input, select, textarea, a[href], summary, [tabindex], [contenteditable="true"]'

function isolateTopLayer() {
  for (const [element, inert] of originalInert) element.inert = inert
  originalInert.clear()
  const top = layers[layers.length - 1]
  if (!top) return
  const background = document.getElementById("root")
  for (const element of [background, ...layers]) {
    if (!element || element === top) continue
    originalInert.set(element, element.inert)
    element.inert = true
  }
}

export function isModalOpen() {
  return layers.length > 0
}

/** Owns modality only. Callers retain their existing initial focus, Escape policy,
 *  acknowledgement, save guards and hardware commands. Local dropdowns stay inside. */
export function DialogPortal({ children }: { children: ReactNode }) {
  const root = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const layer = root.current
    if (!layer) return
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const tabbable = () => Array.from(layer.querySelectorAll<HTMLElement>(focusableSelector)).filter(element => (
      element.tabIndex >= 0 && !element.matches(":disabled") && !element.closest("[inert]")
      && element.getClientRects().length > 0 && getComputedStyle(element).visibility !== "hidden"
    ))
    let lastFocus: HTMLElement | null = null
    const focusInside = () => {
      const dialog = layer.querySelector<HTMLElement>('[role="dialog"], [role="alertdialog"]')
      const target = lastFocus?.isConnected && !lastFocus.matches(":disabled") ? lastFocus : tabbable()[0] ?? dialog
      if (target) {
        if (target === dialog && !target.hasAttribute("tabindex")) target.tabIndex = -1
        target.focus({ preventScroll: true })
      }
    }
    const onFocus = (event: FocusEvent) => {
      if (layers[layers.length - 1] !== layer) return
      if (event.target instanceof HTMLElement && layer.contains(event.target)) lastFocus = event.target
      else focusInside()
    }
    const onKey = (event: KeyboardEvent) => {
      if (layers[layers.length - 1] !== layer || event.key !== "Tab" || event.defaultPrevented) return
      const controls = tabbable()
      const first = controls[0], last = controls[controls.length - 1]
      const active = document.activeElement
      if (!first || !layer.contains(active) || event.shiftKey && active === first || !event.shiftKey && active === last) {
        event.preventDefault()
        if (first) (event.shiftKey ? last : first)?.focus()
        else focusInside()
      }
    }
    layers.push(layer)
    isolateTopLayer()
    document.addEventListener("focusin", onFocus)
    document.addEventListener("keydown", onKey, true)
    // Existing callers choose close/acknowledge/retry, never an energize action.
    const frame = requestAnimationFrame(() => {
      if (layers[layers.length - 1] === layer && !layer.contains(document.activeElement)) focusInside()
    })
    return () => {
      cancelAnimationFrame(frame)
      document.removeEventListener("focusin", onFocus)
      document.removeEventListener("keydown", onKey, true)
      const index = layers.indexOf(layer)
      if (index >= 0) layers.splice(index, 1)
      isolateTopLayer()
      requestAnimationFrame(() => {
        if (previousFocus?.isConnected && !previousFocus.closest("[inert]")) previousFocus.focus({ preventScroll: true })
      })
    }
  }, [])
  return createPortal(<div data-arp-modal-root="" ref={root}>{children}</div>, document.body)
}
