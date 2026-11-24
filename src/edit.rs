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

/// The ID identifies the entity; the revision belongs to the observation that authorized the edit.
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RowSelector {
    pub collection: String,
    pub id_field: String,
    pub id: Value,
    pub revision_field: String,
    pub revision: u64,
}

pub fn deserialize_row<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<RowSelector>, D::Error> {
    RowSelector::deserialize(deserializer).map(Some)
}

fn valid_pointer(path: &str) -> bool {
    (path.is_empty() || path.starts_with('/'))
        && path
            .split('~')
            .skip(1)
            .all(|part| part.starts_with(['0', '1']))
}

fn field_pointer(field: &str) -> String {
    format!("/{}", field.replace('~', "~0").replace('/', "~1"))
}

pub fn apply_to_row(
    document: &Value,
    target: &RowSelector,
    changes: &[Change],
) -> Result<Value, String> {
    if !valid_pointer(&target.collection)
        || target.id_field.is_empty()
        || target.revision_field.is_empty()
        || target.id_field == target.revision_field
        || !(target.id.as_str().is_some_and(|s| !s.is_empty())
            || target.id.is_i64()
            || target.id.is_u64())
    {
        return Err("invalid row identity, revision field or collection pointer".into());
    }
    let rows = document
        .pointer(&target.collection)
        .and_then(Value::as_array)
        .ok_or("row collection is absent or is not an array")?;
    let mut matches = rows
        .iter()
        .enumerate()
        .filter(|(_, row)| row.get(&target.id_field) == Some(&target.id));
    let (index, row) = matches.next().ok_or("row ID is absent")?;
    if matches.next().is_some() {
        return Err("row ID is ambiguous".into());
    }
    if row.get(&target.revision_field).and_then(Value::as_u64) != Some(target.revision) {
        return Err("row revision is missing or has changed".into());
    }
    let identity = field_pointer(&target.id_field);
    let revision = field_pointer(&target.revision_field);
    if changes
        .iter()
        .any(|c| c.path == identity || c.path == revision || c.path.split('/').count() != 2)
    {
        return Err(
            "row changes must target data fields directly; ID and revision are reserved".into(),
        );
    }
    let mut updated = apply(row, changes)?;
    let mut next = document.clone();
    if updated != *row {
        let version = target
            .revision
            .checked_add(1)
            .ok_or("row revision exhausted")?;
        updated[&target.revision_field] = version.into();
        next.pointer_mut(&target.collection)
            .and_then(|v| v.get_mut(index))
            .ok_or("row collection is unavailable")?
            .clone_from(&updated);
    }
    Ok(next)
}

pub fn apply(document: &Value, changes: &[Change]) -> Result<Value, String> {
    if changes.is_empty() {
        return Err("provide at least one change".into());
    }
    let mut seen = BTreeSet::new();
    let mut next = document.clone();
    for change in changes {
        let path = &change.path;
        if path.is_empty() || !valid_pointer(path) {
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
