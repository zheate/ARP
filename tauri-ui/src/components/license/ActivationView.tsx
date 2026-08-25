import { useState } from "react"
import { Check, Copy, FileKey2, ShieldAlert, Upload } from "lucide-react"
import { open as openDialog } from "@tauri-apps/plugin-dialog"

import { Button } from "@/components/ui/button"
import { formatLicenseDate, installLicense, licenseReasonLabels, type LicenseStatus } from "@/lib/license"

export function ActivationView({
  status,
  runtimeAvailable,
  onStatusChange,
}: {
  status: LicenseStatus
  runtimeAvailable: boolean
  onStatusChange: (status: LicenseStatus) => void
}) {
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState("")

  const copyMachineCode = async () => {
    try {
      await navigator.clipboard.writeText(status.machineCode)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      setError("无法复制机器码，请手动选择后复制。")
    }
  }

  const selectAndInstall = async () => {
    setBusy(true)
    setError("")
    try {
      const selected = await openDialog({
        multiple: false,
        directory: false,
        title: "选择 ARP 许可证",
        filters: [{ name: "ARP License", extensions: ["json"] }],
      })
      if (typeof selected !== "string" || !selected) return
      const nextStatus = await installLicense(selected)
      onStatusChange(nextStatus)
      if (!nextStatus.valid) setError(licenseReasonLabels[nextStatus.reason])
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="license-activation-page">
      <section className="license-activation-panel" aria-labelledby="license-activation-title">
        <div className="license-activation-mark"><FileKey2 /></div>
        <div className="license-activation-heading">
          <span>软件授权</span>
          <h1 id="license-activation-title">Power Test 需要许可证</h1>
          <p>复制当前设备机器码并交给授权方，收到 license.json 后在这里导入。</p>
        </div>

        <div className="license-status-box" data-valid={status.valid ? "true" : "false"}>
          <ShieldAlert />
          <div>
            <strong>{runtimeAvailable ? licenseReasonLabels[status.reason] : "请在 Power Test 桌面应用中完成授权"}</strong>
            <span>{status.payload ? `${status.payload.customer} · 到期 ${formatLicenseDate(status.payload.expiresAt)}` : "当前设备尚无可用许可证"}</span>
          </div>
        </div>

        <div className="license-machine-box">
          <span>当前设备机器码</span>
          <code>{status.machineCode}</code>
          <Button disabled={!runtimeAvailable || !status.machineHash} onClick={() => void copyMachineCode()} size="sm" variant="outline">
            {copied ? <Check /> : <Copy />}{copied ? "已复制" : "复制机器码"}
          </Button>
        </div>

        {error && <p className="license-activation-error" role="alert">{error}</p>}

        <Button className="license-install-button" disabled={!runtimeAvailable || busy} onClick={() => void selectAndInstall()}>
          <Upload />{busy ? "正在验证..." : "导入许可证"}
        </Button>
        <p className="license-activation-footnote">许可证仅适用于当前设备。更换主板或设备后需要重新签发。</p>
      </section>
    </main>
  )
}
