import { Input } from "@/components/ui/input"
import { RECENT_DIRECTORIES_KEY, readRecentDirectories, recentDirectories } from "@/lib/recent-directories"
import { FolderOpen } from "lucide-react"
import { useEffect, useId, useLayoutEffect, useRef, useState, type ComponentProps } from "react"
import { createPortal } from "react-dom"
import "./output-directory-input.css"

type Props = Omit<ComponentProps<typeof Input>, "value" | "onChange"> & {
  value: string
  onValueChange: (value: string) => void
}

export function OutputDirectoryInput({ value, onValueChange, disabled, onBlur, onFocus, onKeyDown, ...props }: Props) {
  const input = useRef<HTMLInputElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const listId = useId()
  const [history, setHistory] = useState(readRecentDirectories)
  const [open, setOpen] = useState(false)
  const [filtering, setFiltering] = useState(false)
  const [active, setActive] = useState(-1)
  const [position, setPosition] = useState({ left: 0, top: 0, width: 0, maxHeight: 320 })
  const options = recentDirectories(history).filter((path) => !filtering || path.toLowerCase().includes(value.toLowerCase()))
  const expanded = open && !disabled && options.length > 0

  function remember(path: string) {
    const next = recentDirectories(readRecentDirectories(), path)
    setHistory(next)
    try { localStorage.setItem(RECENT_DIRECTORIES_KEY, JSON.stringify(next)) }
    catch { /* Directory entry remains usable when storage is unavailable. */ }
  }

  // External changes include the native folder picker; typing is committed on blur.
  useEffect(() => {
    if (document.activeElement !== input.current && value.trim()) remember(value)
  }, [value])

  useLayoutEffect(() => {
    if (!expanded || !input.current) return
    const rect = input.current.getBoundingClientRect()
    const width = Math.min(Math.max(448, rect.width), window.innerWidth - 24)
    setPosition({ left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)), top: rect.bottom + 6, width, maxHeight: Math.max(80, Math.min(320, window.innerHeight - rect.bottom - 18)) })
    const close = (event: Event) => {
      if (event.target instanceof Node && list.current?.contains(event.target)) return
      setOpen(false)
    }
    window.addEventListener("resize", close)
    window.addEventListener("scroll", close, true)
    return () => {
      window.removeEventListener("resize", close)
      window.removeEventListener("scroll", close, true)
    }
  }, [expanded])

  useEffect(() => {
    if (expanded && active >= 0) list.current?.querySelector(`#${CSS.escape(`${listId}-${active}`)}`)?.scrollIntoView({ block: "nearest" })
  }, [active, expanded, listId])

  function select(path: string) {
    onValueChange(path)
    remember(path)
    setOpen(false)
    setActive(-1)
  }

  return <>
    <Input
      {...props}
      ref={input}
      disabled={disabled}
      value={value}
      autoComplete="off"
      spellCheck={false}
      role="combobox"
      aria-autocomplete="list"
      aria-expanded={expanded}
      aria-controls={expanded ? listId : undefined}
      aria-activedescendant={expanded && active >= 0 ? `${listId}-${active}` : undefined}
      onFocus={(event) => { setHistory(recentDirectories(readRecentDirectories(), value)); setFiltering(false); setActive(-1); setOpen(true); onFocus?.(event) }}
      onClick={() => setOpen(true)}
      onBlur={(event) => { setOpen(false); if (value.trim()) remember(value); onBlur?.(event) }}
      onChange={(event) => { onValueChange(event.target.value); setFiltering(true); setActive(-1); setOpen(true) }}
      onKeyDown={(event) => {
        if (!event.nativeEvent.isComposing) {
          if ((event.key === "ArrowDown" || event.key === "ArrowUp") && options.length) {
            event.preventDefault()
            setOpen(true)
            setActive((index) => (index + (event.key === "ArrowDown" ? 1 : index < 0 ? 0 : -1) + options.length) % options.length)
          } else if (event.key === "Enter" && expanded && options[active]) {
            event.preventDefault()
            select(options[active])
          } else if (event.key === "Escape" && open) {
            event.preventDefault()
            event.stopPropagation()
            setOpen(false)
            setActive(-1)
          }
        }
        onKeyDown?.(event)
      }}
    />
    {expanded && createPortal(<div className="output-directory-menu" style={position}>
      <div className="output-directory-menu__heading">最近使用目录</div>
      <div aria-label="最近使用目录" className="output-directory-menu__list" id={listId} ref={list} role="listbox">
        {options.map((path, index) => <div
          aria-selected={active === index}
          className="output-directory-menu__option"
          id={`${listId}-${index}`}
          key={path}
          onPointerDown={(event) => event.preventDefault()}
          onClick={() => select(path)}
          onPointerMove={() => setActive(index)}
          role="option"
          title={path}
        ><FolderOpen aria-hidden="true" /><span>{path}</span></div>)}
      </div>
    </div>, document.body)}
  </>
}
