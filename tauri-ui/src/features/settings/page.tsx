import { AppearanceSettings } from "@/components/appearance-controls"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { type AccentTheme, type CurveLineStyle, type LatestPointMarkerStyle, type Theme } from "@/lib/appearance"
import { formatLicenseDate, licenseReasonLabels, type LicenseStatus } from "@/lib/license"
import { KeyRound, ShieldCheck, Upload } from "lucide-react"
import { memo, useRef, useState } from "react"


// Measurement snapshots do not affect this page. Local state and appearance
// context still update normally, as do every prop and license-status change.
export const SystemSettingsPage = memo(function SystemSettingsPage({ theme, accentTheme, curveLineStyle, latestPointMarkerStyle, licenseStatus, onThemeChange, onAccentThemeChange, onCurveLineStyleChange, onLatestPointMarkerStyleChange, onImportLicense }: { theme: Theme; accentTheme: AccentTheme; curveLineStyle: CurveLineStyle; latestPointMarkerStyle: LatestPointMarkerStyle; licenseStatus: LicenseStatus; onThemeChange: (theme: Theme) => void; onAccentThemeChange: (theme: AccentTheme) => void; onCurveLineStyleChange: (style: CurveLineStyle) => void; onLatestPointMarkerStyleChange: (style: LatestPointMarkerStyle) => void; onImportLicense: () => Promise<void> }) {
  const licenseLock = useRef(false)
  const [licenseBusy, setLicenseBusy] = useState(false)
  const [licenseError, setLicenseError] = useState("")
  const importLicenseFile = async () => {
    if (licenseLock.current) return
    licenseLock.current = true
    setLicenseBusy(true)
    try {
      await onImportLicense()
      setLicenseError("")
    } catch (reason) {
      setLicenseError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      licenseLock.current = false
      setLicenseBusy(false)
    }
  }
  return (
    <section className="system-settings-page">
      <div className="system-settings-page__heading">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">系统设置 / 外观</h1>
          <p className="mt-1 text-sm text-muted-foreground">选择后立即生效，自动保存。</p>
        </div>
      </div>
      <AppearanceSettings theme={theme} accentTheme={accentTheme} curveLineStyle={curveLineStyle} latestPointMarkerStyle={latestPointMarkerStyle} onThemeChange={onThemeChange} onAccentThemeChange={onAccentThemeChange} onCurveLineStyleChange={onCurveLineStyleChange} onLatestPointMarkerStyleChange={onLatestPointMarkerStyleChange} />
      <Card className="system-settings-card mt-4 overflow-visible shadow-none">
        <CardHeader className="system-settings-card__header">
          <div className="system-settings-card__icon"><KeyRound className="size-5" /></div>
          <div>
            <CardTitle className="text-base">软件授权</CardTitle>
            <CardDescription>查看当前设备许可证及有效期。</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <div className="license-settings-summary">
            <div className="license-settings-status" data-valid={licenseStatus.valid ? "true" : "false"}><ShieldCheck /><span><strong>{licenseReasonLabels[licenseStatus.reason]}</strong><small>{licenseStatus.payload?.customer ?? "当前设备没有可用许可证"}</small></span></div>
            <dl>
              <div><dt>到期日期</dt><dd>{licenseStatus.payload ? formatLicenseDate(licenseStatus.payload.expiresAt) : "--"}</dd></div>
              <div><dt>授权功能</dt><dd>{licenseStatus.payload?.features.join("、") ?? "--"}</dd></div>
              <div><dt>机器码</dt><dd title={licenseStatus.machineCode}>{licenseStatus.machineCode}</dd></div>
            </dl>
            {licenseError && <p className="license-activation-error" role="alert">{licenseError}</p>}
            <Button className="w-40" aria-busy={licenseBusy} disabled={licenseBusy} onClick={() => void importLicenseFile()} size="sm" variant="outline"><Upload className="size-4" />{licenseBusy ? "正在验证..." : "重新导入许可证"}</Button>
          </div>
        </CardContent>
      </Card>
    </section>
  )
})
