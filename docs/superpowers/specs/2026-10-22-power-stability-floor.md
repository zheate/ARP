# Power stability floor and drift guard

User approved the relative-plus-absolute stability design on 2026-10-22.

## Behavior

- LaserPoint relative mode accepts `2 * sample standard deviation <= max(mean * relative_limit / 100, absolute_floor_w)`.
- Start with an explicitly provisional, operator-configurable 0.2 W floor, expressed in calibrated watts (after software gain). Zero restores the previous relative rule. Other drivers retain their existing rules.
- With the floor enabled, fit power against actual sample timestamps by least squares. The absolute fitted change across the window must also be at most `mean * relative_limit / 100`. The floor never widens this drift limit.
- Compute both checks from the same unfiltered samples. Keep the complete sample window, raw realtime series, existing session traces and spectrum data, and existing median-based LIV recording. Display smoothing never feeds measurement or stability.
- Keep existing explicit peak-to-peak limits for below-2 A, 2 A and Ophir points. Preserve complete-window/sample-count/gap rules, setpoint generations, post-stability confirmation, acquisition cadence, emergency power-drop protection and shutdown timing.
- Expose the configured floor and measured limits/drift in backend snapshots; distinguish drift rejection from random fluctuation in the automatic-test status. Persist the configuration with existing session metadata.

## Implementation plan

1. Add regression tests for quantized readings, excessive random noise, increasing/decreasing drift, high-power relative behavior, zero-floor compatibility, absolute-rule precedence and gaps/invalid data.
2. Extend the pure detector and reader contracts, propagate through start/reconnect/configuration/automatic/manual paths and snapshots.
3. Add a LaserPoint floor setting and accurate status/help text; use existing layout and control primitives.
4. Verify original readings and LIV selection stay unchanged. Run focused Python/frontend tests, frontend build, and inspect the preview at two supported desktop sizes. Do not scan or operate hardware, run full suites, package, commit or push.

## Limits

The drift guard detects linear trends across the existing window; it does not establish long-term optical stability. The 0.2 W initial floor needs confirmation from the actual meter's raw noise and resolution. No filter can improve the meter's physical accuracy.
