//! Report presentation defaults mirror the previous operator workflow.
use serde_json::{Value, json};
pub fn suggested(point: &Value, key: &str) -> String {
    let Some(v) = point[key].as_f64().filter(|v| v.is_finite()) else {
        return "N/A".into();
    };
    match key {
        "currentA" => v.to_string(),
        "powerW" => format!("{v:.1}"),
        "voltageV" | "efficiencyPercent" => format!("{v:.2}"),
        _ => format!("{v:.3}")
            .trim_end_matches('0')
            .trim_end_matches('.')
            .to_string(),
    }
}
pub fn defaults(inspection: &Value, kind: &str, current: f64) -> Value {
    let preferences = &inspection["preferences"][kind];
    let point = inspection["points"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|p| {
            p["currentA"]
                .as_f64()
                .is_some_and(|x| (x - current).abs() < 1e-9)
        })
        .cloned()
        .unwrap_or(Value::Null);
    let custom = preferences["customFields"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let mut fields = json!({});
    let mut defs = inspection["fieldDefinitions"][kind]
        .as_array()
        .cloned()
        .unwrap_or_default();
    defs.extend(custom.iter().cloned());
    for f in defs {
        let key = f["key"].as_str().unwrap_or("");
        let value = if let Some(measured) = f["measuredKey"].as_str() {
            suggested(&point, measured)
        } else {
            preferences["manualValues"][key]
                .as_str()
                .unwrap_or("")
                .into()
        };
        let include = preferences["selectedFields"]
            .as_array()
            .is_some_and(|a| a.iter().any(|k| k == key));
        fields[key] = json!({"include":include,"value":value});
    }
    let peak = point["peakWavelengthNm"]
        .as_f64()
        .or(point["centroidNm"].as_f64())
        .or_else(|| {
            Some(
                (inspection["spectrumWavelengthRange"]["minimum"].as_f64()?
                    + inspection["spectrumWavelengthRange"]["maximum"].as_f64()?)
                    / 2.,
            )
        })
        .unwrap_or(976.);
    let max = inspection["points"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|p| p["currentA"].as_f64().is_some_and(|x| x <= current + 1e-9))
        .filter_map(|p| p["spectrumPeakCounts"].as_f64())
        .fold(1., f64::max);
    let exponent = 10f64.powf(max.log10().floor());
    let normalized = max / exponent;
    let ceiling = if normalized <= 1. {
        1.
    } else if normalized <= 2. {
        2.
    } else if normalized <= 5. {
        5.
    } else {
        10.
    };
    let mut axis = json!({"mode":"counts","minimum":0,"maximum":ceiling*exponent,"wavelengthMinimum":peak-15.,"wavelengthMaximum":peak+15.});
    if let Some(saved) = preferences["spectrumAxis"].as_object() {
        for (k, v) in saved {
            axis[k] = v.clone();
        }
    }
    json!({"sourcePath":inspection["sourcePath"],"reportType":kind,"reportLanguage":preferences["reportLanguage"].as_str().unwrap_or("en"),"productName":inspection["productName"],"sn":inspection["sn"],"operatingCurrentA":current,"fields":fields,"customFields":custom,"encircledEnergyNa":preferences["encircledEnergyNa"].as_str().unwrap_or("0.17"),"legacyCompletionConfirmed":false,"spectrumAxis":axis})
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn suggested_format_preserves_operator_precision() {
        let p = json!({"currentA":2.5,"powerW":12.345,"voltageV":2.3456,"fwhmNm":0.120});
        assert_eq!(suggested(&p, "currentA"), "2.5");
        assert_eq!(suggested(&p, "powerW"), "12.3");
        assert_eq!(suggested(&p, "voltageV"), "2.35");
        assert_eq!(suggested(&p, "fwhmNm"), "0.12");
        assert_eq!(suggested(&p, "pibPercent"), "N/A");
    }
}
