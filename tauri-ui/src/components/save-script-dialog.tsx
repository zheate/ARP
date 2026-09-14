import { useEffect, useRef, useState } from "react"
import { DialogPortal } from "@/components/dialog-portal"
import { Plus, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

export function SaveScriptDialog({ onClose, onSave }: {
  onClose: () => void
  onSave: (name: string) => Promise<void>
}) {
  const [name, setName] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")
  const lock = useRef(false)
  const input = useRef<HTMLInputElement>(null)
  const dialog = useRef<HTMLFormElement>(null)
  const close = useRef(onClose)
  close.current = onClose

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const { overflow, paddingRight } = document.body.style
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth
    document.body.style.overflow = "hidden"
    if (scrollbarWidth > 0 && !getComputedStyle(document.documentElement).scrollbarGutter.includes("stable")) {
      document.body.style.paddingRight = `${scrollbarWidth}px`
    }
    input.current?.focus()
    const handleKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return
      if (event.key === "Escape" && !lock.current) {
        event.preventDefault()
        close.current()
      }
      if (event.key === "Tab") {
        const controls = Array.from(dialog.current?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled)") ?? [])
        const first = controls[0]
        const last = controls[controls.length - 1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
      }
    }
    document.addEventListener("keydown", handleKey)
    return () => {
      document.body.style.overflow = overflow
      document.body.style.paddingRight = paddingRight
      document.removeEventListener("keydown", handleKey)
      previousFocus?.focus()
    }
  }, [])

  const save = async () => {
    if (lock.current) return
    if (!name.trim()) { setError("请输入脚本名称"); input.current?.focus(); return }
    lock.current = true
    setSaving(true)
    setError("")
    try { await onSave(name.trim()); onClose() }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { lock.current = false; setSaving(false) }
  }

  return (
    <DialogPortal>
    <div className="dialog-overlay motion-overlay-enter" onMouseDown={(event) => { if (event.target === event.currentTarget && !lock.current) onClose() }}>
      <form aria-labelledby="save-script-title" aria-modal="true" className="device-settings-dialog motion-dialog-enter" ref={dialog} role="dialog" onSubmit={(event) => { event.preventDefault(); void save() }}>
        <div className="device-settings-dialog__header">
          <div className="device-settings-dialog__identity">
            <Plus className="size-5" />
            <div><h2 className="device-settings-dialog__title" id="save-script-title">另存为新脚本</h2><p className="device-settings-dialog__description">保存当前测试脚本参数，之后可从脚本列表直接加载</p></div>
          </div>
          <button aria-label="关闭新建脚本弹窗" className="device-settings-dialog__close" disabled={saving} onClick={onClose} type="button"><X className="size-[18px]" /></button>
        </div>
        <div className="device-settings-dialog__body space-y-2">
          <Label htmlFor="new-test-script-name">脚本名称</Label>
          <Input aria-describedby={error ? "new-test-script-error" : undefined} aria-invalid={Boolean(error)} disabled={saving} id="new-test-script-name" maxLength={60} onChange={(event) => { setName(event.target.value); setError("") }} placeholder="例如：10A 标准测试" ref={input} value={name} />
          {error && <p className="text-xs text-destructive" id="new-test-script-error" role="alert">{error}</p>}
        </div>
        <div className="device-settings-dialog__footer">
          <p className="device-settings-dialog__hint">保存后自动选中新脚本</p>
          <div className="device-settings-dialog__actions">
            <Button className="device-settings-dialog__cancel" disabled={saving} onClick={onClose} type="button" variant="outline">取消</Button>
            <Button aria-busy={saving} className="device-settings-dialog__save" disabled={saving} type="submit">{saving ? "保存中…" : "保存脚本"}</Button>
          </div>
        </div>
      </form>
    </div>
    </DialogPortal>
  )
}
