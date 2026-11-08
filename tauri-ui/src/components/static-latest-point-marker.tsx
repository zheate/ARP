import { memo, useId, type Ref } from "react"
import type { CanvasChartLine } from "./canvas-line-chart"

export const StaticLatestPointMarker = memo(function StaticLatestPointMarker({ line, size, ref }: {
  line: CanvasChartLine
  size: number
  ref?: Ref<HTMLDivElement>
}) {
  const gradientId = useId()
  const glowId = `${gradientId}-glow`
  const outline = "hsl(42 7% 25%)"
  const hollow = line.pointStyle === "hollow"
  return <div aria-hidden="true" data-static-marker={line.latestPointStyle ?? "point"} ref={ref}
    style={{ position: "absolute", left: 0, top: 0, width: size, height: size, visibility: "hidden", pointerEvents: "none", color: line.color }}>
    <svg viewBox="-9 -9 18 18" width="100%" height="100%" style={{ overflow: "visible", display: "block", transform: "rotate(var(--marker-angle, 0rad))" }}>
      <defs><filter id={glowId} x="-200%" y="-200%" width="500%" height="500%" colorInterpolationFilters="sRGB">
        <feGaussianBlur in="SourceAlpha" stdDeviation={line.latestPointStyle === "paper-plane" ? 2.4 : 2} result="blur" />
        <feFlood floodColor="currentColor" /><feComposite in2="blur" operator="in" />
        <feMerge><feMergeNode /><feMergeNode in="SourceGraphic" /></feMerge>
      </filter></defs>
      {line.latestPointStyle === "paper-plane" ? <g stroke={outline} strokeLinejoin="round" strokeLinecap="round">
        <path d="M10.35 0 -8.1 -6.48 -2.52 0 -8.1 6.48Z" fill="currentColor" strokeWidth="1.44" filter={`url(#${glowId})`} />
        <path d="M-8.1 -6.48 -2.52 0 10.35 0" fill="none" strokeWidth="1.08" strokeOpacity="0.72" />
      </g> : line.latestPointStyle === "paw" ? <g fill="currentColor" stroke={outline} strokeWidth="1.1" filter={`url(#${glowId})`}>
        <circle cx="-5.3" cy="-3.2" r="1.8" /><circle cx="-1.8" cy="-5.2" r="1.9" />
        <circle cx="1.8" cy="-5.2" r="1.9" /><circle cx="5.3" cy="-3.2" r="1.8" />
        <path d="M0 -.7C2.5 -.7 4.8 1.4 4.4 3.5C4 5.5 1.8 5.5 0 4.4C-1.8 5.5 -4 5.5 -4.4 3.5C-4.8 1.4 -2.5 -.7 0 -.7Z" />
      </g> : <g fill={hollow ? "var(--card)" : "currentColor"} stroke={hollow ? "currentColor" : outline} strokeWidth={(hollow ? 2 : 1.2) * 18 / (line.pointSize ?? 7)}>
        {line.pointShape === "triangle" ? <path d="M0 -9 8.1 6.75 -8.1 6.75Z" /> : <circle r="9" />}
      </g>}
    </svg>
  </div>
})
