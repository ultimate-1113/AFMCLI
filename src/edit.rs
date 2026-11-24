//! Atomic preview of caller-authorized scalar operations on a JSON document.
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::BTreeSet;

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Change {
    pub path: String,
    pub expect: Value,
    pub op: Op,
    pub value: Value,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Op {
    Set,
    Add,
}

pub fn apply(document: &Value, changes: &[Change]) -> Result<Value, String> {
    if changes.is_empty() {
        return Err("provide at least one change".into());
    }
    let mut seen = BTreeSet::new();
    let mut next = document.clone();
    for change in changes {
        let path = &change.path;
        if !path.starts_with('/') || path.split('~').skip(1).any(|s| !s.starts_with(['0', '1'])) {
            return Err("invalid non-root JSON Pointer".into());
        }
        if !seen.insert(path) {
            return Err(format!("duplicate path {path}"));
        }
        let old = document
            .pointer(path)
            .ok_or_else(|| format!("path is absent: {path}"))?;
        if old.is_array() || old.is_object() || change.value.is_array() || change.value.is_object()
        {
            return Err(format!("expected scalar at {path}"));
        }
        if old != &change.expect {
            return Err(format!("precondition failed at {path}"));
        }
        let new = match change.op {
            Op::Set => change.value.clone(),
            Op::Add => {
                let a = old
                    .as_i64()
                    .ok_or("addition requires signed 64-bit integers")?;
                let b = change
                    .value
                    .as_i64()
                    .ok_or("addition requires signed 64-bit integers")?;
                json!(a.checked_add(b).ok_or("integer overflow")?)
            }
        };
        *next.pointer_mut(path).ok_or("overlapping operations")? = new;
    }
    Ok(next)
}
