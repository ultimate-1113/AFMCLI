//! One independent reading of original clauses. An oversized packet is discarded as a whole.
use super::{
    checked::{Finding, source_finding},
    indexed::{self, Scope},
};
use crate::{
    contract,
    document::Span,
    engine::{Engine, Fail, Run, request},
};
use regex::Regex;
use serde_json::json;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::LazyLock,
};

// This is the total token count of the extra request, including its schema and instructions.
// The byte preflight and scan limits bound host work; none of these limits truncate evidence.
const EXTRA_INPUT_TOKENS: usize = 850;
const MAX_SCAN_BYTES: usize = 2 * 1024 * 1024;
const MAX_LINES: usize = 16_384;
const MAX_ANCHORS: usize = 128;

static DECISION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?ix)\b(approv\w*|authori[sz]\w*|confirm\w*|accept\w*|decid\w*|designat\w*|
    assign\w*|adopt\w*|ratif\w*|cho(?:se|sen)|select(?:ed|ion)?|issu(?:e|ed)|book(?:ed|ing)|
    propos\w*|nomina\w*|tentative|draft|undecided|den(?:y|ied)|reject\w*|
    subject\s+to|not\s+yet|no\s+longer|never\s+approved)\b|
    承認|許可|確定|決定|決裁|正式|採用|合意|指定|割[り当]|発行|発効|決ま|選定|任命|
    提案|候補|仮決|草案|見送|否決|未決|予定|仮押さえ",
    )
    .unwrap()
});

struct Line<'a> {
    part: usize,
    start: usize,
    text: &'a str,
    scope: Scope,
    hits: Vec<String>,
}

struct Packet<'a> {
    parts: Vec<(usize, &'a str)>,
    complete: bool,
}

fn packet<'a>(question: &str, parts: &[(usize, &'a str)], anchors: &[Span]) -> Option<Packet<'a>> {
    if anchors.is_empty()
        || anchors.len() > MAX_ANCHORS
        || indexed::AGGREGATE.is_match(question)
        || question.len() > 4096
        || parts.is_empty()
        || parts
            .iter()
            .try_fold(0usize, |n, (_, s)| n.checked_add(s.len()))?
            > MAX_SCAN_BYTES
    {
        return None;
    }
    let terms: BTreeSet<_> = indexed::tokens(question)
        .into_iter()
        .filter(|s| {
            s.chars().count() >= 2
                && !s
                    .chars()
                    .all(|c| matches!(c, 'ぁ'..='ゖ' | 'ァ'..='ヺ') || c.is_ascii_digit())
        })
        .collect();
    if terms.is_empty() {
        return None;
    }
    let mut lines = Vec::new();
    let mut frequency = BTreeMap::<String, usize>::new();
    let mut previous_end = None;
    for (part, &(offset, source)) in parts.iter().enumerate() {
        if previous_end.is_some_and(|end| end > offset) {
            return None;
        }
        previous_end = Some(offset.checked_add(source.len())?);
        let mut scope = Scope::default();
        let mut pos = offset;
        for raw in source.split_inclusive('\n') {
            let text = raw.trim();
            if !text.is_empty() {
                if lines.len() == MAX_LINES {
                    return None;
                }
                let start = pos + (raw.len() - raw.trim_start().len());
                scope.advance(text, lines.len());
                let hits: Vec<_> = indexed::tokens(text)
                    .intersection(&terms)
                    .cloned()
                    .collect();
                for term in &hits {
                    *frequency.entry(term.clone()).or_default() += 1;
                }
                lines.push(Line {
                    part,
                    start,
                    text,
                    scope,
                    hits,
                });
            }
            pos += raw.len();
        }
    }
    // Anchor ranges must fit a supplied original part. Each touched physical line is indivisible.
    let mut seeds = BTreeSet::new();
    for span in anchors {
        if !parts.iter().any(|(base, source)| {
            span.start_byte >= *base
                && span.start_byte < span.end_byte
                && span.end_byte <= base.saturating_add(source.len())
                && source
                    .get(span.start_byte - *base..span.end_byte - *base)
                    .is_some()
        }) {
            return None;
        }
        let first = lines.partition_point(|s| s.start + s.text.len() <= span.start_byte);
        let end = lines.partition_point(|s| s.start < span.end_byte);
        if first >= end {
            return None;
        }
        seeds.extend(first..end);
    }
    let n = lines.len();
    let rare = (n / 64).max(2);
    let moderate = (n / 4).max(4);
    for (i, l) in lines.iter().enumerate() {
        // A change can use a pronoun or concern another subject with the same name.
        // Include every explicit decision/change, plus rare query terms or corroborating term pairs.
        if indexed::REVISION.is_match(l.text)
            || DECISION.is_match(l.text)
            || l.hits.iter().any(|t| frequency[t] <= rare)
            || l.hits
                .iter()
                .filter(|t| frequency[*t] <= moderate)
                .take(2)
                .count()
                == 2
        {
            seeds.insert(i);
        }
    }
    let mut keep = BTreeSet::new();
    for i in seeds {
        for k in i.saturating_sub(1)..=(i + 1).min(n - 1) {
            if lines[k].part == lines[i].part {
                keep.insert(k);
                keep.extend(lines[k].scope.headers());
            }
        }
    }
    let complete = keep.len() == n && parts.len() == 1 && parts[0].0 == 0;
    let mut ranges = Vec::<(usize, usize)>::new();
    for i in keep {
        if let Some((_, end)) = ranges.last_mut()
            && *end + 1 == i
            && lines[*end].part == lines[i].part
        {
            *end = i;
        } else {
            ranges.push((i, i));
        }
    }
    let source_ranges = ranges
        .into_iter()
        .map(|(i, j)| {
            let first = &lines[i];
            let last = &lines[j];
            let (offset, src) = parts[first.part];
            (
                first.start,
                &src[first.start - offset..last.start + last.text.len() - offset],
            )
        })
        .collect();
    Some(Packet {
        parts: source_ranges,
        complete,
    })
}

pub(super) fn decide(
    engine: &mut Engine<'_>,
    question: &str,
    parts: &[(usize, &str)],
    candidate_spans: &[Span],
) -> Run<Option<Finding>> {
    if engine.metrics.calls >= engine.policy.max_calls {
        return Ok(None);
    }
    let Some(packet) = packet(question, parts, candidate_spans) else {
        return Ok(None);
    };
    let cap = EXTRA_INPUT_TOKENS.min(engine.policy.input_budget);
    if packet.parts.iter().map(|(_, s)| s.len()).sum::<usize>() > cap.saturating_mul(12) {
        return Ok(None);
    }
    let schema = contract::object([
        (
            "state".into(),
            json!({"type":"string","enum":["set","unset","missing"]}),
        ),
        ("clause".into(), json!({"type":"string"})),
        ("value".into(), json!({"type":"string"})),
    ]);
    let excerpts: Vec<_> = packet.parts.iter().map(|(_, s)| s).collect();
    let req = request(
        "Resolve the question from these original passages in document order. Match the named subject, requested attribute and effective time. A proposal does not establish approval. Check revisions, including ones for other subjects. For set, quote the decisive original clause and copy the shortest exact value it establishes. For unset, quote the clause explicitly withdrawing or leaving this specific fact unsettled, with an empty value. If the passages do not decide the question, use missing with both strings empty.",
        json!({"question":question,"passages":excerpts}),
        Some(schema),
    );
    if engine.count(&req)? > cap {
        return Ok(None);
    }
    let repairs = engine.policy.repairs;
    engine.policy.repairs = 0;
    let judged = engine.structured(req, |v| {
        let f = source_finding(v, &packet.parts, question);
        if Some(f.state) != v["state"].as_str() {
            return Ok(None);
        }
        if f.state == "missing" && !(packet.complete && v["clause"] == "" && v["value"] == "") {
            return Ok(None);
        }
        if f.state != "missing" {
            let all = source_finding(v, parts, question);
            if f.state != all.state || f.spans != all.spans || f.value_spans != all.value_spans {
                return Ok(None);
            }
        }
        Ok(Some(f))
    });
    engine.policy.repairs = repairs;
    match judged {
        Err(Fail::Held(_)) => Ok(None),
        other => other,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        engine::Policy,
        fm::{Model, Request},
    };

    const Q: &str = "Which venue is approved for the Cedar team?";
    const FIRST: &str = "The Cedar team proposed Arc Hall as its venue.";
    const LATER: &str = "The Cedar team approved Pearl Hall as its venue.";
    fn anchor(source: &str, clause: &str) -> Span {
        let start = source.find(clause).unwrap();
        Span {
            start_byte: start,
            end_byte: start + clause.len(),
        }
    }
    struct Backend {
        input: usize,
        output: String,
        seen: Vec<Request>,
    }
    impl Model for Backend {
        fn ready(&mut self) -> Result<(), String> {
            Ok(())
        }
        fn count(&mut self, _: &Request) -> Result<usize, String> {
            Ok(self.input)
        }
        fn generate(&mut self, r: &Request) -> Result<String, String> {
            self.seen.push(r.clone());
            Ok(self.output.clone())
        }
    }
    fn backend(input: usize, output: &str) -> Backend {
        Backend {
            input,
            output: output.into(),
            seen: vec![],
        }
    }

    #[test]
    fn packet_preserves_distant_and_other_subject_updates_in_original_order() {
        let mut rows = vec!["# Archive".to_string(), FIRST.into()];
        rows.extend((0..55).map(|i| format!("Clerk {i} cleaned a storage shelf.")));
        rows.extend(
            [
                "## New notices",
                "Subject: Cedar",
                LATER,
                "The Clay team rejected Pearl Hall.",
            ]
            .map(str::to_owned),
        );
        let src = rows.join("\r\n");
        let packet = packet(Q, &[(0, &src)], &[anchor(&src, FIRST)]).unwrap();
        let excerpts = packet
            .parts
            .iter()
            .map(|(_, text)| *text)
            .collect::<Vec<_>>()
            .join("\n");
        assert!(excerpts.contains("## New notices") && excerpts.contains("Subject: Cedar"));
        assert!(excerpts.find(FIRST).unwrap() < excerpts.find(LATER).unwrap());
        assert!(excerpts.contains("Clay team rejected") && !excerpts.contains("Clerk 27"));
        for &(start, text) in &packet.parts {
            assert_eq!(&src[start..start + text.len()], text);
        }
        assert!(!packet.complete);
    }

    #[test]
    fn actual_token_budget_holds_entire_packet_without_a_generation() {
        let src = format!("{FIRST}\n{LATER}");
        let mut m = backend(EXTRA_INPUT_TOKENS + 1, "{}");
        let mut e = Engine::new(&mut m, Policy::default());
        assert!(
            decide(&mut e, Q, &[(0, &src)], &[anchor(&src, FIRST)])
                .unwrap()
                .is_none()
        );
        assert_eq!((e.metrics.token_checks, e.metrics.calls), (1, 0));
        assert!(e.policy.repairs > 0);
    }

    #[test]
    fn invalid_or_missing_anchors_and_aggregate_questions_skip_before_counting() {
        let src = "決定。受取場所は朝日館。";
        let invalid = Span {
            start_byte: 1,
            end_byte: 5,
        };
        let mut m = backend(350, "{}");
        let mut e = Engine::new(&mut m, Policy::default());
        assert!(
            decide(&mut e, "受取場所はどこ？", &[(0, src)], &[invalid])
                .unwrap()
                .is_none()
        );
        assert!(decide(&mut e, Q, &[(0, src)], &[]).unwrap().is_none());
        assert!(
            decide(
                &mut e,
                "Count all entries",
                &[(0, src)],
                &[anchor(src, "決定")]
            )
            .unwrap()
            .is_none()
        );
        assert_eq!((e.metrics.token_checks, e.metrics.calls), (0, 0));
    }

    #[test]
    fn invalid_reply_is_tried_once_and_does_not_change_repair_policy() {
        let src = format!("{FIRST}\n{LATER}");
        let mut m = backend(360, "{");
        let mut e = Engine::new(&mut m, Policy::default());
        assert!(
            decide(&mut e, Q, &[(0, &src)], &[anchor(&src, FIRST)])
                .unwrap()
                .is_none()
        );
        assert_eq!(
            (e.metrics.calls, e.metrics.repairs, e.policy.repairs),
            (1, 0, 1)
        );
        let (_, payload) = m.seen[0].prompt.split_once('\n').unwrap();
        let v: serde_json::Value = serde_json::from_str(payload).unwrap();
        assert_eq!(v.as_object().unwrap().len(), 2);
        assert_eq!(v["question"], Q);
        assert!(
            v["passages"]
                .as_array()
                .unwrap()
                .iter()
                .any(|s| s.as_str().unwrap().contains(LATER))
        );
    }

    #[test]
    fn text_outside_the_passages_cannot_support_a_set_finding() {
        let src = format!("{FIRST}\n{LATER}");
        let output =
            json!({"state":"set","clause":"Cedar approved Coast Hall.","value":"Coast Hall"})
                .to_string();
        let mut m = backend(340, &output);
        let mut e = Engine::new(&mut m, Policy::default());
        assert!(
            decide(&mut e, Q, &[(0, &src)], &[anchor(&src, LATER)])
                .unwrap()
                .is_none()
        );
        assert_eq!(e.metrics.calls, 1);
    }
    #[test]
    fn set_and_unset_require_the_model_to_quote_an_original_supporting_clause() {
        let later = "The Cedar team's venue remains unconfirmed.";
        for (state, clause, value) in [("set", LATER, "Pearl Hall"), ("unset", later, "")] {
            let src = format!("{FIRST}\n{clause}");
            let output = json!({"state":state,"clause":clause,"value":value}).to_string();
            let mut model = backend(380, &output);
            let mut engine = Engine::new(&mut model, Policy::default());
            let finding = decide(&mut engine, Q, &[(0, &src)], &[anchor(&src, FIRST)])
                .unwrap()
                .unwrap();
            assert_eq!(finding.state, state);
            assert_eq!(finding.value, value);
            assert!(finding.spans.contains(&anchor(&src, clause)));
            assert_eq!(engine.metrics.calls, 1);
        }
    }

    #[test]
    fn a_partial_packet_cannot_confirm_universal_absence() {
        let mut rows = vec![FIRST.to_string()];
        rows.extend((0..20).map(|i| format!("Worker {i} signed a time sheet.")));
        let src = rows.join("\n");
        let mut model = backend(350, r#"{"state":"missing","clause":"","value":""}"#);
        let mut engine = Engine::new(&mut model, Policy::default());
        let finding = decide(&mut engine, Q, &[(0, &src)], &[anchor(&src, FIRST)]).unwrap();
        assert!(finding.is_none());
        assert_eq!(engine.metrics.calls, 1);
    }
}
