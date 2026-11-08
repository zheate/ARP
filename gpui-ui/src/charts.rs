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
pub fn chart(
    title: &str,
    x_label: &str,
    y_label: &str,
    series: Vec<(String, u32, Vec<(f64, f64)>)>,
    cx: &App,
) -> AnyElement {
    let bg = cx.theme().background;
    let grid = cx.theme().border;
    let muted = cx.theme().muted_foreground;
    let mut min_x = f64::INFINITY;
    let mut max_x = f64::NEG_INFINITY;
    let mut min_y = f64::INFINITY;
    let mut max_y = f64::NEG_INFINITY;
    for (_, _, points) in &series {
        for &(x, y) in points {
            if x.is_finite() && y.is_finite() {
                min_x = min_x.min(x);
                max_x = max_x.max(x);
                min_y = min_y.min(y);
                max_y = max_y.max(y);
            }
        }
    }
    let empty = !min_x.is_finite();
    if empty {
        min_x = 0.;
        max_x = 1.;
        min_y = 0.;
        max_y = 1.;
    }
    if max_x <= min_x {
        max_x = min_x + 1.;
    }
    if max_y <= min_y {
        let pad = min_y.abs().max(1.) * 0.02;
        min_y -= pad;
        max_y += pad;
        if y_label.contains("counts") {
            min_y = 0.;
        }
    }
    let pad = (max_y - min_y) * 0.05;
    min_y -= pad;
    max_y += pad;
    if y_label.contains("counts") {
        min_y = 0.;
    }
    let state = cx
        .global::<ChartRegistry>()
        .0
        .lock()
        .unwrap()
        .entry(title.to_string())
        .or_default()
        .clone();
    let saved_range = { state.lock().unwrap().range };
    if let Some((lo, hi)) = saved_range {
        if lo < max_x && hi > min_x {
            min_x = lo.max(min_x);
            max_x = hi.min(max_x);
        } else {
            state.lock().unwrap().range = None;
        }
    }
    let hover = state.lock().unwrap().hover.clone();
    let paint_state = state.clone();
    let series = Arc::new(series);
    let legends = series
        .iter()
        .map(|(name, color, _)| div().text_sm().text_color(rgb(*color)).child(name.clone()))
        .collect::<Vec<_>>();
    let hover_series = series.clone();
    let graph = canvas(
        |_, _, _| (),
        move |bounds, _, window, _| {
            paint_state.lock().unwrap().bounds = bounds;
            let left = bounds.left() + px(8.);
            let top = bounds.top() + px(8.);
            let w = (bounds.size.width - px(16.)).max(px(1.));
            let h = (bounds.size.height - px(16.)).max(px(1.));
            for i in 0..=4 {
                let mut path = PathBuilder::stroke(px(1.));
                let y = top + h * (i as f32 / 4.);
                path.move_to(point(left, y));
                path.line_to(point(left + w, y));
                if let Ok(p) = path.build() {
                    window.paint_path(p, grid);
                }
            }
            for (_, color, points) in series.iter() {
                let mut path = PathBuilder::stroke(px(1.5));
                let mut pen = false;
                for &(x, y) in points {
                    if !x.is_finite() || !y.is_finite() || x < min_x || x > max_x {
                        pen = false;
                        continue;
                    }
                    let p = point(
                        left + w * ((x - min_x) / (max_x - min_x)) as f32,
                        top + h * (1. - ((y - min_y) / (max_y - min_y)) as f32),
                    );
                    if pen {
                        path.line_to(p);
                    } else {
                        path.move_to(p);
                        pen = true;
                    }
                }
                if let Ok(p) = path.build() {
                    window.paint_path(p, rgb(*color));
                }
            }
        },
    )
    .w_full()
    .h(px(220.));
    div()
        .flex()
        .flex_col()
        .gap_2()
        .p_4()
        .w_full()
        .min_w_0()
        .bg(bg)
        .border_1()
        .border_color(grid)
        .rounded_lg()
        .child(
            div()
                .flex()
                .justify_between()
                .child(title.to_string())
                .child(div().flex().gap_3().children(legends)),
        )
        .child(
            div()
                .text_xs()
                .text_color(muted)
                .child(format!("{y_label}  {min_y:.4} — {max_y:.4}")),
        )
        .child(
            div()
                .id(SharedString::from(format!("chart-{title}")))
                .w_full()
                .child(graph)
                .on_mouse_move({
                    let state = state.clone();
                    move |event, _, cx| {
                        let mut st = state.lock().unwrap();
                        let bounds = st.bounds;
                        let fraction = f32::from(
                            (event.position.x - bounds.left()) / bounds.size.width.max(px(1.)),
                        )
                        .clamp(0., 1.) as f64;
                        let x = min_x + fraction * (max_x - min_x);
                        let mut labels = vec![];
                        for (name, _, points) in hover_series.iter() {
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
                    }
                })
                .on_scroll({
                    let state = state.clone();
                    move |event, _, cx| {
                        if !(event.modifiers.control || event.modifiers.platform) {
                            return;
                        }
                        let delta = f32::from(event.delta.pixel_delta(px(16.)).y);
                        let factor = if delta > 0. { 0.8 } else { 1.25 };
                        let center = (min_x + max_x) / 2.;
                        let half = (max_x - min_x) * factor / 2.;
                        if half.is_finite() && half > f64::EPSILON {
                            state.lock().unwrap().range = Some((center - half, center + half));
                            cx.stop_propagation();
                            cx.refresh_windows();
                        }
                    }
                })
                .on_mouse_down(MouseButton::Left, {
                    let state = state.clone();
                    move |event, _, cx| {
                        if event.click_count == 2 {
                            state.lock().unwrap().range = None;
                            cx.refresh_windows();
                        }
                    }
                }),
        )
        .child(
            div()
                .h(px(16.))
                .text_xs()
                .text_color(muted)
                .child(if hover.is_empty() {
                    "移动光标读取数据 · Ctrl/⌘ + 滚轮缩放 · 双击复位".to_string()
                } else {
                    hover
                }),
        )
        .child(
            div()
                .flex()
                .justify_between()
                .text_xs()
                .text_color(muted)
                .child(format!("{min_x:.3}"))
                .child(if empty {
                    "暂无数据".into()
                } else {
                    x_label.to_string()
                })
                .child(format!("{max_x:.3}")),
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
