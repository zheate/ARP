import type { AutomaticTerminalOutcome, SafetyShutdownResult } from "@/lib/backend"

export function automaticTerminalTitle(outcome: AutomaticTerminalOutcome): string {
  if (outcome === "succeeded") return "测试完成"
  if (outcome === "stopped_shutdown_confirmed") return "测试已提前结束"
  if (outcome === "shutdown_unconfirmed") return "测试已停止，但下电状态未确认"
  return "测试异常中止"
}

export function shutdownPresentation(
  outcome: AutomaticTerminalOutcome | null | undefined,
  shutdown: SafetyShutdownResult | undefined,
): { confirmed: boolean; label: string } {
  const confirmed = shutdown?.confirmed === true && outcome !== "shutdown_unconfirmed"
  if (!confirmed) return { confirmed: false, label: "下电状态未确认" }
  if (typeof shutdown.measuredCurrentA === "number") {
    return {
      confirmed: true,
      label: `已确认安全下电 (${shutdown.measuredCurrentA.toFixed(2)} A)`,
    }
  }
  return { confirmed: true, label: "已确认安全下电" }
}
