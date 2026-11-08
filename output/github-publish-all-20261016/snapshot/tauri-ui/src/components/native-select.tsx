import { Children, Fragment, isValidElement, useEffect, useId, useLayoutEffect, useRef, useState, type FocusEventHandler, type ReactElement, type ReactNode } from "react"
import { ChevronDown } from "lucide-react"
import { useFieldLabel } from "@/components/field"

type SelectOption = ReactElement<{ value?: string | number; children?: ReactNode; disabled?: boolean }>

function selectOptions(children: ReactNode): SelectOption[] {
  return Children.toArray(children).flatMap((child) => {
    if (!isValidElement<{ children?: ReactNode }>(child)) return []
    if (child.type === Fragment) return selectOptions(child.props.children)
    return child.type === "option" ? [child as SelectOption] : []
  })
}

function optionText(children: ReactNode): string {
  return Children.toArray(children).map((child) => typeof child === "string" || typeof child === "number" ? String(child) : "").join("")
}

function optionValue(option: SelectOption): string {
  return String(option.props.value ?? optionText(option.props.children))
}

export function NativeSelect({ value, onChange, onFocus, children, disabled, ariaLabel, "aria-label": accessibleLabel }: {
  value: string | number
  onChange: (value: string) => void
  onFocus?: FocusEventHandler<HTMLButtonElement>
  children: ReactNode
  disabled?: boolean
  ariaLabel?: string
  "aria-label"?: string
}) {
  const labelId = useFieldLabel()
  const listId = useId()
  const [placement, setPlacement] = useState({ above: false, height: 224 })
  const options = selectOptions(children)
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const restoringFocusRef = useRef(false)
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([])
  const searchRef = useRef({ text: "", time: 0 })
  const selectedValue = String(value)
  const selectedOption = options.find((option) => optionValue(option) === selectedValue)
  const selectedIndex = options.findIndex((option) => optionValue(option) === selectedValue)
  const displayValue = selectedOption?.props.children ?? (value || "请选择")

  useEffect(() => {
    if (!open) return
    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener("pointerdown", handlePointerDown)
    return () => document.removeEventListener("pointerdown", handlePointerDown)
  }, [open])

  useEffect(() => {
    if (!open || options.length === 0) return
    const initialIndex = selectedIndex >= 0 && !options[selectedIndex]?.props.disabled
      ? selectedIndex
      : options.findIndex((option) => !option.props.disabled)
    const frame = window.requestAnimationFrame(() => focusOption(initialIndex))
    return () => window.cancelAnimationFrame(frame)
  }, [open, options.length, selectedIndex])

  useEffect(() => { if (disabled) setOpen(false) }, [disabled])

  useLayoutEffect(() => {
    if (!open) return
    const updatePlacement = () => {
      const trigger = triggerRef.current
      if (!trigger) return
      const box = trigger.getBoundingClientRect()
      let top = 8, bottom = window.innerHeight - 8
      // Intersect every clipping ancestor, not just the viewport. No portal and
      // no menu in normal flow: opening a list cannot move the dialog footer.
      for (let parent = trigger.parentElement; parent; parent = parent.parentElement) {
        if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(parent).overflowY)) {
          const rect = parent.getBoundingClientRect()
          top = Math.max(top, rect.top + parent.clientTop + 4)
          bottom = Math.min(bottom, rect.top + parent.clientTop + parent.clientHeight - 4)
        }
      }
      const below = Math.max(0, bottom - box.bottom - 4)
      const above = Math.max(0, box.top - top - 4)
      const openAbove = below < 160 && above > below
      setPlacement({ above: openAbove, height: Math.max(0, Math.min(224, openAbove ? above : below)) })
    }
    updatePlacement()
    window.addEventListener("resize", updatePlacement)
    document.addEventListener("scroll", updatePlacement, true)
    return () => {
      window.removeEventListener("resize", updatePlacement)
      document.removeEventListener("scroll", updatePlacement, true)
    }
  }, [open])

  const closeAndRestoreFocus = () => {
    setOpen(false)
    // Restore synchronously so a delayed callback cannot steal focus from the
    // next control. This is not a new field visit (e.g. activating a cursor).
    restoringFocusRef.current = true
    triggerRef.current?.focus({ preventScroll: true })
    restoringFocusRef.current = false
  }

  // Scroll only the menu; focusing a late option must not move the workbench.
  const focusOption = (index: number) => {
    const option = optionRefs.current[index]
    const list = option?.parentElement
    if (!option || !list) return
    option.focus({ preventScroll: true })
    if (option.offsetTop < list.scrollTop) list.scrollTop = option.offsetTop
    else if (option.offsetTop + option.offsetHeight > list.scrollTop + list.clientHeight) {
      list.scrollTop = option.offsetTop + option.offsetHeight - list.clientHeight
    }
  }

  const moveOptionFocus = (currentIndex: number, offset: number) => {
    for (let distance = 1; distance <= options.length; distance += 1) {
      const nextIndex = (currentIndex + offset * distance + options.length) % options.length
      if (!options[nextIndex]?.props.disabled) {
        focusOption(nextIndex)
        return
      }
    }
  }

  const focusBoundaryOption = (fromEnd: boolean) => {
    const indices = options.map((_option, index) => index)
    if (fromEnd) indices.reverse()
    const index = indices.find((candidate) => !options[candidate]?.props.disabled)
    if (index !== undefined) focusOption(index)
  }

  return (
    <div className="native-select relative w-full min-w-0" data-open={open} ref={rootRef} onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false)
    }}>
      <button
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label={accessibleLabel ?? ariaLabel}
        aria-labelledby={accessibleLabel || ariaLabel ? undefined : labelId}
        aria-controls={open ? listId : undefined}
        className="flex h-9 w-full items-center justify-between gap-2 rounded-lg border border-input bg-background px-3 text-left text-sm outline-none transition-[color,background-color,border-color,box-shadow] duration-150 hover:border-[var(--app-border-strong)] hover:bg-muted/25 focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/20 disabled:cursor-not-allowed disabled:border-border disabled:bg-muted/30 disabled:text-muted-foreground/55"
        disabled={disabled}
        onFocus={(event) => { if (!restoringFocusRef.current) onFocus?.(event) }}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && open) {
            event.stopPropagation()
            event.preventDefault()
            setOpen(false)
          } else if (event.key === "Enter" || event.key === " " || event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault()
            setOpen(true)
          }
        }}
        role="combobox"
        ref={triggerRef}
        type="button"
      >
        <span className="min-w-0 truncate" title={typeof displayValue === "string" ? displayValue : undefined}>{displayValue}</span>
        <ChevronDown className={`size-4 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && <div aria-label={accessibleLabel ?? ariaLabel} aria-labelledby={accessibleLabel || ariaLabel ? undefined : labelId} className="native-select__list absolute left-0 z-[80] w-full overflow-y-auto border border-[var(--plm-flat-border-strong)] bg-popover p-1" id={listId} style={{ maxHeight: placement.height, ...(placement.above ? { bottom: "calc(100% + 4px)" } : { top: "calc(100% + 4px)" }) }} role="listbox">
        {options.map((option, index) => {
          const nextValue = optionValue(option)
          const selected = nextValue === selectedValue
          const optionDisabled = Boolean(option.props.disabled)
          return <button
            aria-disabled={optionDisabled}
            aria-selected={selected}
            className={`relative flex w-full min-w-0 break-all items-center rounded-md px-3 py-2 text-left text-sm outline-none transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-45 ${optionDisabled ? "" : "hover:bg-[var(--plm-flat-hover)] hover:text-foreground focus-visible:bg-[var(--plm-flat-hover)] focus-visible:text-foreground"} ${selected ? "bg-[var(--app-selected-soft)] pl-4 text-foreground before:absolute before:inset-y-2 before:left-1.5 before:w-0.5 before:rounded-full before:bg-primary" : "text-muted-foreground"}`}
            disabled={optionDisabled}
            key={`${nextValue}-${index}`}
            onClick={() => {
              onChange(nextValue)
              closeAndRestoreFocus()
            }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault()
                moveOptionFocus(index, 1)
              } else if (event.key === "ArrowUp") {
                event.preventDefault()
                moveOptionFocus(index, -1)
              } else if (event.key === "Home") {
                event.preventDefault()
                focusBoundaryOption(false)
              } else if (event.key === "End") {
                event.preventDefault()
                focusBoundaryOption(true)
              } else if (event.key === "Escape") {
                event.stopPropagation()
                event.preventDefault()
                closeAndRestoreFocus()
              } else if (event.key.length === 1 && event.key !== " " && !event.ctrlKey && !event.metaKey && !event.altKey) {
                event.preventDefault()
                const now = performance.now()
                const previous = now - searchRef.current.time < 700 ? searchRef.current.text : ""
                const text = previous === event.key ? event.key : previous + event.key
                searchRef.current = { text, time: now }
                for (let distance = 1; distance <= options.length; distance += 1) {
                  const nextIndex = (index + distance) % options.length
                  const candidate = options[nextIndex]
                  if (!candidate.props.disabled && optionText(candidate.props.children).trim().toLocaleLowerCase().startsWith(text.toLocaleLowerCase())) {
                    focusOption(nextIndex)
                    break
                  }
                }
              }
            }}
            ref={(element) => { optionRefs.current[index] = element }}
            tabIndex={-1}
            role="option"
            type="button"
          >{option.props.children}</button>
        })}
      </div>}
    </div>
  )
}
