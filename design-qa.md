# ARP appearance implementation QA — 2026-09-10

final result: passed

Scope: the approved automatic-test workbench, appearance settings, device details, density menu and completion summary. This result covers the rendered frontend and automated logic checks; it is not instrument or native WebView acceptance.

## Visual truth and captures

Reference directory: `/Users/zh/.codex/generated_images/01a089eb-8f65-7d51-9017-aab25fe48818/`.

- Main workbench: `exec-c1d3f28a-e64f-49ea-820d-003751f0f985.png`.
- Settings: `exec-73fc74d2-1119-4d76-b440-eb134ab9fdd8.png`.
- Completion/device/density detail board: `exec-b6e13411-0846-4936-8965-dcc91eff25ed.png`.

Implementation: `http://127.0.0.1:1437/?demo=1`, running the actual ARP frontend with its explicit demo snapshot. Captures are in `/Users/zh/Documents/test/ARP/output/appearance-qa/`:

| State | Final evidence |
| --- | --- |
| Automatic test, waiting for stability, dark/orange, comfortable | `automatic-final.png` |
| Settings, default values, all appearance controls visible | `settings-final.png` |
| Settings, light/blue | `settings-light.png` |
| Confirmed completion, recorded sample ARP-QA-001 | `completed-final.png` |
| Operator stop, separately identified from successful completion | `stopped.png` |
| Read-only meter details while running | `device-tooltip.png` |
| Density menu | `display-menu.png` |
| Compact and distant modes at 1280 × 800 | `compact-1280.png`, `distant-1280.png` |
| Narrow window at 800 × 900 | `narrow-800.png` |

Main/settings source and implementation captures are 1586 × 992 pixels. Browser CSS viewport is 1586 × 992 at 1:1 output density; no image resampling or browser chrome is included. The 1513 × 1040 detail source is a presentation board, not a full app viewport: only its corresponding summary, tooltip and menu regions are compared. Different sample IDs, point counts, duration and measured curves are explicit fixture differences, not layout drift.

The main source and final main capture were opened together in one comparison input; the settings pair was opened in that same comparison input. The detail source and the final completion, tooltip and menu captures were then opened together. Full views and the following readable regions were inspected: the 64 px header; nameplate/device rows; chart headings and metric row; settings selectors and lower controls; completion facts and buttons; meter popover and density menu. These full-resolution inputs keep those regions legible, so additional raster crops were not required.

## Findings and iteration history

No actionable P0/P1/P2 findings remain within the scoped frontend work.

| Earlier finding | Fix | Post-fix evidence |
| --- | --- | --- |
| P2: main titles and readings were too small; power/efficiency and spectrum headings were missing | Restored chart headings, increased desktop nameplate/readout/navigation type, retained the existing font family | `automatic-initial.png` → `automatic-revised.png` → `automatic-final.png` |
| P2: device-row start/stop controls were too short relative to the approved rail | Increased device-row button height to 2.6 rem | `automatic-final.png` |
| P2: settings placed the number-change option and reset below the intended desktop view | Aligned tail controls horizontally on wide screens and tightened section spacing | `settings.png` → `settings-revised.png` → `settings-final.png` |
| P2: grid presets were too faint against dark cards | Used the muted foreground token for grid ink, retaining user-controlled opacity | `settings-final.png`, `automatic-final.png` |
| P2: distant readouts were capped at the comfortable size on smaller desktops | Raised the size cap while preserving compact sizes | `compact-1280.png` versus `distant-1280.png` |
| P2: narrow layout hid the Display button's accessible name along with its visible text | Added an explicit accessible label | Final component check; the button remains reachable at 800 px |
| P2: existing demo simulation discarded the entered SN and reset state on navigation | Preserve demo configuration/state, capture start parameters, and invalidate older simulation timers on stop/new run | Entered ARP-QA-001 → started → `completed-final.png`; this affects demo mode only |
| P2: completion facts and primary actions needed stronger hierarchy | Enlarged summary facts, sample ID and action buttons on desktop | `completed-final.png` |

## Required fidelity surfaces

- **Fonts and typography:** existing ARP Interface/DIN/MiSans assets retained. SN and tabular measurements are the dominant text. Changed digits use fixed character positions. Headings, labels, units and legends remain distinct; source illustration lettering is not substituted for the product's font assets.
- **Spacing and layout:** same graphite workbench structure, orange nameplate edge, compact device strip, two upper plots, four metrics and full-width spectrum. All appearance controls fit the target desktop viewport. At 1280 px the three densities preserve the charts and controls. At 800 px, fields and charts stack inside the scrolling work area; emergency stop remains in the fixed header. DOM viewport checks found no horizontal overflow at 1280 or 800 px.
- **Colors and tokens:** existing dark/light and six accent themes retained. Fine, clean and engineering grids use the same chart renderer. The new tail re-strokes the same path and keeps historical samples visible. Existing red/green measurement-specification decisions remain visible; the source mock's neutral values do not override them.
- **Image quality and assets:** this target contains UI and instrument charts rather than raster artwork. Existing Lucide icons and application fonts are reused. Chart thumbnails and the large preview are rendered with the actual Canvas component, so controls show their real effects. No screenshot is used as a substitute for the functioning interface.
- **Copy and content:** phase labels, SN, station, temperature, script and result status are in product language. Completion distinguishes elapsed duration, journal persistence, Excel export and confirmed shutdown. There is no invented overall PASS/FAIL. The density menu and settings use the same labels.

Intentional product differences: the main chart keeps the current instrument data, tick/axis behavior, detected-peak legend and semantic specification colors. The settings preview shows live rendered controls/readouts rather than decorative mini-screens. Active testing keeps its phase track visible while settings are open. Existing license controls remain below appearance settings. Completion retains access to the existing detailed result dialog and export recovery actions.

## Interaction and logic validation

- Changed engineering/clean/fine canvas, grid opacity, line width and tail duration; the preview changed immediately. Clean canvas disables grid-opacity adjustment.
- Enabled number-change highlighting, observed the 0.860/0.861 example, then restored default OFF. No runtime errors occurred during the animation interval. Digit-change tests verify only the changed numeric positions are selected.
- Read the actual DOM geometry at 0.861 and 0.860: total width stayed 103.6640625 px and every character's x-position/width was identical. Evidence: `numeric-position-check.json`.
- Reloaded the page and reopened settings: engineering canvas, 35% opacity, 2.0 px line, 10 s tail and enabled highlight were retained. Restore defaults returned all values and appearance choices to their defaults.
- Changed density through the header menu; compact and distant screen captures confirm real layout/readout changes. Settings reflects the same selection.
- Changed to light/blue and verified readable controls and chart preview, then restored dark/orange/comfortable.
- While running, keyboard focus opened meter details; Enter did not open editable settings. While idle, the meter settings dialog opened with the existing configuration fields.
- Walked through operator stop, the existing stop details dialog, next device, SN entry, start, progression and confirmed completion. The summary retained ARP-QA-001 and frozen elapsed time. Next device cleared results and focused the SN field.
- Frontend tests: **52 passed** (`npm test`). Includes missing-data handling, curve preservation, display preference normalization, changed digits, phase/record-count distinctions and journal/export status.
- Backend tests: **420 run, 418 passed, 2 skipped** (`/Users/zh/miniconda3/envs/sth_eb314/bin/python -m unittest discover -s tests`). New timing/context coverage verifies live time, frozen completion time, copied run metadata, captured start context and next-device reset.
- Production frontend build: **passed** (`npm run build`). Vite reports a non-blocking bundle-size warning.
- `git diff --check`: **passed**.
- Console: no warnings or errors after the clean reload at `2026-09-10T07:23:28.696Z`; evidence in `console-check.json`. Earlier live editing added hooks and triggered a Fast Refresh hook-order error; a full reload cleared that editing-only state, and subsequent interaction checks were clean.

## Remaining acceptance limits and P3 polish

- Real instruments, the native Tauri/WebView2 surface and system file dialogs were not exercised. Backend safety/measurement behavior remains covered by the existing suite, but hardware acceptance is a separate step.
- Export failure and unconfirmed shutdown distinctions are covered by logic tests and retain the existing error/retry UI; they were not forced on connected hardware.
- P3: chart tick text and some secondary controls retain the existing product typography rather than matching the generated mock's larger lettering pixel for pixel.
- P3: evaluate the default grid opacity and line width on the actual production monitor under workshop lighting; both are adjustable immediately in settings.

## Implementation checklist

- [x] Preserve acquisition data, full spectrum samples, existing line/endpoint options and hardware command paths.
- [x] Implement all eight approved appearance details in the existing app.
- [x] Persist appearance choices and retain safe defaults.
- [x] Compare source and rendered implementation, fix P2 issues, and recapture.
- [x] Verify primary interactions, responsive layouts, tests, build and console.
- [x] Keep the local preview available; no commit, push, packaging or deployment performed.
