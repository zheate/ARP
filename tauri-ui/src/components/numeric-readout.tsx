import NumberFlow from "@number-flow/react"
import { memo, useMemo } from "react"
import { useAppearance } from "@/hooks/use-appearance"
import { numberFlowReadout } from "@/lib/numeric-readout"

// Live snapshots can arrive every 100 ms. Finish each transition within that interval.
const READOUT_TIMING = { duration: 100, easing: "ease-out" }
const OPACITY_TIMING = { duration: 80, easing: "ease-out" }

export const NumericReadout = memo(function NumericReadout({ value, minimumIntegerDigits = 1 }: { value: string; minimumIntegerDigits?: number }) {
  const { appearance } = useAppearance()
  const readout = useMemo(() => numberFlowReadout(value, minimumIntegerDigits), [value, minimumIntegerDigits])

  return <span className="numeric-readout" aria-label={value}>
    {readout ? <NumberFlow
      aria-hidden="true"
      className="numeric-readout__flow"
      value={readout.value}
      format={readout.format}
      locales="en-US"
      animated={appearance.highlightChanges}
      respectMotionPreference
      trend={0}
      transformTiming={READOUT_TIMING}
      spinTiming={READOUT_TIMING}
      opacityTiming={OPACITY_TIMING}
    /> : <span aria-hidden="true">{value}</span>}
  </span>
})
