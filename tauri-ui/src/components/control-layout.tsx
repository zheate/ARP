import type { ComponentProps } from "react"
import { cn } from "@/lib/utils"

/** Keep a selector/input and its action on the same row without shrinking the action. */
export function ControlRow({ className, actionWidth = "content", ...props }: ComponentProps<"div"> & {
  actionWidth?: "content" | "path"
}) {
  return <div {...props} className={cn("control-row", className)} data-action-width={actionWidth} />
}

/** Fields and actions share column gaps; responsive column counts remain page-owned. */
export function ControlGrid({ className, ...props }: ComponentProps<"div">) {
  return <div {...props} className={cn("control-grid", className)} />
}

export function ParameterControl({ className, ...props }: ComponentProps<"div">) {
  return <div {...props} className={cn("parameter-control", className)} />
}
