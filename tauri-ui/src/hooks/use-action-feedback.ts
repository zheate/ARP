import { useRef, useState } from "react"

export function useActionFeedback(blocked = false) {
  const lock = useRef(false)
  const [action, setAction] = useState<string | null>(null)
  const [error, setError] = useState("")
  const runAction = async (name: string, operation: () => unknown | Promise<unknown>) => {
    if (blocked || lock.current) return
    lock.current = true
    setAction(name)
    try {
      await operation()
      setError("")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      lock.current = false
      setAction(null)
    }
  }
  return { action, error, runAction }
}
