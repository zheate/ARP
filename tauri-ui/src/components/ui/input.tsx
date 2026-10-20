import * as React from "react"

import { useFieldLabel } from "@/components/field"

import { cn } from "@/lib/utils"

function Input({ className, type, onWheel, onFocus, ...props }: React.ComponentProps<"input">) {
  const fieldLabel = useFieldLabel()
  return (
    <input
      autoComplete="off"
      aria-labelledby={props["aria-label"] ? undefined : fieldLabel}
      type={type}
      data-slot="input"
      onWheel={(event) => {
        if (type === "number") event.currentTarget.blur()
        onWheel?.(event)
      }}
      onFocus={(event) => {
        if (type === "number") event.currentTarget.select()
        onFocus?.(event)
      }}
      className={cn(
        "h-8 w-full min-w-0 rounded-lg border border-input bg-background px-2.5 py-1 text-base transition-[background-color,border-color,color] duration-150 outline-none file:inline-flex file:h-6 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground hover:border-[var(--app-border-strong)] hover:bg-muted/25 focus:border-ring disabled:pointer-events-none disabled:cursor-not-allowed disabled:border-border disabled:bg-muted/30 disabled:text-muted-foreground/55 aria-invalid:border-destructive md:text-sm dark:aria-invalid:border-destructive/50",
        className
      )}
      {...props}
    />
  )
}

export { Input }
