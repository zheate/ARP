import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { OutputDirectoryInput } from "@/components/output-directory-input"
import { Label } from "@/components/ui/label"
import type { UpdateConfig } from "@/features/configuration/model"
import type { RunCommand } from "@/features/shared/controls"
import type { AppConfiguration } from "@/lib/backend"
import { open as openDialog } from "@tauri-apps/plugin-dialog"
import { FolderOpen, LoaderCircle, Milestone, QrCode, Save } from "lucide-react"
import { memo, useRef } from "react"

export const ManualSampleControls = memo(function ManualSampleControls({ config, update, active, controlsAllowed, pending, pendingPointCount, run }: {
  config: AppConfiguration
  update: UpdateConfig
  active: boolean
  controlsAllowed: boolean
  pending: boolean
  pendingPointCount: number
  run: RunCommand
}) {
  const commandLock = useRef(false)
  const disabled = !active || !controlsAllowed || pending
  const saveReady = !disabled && pendingPointCount > 0 && Boolean(config.sn.trim() && config.station.trim() && config.outputDir.trim())
  const savePoint = async () => {
    if (!saveReady || commandLock.current) return
    commandLock.current = true
    try { await run("manual.saveExcel", {}, true) }
    finally { commandLock.current = false }
  }
  const chooseOutputDir = async () => {
    if (disabled) return
    try {
      const selected = await openDialog({ multiple: false, directory: true, title: "选择手动测试数据输出目录", defaultPath: config.outputDir.trim() || undefined })
      if (typeof selected === "string" && selected) update("outputDir", selected)
    } catch {
      // dialog cancelled or failed
    }
  }

  return (
    <section aria-label="手动测试样品与保存" className="manual-titlebar-controls">
      <div className="manual-titlebar-controls__field">
        <Label htmlFor="manual-sn"><QrCode className="size-3" />产品 SN</Label>
        <Input className="font-mono" disabled={disabled} id="manual-sn" onChange={(event) => update("sn", event.target.value)} placeholder="例如 HAM540001" title={config.sn} value={config.sn} />
      </div>
      <div className="manual-titlebar-controls__field">
        <Label htmlFor="manual-station"><Milestone className="size-3" />测试站别</Label>
        <Input disabled={disabled} id="manual-station" onChange={(event) => update("station", event.target.value)} placeholder="例如 老化" title={config.station} value={config.station} />
      </div>
      <div className="manual-titlebar-controls__field manual-titlebar-controls__output">
        <Label htmlFor="manual-output-directory"><FolderOpen className="size-3" />输出目录</Label>
        <div className="manual-titlebar-controls__directory">
          <OutputDirectoryInput disabled={disabled} id="manual-output-directory" onValueChange={(value) => update("outputDir", value)} placeholder="测试数据保存目录" title={config.outputDir} value={config.outputDir} />
          <Button aria-label="选择输出目录" disabled={disabled} onClick={() => void chooseOutputDir()} size="icon" title="选择输出目录" type="button" variant="outline"><FolderOpen className="size-4" /></Button>
        </div>
      </div>
      <Button className="manual-titlebar-controls__save" disabled={!saveReady} onClick={() => void savePoint()} type="button" variant="outline">
        {pending ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}保存
      </Button>
    </section>
  )
})
