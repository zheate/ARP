# Design QA

- Source visual truth: `C:\Users\24561\AppData\Local\Temp\codex-clipboard-bf86ff9d-772d-4eea-8876-2fd73352d84b.png`
- Source pixels: 603 x 173
- Implementation: `http://127.0.0.1:1420/?demo=1`
- Implementation screenshot: Codex in-app browser capture from the current local preview (inline capture; no local screenshot path exposed by the browser)
- Browser viewport: 1280 x 720 CSS px
- Device pixel ratio: 2.2649998664855957; the browser capture was reviewed at its displayed viewport size
- State: dark theme, automatic-test demo, live metric cards visible

## Full-view comparison evidence

The four live metric cards preserve the existing typography, colors, borders, radii, and content. Each title is aligned to the upper-right of its card, while each value-and-unit row is centered across the card's content width.

DOM geometry confirmed that all four value rows have the same horizontal center as their cards (`centeredDelta: 0`) and that all four title rows use `justify-content: flex-end`.

## Focused region comparison evidence

A separate crop was not needed because all four card labels, values, and units were legible in the 1280 x 720 full-view capture. The live-power card was also checked independently before applying the same rule to the other three cards.

## Required fidelity surfaces

- Fonts and typography: unchanged from the existing workbench theme; numeric and unit weights remain intact.
- Spacing and layout rhythm: values and units are centered as one group; titles share one right-aligned edge within their respective cards.
- Colors and visual tokens: unchanged; semantic metric colors and muted units remain intact.
- Image quality and asset fidelity: no image assets are present in the modified card region.
- Copy and content: unchanged (`实时功率`, `中心波长`, `FWHM`, `PIB` and their units).

## Comparison history

- Earlier finding (P2): only the live-power card used the requested alignment, leaving the other three cards inconsistent.
- Fix: generalized the alignment selectors from the power card to every card inside `.automatic-metric-grid`.
- Post-fix evidence: browser capture shows all four titles at upper-right and all four value/unit groups centered; computed geometry reports zero horizontal center offset for every value row.

## Findings

No remaining P0, P1, or P2 visual mismatches for the requested card alignment.

## Interaction and console checks

- No interaction behavior was changed by this CSS-only adjustment.
- Browser console errors: none.

final result: passed

---

## 2026-09-14 — Full workbench review

The record above is the historical card-alignment review, not a claim that every workspace or safety state was validated. The current systematic audit, findings, priority decisions, measured geometry and limitations are in [the workbench audit](docs/ui-audit-2026-09-14.md). `DESIGN.md` remains the design baseline.

The reusable entry point is `tauri-ui/scripts/ui-audit.mjs`; CI is `.github/workflows/frontend-qa.yml`. Run the existing tests and production build before browser review. Use a loopback demo only; the QA refuses external hosts and does not start Tauri or communicate with hardware.

Evidence includes screenshots plus JSON checks for six logical-viewport/DPR combinations, real numeric updates, modal focus/background isolation, local dropdown geometry, failed/pending saves, long diagnostic text, safe-exit guards and PD running controls. Real React components and the production CSS are used in synthetic state fixtures. No product dependency was added for browser QA.

The first accepted source revision passed 97 frontend tests and 455 browser checks with 120 screenshots. Consult the artifact's commit.txt for the exact source revision of every subsequent run; build success alone is not visual acceptance.

Windows/WebView2 native 100/125/150/200% DPI, physical devices and real report/export data remain explicit release-validation requirements. At a 960×540 logical viewport, intentional vertical scrolling is retained; DPR emulation is not native Windows certification.
