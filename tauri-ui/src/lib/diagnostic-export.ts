import type { BackendSnapshot } from "./backend"

export function diagnosticExportAvailable(auto: BackendSnapshot["automaticTest"]): boolean {
  return auto.state === "completed" && Boolean(auto.terminalOutcome && auto.sessionId && auto.journalPath)
    && !["recording", "exporting"].includes(auto.exportStatus ?? "")
    && auto.detail !== "正在写入最终测试状态"
}
