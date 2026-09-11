"use client"

import { AlertCircle, Check, Info, TriangleAlert, X, type LucideIcon } from "lucide-react"
import { AnimatePresence, motion, useReducedMotion } from "motion/react"
import type { ReactNode } from "react"

export type ToastStatus = "info" | "success" | "warning" | "error"

export type AnimatedToast = {
  id: string
  title: ReactNode
  description?: ReactNode
  status: ToastStatus
}

type AnimatedToastStackProps = {
  toasts: AnimatedToast[]
  disabled?: boolean
  onDismiss: (id: string) => void
}

const statusIcons: Record<ToastStatus, LucideIcon> = {
  info: Info,
  success: Check,
  warning: TriangleAlert,
  error: AlertCircle,
}

const statusClasses: Record<ToastStatus, { border: string; icon: string }> = {
  info: {
    border: "border-[color-mix(in_srgb,var(--app-validation)_40%,transparent)]",
    icon: "bg-[color-mix(in_srgb,var(--app-validation)_12%,transparent)] text-[var(--app-validation)]",
  },
  success: {
    border: "border-[color-mix(in_srgb,var(--app-validation)_40%,transparent)]",
    icon: "bg-[color-mix(in_srgb,var(--app-validation)_12%,transparent)] text-[var(--app-validation)]",
  },
  warning: {
    border: "border-[color-mix(in_srgb,var(--app-warning)_40%,transparent)]",
    icon: "bg-[color-mix(in_srgb,var(--app-warning)_12%,transparent)] text-[var(--app-warning)]",
  },
  error: {
    border: "border-[color-mix(in_srgb,var(--app-danger)_40%,transparent)]",
    icon: "bg-[color-mix(in_srgb,var(--app-danger)_12%,transparent)] text-[var(--app-danger)]",
  },
}

/**
 * ARP's controlled adaptation of beUI's Animated Toast Stack.  The backend
 * remains the source of notification truth; this component only presents and
 * dismisses its current notification list.
 */
export function AnimatedToastStack({ toasts, disabled = false, onDismiss }: AnimatedToastStackProps) {
  const reduce = useReducedMotion()

  return (
    <ol aria-atomic="false" aria-live="polite" className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(420px,calc(100vw-2rem))] flex-col gap-2">
      <AnimatePresence initial={false} mode="popLayout">
        {toasts.slice(-4).map((toast) => {
          const Icon = statusIcons[toast.status]
          const styles = statusClasses[toast.status]
          const canDismiss = !disabled
          return (
            <motion.li
              animate={reduce ? { opacity: 1 } : { opacity: 1, x: 0, scale: 1, filter: "blur(0px)" }}
              className="pointer-events-auto relative will-change-transform"
              drag={canDismiss && !reduce ? "x" : false}
              dragConstraints={{ left: 0, right: 0 }}
              dragElastic={0.18}
              exit={reduce ? { opacity: 0 } : { opacity: 0, x: 32, scale: 0.96, filter: "blur(8px)" }}
              initial={reduce ? { opacity: 0 } : { opacity: 0, x: 20, scale: 0.96, filter: "blur(10px)" }}
              key={toast.id}
              layout
              onDragEnd={(_, info) => {
                if (canDismiss && (Math.abs(info.offset.x) > 72 || Math.abs(info.velocity.x) > 520)) onDismiss(toast.id)
              }}
              transition={{ type: "spring", stiffness: 420, damping: 34, mass: 0.75 }}
            >
              <div className={`flex items-start gap-3 overflow-hidden rounded-2xl border bg-[color-mix(in_srgb,var(--app-surface-muted)_95%,transparent)] p-3 text-[var(--app-text)] shadow-2xl shadow-black/25 backdrop-blur-xl ${styles.border}`} role={toast.status === "error" ? "alert" : "status"}>
                <span className={`mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-full ${styles.icon}`}>
                  <Icon className="size-3.5" />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium leading-5">{toast.title}</p>
                  {toast.description && <p className="mt-0.5 line-clamp-2 text-xs leading-4 text-muted-foreground">{toast.description}</p>}
                </div>
                <button aria-label="关闭提示" className="inline-flex size-7 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-[var(--app-hover-soft)] hover:text-foreground disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent disabled:hover:text-muted-foreground" disabled={!canDismiss} onClick={() => onDismiss(toast.id)} type="button">
                  <X className="size-3.5" />
                </button>
              </div>
            </motion.li>
          )
        })}
      </AnimatePresence>
    </ol>
  )
}
