//! A single reading of a bounded set of original passages. A supported value is a review candidate.
use super::indexed;
use crate::{
    contract,
    document::{self, Evidence, Segment},
    engine::{ContextUsage, Engine, Fail, Output, Run, request},
};
use regex::Regex;
use serde_json::{Value, json};
use std::{cmp::Reverse, collections::BTreeSet, sync::LazyLock};

const MAX_BYTES: usize = 5_600;
const MAX_PASSAGES: usize = 14;

static DECISION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?ix)
        \b((?:re)?approv\w*|(?:re)?confirm\w*|(?:re)?assign\w*|designat\w*|authori[sz]\w*|
        decid\w*|chose[n]?|select\w*|adopt\w*|ratif\w*|reinstat\w*|restor\w*|withdrew|
        unresolved|undecided|unapproved|unassigned|without\s+a\s+replacement)\b
        |承認|確定|決定|指定|採用|選定|割り当て|再開|未決|未割当|不明|検討中",
    )
    .unwrap()
});

/// Include late decisions, earlier evidence and the neighboring lines that identify their subject.
fn select_rows(rows: &[Segment<'_>], question: &str) -> Vec<usize> {
    let seeds: BTreeSet<_> = indexed::seed_rows(rows, question).into_iter().collect();
    let query = indexed::tokens(question);
    let mut hits = vec![0; rows.len()];
    let mut changes = Vec::new();
    let mut decisions = Vec::new();
    let mut notice = Vec::new();
    let mut scope = indexed::Scope::default();
    let mut headings = Vec::with_capacity(rows.len());
    for (i, row) in rows.iter().enumerate() {
        scope.advance(row.text, i);
        headings.push(scope.headers().max());
        let change = indexed::REVISION.is_match(row.text);
        let decision = DECISION.is_match(row.text);
        if change || decision {
            notice.push(i);
            if change {
                changes.push(i);
            } else {
                decisions.push(i);
            }
        }
        if change || decision || seeds.contains(&i) {
            hits[i] = query.intersection(&indexed::tokens(row.text)).count();
        }
    }
    let relevance = |i: usize| {
        let near = &hits[i.saturating_sub(1)..(i + 2).min(hits.len())];
        (3 * hits[i])
            .max(*near.iter().max().unwrap_or(&0))
            .max(headings[i].map_or(0, |h| hits[h]))
    };
    let mut direct: Vec<_> = seeds.into_iter().collect();
    direct.sort_unstable_by_key(|&i| Reverse((hits[i], i)));
    changes.sort_unstable_by_key(|&i| Reverse((relevance(i), i)));
    decisions.sort_unstable_by_key(|&i| Reverse((relevance(i), i)));
    let mut primary = Vec::new();
    for &i in notice
        .iter()
        .rev()
        .filter(|&&i| relevance(i) > 0)
        .take(2)
        .chain(changes.iter().take(2))
        .chain(decisions.iter().take(2))
        .chain(direct.iter().take(4))
    {
        if !primary.contains(&i) {
            primary.push(i);
        }
    }
    let neighbors = primary.iter().flat_map(|&i| {
        [i.checked_sub(1), (i + 1 < rows.len()).then_some(i + 1)]
            .into_iter()
            .flatten()
    });
    let priority = primary
        .iter()
        .copied()
        .chain(primary.iter().filter_map(|&i| headings[i]))
        .chain(neighbors)
        .chain(direct)
        .chain([0, rows.len() - 1]);
    let mut selected = BTreeSet::new();
    let mut bytes = 0;
    for i in priority {
        if bytes + rows[i].text.len() <= MAX_BYTES && selected.insert(i) {
            bytes += rows[i].text.len();
            if selected.len() == MAX_PASSAGES {
                break;
            }
        }
    }
    selected.into_iter().collect()
}

fn candidate(source: &str, parts: &[(usize, &str)], v: &Value) -> Result<Option<Evidence>, String> {
    if v["state"] == "missing" {
        return if v["passage"] == 0 && v["quote"] == "" && v["value"] == "" {
            Ok(None)
        } else {
            Err("a missing answer must have empty evidence and passage zero".into())
        };
    }
    let id = v["passage"]
        .as_u64()
        .and_then(|n| usize::try_from(n).ok())
        .and_then(|n| n.checked_sub(1))
        .ok_or("cite a supplied passage number")?;
    let &(offset, text) = parts.get(id).ok_or("cite a supplied passage number")?;
    let quote = v["quote"].as_str().unwrap_or("");
    let value = v["value"].as_str().unwrap_or("");
    if quote == value && value != source.trim() {
        return Err("quote the source statement that supports the value".into());
    }
    let quotes = document::locate_parts(parts, quote)?;
    let Some(clause) = quotes.first().filter(|_| quotes.len() == 1) else {
        return Err("the supporting quote is ambiguous in the supplied excerpts".into());
    };
    if clause.start_byte < offset
        || clause.end_byte > offset + text.len()
        || source.get(clause.start_byte..clause.end_byte) != Some(quote)
    {
        return Err("the supporting quote does not match its cited original passage".into());
    }
    let spans = document::locate(quote, clause.start_byte, value)?;
    if spans.len() != 1
        || spans
            .iter()
            .any(|s| source.get(s.start_byte..s.end_byte) != Some(value))
    {
        return Err("the value needs one exact occurrence in the cited source statement".into());
    }
    Ok(Some(Evidence {
        value: value.to_owned(),
        spans,
    }))
}

pub(crate) fn answer(engine: &mut Engine<'_>, source: &str, question: &str) -> Run<Output> {
    let rows = document::segments(source);
    engine.metrics.context = Some(ContextUsage {
        method: "packet_preview",
        source_segments: rows.len(),
        retained_segments: 0,
        selection_windows: 0,
        retrieval: None,
    });
    if rows.is_empty() {
        return Err(Fail::Held(
            "no source passages are available for the excerpt reader".into(),
        ));
    }
    let (parts, retained): (Vec<_>, _) = if source.len() <= MAX_BYTES {
        // Keep the exact whole source, including whitespace between short sentences.
        (vec![(0, source)], rows.len())
    } else {
        if source.len() > 2 * 1024 * 1024 || rows.len() > 32_768 || question.len() > 4096 {
            return Err(Fail::Held(
                "source or question exceeds the local excerpt index limit".into(),
            ));
        }
        let indices = select_rows(&rows, question);
        let parts = indices
            .iter()
            .map(|&i| (rows[i].start, rows[i].text))
            .collect();
        (parts, indices.len())
    };
    engine.metrics.context.as_mut().unwrap().retained_segments = retained;
    let passages: Vec<_> = parts
        .iter()
        .enumerate()
        .map(|(i, p)| json!([i + 1, p.1]))
        .collect();
    let req = request(
        "Answer from the numbered original passages in source order. Gaps may exist. Track the exact subject, property and time in the question. A proposal, example, or decision for another subject does not establish the answer. Use the applicable decision, including a later correction or reinstatement. For set, copy the shortest exact answer as value and a contiguous supporting clause containing it as quote. Give its passage number. For missing evidence, uncertainty, or a withdrawal without replacement, use missing, passage 0 and empty strings. Preserve source spelling and characters.",
        json!({"question":question, "passages":passages}),
        Some(contract::object([
            (
                "state".into(),
                json!({"type":"string", "enum":["set","missing"]}),
            ),
            ("passage".into(), json!({"type":"integer"})),
            ("quote".into(), json!({"type":"string"})),
            ("value".into(), json!({"type":"string"})),
        ])),
    );
    let repairs = std::mem::replace(&mut engine.policy.repairs, 0);
    let reading = engine.structured(req, |v| candidate(source, &parts, v));
    engine.policy.repairs = repairs;
    let evidence = reading?
        .ok_or_else(|| Fail::Held("the selected excerpts did not establish an answer".into()))?;
    let mut output = Output::new("review", json!({"answer":evidence}));
    output
        .notes
        .push("Candidate from one reading of the selected source passages.".into());
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        engine::{AnswerMode, Policy},
        fm::{Model, Request},
        job::Job,
    };

    struct Reply {
        tokens: Result<usize, String>,
        raw: Result<String, String>,
        sent: Vec<Request>,
    }

    impl Reply {
        fn new(v: Value) -> Self {
            Self {
                tokens: Ok(42),
                raw: Ok(v.to_string()),
                sent: vec![],
            }
        }
    }

    impl Model for Reply {
        fn ready(&mut self) -> Result<(), String> {
            Ok(())
        }
        fn count(&mut self, _: &Request) -> Result<usize, String> {
            self.tokens.clone()
        }
        fn generate(&mut self, r: &Request) -> Result<String, String> {
            self.sent.push(r.clone());
            self.raw.clone()
        }
    }

    fn stated(n: usize, quote: &str, value: &str) -> Value {
        json!({"state":"set","passage":n,"quote":quote,"value":value})
    }

    #[test]
    fn reads_the_complete_short_source_and_returns_original_utf8_bytes() {
        let source = "備考。\r\n  星砂の営業日は木曜日です。\n資料終わり。";
        let mut model = Reply::new(stated(1, "星砂の営業日は木曜日です。", "木曜日"));
        let out = Engine::new(
            &mut model,
            Policy {
                trace: true,
                ..Policy::default()
            },
        )
        .execute_with_answer_mode(
            &Job::Answer {
                source: source.into(),
                question: "星砂の営業日はいつ？".into(),
            },
            Some(AnswerMode::Preview),
        );
        let start = source.find("木曜日").unwrap();
        assert_eq!(out.status, "review");
        assert_eq!(
            out.result["answer"],
            json!({"value":"木曜日", "spans":[{"start_byte":start, "end_byte":start+"木曜日".len()}]})
        );
        let (_, body) = model.sent[0].prompt.split_once('\n').unwrap();
        let payload: Value = serde_json::from_str(body).unwrap();
        assert_eq!(payload["passages"], json!([[1, source]]));
        assert_eq!(model.sent.len(), 1);
        assert_eq!(out.metrics.calls, 1);
        assert_eq!(out.metrics.sent_input_tokens, 42);
        assert_eq!(out.attempts.len(), 1);
        let context = out.metrics.context.unwrap();
        assert_eq!(context.retained_segments, context.source_segments);
        assert_eq!(context.selection_windows, 0);
        assert!(context.retrieval.is_none());
    }

    #[test]
    fn original_passages_include_subject_neighbors_and_late_changes_within_bounds() {
        let mut lines: Vec<_> = (0..140)
            .map(|i| format!("Inspection {i} recorded a clean stand and counted the spare pieces."))
            .collect();
        lines[16] = "The Alder project initially approved the cobalt material.".into();
        lines[67] = "Subject: Alder material".into();
        lines[68] = "That decision was withdrawn pending a new sample.".into();
        lines[106] = "The Alder project approved silver as the replacement material.".into();
        let source = lines.join("\n");
        assert!(source.len() > MAX_BYTES);
        let rows = document::segments(&source);
        let selected = select_rows(&rows, "Which material was approved for the Alder project?");
        assert!(
            selected.contains(&16)
                && selected.contains(&67)
                && selected.contains(&68)
                && selected.contains(&106),
            "{selected:?}"
        );
        assert!(selected.windows(2).all(|w| w[0] < w[1]));
        assert!(selected.len() <= MAX_PASSAGES);
        assert!(selected.iter().map(|&i| rows[i].text.len()).sum::<usize>() <= MAX_BYTES);
    }

    #[test]
    fn rejects_ambiguous_quotes_wrong_passages_and_values_outside_the_quote() {
        let source = "Alpha uses silk.\n旧条項\nBeta uses oak and oak.\nGamma uses oak.";
        let parts: Vec<_> = source
            .split('\n')
            .filter(|s| s.is_ascii())
            .map(|s| (source.find(s).unwrap(), s))
            .collect();
        for v in [
            stated(1, "uses", "uses"),
            stated(2, "Alpha uses silk.", "silk"),
            stated(1, "甲は旧条項", "旧条項"),
            stated(3, "Gamma uses oak.", "silk"),
            stated(2, "Beta uses oak and oak.", "oak"),
            stated(3, "Gamma uses oak.", "oa"),
            stated(3, "Gamma uses oak.", " oak"),
            stated(0, "Gamma uses oak.", "oak"),
        ] {
            assert!(candidate(source, &parts, &v).is_err(), "{v}");
        }
        let repeated = [(0, "X uses oak."), (12, "X uses oak.")];
        assert!(
            candidate(
                "X uses oak.\nX uses oak.",
                &repeated,
                &stated(1, "X uses oak.", "oak")
            )
            .is_err()
        );
        assert!(
            candidate(
                source,
                &[(0, "Gamma uses oak.")],
                &stated(1, "Gamma uses oak.", "oak")
            )
            .is_err()
        );
        let evidence = candidate(source, &parts, &stated(3, "Gamma uses oak.", "oak"))
            .unwrap()
            .unwrap();
        assert_eq!(
            evidence.spans.first().unwrap().start_byte,
            source.rfind("oak").unwrap()
        );
    }

    #[test]
    fn one_generation_even_with_repairs_and_missing_remains_held() {
        let source = "Details for Willow will be available later.";
        let job = Job::Answer {
            source: source.into(),
            question: "Which room was selected?".into(),
        };
        for (reply, invalid) in [
            (
                json!({"state":"missing","passage":0,"quote":"","value":""}),
                false,
            ),
            (
                json!({"state":"missing","passage":1,"quote":source,"value":"Willow"}),
                true,
            ),
            (stated(1, source, "Maple"), true),
            (json!({"extra":true}), true),
        ] {
            let mut model = Reply::new(reply);
            let policy = Policy {
                repairs: 3,
                trace: true,
                ..Policy::default()
            };
            let out = Engine::new(&mut model, policy)
                .execute_with_answer_mode(&job, Some(AnswerMode::Preview));
            assert_eq!(out.status, "held");
            assert!(out.result.is_null());
            assert_eq!(out.metrics.calls, 1);
            assert_eq!(out.metrics.repairs, 0);
            assert_eq!(out.attempts.len(), 1);
            assert_eq!(out.attempts[0].get("validation_error").is_some(), invalid);
        }
    }

    #[test]
    fn preserves_policy_and_operational_failures() {
        let source = "The kiln fuel is peat.";
        let q = "Which fuel is used?";
        let mut model = Reply::new(stated(1, source, "peat"));
        let policy = Policy {
            repairs: 4,
            input_budget: 41,
            ..Policy::default()
        };
        let mut engine = Engine::new(&mut model, policy);
        assert!(matches!(answer(&mut engine, source, q), Err(Fail::Held(_))));
        assert_eq!(engine.metrics.calls, 0);
        assert_eq!(engine.policy.repairs, 4);
        drop(engine);
        model.raw = Err("fm refused the request: unavailable".into());
        let mut engine = Engine::new(
            &mut model,
            Policy {
                input_budget: 42,
                ..policy
            },
        );
        assert!(matches!(
            answer(&mut engine, source, q),
            Err(Fail::Refused(_))
        ));
        assert_eq!(engine.policy.repairs, 4);
        drop(engine);
        model.tokens = Err("token service failed".into());
        let mut engine = Engine::new(&mut model, policy);
        assert!(matches!(
            answer(&mut engine, source, q),
            Err(Fail::Backend(_))
        ));
        assert_eq!(engine.metrics.calls, 0);
        assert_eq!(engine.policy.repairs, 4);
    }
}
