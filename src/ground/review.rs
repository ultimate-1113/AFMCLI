//! Check a compact-source answer without disclosing the proposed value to the second reader.
use crate::{
    contract,
    document::{self, Evidence, Span},
    engine::{Engine, Run, request},
    fm::Request,
};
use serde_json::{Value, json};
use std::collections::BTreeSet;

fn review_request(question: &str, context: &Value) -> Request {
    let guidance = if context.get("log").is_some() {
        "Read the log in order. In each numeric table, alternate its literal parts with the digit strings from a row to recover the original notice. Track the exact subject and property in the question through any later amendments. In basis copy one exact clause that assigns the value. In update copy one later clause that changes or withdraws that fact, otherwise leave it empty. In value give only the established answer as printed. Preserve digits and leading zeroes. Leave value empty for an unresolved or withdrawn fact."
    } else {
        "Read this record in order for the subject, attribute and time in the question. In basis copy one exact clause stating the applicable value, without joining passages. In update quote a later decision about that fact if present, otherwise leave it empty. A decision about another group or another attribute leaves the fact unchanged. In value give only the requested name, place, date or amount as written in the applicable clause. A cancelled or unapproved fact has no value. In sequence [i,n] repeats dictionary[i] n times. Excerpts have gaps."
    };
    request(
        guidance,
        json!({"question":question,"material":context}),
        Some(contract::object([
            ("basis".into(), json!({"type":"string"})),
            ("update".into(), json!({"type":"string"})),
            ("value".into(), json!({"type":"string"})),
        ])),
    )
}

fn notice_request(question: &str, notice: &str) -> Request {
    request(
        "Which effect does this notice have on the subject, property and time asked about? Choose keep for a different subject or property, or when the previous arrangement stands. Choose unset only for the requested subject when the notice withdraws its decision without a replacement or leaves it pending. Choose set if it supplies a definite new value for that subject. For set copy the new value only. Otherwise return an empty value.",
        json!({"question":question,"notice":notice}),
        Some(contract::object([
            (
                "effect".into(),
                json!({"type":"string","enum":["unset","set","keep"]}),
            ),
            ("value".into(), json!({"type":"string"})),
        ])),
    )
}

fn source_quotes<'a>(
    parts: &[(usize, &'a str)],
    quote: &str,
) -> Result<Vec<(usize, &'a str)>, String> {
    let spans = document::locate_parts(parts, quote).map_err(|_| {
        "copy one contiguous phrase from the original for each quote; do not join separate sentences or add a speaker".to_owned()
    })?;
    Ok(spans
        .into_iter()
        .map(|s| {
            // locate_parts only returns offsets inside one retained range.
            let (base, text) = parts[parts.partition_point(|p| p.0 <= s.start_byte) - 1];
            (s.start_byte, &text[s.start_byte - base..s.end_byte - base])
        })
        .collect())
}

struct Reading<'a> {
    value: String,
    basis: BTreeSet<Span>,
    latest: Option<(usize, &'a str)>,
}

fn reading<'a>(
    v: &Value,
    parts: &[(usize, &'a str)],
    proposal: &str,
) -> Result<Option<Reading<'a>>, String> {
    let value = v["value"].as_str().unwrap();
    // A disagreement is an outcome. Repairing a citation after disagreement
    // can turn a rejected answer into an apparently supported one.
    if value.is_empty() || document::locate(proposal, 0, value).is_err() {
        return Ok(None);
    }
    let mut quotes = source_quotes(parts, v["basis"].as_str().unwrap())?;
    let basis = document::locate_parts(&quotes, value);
    let change = v["update"].as_str().unwrap();
    let latest = if change.is_empty() {
        return Ok(Some(Reading {
            value: value.into(),
            basis: basis?,
            latest: None,
        }));
    } else {
        quotes.extend(source_quotes(parts, change)?);
        // At least one of the two verified clauses must contain the claimed value.
        document::locate_parts(&quotes, value)?;
        quotes
            .into_iter()
            .max_by_key(|(offset, text)| (offset + text.len(), text.len()))
    };
    Ok(Some(Reading {
        value: value.into(),
        basis: basis.unwrap_or_default(),
        latest,
    }))
}

pub(super) fn confirm(
    engine: &mut Engine<'_>,
    question: &str,
    context: &Value,
    parts: &[(usize, &str)],
    proposal: &str,
) -> Run<Option<Evidence>> {
    let check = engine.structured(review_request(question, context), |v| {
        reading(v, parts, proposal)
    })?;
    let Some(Reading {
        value,
        basis,
        latest,
    }) = check
    else {
        return Ok(None);
    };
    let spans = if let Some((start, notice)) = latest {
        engine.structured(notice_request(question, notice), |v| {
            let assigned = v["value"].as_str().unwrap();
            let spans = match v["effect"].as_str().unwrap() {
                "keep" if assigned.is_empty() && !basis.is_empty() => Some(basis.clone()),
                "set"
                    if document::locate(assigned, 0, &value).is_ok()
                        && document::locate(notice, start, assigned).is_ok() =>
                {
                    document::locate(notice, start, &value).ok()
                }
                _ => None,
            };
            Ok(spans)
        })?
    } else {
        Some(basis)
    };
    Ok(spans.map(|spans| Evidence { value, spans }))
}
