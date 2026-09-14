import { useRef, useState, type ComponentProps } from "react"
import { RefreshCw } from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import "./device-detect-button.css"

type DeviceDetectButtonProps = Omit<ComponentProps<typeof Button>, "children" | "onClick" | "asChild"> & {
  detecting?: boolean
  onClick: () => void | Promise<unknown>
}

export function DeviceDetectButton({ detecting = false, disabled, className, onClick, ...props }: DeviceDetectButtonProps) {
  const requestLock = useRef(false)
  const [requestPending, setRequestPending] = useState(false)
  const busy = detecting || requestPending

  const handleClick = async () => {
    if (disabled || busy || requestLock.current) return
    requestLock.current = true
    setRequestPending(true)
    try {
      // Cover configuration sync as well as the backend's discovery state.
      await onClick()
    } finally {
      requestLock.current = false
      setRequestPending(false)
    }
  }

  return (
    <Button
      {...props}
      aria-busy={busy}
      className={cn("device-detect-button", className)}
      disabled={disabled || busy}
      onClick={handleClick}
      type="button"
      variant="outline"
    >
      <span aria-hidden="true" className="device-detect-button__icon">
        <RefreshCw className="device-detect-button__refresh size-4" />
        <svg className="device-detect-button__spinner size-4" viewBox="0 0 24 24" fill="none">
          <circle className="device-detect-button__track" cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2.5" />
          <circle className="device-detect-button__arc" cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
        </svg>
      </span>
      <span className="device-detect-button__label">{busy ? "识别中" : "识别"}</span>
    </Button>
  )
}
