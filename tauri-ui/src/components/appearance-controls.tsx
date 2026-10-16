import { useEffect, useState } from "react"
import { Check, ChevronDown, Eye, Monitor, Moon, PanelTop, PanelTopDashed, RotateCcw, Settings, Sun } from "lucide-react"
import { DropdownMenu } from "radix-ui"
import { Button } from "@/components/ui/button"
import { CanvasLineChart } from "@/components/canvas-line-chart"
import { NumericReadout } from "@/components/numeric-readout"
import { useAppearance } from "@/hooks/use-appearance"
import { ACCENT_THEMES, CHART_CANVASES, DEFAULT_APPEARANCE, INTERFACE_DENSITIES, LATEST_POINT_MARKER_OPTIONS, type AccentTheme, type AppearancePreferences, type CurveLineStyle, type InterfaceDensity, type LatestPointMarkerStyle, type Theme } from "@/lib/appearance"

const PREVIEW_DATA = Array.from({ length: 181 }, (_, index) => ({
  elapsedS: index / 3,
  powerW: 0.86 + Math.sin(index * 0.53) * 0.001 + Math.sin(index * 0.14) * 0.00035,
}))
const DENSITY_ICONS = { compact: PanelTopDashed, comfortable: PanelTop, distant: Monitor }

function PreviewChart({ appearance, curveLineStyle = "solid", marker = "point", miniature = false }: {
  appearance?: Partial<AppearancePreferences>
  curveLineStyle?: CurveLineStyle
  marker?: LatestPointMarkerStyle
  miniature?: boolean
}) {
  return <div className={miniature ? "appearance-chart-thumbnail" : "appearance-preview__chart"} aria-hidden={miniature || undefined}>
    <CanvasLineChart
      appearance={appearance}
      ariaLabel="外观示例功率曲线"
      data={PREVIEW_DATA}
      lines={[{ key: "powerW", label: "示例功率", color: "var(--chart-1)", showLatestPoint: true, latestPointStyle: marker, trail: true, dash: curveLineStyle === "dashed" ? [10, 6] : curveLineStyle === "dotted" ? [1, 5] : [] }]}
      preserveAllPoints
      tightInsets={miniature}
      xDomain={[0, 60]}
      xKey="elapsedS"
      xTicks={miniature ? [0, 30, 60] : [0, 10, 20, 30, 40, 50, 60]}
      xTickSuffix=" s"
      yDomain={[0.856, 0.864]}
      yLabel={miniature ? undefined : "W"}
    />
  </div>
}

function DensityChoice({ value, onChange }: { value: InterfaceDensity; onChange: (density: InterfaceDensity) => void }) {
  return <fieldset className="appearance-density">
    <legend>界面密度</legend>
    <p className="appearance-help">远看放大关键读数；顶部「显示」也可快速切换。</p>
    <div className="appearance-density__choices">
      {INTERFACE_DENSITIES.map((density) => {
        const Icon = DENSITY_ICONS[density.id]
        return <label className="appearance-choice" data-selected={value === density.id} key={density.id}>
          <input className="sr-only" type="radio" name="interface-density" checked={value === density.id} onChange={() => onChange(density.id)} />
          <span className="appearance-density__sample" data-density-sample={density.id} aria-hidden="true"><Icon /><span><small>实时功率</small><strong>0.860 <small>W</small></strong></span></span>
          <span className="appearance-choice__caption">{density.label}{value === density.id && <Check className="size-3.5" />}</span>
        </label>
      })}
    </div>
  </fieldset>
}

export function DisplayMenu({ onOpenSettings }: { onOpenSettings: () => void }) {
  const { appearance, updateAppearance } = useAppearance()
  return <DropdownMenu.Root>
    <DropdownMenu.Trigger asChild>
      <Button aria-label="显示" className="app-display-menu-trigger" size="sm" variant="outline"><Eye className="size-4" /><span>显示</span><ChevronDown className="size-3.5" /></Button>
    </DropdownMenu.Trigger>
    <DropdownMenu.Portal>
      <DropdownMenu.Content align="end" sideOffset={8} collisionPadding={12} className="display-menu">
        <DropdownMenu.Label className="display-menu__label">界面密度</DropdownMenu.Label>
        <DropdownMenu.RadioGroup value={appearance.density} onValueChange={(value) => updateAppearance({ density: value as InterfaceDensity })}>
          {INTERFACE_DENSITIES.map((density) => {
            const Icon = DENSITY_ICONS[density.id]
            return <DropdownMenu.RadioItem className="display-menu__item" value={density.id} key={density.id}>
              <Icon className="size-4" /><span>{density.label}<small>{density.description}</small></span><DropdownMenu.ItemIndicator><Check className="size-4" /></DropdownMenu.ItemIndicator>
            </DropdownMenu.RadioItem>
          })}
        </DropdownMenu.RadioGroup>
        <DropdownMenu.Separator className="display-menu__separator" />
        <DropdownMenu.Item className="display-menu__item" onSelect={onOpenSettings}><Settings className="size-4" />打开外观设置</DropdownMenu.Item>
      </DropdownMenu.Content>
    </DropdownMenu.Portal>
  </DropdownMenu.Root>
}

function AppearanceSwitch({ checked, label, onChange }: { checked: boolean; label: string; onChange: (checked: boolean) => void }) {
  return <button aria-checked={checked} aria-label={label} className="theme-switch" data-state={checked ? "checked" : "unchecked"} onClick={() => onChange(!checked)} role="switch" type="button"><span className="theme-switch__thumb" /></button>
}

export function AppearanceSettings({ theme, accentTheme, curveLineStyle, latestPointMarkerStyle, onThemeChange, onAccentThemeChange, onCurveLineStyleChange, onLatestPointMarkerStyleChange }: {
  theme: Theme
  accentTheme: AccentTheme
  curveLineStyle: CurveLineStyle
  latestPointMarkerStyle: LatestPointMarkerStyle
  onThemeChange: (theme: Theme) => void
  onAccentThemeChange: (theme: AccentTheme) => void
  onCurveLineStyleChange: (style: CurveLineStyle) => void
  onLatestPointMarkerStyleChange: (style: LatestPointMarkerStyle) => void
}) {
  const { appearance, updateAppearance } = useAppearance()
  const [previewValue, setPreviewValue] = useState("0.860")
  useEffect(() => {
    if (!appearance.highlightChanges) {
      setPreviewValue("0.860")
      return
    }
    const timer = window.setInterval(() => setPreviewValue((value) => value === "0.860" ? "0.861" : "0.860"), 1600)
    return () => window.clearInterval(timer)
  }, [appearance.highlightChanges])

  const reset = () => {
    updateAppearance({ ...DEFAULT_APPEARANCE })
    onThemeChange("dark")
    onAccentThemeChange("orange")
    onCurveLineStyleChange("solid")
    onLatestPointMarkerStyleChange("point")
  }
  return <div className="appearance-settings">
    <div className="appearance-settings__controls">
      <div className="appearance-palette-heading">
        <div><h2>主题配色</h2><p className="appearance-help">选择适合当前环境的界面主题。</p></div>
        <div className="appearance-mode" role="group" aria-label="界面明暗">
          <button aria-pressed={theme === "dark"} onClick={() => onThemeChange("dark")} type="button"><Moon className="size-4" />深色</button>
          <button aria-pressed={theme === "light"} onClick={() => onThemeChange("light")} type="button"><Sun className="size-4" />浅色</button>
        </div>
      </div>
      <fieldset className="appearance-palette">
        <legend className="sr-only">主题配色</legend>
        {ACCENT_THEMES.map((preset) => <label className="appearance-choice appearance-palette__choice" data-accent={preset.id} data-selected={accentTheme === preset.id} key={preset.id}>
          <input className="sr-only" type="radio" name="accent-theme" checked={accentTheme === preset.id} onChange={() => onAccentThemeChange(preset.id)} />
          <span className="appearance-palette__sample" aria-hidden="true"><span>0.860</span><small>W</small></span>
          <span className="appearance-choice__caption">{preset.label}{accentTheme === preset.id && <Check className="size-3" />}</span>
        </label>)}
      </fieldset>

      <fieldset className="appearance-section">
        <legend>图表画布</legend>
        <p className="appearance-help">点击样式，右侧立即预览。</p>
        <div className="appearance-canvas-choices">
          {CHART_CANVASES.map((canvas) => <label className="appearance-choice" data-selected={appearance.canvas === canvas.id} key={canvas.id}>
            <input className="sr-only" type="radio" name="chart-canvas" checked={appearance.canvas === canvas.id} onChange={() => updateAppearance({ canvas: canvas.id })} />
            <PreviewChart appearance={{ canvas: canvas.id }} miniature />
            <span className="appearance-choice__caption">{canvas.label}{appearance.canvas === canvas.id && <Check className="size-3.5" />}</span>
          </label>)}
        </div>
      </fieldset>

      <div className="appearance-sliders appearance-section">
        <label><span>网格浓度<output>{Math.round(appearance.gridOpacity * 100)}%</output></span><input aria-label="网格浓度" type="range" min="10" max="80" step="5" disabled={appearance.canvas === "clean"} value={appearance.gridOpacity * 100} onChange={(event) => updateAppearance({ gridOpacity: Number(event.target.value) / 100 })} /></label>
        <label><span>曲线粗细<output>{appearance.lineWidth.toFixed(1)} px</output></span><input aria-label="曲线粗细" type="range" min="1" max="4" step="0.2" value={appearance.lineWidth} onChange={(event) => updateAppearance({ lineWidth: Number(event.target.value) })} /></label>
      </div>

      <div className="appearance-section appearance-trail-section">
        <div className="appearance-option-row"><div><h2>曲线尾迹</h2><p className="appearance-help">自动测试最近 {appearance.trailSeconds} 秒提亮，保留完整历史曲线。</p></div><AppearanceSwitch checked={appearance.trailEnabled} label="曲线尾迹" onChange={(trailEnabled) => updateAppearance({ trailEnabled })} /></div>
        <div className="appearance-chart-options">
          <label>尾迹时长<select aria-label="尾迹时长" disabled={!appearance.trailEnabled} value={appearance.trailSeconds} onChange={(event) => updateAppearance({ trailSeconds: Number(event.target.value) })}>{[3, 5, 10, 15].map((seconds) => <option value={seconds} key={seconds}>{seconds} 秒</option>)}</select></label>
          <label>曲线线型<select aria-label="自动测试功率实时曲线线型" value={curveLineStyle} onChange={(event) => onCurveLineStyleChange(event.target.value as CurveLineStyle)}><option value="solid">实线</option><option value="dashed">虚线</option><option value="dotted">点线</option></select></label>
          <label>末端样式<select aria-label="自动测试功率实时曲线末端样式" value={latestPointMarkerStyle} onChange={(event) => onLatestPointMarkerStyleChange(event.target.value as LatestPointMarkerStyle)}>{LATEST_POINT_MARKER_OPTIONS.map((marker) => <option value={marker.id} key={marker.id}>{marker.label}</option>)}</select></label>
        </div>
      </div>

      <div className="appearance-section"><DensityChoice value={appearance.density} onChange={(density) => updateAppearance({ density })} /></div>
      <div className="appearance-option-row appearance-section"><div><h2>数值变化动效</h2><p className="appearance-help">可选 · 变化的数字短暂滚动，小数位和单位保持稳定。</p></div><AppearanceSwitch checked={appearance.highlightChanges} label="数值变化动效" onChange={(highlightChanges) => updateAppearance({ highlightChanges })} /></div>
      <button className="appearance-reset" onClick={reset} type="button"><RotateCcw className="size-4" />恢复外观默认值</button>
    </div>

    <aside className="appearance-settings__preview" aria-label="外观即时预览">
      <div className="appearance-preview__heading"><h2>即时预览</h2><span>示例数据</span></div>
      <div className="appearance-preview" data-preview-density={appearance.density}>
        <div className="appearance-preview__toolbar"><strong>Power Test</strong><span><Eye className="size-3.5" />{INTERFACE_DENSITIES.find((item) => item.id === appearance.density)?.label}</span></div>
        <div className="appearance-preview__sample"><div><small>当前样品</small><strong>ARP-DEMO-001</strong></div><div><small>测试温度</small><span>水冷 25°C</span></div></div>
        <div className="appearance-preview__chart-heading"><strong>功率实时</strong><small>最近 60 s{appearance.trailEnabled ? ` · 尾迹 ${appearance.trailSeconds} s` : ""}</small></div>
        <PreviewChart curveLineStyle={curveLineStyle} marker={latestPointMarkerStyle} />
        <div className="appearance-preview__metrics">{[["实时功率", "0.860", "W"], ["中心波长", "976.200", "nm"], ["FWHM", "0.776", "nm"], ["PIB", "96.85", "%"]].map(([label, value, unit]) => <div key={label}><small>{label}</small><strong>{value}<small>{unit}</small></strong></div>)}</div>
      </div>
      <p className="appearance-preview__caption">{CHART_CANVASES.find((item) => item.id === appearance.canvas)?.label} · {INTERFACE_DENSITIES.find((item) => item.id === appearance.density)?.label} · {appearance.trailEnabled ? `尾迹 ${appearance.trailSeconds} s` : "尾迹关闭"}</p>
      <div className="appearance-readout-example"><small>实时功率 · 数字变化示例</small><strong><NumericReadout value={previewValue} /><small>W</small></strong><p className="appearance-help">{appearance.highlightChanges ? "0.860 ↔ 0.861：末位数字滚动；系统减少动态效果时直接更新。" : "开启数值变化动效，在这里查看数字滚动效果。"}</p></div>
    </aside>
  </div>
}
