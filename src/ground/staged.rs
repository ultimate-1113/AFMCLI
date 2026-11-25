//! Read the question, collect original evidence across all windows, then resolve its state.
use super::{indexed, window};
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
        "Collect up to six distinctive, short verbatim quotes about the requested subject and field from this section. Include changes, proposals and withdrawals. When nothing relates, return an empty quotes array. Copy text as written. Do not answer the question.",
        json!({"question":question,"scope":scope,"rows":original_rows(lines)}),
        Some(contract::object([(
            "quotes".into(),
            json!({"type":"array","items":{"type":"string"}}),
        )])),
    )
}

fn decision_request(question: &str, lines: &[Segment<'_>]) -> Request {
    request(
        "Locate the original row that controls the answer at the time the question asks about. Match both the subject and attribute. Prefer the applicable decision or withdrawal to an earlier proposal or a notice about another item. A later explicit reinstatement can supersede a withdrawal. Return just its supplied row ID, or zero if none. Do not produce a value.",
        json!({"question":question,"original_evidence":original_rows(lines)}),
        Some(contract::object([(
            "row".into(),
            json!({"type":"integer"}),
        )])),
    )
}

fn resolve(v: &Value, parts: &[(usize, &str)]) -> Result<Option<Evidence>, String> {
    let value = v["answer"].as_str().ok_or("copy the exact value")?;
    if value.is_empty() {
        return Ok(None);
    }
    let spans = document::locate_parts(parts, value)?;
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
    for index in indexed::seed_rows(&source_rows, question) {
        keep[index] = true;
    }
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
        let selected = engine.structured(collect_request(question, &scope, block), |v| {
            let mut selected = Vec::new();
            for quote in v["quotes"].as_array().unwrap().iter().take(6) {
                let quote = quote.as_str().unwrap().trim();
                if quote.chars().count() < 3 {
                    continue;
                }
                let matches: Vec<_> = block
                    .iter()
                    .filter(|r| r.text.contains(quote))
                    .take(5)
                    .collect();
                if matches.len() <= 4 {
                    for r in matches {
                        selected.push(r.id - 1);
                    }
                }
            }
            Ok(selected)
        })?;
        for index in selected {
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
    let decision = engine.structured(req, |v| {
        let id = v["row"].as_u64().and_then(|n| usize::try_from(n).ok());
        if id == Some(0) {
            return Ok(None);
        }
        evidence_rows
            .iter()
            .find(|r| Some(r.id) == id)
            .copied()
            .map(Some)
            .ok_or_else(|| "select an existing row ID or zero".to_string())
    });
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
    let Some(row) = decision? else {
        return Ok(Output::new("abstained", json!({"answer":null})));
    };
    // A sentence giving the value may share the same signed notice with a sentence giving its status.
    // Restrict the candidate span to the selected paragraph; keep long unbroken input bounded.
    let mut start = source[..row.start].rfind('\n').map_or(0, |n| n + 1);
    let mut end = source[row.end()..]
        .find('\n')
        .map_or(source.len(), |n| row.end() + n);
    if end - start > 2400 {
        start = row.start;
        end = row.end();
    }
    let passage = &source[start..end];
    let req = request(
        "Read this original decision notice. For the question, copy just the exact name, number, place or other requested value it establishes. If only a proposal, a cancelled decision without a replacement, an unrelated field, or an undecided matter appears, use an empty string. Do not give a status sentence as the value.",
        json!({"question":question,"notice":passage}),
        Some(contract::object([(
            "answer".into(),
            json!({"type":"string"}),
        )])),
    );
    match engine.structured(req, |v| resolve(v, &[(start, passage)]))? {
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
        let selected = &[(rows[1].start, rows[1].text)];
        let good = resolve(&json!({"answer":"北"}), selected).unwrap().unwrap();
        let span = good.spans.first().unwrap();
        assert_eq!(
            &"案は「南」。\n決定は「北」。\n別班は「南」。"[span.start_byte..span.end_byte],
            "北"
        );
        assert!(resolve(&json!({"answer":"南"}), selected).is_err());
        assert!(resolve(&json!({"answer":""}), selected).unwrap().is_none());
    }
}
