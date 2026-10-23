// Production completion controls with a local, simulated bridge; no file/device IO.
import { useState } from "react"
import { createRoot } from "react-dom/client"
import { AutomaticCompletionSummary } from "../../src/components/automatic-overview"
import { AutomaticCompletionDialog } from "../../src/features/automatic/lifecycle-dialogs"
import { diagnosticExportAvailable } from "../../src/lib/diagnostic-export"
import { DEMO_SNAPSHOT } from "../../src/lib/demo-snapshot"
import { emptyConfig } from "../../src/features/configuration/model"
import type { BackendSnapshot } from "../../src/lib/backend"
import "../../src/index.css"
import "../../src/workbench-appearance.css"

const mode = new URLSearchParams(location.search).get("mode") ?? "success"
const calls: { command: string; args: Record<string, unknown> }[] = []
Object.assign(window, { diagnosticFixtureCalls: calls, __TAURI_INTERNALS__: {
  invoke: async (command: string, args: Record<string, unknown>) => {
    calls.push({ command, args })
    if (command === "bridge_request" && args.method === "automatic.exportDiagnostics") {
      await new Promise(resolve => setTimeout(resolve, 400))
      if (mode === "write-failed") throw new Error("磁盘空间不足（验证示例）")
      const params = args.params as { sessionId: string }
      return { path: `C:\\ARP\\diagnostics\\${params.sessionId}.zip`, sessionId: params.sessionId,
        warnings: mode === "partial" ? ["本轮 Excel 不可用：文件被占用", "本轮本底原始样本缺失；已保留冻结模型，未混入其他学习结果"] : [] }
    }
    if (command === "plugin:opener|reveal_item_in_dir") {
      if (mode === "reveal-failed") throw new Error("文件夹无法打开（验证示例）")
      return
    }
    throw new Error(`Fixture unexpected command: ${command}`)
  },
} })

function Fixture() {
  const [dialog, setDialog] = useState(false)
  const [round, setRound] = useState(1)
  const [showResult, setShowResult] = useState(true)
  const config = { ...emptyConfig, ...DEMO_SNAPSHOT.configuration!, sn: `样品-${round}` }
  const snapshot: BackendSnapshot = { ...DEMO_SNAPSHOT,
    backend: { ...DEMO_SNAPSHOT.backend, mode: "active", connected: true },
    safety: { ...DEMO_SNAPSHOT.safety, shutdown: { ...DEMO_SNAPSHOT.safety.shutdown, confirmed: true } },
    automaticTest: { ...DEMO_SNAPSHOT.automaticTest, state: "completed", detail: "本轮已结束", terminalOutcome: "succeeded",
      sessionId: `round-${round}`, journalPath: `C:\\ARP\\round-${round}.jsonl`,
      exportStatus: mode === "exporting" ? "exporting" : mode === "partial" ? "failed" : "completed",
      exportPath: `C:\\ARP\\round-${round}.xlsx`, runContext: config, savedPointCount: 3, currents: [1, 2, 3], canNextDevice: true },
  }
  const auto = snapshot.automaticTest
  const noAction = async () => {}
  return <main style={{ padding: 24, minHeight: "110vh" }}>
    <p>隔离界面验证：模拟桥接，不读写文件或操作设备。</p>
    <button onClick={() => setRound(value => value + 1)}>下一轮示例</button>
    <button onClick={() => setShowResult(false)}>隐藏结果示例</button>
    {showResult && <AutomaticCompletionSummary snapshot={snapshot} config={config} active pending={false} onNextDevice={noAction}
      onViewData={() => {}} onRetryExport={noAction} onChooseExportDirectory={noAction} onShowDetails={() => setDialog(true)} />}
    {dialog && <AutomaticCompletionDialog title="测试完成" message="本轮已结束" onClose={() => setDialog(false)}
      sn={config.sn} outcome="succeeded" shutdown={snapshot.safety.shutdown} totalPoints={3} completedPoints={3}
      exportStatus={auto.exportStatus} journalPath={auto.journalPath} sessionId={auto.sessionId}
      canExportDiagnostics={diagnosticExportAvailable(auto)} canNextDevice canRetryExport={false}
      onNextDevice={noAction} onRetryExport={noAction} onChooseExportDirectory={noAction} />}
  </main>
}
createRoot(document.getElementById("root")!).render(<Fixture />)
