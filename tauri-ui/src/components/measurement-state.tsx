import { Check, Minus, OctagonAlert } from "lucide-react"
import type { MeasurementVerdict } from "@/lib/measurement-verdict"

export function MeasurementState({ verdict, limit }: { verdict: MeasurementVerdict; limit: string }) {
  const Icon = verdict.tone === "success" ? Check : verdict.tone === "danger" ? OctagonAlert : Minus
  return <span className={`measurement-state is-${verdict.tone}`} title={`${verdict.label}；${limit}`}>
    <Icon aria-hidden="true" /><span>{verdict.label}</span>
  </span>
}
