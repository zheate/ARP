use gpui_kit::component::ActiveTheme;
use gpui_kit::*;
use serde_json::Value;
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
};
#[derive(Default)]
pub struct ChartRegistry(Mutex<HashMap<String, Arc<Mutex<ChartState>>>>);
impl Global for ChartRegistry {}
#[derive(Default)]
struct ChartState {
    range: Option<(f64, f64)>,
    bounds: Bounds<Pixels>,
    hover: String,
}

/// Display coordinates use f32 pixels; acquisition/storage values remain f64.
type Series = (String, u32, Vec<(f64, f64)>);
pub struct ChartOptions {
    pub secondary: Option<Series>,
    pub height: f32,
    pub x_range: Option<(f64, f64)>,
    pub smooth: bool,
}
impl Default for ChartOptions {
    fn default() -> Self {
        Self {
            secondary: None,
            height: 224.,
            x_range: None,
            smooth: false,
        }
    }
}
pub fn chart(
    title: &str,
    x_label: &str,
    y_label: &str,
    series: Vec<Series>,
    cx: &App,
) -> AnyElement {
    render_chart(
        title,
        x_label,
        y_label,
        series,
        ChartOptions {
            height: 300.,
            ..Default::default()
        },
        false,
        cx,
    )
}
pub fn workbench_chart(
    title: &str,
    x_label: &str,
    y_label: &str,
    series: Vec<Series>,
    options: ChartOptions,
    cx: &App,
) -> AnyElement {
    render_chart(title, x_label, y_label, series, options, true, cx)
}
/// Port of tauri-ui/src/lib/monotone-curve.ts. This only changes painted
/// geometry: sample positions remain exact and local extrema cannot overshoot.
fn trace_curve(path: &mut PathBuilder, points: &[Point<Pixels>], smooth: bool) {
    if points.is_empty() {
        return;
    }
    path.move_to(points[0]);
    if !smooth || points.len() < 3 {
        for &p in &points[1..] {
            path.line_to(p);
        }
        return;
    }
    let widths = points
        .windows(2)
        .map(|p| f32::from(p[1].x - p[0].x))
        .collect::<Vec<_>>();
    let slopes = points
        .windows(2)
        .zip(&widths)
        .map(|(p, w)| f32::from(p[1].y - p[0].y) / w)
        .collect::<Vec<_>>();
    let mut tangents = vec![slopes[0]];
    for i in 1..points.len() - 1 {
        let (left, right) = (slopes[i - 1], slopes[i]);
        tangents.push(
            if left == 0. || right == 0. || left.signum() != right.signum() {
                0.
            } else {
                let (lw, rw) = (
                    2. * widths[i] + widths[i - 1],
                    widths[i] + 2. * widths[i - 1],
                );
                (lw + rw) / (lw / left + rw / right)
            },
        );
    }
    tangents.push(*slopes.last().unwrap());
    for i in 0..points.len() - 1 {
        let (left, right) = (points[i], points[i + 1]);
        let third = px(widths[i] / 3.);
        let (lo, hi) = (left.y.min(right.y), left.y.max(right.y));
        path.cubic_bezier_to(
            right,
            point(left.x + third, (left.y + third * tangents[i]).clamp(lo, hi)),
            point(
                right.x - third,
                (right.y - third * tangents[i + 1]).clamp(lo, hi),
            ),
        );
    }
}
fn domain(series: &[(String, u32, Vec<(f64, f64)>)], axis: usize) -> (f64, f64) {
    let mut lo = f64::INFINITY;
    let mut hi = f64::NEG_INFINITY;
    for (_, _, points) in series {
        for &(x, y) in points {
            let v = if axis == 0 { x } else { y };
            if x.is_finite() && y.is_finite() {
                lo = lo.min(v);
                hi = hi.max(v);
            }
        }
    }
    if !lo.is_finite() {
        return (0., 1.);
    }
    if hi <= lo {
        let p = lo.abs().max(1.) * 0.02;
        lo -= p;
        hi += p;
    }
    if axis == 1 {
        let p = (hi - lo) * 0.08;
        lo -= p;
        hi += p;
    }
    (lo, hi)
}
// A planned-current range is a display hint. It must never hide measured
// custom points that lie outside that plan.
fn horizontal_domain(series: &[Series], hint: Option<(f64, f64)>) -> (f64, f64) {
    let measured = domain(series, 0);
    match hint.filter(|(lo, hi)| lo.is_finite() && hi.is_finite() && hi > lo) {
        Some((lo, hi))
            if series.iter().any(|(_, _, points)| {
                points.iter().any(|(x, y)| x.is_finite() && y.is_finite())
            }) =>
        {
            (lo.min(measured.0), hi.max(measured.1))
        }
        Some(hint) => hint,
        None => measured,
    }
}
fn tick(value: f64) -> String {
    let digits = if value.abs() >= 1000. {
        0
    } else if value.abs() >= 10. {
        1
    } else if value.abs() >= 1. {
        2
    } else {
        3
    };
    let s = format!("{value:.digits$}");
    if s.contains('.') {
        s.trim_end_matches('0').trim_end_matches('.').to_string()
    } else {
        s
    }
}
fn paint_label(
    label: String,
    origin: Point<Pixels>,
    color: Hsla,
    size: f32,
    window: &mut Window,
    cx: &mut App,
) {
    let font = window.text_style().font();
    let run = TextRun {
        len: label.len(),
        font,
        color,
        ..Default::default()
    };
    let line = window
        .text_system()
        .shape_line(label.into(), px(size), &[run], None);
    let _ = line.paint(origin, px(size + 4.), TextAlign::Left, None, window, cx);
}
fn render_chart(
    title: &str,
    x_label: &str,
    y_label: &str,
    series: Vec<(String, u32, Vec<(f64, f64)>)>,
    options: ChartOptions,
    compact: bool,
    cx: &App,
) -> AnyElement {
    let ChartOptions {
        secondary,
        height,
        x_range,
        smooth,
    } = options;
    let mut all = series.clone();
    if let Some(s) = secondary.clone() {
        all.push(s);
    }
    let (mut min_x, mut max_x) = horizontal_domain(&all, x_range);
    let (mut min_y, max_y) = domain(&series, 1);
    if y_label.contains("counts") || secondary.is_some() {
        min_y = min_y.min(0.);
    }
    let secondary_domain = secondary.as_ref().map(|s| {
        let (lo, hi) = domain(std::slice::from_ref(s), 1);
        (lo.min(0.), hi.max(f64::EPSILON))
    });
    let empty = all
        .iter()
        .all(|(_, _, p)| p.iter().all(|(x, y)| !x.is_finite() || !y.is_finite()));
    let state = cx
        .global::<ChartRegistry>()
        .0
        .lock()
        .unwrap()
        .entry(title.to_string())
        .or_default()
        .clone();
    {
        let mut st = state.lock().unwrap();
        if let Some((lo, hi)) = st.range {
            if lo < max_x && hi > min_x {
                min_x = lo.max(min_x);
                max_x = hi.min(max_x);
            } else {
                st.range = None;
            }
        }
    }
    let muted = cx.theme().muted_foreground;
    let grid = cx.theme().border;
    let hover = state.lock().unwrap().hover.clone();
    let paint_state = state.clone();
    let hover_state = state.clone();
    let zoom_state = state.clone();
    let reset_state = state.clone();
    let title_owned = title.to_string();
    let x_owned = x_label.to_string();
    let y_owned = y_label.to_string();
    let hover_series = all.clone();
    let plot = canvas(
        |_, _, _| (),
        move |bounds, _, window, cx| {
            let left = bounds.left() + px(44.);
            let top = bounds.top() + px(28.);
            let w = (bounds.size.width - px(if secondary_domain.is_some() { 88. } else { 58. }))
                .max(px(1.));
            let h = (bounds.size.height - px(56.)).max(px(1.));
            paint_state.lock().unwrap().bounds = Bounds::new(point(left, top), size(w, h));
            for i in 0..=4 {
                let f = i as f32 / 4.;
                let y = top + h * f;
                let x = left + w * f;
                let mut path = PathBuilder::stroke(px(1.));
                let mut offset = px(0.);
                while offset < w {
                    path.move_to(point(left + offset, y));
                    path.line_to(point(left + (offset + px(2.)).min(w), y));
                    offset += px(6.);
                }
                offset = px(0.);
                while offset < h {
                    path.move_to(point(x, top + offset));
                    path.line_to(point(x, top + (offset + px(2.)).min(h)));
                    offset += px(6.);
                }
                if let Ok(p) = path.build() {
                    window.paint_path(p, grid);
                }
                paint_label(
                    tick(max_y - (max_y - min_y) * f as f64),
                    point(bounds.left() + px(3.), y - px(6.)),
                    muted,
                    10.,
                    window,
                    cx,
                );
                paint_label(
                    format!("{} {}", tick(min_x + (max_x - min_x) * f as f64), x_owned),
                    point(x - px(12.), top + h + px(8.)),
                    muted,
                    10.,
                    window,
                    cx,
                );
                if let Some((lo, max)) = secondary_domain {
                    paint_label(
                        tick(max - (max - lo) * f as f64),
                        point(left + w + px(7.), y - px(6.)),
                        rgb(0xf5c56b).into(),
                        10.,
                        window,
                        cx,
                    );
                }
            }
            paint_label(
                if hover.is_empty() {
                    format!("{title_owned} · {y_owned}")
                } else {
                    hover.clone()
                },
                point(left, bounds.top() + px(4.)),
                muted,
                11.,
                window,
                cx,
            );
            if secondary_domain.is_some() {
                paint_label(
                    "功率 (W)".into(),
                    point(left + px(8.), top + px(12.)),
                    rgb(0xff7a1a).into(),
                    11.,
                    window,
                    cx,
                );
                paint_label(
                    "效率 (%)".into(),
                    point(left + px(8.), top + px(30.)),
                    rgb(0xf5c56b).into(),
                    11.,
                    window,
                    cx,
                );
            }
            for (index, (_, color, points)) in all.iter().enumerate() {
                let (lo, hi) = if index >= series.len() {
                    secondary_domain.unwrap_or((0., 1.))
                } else {
                    (min_y, max_y)
                };
                let mut path = PathBuilder::stroke(px(1.6));
                let mut segment = Vec::<Point<Pixels>>::new();
                let mut last = None;
                for &(x, y) in points {
                    if !x.is_finite() || !y.is_finite() || x < min_x || x > max_x {
                        trace_curve(&mut path, &segment, smooth);
                        segment.clear();
                        continue;
                    }
                    let p = point(
                        left + w * ((x - min_x) / (max_x - min_x)) as f32,
                        top + h * (1. - ((y - lo) / (hi - lo)) as f32),
                    );
                    if segment.last().is_some_and(|last| p.x <= last.x) {
                        trace_curve(&mut path, &segment, smooth);
                        segment.clear();
                    }
                    segment.push(p);
                    last = Some(p);
                }
                trace_curve(&mut path, &segment, smooth);
                if let Ok(p) = path.build() {
                    window.paint_path(p, rgb(*color));
                }
                if let Some(p) = last {
                    window.paint_quad(
                        fill(
                            Bounds::new(p - point(px(2.5), px(2.5)), size(px(5.), px(5.))),
                            rgb(*color),
                        )
                        .corner_radii(px(2.5)),
                    );
                }
            }
            if empty {
                paint_label(
                    "暂无数据".into(),
                    point(left + w * 0.4, top + h * 0.5),
                    muted,
                    12.,
                    window,
                    cx,
                );
            }
        },
    )
    .w_full()
    .h(px(height - 16.));
    let mut panel = div()
        .flex()
        .flex_col()
        .w_full()
        .min_w_0()
        .h(px(height))
        .p_2()
        .border_1()
        .border_color(grid)
        .rounded_lg()
        .bg(if compact {
            crate::workbench::surface(cx)
        } else {
            cx.theme().background
        });
    if !compact {
        panel = panel.h(px(height + 20.)).child(
            div()
                .text_size(px(12.))
                .text_color(muted)
                .child("移动光标读取数据 · Ctrl/⌘ + 滚轮缩放 · 双击复位"),
        );
    }
    panel
        .child(
            div()
                .id(SharedString::from(format!("chart-{title}")))
                .w_full()
                .child(plot)
                .on_mouse_move(move |event, _, cx| {
                    let mut st = hover_state.lock().unwrap();
                    let b = st.bounds;
                    let f = f32::from((event.position.x - b.left()) / b.size.width.max(px(1.)))
                        .clamp(0., 1.) as f64;
                    let x = min_x + f * (max_x - min_x);
                    let mut labels = vec![];
                    for (name, _, points) in &hover_series {
                        let i = points
                            .partition_point(|(px, _)| *px < x)
                            .min(points.len().saturating_sub(1));
                        if let Some((px, y)) = points.get(i) {
                            if px.is_finite() && y.is_finite() {
                                labels.push(format!("{name}: {px:.6}, {y:.6}"));
                            }
                        }
                    }
                    st.hover = labels.join(" · ");
                    drop(st);
                    cx.refresh_windows();
                })
                .on_scroll(move |event, _, cx| {
                    if !(event.modifiers.control || event.modifiers.platform) {
                        return;
                    }
                    let delta = f32::from(event.delta.pixel_delta(px(16.)).y);
                    let half = (max_x - min_x) * if delta > 0. { 0.8 } else { 1.25 } / 2.;
                    let center = (max_x + min_x) / 2.;
                    if half.is_finite() && half > f64::EPSILON {
                        zoom_state.lock().unwrap().range = Some((center - half, center + half));
                        cx.stop_propagation();
                        cx.refresh_windows();
                    }
                })
                .on_mouse_down(MouseButton::Left, move |event, _, cx| {
                    if event.click_count == 2 {
                        reset_state.lock().unwrap().range = None;
                        cx.refresh_windows();
                    }
                }),
        )
        .into_any_element()
}
pub fn rows(value: &Value, x: &str, y: &str) -> Vec<(f64, f64)> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .map(|p| {
            (
                p[x].as_f64().unwrap_or(f64::NAN),
                p[y].as_f64().unwrap_or(f64::NAN),
            )
        })
        .collect()
}
pub fn spectrum(value: &Value) -> Vec<(f64, f64)> {
    let xs = value["wavelengthNm"].as_array();
    let ys = value["intensity"].as_array();
    match (xs, ys) {
        (Some(xs), Some(ys)) => xs
            .iter()
            .zip(ys)
            .map(|(x, y)| {
                (
                    x.as_f64().unwrap_or(f64::NAN),
                    y.as_f64().unwrap_or(f64::NAN),
                )
            })
            .collect(),
        _ => vec![],
    }
}

#[cfg(test)]
mod drawing_tests {
    use super::*;

    #[::core::prelude::v1::test]
    fn nonuniform_peak_curve_stays_inside_measured_vertical_range() {
        // The narrow peak next to a long wavelength gap exposes cubic overshoot.
        let points =
            [(0., 40.), (0.1, 0.), (0.2, 0.1), (90., 39.)].map(|(x, y)| point(px(x), px(y)));
        // Fill tessellation measures the curve itself without stroke join expansion.
        let mut path = PathBuilder::fill();
        trace_curve(&mut path, &points, true);
        let path = path.build().expect("finite monotone curve");
        assert!(!path.vertices.is_empty());
        for vertex in &path.vertices {
            let y = f32::from(vertex.xy_position.y);
            assert!(
                y.is_finite() && (-0.0001..=40.0001).contains(&y),
                "overshoot: {y}"
            );
        }
    }

    #[::core::prelude::v1::test]
    fn current_plan_hint_cannot_hide_measured_custom_points() {
        let series = vec![("power".into(), 0, vec![(3., 0.3), (20., 2.)])];
        assert_eq!(horizontal_domain(&series, Some((2., 12.))), (2., 20.));
        assert_eq!(horizontal_domain(&[], Some((2., 12.))), (2., 12.));
    }

    #[::core::prelude::v1::test]
    fn chart_domains_keep_negative_samples_and_ignore_invalid_pairs() {
        let series = vec![(
            "power".into(),
            0,
            vec![(0., -2.), (1., 3.), (f64::NAN, 99.)],
        )];
        let (lo, hi) = domain(&series, 1);
        assert!(lo < -2. && hi > 3. && hi < 4.);
        assert_eq!(domain(&[], 0), (0., 1.));
    }
}
