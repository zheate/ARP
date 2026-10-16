//! Allocation-light realtime calculations shared with the Python device host.
//!
//! The exported functions intentionally use a small C ABI so the existing
//! Python backend can adopt the Rust hot path without changing device
//! ownership or the Tauri bridge contract.

use std::slice;

pub const ABI_VERSION: u32 = 2;

#[repr(C)]
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct SpectrumStats {
    pub peak_wavelength_nm: f64,
    pub peak_intensity: f64,
    pub centroid_nm: f64,
    pub fwhm_nm: f64,
}

#[repr(C)]
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct SampleSummary {
    pub latest: f64,
    pub mean: f64,
    pub minimum: f64,
    pub maximum: f64,
    pub standard_deviation: f64,
    pub rms: f64,
}

fn invalid_spectrum_stats() -> SpectrumStats {
    SpectrumStats {
        peak_wavelength_nm: f64::NAN,
        peak_intensity: f64::NAN,
        centroid_nm: f64::NAN,
        fwhm_nm: f64::NAN,
    }
}

fn inputs_are_valid(x: &[f64], y: &[f64]) -> bool {
    !x.is_empty()
        && x.len() == y.len()
        && x.iter()
            .zip(y)
            .all(|(x_value, y_value)| x_value.is_finite() && y_value.is_finite())
}

fn standard_deviation(values: &[f64]) -> f64 {
    let mean = values.iter().sum::<f64>() / values.len() as f64;
    let variance = values
        .iter()
        .map(|value| {
            let delta = value - mean;
            delta * delta
        })
        .sum::<f64>()
        / values.len() as f64;
    variance.sqrt()
}

fn legacy_linear_crossing(x: &[f64], y: &[f64], left_index: usize, target: f64) -> Option<f64> {
    let x1 = x[left_index];
    let x2 = x[left_index + 1];
    let y1 = y[left_index];
    let y2 = y[left_index + 1];
    if x2 == x1 || y2 == y1 {
        return None;
    }
    let slope = (y2 - y1) / (x2 - x1);
    if !slope.is_finite() || slope == 0.0 {
        return None;
    }
    let intercept = y1 - slope * x1;
    let crossing = (target - intercept) / slope;
    crossing.is_finite().then_some(crossing)
}

fn absolute_half_max_crossings(x: &[f64], y: &[f64]) -> Option<(f64, f64)> {
    if x.len() < 3 || x.len() != y.len() {
        return None;
    }
    let maximum = y.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let half_maximum = maximum / 2.0;
    let mut first = None;
    let mut last = None;
    for index in 0..y.len() - 1 {
        if (y[index] - half_maximum) * (y[index + 1] - half_maximum) <= 0.0 {
            first.get_or_insert(index);
            last = Some(index);
        }
    }
    let (first, last) = (first?, last?);
    if first == last {
        return None;
    }
    Some((
        legacy_linear_crossing(x, y, first, half_maximum)?,
        legacy_linear_crossing(x, y, last, half_maximum)?,
    ))
}

fn calculate_central(x: &[f64], y: &[f64]) -> f64 {
    let Some((left, right)) = absolute_half_max_crossings(x, y) else {
        return f64::NAN;
    };
    let central = (left + right) / 2.0;
    if central.is_finite() {
        central
    } else {
        f64::NAN
    }
}

fn calculate_fwhm(x: &[f64], y: &[f64]) -> f64 {
    let Some((left, right)) = absolute_half_max_crossings(x, y) else {
        return f64::NAN;
    };
    let fwhm = right - left;
    if fwhm.is_finite() {
        fwhm
    } else {
        f64::NAN
    }
}

fn signal_region(y: &[f64], peak_index: usize) -> (usize, usize) {
    const MOVE_LENGTH: usize = 7;
    let mut left_index = 0;
    let left_stop = peak_index.saturating_sub(MOVE_LENGTH - 1);
    for index in 0..left_stop {
        if standard_deviation(&y[index..index + MOVE_LENGTH]) > 200.0 {
            break;
        }
        left_index = index;
    }

    let mut right_index = y.len() - 1;
    let right_stop = peak_index + MOVE_LENGTH;
    for index in (right_stop + 1..=y.len()).rev() {
        if standard_deviation(&y[index - MOVE_LENGTH..index]) > 200.0 {
            break;
        }
        right_index = index - 1;
    }
    (left_index, right_index)
}

fn calculate_centroid(x: &[f64], y: &[f64], peak_index: usize) -> f64 {
    let (left_index, right_index) = signal_region(y, peak_index);
    if left_index > peak_index || peak_index > right_index || right_index >= y.len() {
        return f64::NAN;
    }
    let noise = (y[left_index] + y[right_index]) / 2.0;
    let mut weight_total = 0.0;
    let mut weighted_x = 0.0;
    for index in left_index..=right_index {
        let weight = y[index] - noise;
        weight_total += weight;
        weighted_x += x[index] * weight;
    }
    if !weight_total.is_finite() || weight_total.abs() < 0.01 {
        return f64::NAN;
    }
    let centroid = weighted_x / weight_total;
    if centroid.is_finite() {
        centroid
    } else {
        f64::NAN
    }
}

pub fn spectrum_stats(wavelength: &[f64], intensity: &[f64]) -> SpectrumStats {
    if !inputs_are_valid(wavelength, intensity) {
        return invalid_spectrum_stats();
    }
    let peak_index = intensity
        .iter()
        .enumerate()
        .max_by(|left, right| left.1.total_cmp(right.1).then_with(|| right.0.cmp(&left.0)))
        .map(|(index, _)| index)
        .unwrap_or(0);
    SpectrumStats {
        peak_wavelength_nm: calculate_central(wavelength, intensity),
        peak_intensity: intensity[peak_index],
        centroid_nm: calculate_centroid(wavelength, intensity, peak_index),
        fwhm_nm: calculate_fwhm(wavelength, intensity),
    }
}

fn median3(left: f64, middle: f64, right: f64) -> f64 {
    if left > middle {
        if middle > right {
            middle
        } else if left > right {
            right
        } else {
            left
        }
    } else if left > right {
        left
    } else if middle > right {
        right
    } else {
        middle
    }
}

fn median_filter_three(values: &[f64]) -> Vec<f64> {
    (0..values.len())
        .map(|index| {
            let left = index.checked_sub(1).map_or(0.0, |offset| values[offset]);
            let right = values.get(index + 1).copied().unwrap_or(0.0);
            median3(left, values[index], right)
        })
        .collect()
}


fn trapezoidal_integral(x: &[f64], y: &[f64]) -> f64 {
    x.windows(2)
        .zip(y.windows(2))
        .map(|(x_pair, y_pair)| (x_pair[1] - x_pair[0]) * (y_pair[0] + y_pair[1]) / 2.0)
        .sum()
}

fn slice_median(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let mut sorted = values.to_vec();
    sorted.sort_by(|a, b| a.total_cmp(b));
    let mid = sorted.len() / 2;
    if sorted.len() % 2 == 0 {
        (sorted[mid - 1] + sorted[mid]) / 2.0
    } else {
        sorted[mid]
    }
}

pub fn power_in_band(
    wavelength: &[f64],
    intensity: &[f64],
    center_nm: f64,
    half_range_nm: f64,
) -> f64 {
    if !inputs_are_valid(wavelength, intensity) {
        return f64::NAN;
    }
    if !center_nm.is_finite() || !half_range_nm.is_finite() || half_range_nm < 0.0 {
        return f64::NAN;
    }

    let filtered = median_filter_three(intensity);
    let effective_center = if center_nm < 1e-6 {
        let peak_index = filtered
            .iter()
            .enumerate()
            .max_by(|left, right| left.1.total_cmp(right.1).then_with(|| right.0.cmp(&left.0)))
            .map(|(index, _)| index)
            .unwrap_or(0);
        wavelength[peak_index]
    } else {
        center_nm
    };
    if !effective_center.is_finite() {
        return f64::NAN;
    }

    const ANALYSIS_SPAN_NM: f64 = 20.0;
    let min_w = effective_center - ANALYSIS_SPAN_NM;
    let max_w = effective_center + ANALYSIS_SPAN_NM;

    let mut sub_w = Vec::new();
    let mut sub_y = Vec::new();
    for (&w, &y) in wavelength.iter().zip(filtered.iter()) {
        if w >= min_w && w <= max_w {
            sub_w.push(w);
            sub_y.push(y);
        }
    }
    if sub_w.is_empty() {
        sub_w = wavelength.to_vec();
        sub_y = filtered;
    }
    if sub_w.len() < 2 {
        return 0.0;
    }

    let baseline = slice_median(&sub_y);
    let abs_diffs: Vec<f64> = sub_y.iter().map(|&v| (v - baseline).abs()).collect();
    let noise_sigma = 1.4826 * slice_median(&abs_diffs);
    let threshold = (2.0 * noise_sigma).max(1e-9);

    let corrected_y: Vec<f64> = sub_y
        .iter()
        .map(|&v| {
            let diff = v - baseline;
            if diff >= threshold {
                diff
            } else {
                0.0
            }
        })
        .collect();

    let total_power = trapezoidal_integral(&sub_w, &corrected_y);
    if total_power < 1e-6 {
        return 0.0;
    }

    let band_min = effective_center - half_range_nm;
    let band_max = effective_center + half_range_nm;
    let mut band_w = Vec::new();
    let mut band_y = Vec::new();
    for (&w, &y) in sub_w.iter().zip(corrected_y.iter()) {
        if w >= band_min && w <= band_max {
            band_w.push(w);
            band_y.push(y);
        }
    }
    if band_w.len() < 2 {
        return 0.0;
    }
    let band_power = trapezoidal_integral(&band_w, &band_y);
    (band_power / total_power).clamp(0.0, 1.0)
}

pub fn sample_summary(values: &[f64]) -> Option<SampleSummary> {
    if values.is_empty() || values.iter().any(|value| !value.is_finite()) {
        return None;
    }
    let latest = *values.last()?;
    let mut sum = 0.0;
    let mut square_sum = 0.0;
    let mut minimum = f64::INFINITY;
    let mut maximum = f64::NEG_INFINITY;
    for value in values {
        sum += value;
        square_sum += value * value;
        minimum = minimum.min(*value);
        maximum = maximum.max(*value);
    }
    let mean = sum / values.len() as f64;
    let variance = values
        .iter()
        .map(|value| {
            let delta = value - mean;
            delta * delta
        })
        .sum::<f64>()
        / values.len() as f64;
    Some(SampleSummary {
        latest,
        mean,
        minimum,
        maximum,
        standard_deviation: variance.sqrt(),
        rms: (square_sum / values.len() as f64).sqrt(),
    })
}

#[no_mangle]
pub extern "C" fn arp_realtime_core_abi_version() -> u32 {
    ABI_VERSION
}

unsafe fn ffi_slices<'a>(
    first: *const f64,
    second: *const f64,
    len: usize,
) -> Option<(&'a [f64], &'a [f64])> {
    if len == 0 || first.is_null() || second.is_null() {
        return None;
    }
    Some((unsafe { slice::from_raw_parts(first, len) }, unsafe {
        slice::from_raw_parts(second, len)
    }))
}

/// Returns 0 on success and -1 for invalid pointers or an empty input.
///
/// # Safety
///
/// `wavelength` and `intensity` must each reference `len` readable `f64`
/// values, and `output` must reference writable memory for one
/// [`SpectrumStats`]. The input and output regions must remain valid and must
/// not be concurrently mutated for the duration of the call.
#[no_mangle]
pub unsafe extern "C" fn arp_calculate_spectrum_stats(
    wavelength: *const f64,
    intensity: *const f64,
    len: usize,
    output: *mut SpectrumStats,
) -> i32 {
    if output.is_null() {
        return -1;
    }
    let Some((wavelength, intensity)) = (unsafe { ffi_slices(wavelength, intensity, len) }) else {
        unsafe { output.write(invalid_spectrum_stats()) };
        return -1;
    };
    unsafe { output.write(spectrum_stats(wavelength, intensity)) };
    0
}

/// Returns 0 on success and -1 for invalid pointers or an empty input.
///
/// # Safety
///
/// `wavelength` and `intensity` must each reference `len` readable `f64`
/// values, and `output` must reference writable memory for one `f64`. The
/// input and output regions must remain valid and must not be concurrently
/// mutated for the duration of the call.
#[no_mangle]
pub unsafe extern "C" fn arp_calculate_pib(
    wavelength: *const f64,
    intensity: *const f64,
    len: usize,
    center_nm: f64,
    half_range_nm: f64,
    output: *mut f64,
) -> i32 {
    if output.is_null() {
        return -1;
    }
    let Some((wavelength, intensity)) = (unsafe { ffi_slices(wavelength, intensity, len) }) else {
        unsafe { output.write(f64::NAN) };
        return -1;
    };
    unsafe {
        output.write(power_in_band(
            wavelength,
            intensity,
            center_nm,
            half_range_nm,
        ))
    };
    0
}

/// Returns 0 on success and -1 for invalid, empty, or non-finite input.
///
/// # Safety
///
/// `values` must reference `len` readable `f64` values, and `output` must
/// reference writable memory for one [`SampleSummary`]. The input and output
/// regions must remain valid and must not be concurrently mutated for the
/// duration of the call.
#[no_mangle]
pub unsafe extern "C" fn arp_summarize_samples(
    values: *const f64,
    len: usize,
    output: *mut SampleSummary,
) -> i32 {
    if values.is_null() || output.is_null() || len == 0 {
        return -1;
    }
    let values = unsafe { slice::from_raw_parts(values, len) };
    let Some(summary) = sample_summary(values) else {
        return -1;
    };
    unsafe { output.write(summary) };
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn assert_close(actual: f64, expected: f64) {
        assert!((actual - expected).abs() <= 1e-9, "{actual} != {expected}");
    }

    #[test]
    fn calculates_peak_centroid_and_fwhm() {
        let wavelength = [974.0, 975.0, 976.0, 977.0, 978.0];
        let intensity = [0.0, 5.0, 10.0, 5.0, 0.0];
        let result = spectrum_stats(&wavelength, &intensity);
        assert_close(result.peak_wavelength_nm, 976.0);
        assert_close(result.peak_intensity, 10.0);
        assert_close(result.centroid_nm, 976.0);
        assert_close(result.fwhm_nm, 2.0);
    }

    #[test]
    fn rejects_non_finite_spectrum() {
        let result = spectrum_stats(&[975.0, 976.0, 977.0], &[0.0, f64::NAN, 0.0]);
        assert!(result.peak_wavelength_nm.is_nan());
        assert!(result.peak_intensity.is_nan());
    }

    #[test]
    fn calculates_population_summary() {
        let result = sample_summary(&[1.0, 2.0, 3.0, 4.0]).expect("summary");
        assert_close(result.latest, 4.0);
        assert_close(result.mean, 2.5);
        assert_close(result.minimum, 1.0);
        assert_close(result.maximum, 4.0);
        assert_close(result.standard_deviation, 1.118_033_988_749_895);
        assert_close(result.rms, 2.738_612_787_525_830_6);
    }

    #[test]
    fn calculates_power_in_band_for_centered_signal() {
        let wavelength = [973.0, 974.0, 975.0, 976.0, 977.0, 978.0, 979.0];
        let intensity = [0.0, 0.0, 2.0, 10.0, 2.0, 0.0, 0.0];
        let result = power_in_band(&wavelength, &intensity, 976.0, 1.5);
        assert!(result.is_finite());
        assert!((0.0..=1.0).contains(&result));
    }
}
