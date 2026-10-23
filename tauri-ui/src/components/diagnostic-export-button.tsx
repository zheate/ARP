import { Button } from "@/components/ui/button"
import { sendBackendRequest, type DiagnosticExportResult } from "@/lib/backend"
import { revealItemInDir } from "@tauri-apps/plugin-opener"
import { Archive, FolderOpen, LoaderCircle } from "lucide-react"
import { useEffect, useRef, useState } from "react"

const exportSession = (sessionId: string) => sendBackendRequest("automatic.exportDiagnostics", { sessionId })

export function DiagnosticExportButton({ sessionId, disabled = false, request = exportSession, reveal = revealItemInDir }: {
  sessionId?: string | null
  disabled?: boolean
  request?: (sessionId: string) => Promise<DiagnosticExportResult>
  reveal?: (path: string) => Promise<void>
}) {
  const lock = useRef(false)
  const mounted = useRef(true)
  const currentSession = useRef(sessionId)
  currentSession.current = sessionId
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<DiagnosticExportResult | null>(null)
  const [error, setError] = useState("")
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  useEffect(() => { setResult(null); setError("") }, [sessionId])

  async function exportOrReveal() {
    if (!sessionId || disabled || lock.current) return
    lock.current = true
    setBusy(true)
    setError("")
    const startedSession = sessionId
    try {
      const exported = result ?? await request(startedSession)
      if (!mounted.current || startedSession !== currentSession.current) return
      if (exported.sessionId !== startedSession) throw new Error("诊断包轮次不匹配，请重新导出")
      setResult(exported)
      try {
        await reveal(exported.path)
      } catch (reason) {
        if (mounted.current && startedSession === currentSession.current) setError(`ZIP 已生成，无法定位文件：${reason instanceof Error ? reason.message : String(reason)}。路径：${exported.path}`)
      }
    } catch (reason) {
      if (mounted.current && startedSession === currentSession.current) setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      lock.current = false
      if (mounted.current) setBusy(false)
    }
  }

  return <div className="flex min-w-0 flex-col gap-1" style={{ maxWidth: "18rem" }}>
    <Button className="w-44" size="sm" variant="outline" disabled={disabled || !sessionId || busy} aria-busy={busy} onClick={() => void exportOrReveal()} title={result?.path ?? "将本轮本底、过程日志和 Excel 打包为 ZIP，并打开所在文件夹"}>
      {busy ? <LoaderCircle className="size-4 animate-spin" /> : result ? <FolderOpen className="size-4" /> : <Archive className="size-4" />}
      {busy ? "正在打包…" : result ? "定位诊断包" : "一键导出诊断包"}
    </Button>
    <div className="min-h-4">
      {result && <p role="status" className="text-xs text-muted-foreground" title={result.path}>{result.warnings.length ? `ZIP 已生成；缺失项：${result.warnings.join("；")}` : "ZIP 已生成，可用于问题分析"}</p>}
      {error && <p role="alert" className="break-all text-xs text-destructive">{error}</p>}
    </div>
  </div>
}
