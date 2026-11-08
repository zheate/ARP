import { createContext, useContext, useId, useRef, type ReactNode } from "react"
import { Label } from "@/components/ui/label"

const FieldLabel = createContext<string | undefined>(undefined)
export const useFieldLabel = () => useContext(FieldLabel)

/** Labels composite controls (including NumberInput and resource selectors) without
 * assigning the same input ID to every descendant in a compound field. */
export function Field({ label, children, className = "", layout = "stacked" }: {
  label: string; children: ReactNode; className?: string; layout?: "inline" | "stacked"
}) {
  const id = useId()
  const root = useRef<HTMLDivElement>(null)
  const inline = layout === "inline"
  return <FieldLabel.Provider value={id}>
    <div className={`${inline ? "manual-inline-field" : "space-y-1.5"} ${className}`} ref={root}>
      <Label className={inline ? "manual-inline-field-label" : undefined} id={id} onClick={() => {
        root.current?.querySelector<HTMLElement>('input:not(:disabled), button[role="combobox"]:not(:disabled), select:not(:disabled), textarea:not(:disabled)')?.focus()
      }}>{label}{inline ? "：" : ""}</Label>
      {inline ? <div className="manual-inline-field-control">{children}</div> : children}
    </div>
  </FieldLabel.Provider>
}
