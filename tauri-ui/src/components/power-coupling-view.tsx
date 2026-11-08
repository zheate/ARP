import { DialogPortal } from "@/components/dialog-portal"
import { NumericReadout } from "@/components/numeric-readout"
import { PowerRealtimeChart } from "@/features/charts/realtime"
import type { BackendSnapshot } from "@/lib/backend"
import type { CurveLineStyle, LatestPointMarkerStyle } from "@/lib/appearance"
import type { CSSProperties } from "react"
import "./power-coupling-view.css"

export function couplingPowerText(snapshot: BackendSnapshot | null): string {
  const meter = snapshot?.devices.powerMeter
  return snapshot?.backend.connected && meter?.running && meter.quality === "live"
    && typeof meter.powerW === "number" && Number.isFinite(meter.powerW)
    ? meter.powerW.toFixed(3) : "--"
}

export function PowerCouplingView({ snapshot, curveLineStyle, latestPointMarkerStyle }: { snapshot: BackendSnapshot | null; curveLineStyle?: CurveLineStyle; latestPointMarkerStyle?: LatestPointMarkerStyle }) {
  const text = couplingPowerText(snapshot)
  return <DialogPortal>
    <section aria-label="功率耦合模式，按 Escape 退出" aria-modal="true" className="power-coupling" role="dialog" tabIndex={-1}>
      <div className="power-coupling__reading" style={{ "--power-font-width": `${155 / (text.length + 1)}vw` } as CSSProperties}>
        <output aria-label={`功率 ${text} W`} className="power-coupling__value"><NumericReadout value={text} /></output>
        <span className="power-coupling__unit">W</span>
      </div>
      <div className="power-coupling__chart">
        <PowerRealtimeChart snapshot={snapshot} curveLineStyle={curveLineStyle} latestPointMarkerStyle={latestPointMarkerStyle} smoothing displayScale={2} lineWidth={4} empty="" stretch />
      </div>
    </section>
  </DialogPortal>
}
