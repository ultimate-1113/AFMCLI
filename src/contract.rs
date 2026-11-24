//! A closed, offline subset of JSON Schema shared by model guidance and local checks.
use serde_json::{Value, json};
use std::collections::BTreeSet;

pub fn object(pairs: impl IntoIterator<Item = (String, Value)>) -> Value {
    let props: serde_json::Map<_, _> = pairs.into_iter().collect();
    let required: Vec<_> = props.keys().collect();
    json!({"type":"object","properties":props,"required":required,"additionalProperties":false})
}

fn kind(s: &Value) -> &str {
    s["type"].as_str().unwrap_or("")
}
pub fn check(s: &Value) -> Result<(), String> {
    if kind(s) != "object" {
        return Err("the root schema must be an object".into());
    }
    inspect(s, 0)
}
fn inspect(s: &Value, d: usize) -> Result<(), String> {
    if d > 12 {
        return Err("schema depth exceeds 12".into());
    }
    let map = s.as_object().ok_or("schema must be an object")?;
    let extra: &[&str] = match kind(s) {
        "object" => &["properties", "required", "additionalProperties"],
        "array" => &["items"],
        "string" => &["enum"],
        "integer" | "number" | "boolean" => &[],
        t => return Err(format!("unsupported schema type {t:?}")),
    };
    for k in map.keys() {
        if k != "type" && k != "description" && !extra.contains(&k.as_str()) {
            return Err(format!("unsupported schema keyword {k}"));
        }
    }
    if map.get("description").is_some_and(|v| !v.is_string()) {
        return Err("description must be text".into());
    }
    match kind(s) {
        "object" => {
            let p = s["properties"].as_object().ok_or("missing properties")?;
            let req = s["required"]
                .as_array()
                .ok_or("supply the required array, even if empty")?;
            if s["additionalProperties"] != false {
                return Err("set additionalProperties to false".into());
            }
            let mut seen = BTreeSet::new();
            for name in req {
                let key = name.as_str().ok_or("invalid required name")?;
                if !p.contains_key(key) || !seen.insert(key) {
                    return Err("unknown or duplicate required name".into());
                }
            }
            for v in p.values() {
                inspect(v, d + 1)?;
            }
        }
        "array" => inspect(&s["items"], d + 1)?,
        "string" if map.contains_key("enum") => {
            let choices = s["enum"].as_array().ok_or("enum must be an array")?;
            let mut seen = BTreeSet::new();
            if choices.is_empty()
                || choices
                    .iter()
                    .any(|v| v.as_str().is_none_or(|x| !seen.insert(x)))
            {
                return Err("enum requires distinct strings".into());
            }
        }
        _ => {}
    }
    Ok(())
}
pub fn validate(s: &Value, v: &Value) -> Result<(), String> {
    validate_at(s, v, "$")
}
fn validate_at(s: &Value, v: &Value, path: &str) -> Result<(), String> {
    let valid = match kind(s) {
        "object" => v.is_object(),
        "array" => v.is_array(),
        "string" => v.is_string(),
        "integer" => v.is_i64() || v.is_u64(),
        "number" => v.is_number(),
        "boolean" => v.is_boolean(),
        _ => false,
    };
    if !valid {
        return Err(format!("{path} must be {}", kind(s)));
    }
    if let Some(fields) = v.as_object() {
        let props = s["properties"].as_object().ok_or("invalid object schema")?;
        for key in s["required"].as_array().ok_or("invalid required")? {
            let k = key.as_str().ok_or("invalid required name")?;
            if !fields.contains_key(k) {
                return Err(format!("{path} lacks required field {k}"));
            }
        }
        for (k, val) in fields {
            let sub = props
                .get(k)
                .ok_or_else(|| format!("{path} has an unexpected field"))?;
            validate_at(sub, val, &format!("{path}.{k}"))?;
        }
    }
    if let Some(a) = v.as_array() {
        for (i, x) in a.iter().enumerate() {
            validate_at(&s["items"], x, &format!("{path}[{i}]"))?;
        }
    }
    if s["enum"].as_array().is_some_and(|a| !a.contains(v)) {
        return Err(format!("{path} is outside the allowed choices"));
    }
    Ok(())
}
