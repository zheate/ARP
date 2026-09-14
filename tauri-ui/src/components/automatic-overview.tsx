import { useEffect, useRef, useState, type CSSProperties } from "react"
import { Check, CheckCircle2, ChevronRight, CircleAlert, Clock3, FileText, FolderOpen, Info, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { automaticPhase, automaticStabilityPresentation, automaticStoragePresentation, formatTestDuration, powerStabilityPresentation } from "@/lib/automatic-presentation"
import { automaticTerminalTitle, shutdownPresentation } from "@/lib/safety-state"
import type { AppConfiguration, BackendSnapshot, DeviceSnapshot } from "@/lib/backend"

function useCurrentSetNotice(auto: BackendSnapshot["automaticTest"] | undefined) {
  const currentA = auto?.currentA ?? auto?.currents?.[auto.currentIndex ?? -1]
  const pointKey = typeof currentA === "number" && Number.isFinite(currentA)
    ? `${auto?.currentIndex ?? -1}:${currentA}` : null
  const setting = auto?.state === "setting_current"
  const active = ["setting_current", "waiting_stable", "waiting_voltage", "saving_point"].includes(auto?.state ?? "")
  const available = auto !== undefined
  const previous = useRef<{ pointKey: string | null; setting: boolean } | null>(null)
  const [notice, setNotice] = useState<{ pointKey: string; label: string } | null>(null)

  useEffect(() => {
    const last = previous.current
    previous.current = available ? { pointKey, setting } : null
    if (!active || pointKey === null || currentA === undefined || currentA === null) {
      setNotice(null)
      return
    }
    // A fast set command can finish between snapshots. A changed test point
    // still announces its target, while ordinary status refreshes leave it alone.
    if ((setting && !last?.setting) || (last && last.pointKey !== pointKey)) {
      setNotice({ pointKey, label: `set ${currentA.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 2, useGrouping: false })} A` })
    }
  }, [active, available, currentA, pointKey, setting])

  useEffect(() => {
    if (!notice) return
    const timeout = window.setTimeout(() => setNotice(null), 3000)
    return () => window.clearTimeout(timeout)
  }, [notice])

  return active && notice?.pointKey === pointKey ? notice?.label : null
}

export function AutomaticPhaseTrack({ snapshot }: { snapshot: BackendSnapshot | null }) {
  const auto = snapshot?.automaticTest
  const phase = automaticPhase(auto, snapshot?.safety.shutdown)
  const stability = automaticStabilityPresentation(auto, snapshot?.devices.powerMeter)
  const currentNotice = useCurrentSetNotice(auto)
  const [recordedRecently, setRecordedRecently] = useState(false)
  const savedCount = auto?.savedPointCount ?? snapshot?.measurements?.stable.length ?? 0
  const previousSaved = useRef(savedCount)
  useEffect(() => {
    const increased = savedCount > previousSaved.current
    previousSaved.current = savedCount
    if (!increased || auto?.state === "completed" || auto?.state === "idle") {
      setRecordedRecently(false)
      return
    }
    setRecordedRecently(true)
    const timeout = window.setTimeout(() => setRecordedRecently(false), 650)
    return () => window.clearTimeout(timeout)
  }, [auto?.state, savedCount])
  return <div
    className="automatic-phase-track"
    data-dwell={phase.dwell !== null}
    data-live={phase.live}
    data-tone={phase.tone}
    style={phase.dwell === null ? undefined : { "--phase-progress": phase.dwell } as CSSProperties}
    title={auto?.detail}
    aria-label={`测试阶段：${phase.labels[phase.activeIndex]}，${phase.terminal ? "已记录" : "当前第"} ${phase.point} / ${phase.total} 点`}
  >
    <ol>
      {phase.labels.map((label, index) => <li
        aria-current={index === phase.activeIndex ? "step" : undefined}
        data-done={phase.complete || (!phase.terminal && index < phase.activeIndex)}
        data-recent={index === 3 && recordedRecently}
        key={index}
      ><span className="automatic-phase-track__node" aria-hidden="true">{index === 4 && phase.complete ? <Check /> : null}</span><span>{label}</span></li>)}
    </ol>
    <span className="automatic-phase-track__count">
      <strong>{phase.terminal ? "已记录 " : phase.point > 0 ? "第 " : "待测 "}{phase.point} / {phase.total}{phase.terminal ? "" : " 点"}</strong>
      {phase.dwell !== null && <small>停留 {Math.floor(phase.dwell * (auto?.currentDwellS ?? 0))} / {auto?.currentDwellS} s</small>}
    </span>
    <span className="automatic-phase-track__compact">{phase.labels[phase.activeIndex]}</span>
    <div className="automatic-phase-track__stability" data-tone={currentNotice ? "setting" : stability.tone} title={currentNotice ? `加电设定：${currentNotice}` : stability.detail}>
      <span className="automatic-phase-track__stability-state"><i aria-hidden="true" />{currentNotice ?? stability.label}</span>
      <span className="automatic-phase-track__stability-time"><span>稳定后保持</span><strong>{stability.time}</strong></span>
    </div>
  </div>
}

export function PowerStabilityStatus({ meter, automatic }: { meter: DeviceSnapshot | undefined; automatic?: BackendSnapshot["automaticTest"] }) {
  const progress = automaticStabilityPresentation(automatic, meter)
  const status = automatic
    ? { ...progress, time: `稳定后保持 ${progress.time}` }
    : powerStabilityPresentation(meter)
  return <div className="power-stability-status" data-tone={status.tone} aria-label={`功率稳定状态：${status.label}，${status.time}`} title={automatic ? progress.detail : "稳定时间从本轮功率读数达到稳定后计算；读数中断、失稳或重新判稳后重新计时。"}>
    <span><i aria-hidden="true" /><span className="power-stability-status__label" title={status.label}>{status.label}</span></span>
    <span>{status.time}</span>
  </div>
}

export function AutomaticCompletionSummary({ snapshot, config, active, pending, onNextDevice, onViewData, onRetryExport, onChooseExportDirectory, onShowDetails }: {
  snapshot: BackendSnapshot
  config: AppConfiguration
  active: boolean
  pending: boolean
  onNextDevice: () => void
  onViewData: () => void
  onRetryExport: () => void
  onChooseExportDirectory: () => void
  onShowDetails: () => void
}) {
  const auto = snapshot.automaticTest
  if (auto.state !== "completed" || !auto.terminalOutcome) return null
  const run = auto.runContext ?? config
  const storage = automaticStoragePresentation(auto)
  const shutdown = shutdownPresentation(auto.terminalOutcome, snapshot.safety.shutdown)
  const title = automaticTerminalTitle(auto.terminalOutcome)
  const success = auto.terminalOutcome === "succeeded" && shutdown.confirmed
  const aging = run.selectedScriptId === "aging"
  const reason = (storage.exportFailed || !success)
    ? (auto.exportError || auto.terminalReason || auto.detail)
    : null

  return (
    <section className="automatic-completion-summary" data-tone={success ? "success" : "warning"} aria-label="测试结果摘要">
      <div className="automatic-completion-summary__main">
        <div className="automatic-completion-summary__status">
          <span className="automatic-completion-summary__status-icon">
            {success ? <CheckCircle2 className="size-5 shrink-0" /> : <CircleAlert className="size-5 shrink-0" />}
          </span>
          <div className="automatic-completion-summary__status-text min-w-0">
            <div className="automatic-completion-summary__title-row">
              <strong>{title}</strong>
              <button
                className="automatic-completion-summary__detail-link"
                onClick={onShowDetails}
                title="查看结束详情"
                type="button"
              >
                <Info className="size-3 shrink-0" />
                详情
              </button>
            </div>
            {reason && (
              <p className="automatic-completion-summary__reason" role="status" title={reason}>
                {reason}
              </p>
            )}
          </div>
        </div>

        <div className="automatic-completion-summary__sample">
          <small>当前样品</small>
          <strong title={run.sn}>{run.sn || "未填写 SN"}</strong>
          <small className="truncate">{run.testTemperatureC || "未填写温度"} · {run.station || "未填写站别"}</small>
        </div>

        <dl className="automatic-completion-summary__facts">
          <div>
            <dt><Clock3 />耗时</dt>
            <dd>{formatTestDuration(auto.elapsedS)}</dd>
          </div>
          <div>
            <dt>{aging ? "老化采样" : "测试点"}</dt>
            <dd>{aging ? `${snapshot.measurements?.agingPower.length ?? 0} 次` : `${auto.savedPointCount ?? 0} / ${auto.currents?.length ?? 0}`}</dd>
          </div>
          <div>
            <dt>数据</dt>
            <dd className={storage.saved ? "is-success" : "is-warning"}>{aging ? "见老化曲线" : storage.label}</dd>
            {storage.exportLabel && <small className={storage.exportFailed ? "is-danger" : ""}>{storage.exportLabel}</small>}
          </div>
          <div>
            <dt>安全下电</dt>
            <dd className={shutdown.confirmed ? "is-success" : "is-danger"} title={shutdown.label}>
              {shutdown.confirmed ? "已确认" : "未确认"}
            </dd>
          </div>
        </dl>

        <div className="automatic-completion-summary__actions">
          {auto.canRetryExport && (
            <>
              <Button disabled={!active || pending} onClick={onRetryExport} size="sm" variant="outline">
                <RefreshCw className="size-3.5" />重试 Excel
              </Button>
              <Button disabled={!active || pending} onClick={onChooseExportDirectory} size="sm" variant="outline">
                <FolderOpen className="size-3.5" />更换目录
              </Button>
            </>
          )}
          <Button onClick={onViewData} size="sm" variant="outline">
            <FileText className="size-4" />查看数据
          </Button>
          <Button disabled={!active || pending || !auto.canNextDevice || !shutdown.confirmed} onClick={onNextDevice} size="sm">
            下一颗<ChevronRight className="size-4" />
          </Button>
        </div>
      </div>
    </section>
  )
}
