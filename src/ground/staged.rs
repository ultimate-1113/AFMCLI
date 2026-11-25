//! Read the question, collect original evidence across all windows, then resolve its state.
use super::{compact, window};
use crate::{
    contract,
    document::{self, Evidence, Segment},
    engine::{
        ContextUsage, Engine, Fail, Output, RetrievalRound, RetrievalUsage, Run, SourceRange,
        request,
    },
    fm::Request,
};
use serde_json::{Value, json};

const WINDOW_ROWS: usize = 72;
const WINDOW_BYTES: usize = 10_000;

fn scope(engine: &mut Engine<'_>, question: &str) -> Run<Value> {
    let req = request(
        "Identify the subject and the attribute requested. Copy short phrases from the question exactly. Use empty strings if implicit. Do not propose an answer.",
        json!({"question":question}),
        Some(contract::object([
            ("subject".into(), json!({"type":"string"})),
            ("attribute".into(), json!({"type":"string"})),
        ])),
    );
    engine.structured(req, |v| {
        let phrase = |key: &str| {
            v[key].as_str().unwrap_or("").trim().to_owned()
        };
        let grounded = |term: String| {
            if term.len() <= 160 && question.contains(&term) { term } else { String::new() }
        };
        Ok(json!({"subject":grounded(phrase("subject")), "attribute":grounded(phrase("attribute"))}))
    })
}

fn original_rows(lines: &[Segment<'_>]) -> Vec<Value> {
    lines.iter().map(|s| json!([s.id, s.text])).collect()
}

fn collect_request(question: &str, scope: &Value, lines: &[Segment<'_>]) -> Request {
    request(
        "Select original row IDs about the question. Include earlier decisions, proposals, cancellations with no replacement, exclusions and later reinstatements even when they give no value. Include nearby context needed to tell similar subjects apart. Do not decide the answer. [] means no relevant rows in this part; [0] means keep the entire part.",
        json!({"question":question,"scope":scope,"rows":original_rows(lines)}),
        Some(contract::object([(
            "ids".into(),
            json!({"type":"array","items":{"type":"integer"}}),
        )])),
    )
}

fn decision_request(question: &str, lines: &[Segment<'_>]) -> Request {
    request(
        "Reconstruct the current decision from these original rows in document order. Match the subject, requested attribute and effective time. An example, proposal, earlier withdrawn assignment, or a different subject's decision does not establish the value. A later explicit decision can replace a withdrawal. Return state=set only for the requested confirmed value: give its original row ID and shortest exact value from that row. Otherwise return state=unset, row=0, value empty. A statement that a value is undecided is not the requested name, place, or number.",
        json!({"question":question,"original_evidence":original_rows(lines)}),
        Some(contract::object([
            (
                "state".into(),
                json!({"type":"string","enum":["set","unset"]}),
            ),
            ("row".into(), json!({"type":"integer"})),
            ("value".into(), json!({"type":"string"})),
        ])),
    )
}

fn resolve(v: &Value, rows: &[Segment<'_>]) -> Result<Option<Evidence>, String> {
    if v["state"].as_str() == Some("unset") {
        return Ok(None);
    }
    let id = v["row"].as_u64().and_then(|n| usize::try_from(n).ok());
    let row = rows
        .iter()
        .find(|s| Some(s.id) == id)
        .ok_or("cite an original evidence row")?;
    let value = v["value"].as_str().ok_or("copy the exact value")?;
    let spans = document::locate(row.text, row.start, value)?;
    Ok(Some(Evidence {
        value: value.to_owned(),
        spans,
    }))
}

fn nearby(keep: &mut [bool], index: usize) {
    let end = (index + 2).min(keep.len());
    keep[index.saturating_sub(1)..end].fill(true);
}

pub(super) fn answer(engine: &mut Engine<'_>, source: &str, question: &str) -> Run<Output> {
    let source_rows = document::segments(source);
    engine.metrics.context = Some(ContextUsage {
        method: "staged_ledger",
        source_segments: source_rows.len(),
        retained_segments: 0,
        selection_windows: 0,
        retrieval: None,
    });
    let scope = scope(engine, question)?;
    let mut keep = vec![false; source_rows.len()];
    let subject = scope["subject"].as_str().unwrap_or("");
    if subject.chars().count() >= 2 {
        let matches: Vec<_> = source_rows
            .iter()
            .filter(|s| s.text.contains(subject))
            .take(33)
            .collect();
        // A rare subject can rescue a missed notice. A common term must not consume the ledger.
        if matches.len() <= 32 {
            for s in matches {
                nearby(&mut keep, s.id - 1);
            }
        }
    }
    let mut pos = 0;
    while pos < source_rows.len() {
        let remaining = &source_rows[pos..];
        let mut bytes = 0;
        let mut rows = 0;
        for row in remaining.iter().take(WINDOW_ROWS) {
            bytes += row.text.len();
            if bytes > WINDOW_BYTES && rows > 0 {
                break;
            }
            rows += 1;
        }
        let n = window(engine, &remaining[..rows], |block| {
            collect_request(question, &scope, block)
        })?;
        let block = &remaining[..n];
        let ids = engine.structured(collect_request(question, &scope, block), |v| {
            compact::selected_indices(v, block)
        })?;
        for index in ids {
            nearby(&mut keep, index);
        }
        engine.metrics.context.as_mut().unwrap().selection_windows += 1;
        pos += n;
    }
    let evidence_rows: Vec<_> = source_rows
        .iter()
        .copied()
        .filter(|r| keep[r.id - 1])
        .collect();
    let usage = engine.metrics.context.as_mut().unwrap();
    usage.retained_segments = evidence_rows.len();
    usage.retrieval = Some(RetrievalUsage {
        rounds: vec![RetrievalRound {
            sent: false,
            ranges: evidence_rows
                .iter()
                .map(|r| SourceRange {
                    document_id: 1,
                    first_segment_id: r.id,
                    last_segment_id: r.id,
                    start_byte: r.start,
                    end_byte: r.end(),
                })
                .collect(),
        }],
        fallback: None,
    });
    if evidence_rows.is_empty() {
        return Ok(Output::new("abstained", json!({"answer":null})));
    }
    let req = decision_request(question, &evidence_rows);
    if engine.count(&req)? > engine.policy.input_budget {
        return Err(Fail::Held(
            "the complete evidence ledger exceeds the input budget".into(),
        ));
    }
    let before = engine.metrics.calls;
    let decision = engine.structured(req, |v| resolve(v, &evidence_rows));
    engine
        .metrics
        .context
        .as_mut()
        .unwrap()
        .retrieval
        .as_mut()
        .unwrap()
        .rounds[0]
        .sent = engine.metrics.calls > before;
    match decision? {
        Some(answer) => Ok(Output::new("ok", json!({"answer":answer}))),
        None => Ok(Output::new("abstained", json!({"answer":null}))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_decision_must_cite_the_actual_selected_row() {
        let rows = document::segments("案は「南」。\n決定は「北」。\n別班は「南」。");
        let good = resolve(&json!({"state":"set","row":2,"value":"北"}), &rows)
            .unwrap()
            .unwrap();
        let span = good.spans.first().unwrap();
        assert_eq!(
            &"案は「南」。\n決定は「北」。\n別班は「南」。"[span.start_byte..span.end_byte],
            "北"
        );
        assert!(resolve(&json!({"state":"set","row":2,"value":"南"}), &rows).is_err());
        assert!(
            resolve(&json!({"state":"unset","row":0,"value":""}), &rows)
                .unwrap()
                .is_none()
        );
    }
}
