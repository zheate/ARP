# ARP Icon Motion Spec

## Source analysis

- Source: `/Users/zh/Documents/test/ARP/tauri-ui/src-tauri/icons/icon.png`
- Raster size: 512 × 512 px
- Mode: RGBA with transparent corners and a dark rounded-square field
- Semantic parts: rounded-square background, outer A-shaped mark, horizontal crossbars, vertical support and base, target ring, orange center dot
- Final-frame contract: the animation must land on the verified static SVG in `logo.svg`

## Motion brief

- Personality: precise, stable, signal
- Usage context: splash / intro reveal, 1500 ms
- Axis selection: low-to-medium energy, serious/professional with one warm orange accent
- Preset derivation: Trustworthy / Professional, with a restrained dot landing accent
- Reveal pattern: staged assembly plus draw-on for the outer mark, crossbars, support, and target ring

## Choreography

| Phase | Time | Action | Principles |
|---|---:|---|---|
| Staging | 0–180 ms | Dark field settles in; mark remains quiet | Staging, Timing |
| Anticipation | 180–300 ms | Main mark holds slightly reduced and transparent | Anticipation |
| Action | 300–900 ms | Outer A draws first; support and crossbars follow | Staging, Slow In / Slow Out |
| Focus | 720–1120 ms | Target ring draws in two arcs; orange dot arrives last | Timing, Arc, Overlapping Action |
| Follow-through | 1120–1500 ms | Dot makes a restrained landing accent; all geometry settles exactly | Follow Through, Appeal, Solid Drawing |

## Tokens

```css
--p2m-duration: 1500ms;
--p2m-ease-enter: cubic-bezier(0.16, 1, 0.3, 1);
--p2m-ease-settle: cubic-bezier(0.4, 0, 0.2, 1);
--p2m-ease-narrative: cubic-bezier(0.34, 0, 0.14, 1);
--p2m-squash: 0;
--p2m-overshoot: 1.0;
```

Keyframe easing is written literally in `motion.css` because Chromium can silently drop custom-property timing functions inside keyframes.

## Atomic studies

- Hover: a small lift and rotation response
- Pulse: the orange dot breathes gently
- Arc: the complete icon makes a restrained spring return
- Press: the icon uses volume-preserving scale

## QA checklist

- Smooth vector geometry is used instead of a pixel-grid trace.
- `logo.svg` is rendered and compared against the source with a cyan overlay.
- Motion frames are captured at 0, 300, 600, 900, 1120, and 1500 ms.
- `?static=1` and `?t=1500` are captured through the same browser pipeline for the Final Frame Contract.
- Reduced motion falls back to the finished static logo.
