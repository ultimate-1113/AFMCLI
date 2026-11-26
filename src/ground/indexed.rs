//! A bounded, request-local index. Only original UTF-8 ranges can support the answer.
use super::{compact, window_budget};
use crate::{
    document::Segment,
    engine::{ContextMode, Engine, Fail, Output, RetrievalRound, RetrievalUsage, Run, SourceRange},
};
use regex::Regex;
use serde_json::json;
use std::{
    collections::{BTreeSet, HashMap},
    sync::LazyLock,
};

const MAX_ROWS: usize = 65_536;
const MAX_SELECTED: usize = 96;

pub(super) static AGGREGATE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?ix)
        \b(across\s+all|how\s+many|how\s+often|combined|cumulative|tally|frequency|
        in\s+(?:total|all)|altogether|overall|sum|total|count|every|each|compare|comparison|all|
        average|mean|median|minimum|maximum|highest|lowest|largest|smallest|greatest|least|
        earliest|latest\s+of|most\s+(?:often|frequent)|list\s+(all|the)|rank(?:ing)?)\b
        |全体|すべて|全て|全件|一覧|総数|合計|合算|通算|何件|何回|いくつの|いくつある|何か所|何箇所|
        何種類|何人|何名|何社|何個|何台|何枚|何組|総計|総額|回数|全拠点|全部署|全支店|全期間|最大|最小|最高|最低|最も|もっとも|平均|中央値|比較|それぞれ|数え|件数").unwrap()
});
pub(super) static REVISION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?ix)
        \b(amend\w*|correct(?:ion|ed|s)?|withdraw\w*|withdrawn|cancel\w*|revis\w*|
        replac\w*|supersed\w*|rescinded|revok\w*|retract\w*|void\w*|pending|instead|
        no\s+longer|no\s+replacement|on\s+hold|now\s+(?:at|in)|moved|changed|
        postpone\w*|reschedul\w*|reject\w*|declin\w*|overrid\w*|remains\s+under\s+discussion)\b
        |訂正|修正|変更|取り消|取消|撤回|撤廃|差し替|差替|更新|無効|失効|見送|延期|中止|保留|
        未定|未承認|審議中|再決定|再指定|取り下げ|破棄|取り止め|移転|移設|改定",
    )
    .unwrap()
});

fn japanese(c: char) -> bool {
    matches!(c as u32, 0x3040..=0x30ff | 0x3400..=0x9fff | 0xf900..=0xfaff | 0x20000..=0x2fa1f)
}

pub(super) fn tokens(text: &str) -> BTreeSet<String> {
    const STOP: &[&str] = &[
        "a",
        "an",
        "the",
        "what",
        "which",
        "who",
        "where",
        "when",
        "how",
        "why",
        "did",
        "do",
        "does",
        "is",
        "are",
        "was",
        "were",
        "for",
        "from",
        "with",
        "and",
        "or",
        "of",
        "in",
        "at",
        "on",
        "by",
        "to",
        "it",
        "its",
        "be",
        "as",
        "have",
        "has",
        "give",
        "only",
        "the",
        "according",
        "return",
        "currently",
        "please",
        "current",
        "final",
    ];
    let mut found = BTreeSet::new();
    for word in text.split(|c: char| !c.is_alphanumeric() || japanese(c)) {
        if !word.is_empty() {
            let term = word.to_lowercase();
            if !STOP.contains(&term.as_str()) {
                found.insert(term);
            }
        }
    }
    // Keep Japanese adjacency. Word boundaries alone miss names inside a Japanese sentence.
    let mut prev = None;
    for current in text.chars() {
        if !japanese(current) {
            prev = None;
            continue;
        }
        if let Some(first) = prev {
            found.insert([first, current].into_iter().collect());
        }
        if current as u32 >= 0x3400 {
            found.insert(current.to_string());
        }
        prev = Some(current);
    }
    found
}

#[derive(Clone, Copy, Default)]
pub(super) struct Scope {
    doc: usize,
    title: Option<usize>,
    section: Option<usize>,
    date: Option<usize>,
    subject: Option<usize>,
    speaker: Option<usize>,
}
impl Scope {
    pub(super) fn headers(self) -> impl Iterator<Item = usize> {
        [
            self.title,
            self.section,
            self.subject,
            self.date,
            self.speaker,
        ]
        .into_iter()
        .flatten()
    }
    pub(super) fn advance(&mut self, line: &str, i: usize) {
        let lower = line.to_ascii_lowercase();
        if line.starts_with("# ") || lower.starts_with("document:") || line.starts_with("文書：")
        {
            *self = Self {
                doc: self.doc + usize::from(i != 0),
                title: Some(i),
                ..Self::default()
            };
        } else if line.starts_with("##")
            || (line.starts_with('[') && line.ends_with(']'))
            || lower.starts_with("section:")
            || line.starts_with("節：")
        {
            self.section = Some(i);
            self.subject = None;
            self.date = None;
            self.speaker = None;
        } else if lower.starts_with("subject:")
            || line.starts_with("件名：")
            || line.starts_with("件名:")
        {
            self.subject = Some(i);
        } else if lower.starts_with("date:")
            || line.starts_with("日付：")
            || line.starts_with("日付:")
        {
            self.date = Some(i);
        } else if ["from:", "speaker:", "author:"]
            .iter()
            .any(|prefix| lower.starts_with(prefix))
            || ["発言者：", "担当者：", "発信者："]
                .iter()
                .any(|prefix| line.starts_with(prefix))
        {
            self.speaker = Some(i);
        }
    }
}

struct Row {
    scope: Scope,
    matched: Vec<usize>,
    weight: usize,
    amendment: bool,
}

struct Index {
    rows: Vec<Row>,
    ranked: Vec<usize>,
}

impl Index {
    fn build(lines: &[Segment<'_>], question: &str, checked: bool) -> Option<Self> {
        let query = tokens(question);
        if query.is_empty() || query.len() > 192 {
            return None;
        }
        let terms: HashMap<_, _> = query.into_iter().enumerate().map(|(i, s)| (s, i)).collect();
        let mut frequency = vec![0; terms.len()];
        let mut rows = Vec::with_capacity(lines.len());
        let mut scope = Scope {
            doc: 1,
            ..Scope::default()
        };
        for (i, line) in lines.iter().enumerate() {
            scope.advance(line.text, i);
            let matched: Vec<_> = tokens(line.text)
                .iter()
                .filter_map(|k| terms.get(k).copied())
                .collect();
            for &term in &matched {
                frequency[term] += 1;
            }
            rows.push(Row {
                scope,
                matched,
                weight: 0,
                amendment: REVISION.is_match(line.text),
            });
        }
        // Score rare question terms most heavily; ubiquitous boilerplate cannot identify a target.
        let weights: Vec<_> = frequency
            .iter()
            .map(|n| {
                let inv = (lines.len() + 1) / (n + 1);
                if inv <= 2 {
                    0
                } else {
                    (inv.ilog2() as usize).pow(2)
                }
            })
            .collect();
        let direct: Vec<usize> = rows
            .iter()
            .map(|r| r.matched.iter().map(|&i| weights[i]).sum())
            .collect();
        for (i, row) in rows.iter_mut().enumerate() {
            let heading = row
                .scope
                .headers()
                .filter(|&id| id != i)
                .map(|id| direct[id])
                .max()
                .unwrap_or(0);
            row.weight = direct[i] * 3 + heading;
            if checked && row.weight > 0 && super::checked::CHANGE.is_match(lines[i].text) {
                row.amendment = true;
            }
        }
        let mut ranked: Vec<_> = (0..rows.len()).filter(|&i| rows[i].weight > 0).collect();
        // Explicit source position breaks ties independently of hash iteration order.
        ranked.sort_unstable_by_key(|&i| (std::cmp::Reverse(rows[i].weight), i));
        (!ranked.is_empty()).then_some(Self { rows, ranked })
    }

    fn select(&self, lines: &[Segment<'_>], round: usize, candidate: Option<&str>) -> Selection {
        let max_seeds = if round == 1 { 4 } else { 12 };
        let mut centers: BTreeSet<_> = self.ranked.iter().take(max_seeds).copied().collect();
        let top = self.rows[self.ranked[0]].weight;
        let strong = self
            .ranked
            .iter()
            .copied()
            .take_while(|&i| self.rows[i].weight * 2 >= top);
        let mut required: BTreeSet<_> = strong.collect();
        // A later withdrawal can omit the old venue, attribute or subject entirely.
        // Scan notices across document boundaries as an amendment can start a new document.
        for (i, row) in self.rows.iter().enumerate() {
            if row.amendment || candidate.is_some_and(|s| lines[i].text.contains(s)) {
                required.insert(i);
                if centers.len() < 2 * max_seeds || centers.contains(&i) {
                    centers.insert(i);
                }
            }
        }
        let radius = round;
        let mut picked = BTreeSet::new();
        for i in centers {
            let scope = self.rows[i].scope;
            picked.extend(scope.headers());
            let start = i.saturating_sub(radius);
            let end = (i + 1 + radius).min(lines.len());
            for (offset, row) in self.rows[start..end].iter().enumerate() {
                if row.scope.doc == scope.doc {
                    picked.insert(start + offset);
                    picked.extend(row.scope.headers());
                }
            }
        }
        let complete = required.is_subset(&picked);
        let mut ranges: Vec<SourceRange> = vec![];
        for i in picked {
            let line = lines[i];
            let doc = self.rows[i].scope.doc;
            if let Some(last) = ranges.last_mut()
                && last.document_id == doc
                && last.last_segment_id + 1 == line.id
            {
                last.last_segment_id = line.id;
                last.end_byte = line.end();
            } else {
                ranges.push(SourceRange {
                    document_id: doc,
                    first_segment_id: line.id,
                    last_segment_id: line.id,
                    start_byte: line.start,
                    end_byte: line.end(),
                });
            }
        }
        Selection { ranges, complete }
    }
}

struct Selection {
    ranges: Vec<SourceRange>,
    complete: bool,
}

/// Extract bounded, source-grounded starting points for a reader that also scans every window.
pub(super) fn seed_rows(lines: &[Segment<'_>], question: &str) -> Vec<usize> {
    if lines.len() > MAX_ROWS || question.len() > 4096 {
        return Vec::new();
    }
    let Some(index) = Index::build(lines, question, false) else {
        return Vec::new();
    };
    let ranges = index.select(lines, 1, None).ranges;
    let rows: Vec<_> = ranges
        .into_iter()
        .flat_map(|r| (r.first_segment_id - 1)..r.last_segment_id)
        .collect();
    if rows.len() <= MAX_SELECTED {
        rows
    } else {
        Vec::new()
    }
}

fn retrieval<'a>(engine: &'a mut Engine<'_>) -> &'a mut RetrievalUsage {
    engine
        .metrics
        .context
        .as_mut()
        .unwrap()
        .retrieval
        .as_mut()
        .unwrap()
}

fn fallback(engine: &mut Engine<'_>, reason: &'static str) -> Run<Option<Output>> {
    retrieval(engine).fallback = Some(reason);
    Ok(None)
}

pub(super) fn answer(
    engine: &mut Engine<'_>,
    source: &str,
    question: &str,
    lines: &[Segment<'_>],
) -> Run<Option<Output>> {
    engine.metrics.context.as_mut().unwrap().retrieval = Some(RetrievalUsage::default());
    if AGGREGATE.is_match(question) {
        return fallback(engine, "whole_document_question");
    }
    if lines.len() > MAX_ROWS || question.len() > 4096 {
        return fallback(engine, "index_size");
    }
    let Some(index) = Index::build(
        lines,
        question,
        engine
            .policy
            .compact
            .is_some_and(ContextMode::checks_decisions),
    ) else {
        return fallback(engine, "no_selective_query_terms");
    };
    let limit = match engine.policy.compact {
        #[cfg(feature = "experimental-index")]
        Some(ContextMode::IndexOnce) => 1,
        _ => 2,
    };
    let mut candidate = None;
    let mut last_reason = "insufficient_evidence";
    for round in 1..=limit {
        let selection = index.select(lines, round, candidate.as_deref());
        let ranges = &selection.ranges;
        let count: usize = ranges
            .iter()
            .map(|s| s.last_segment_id + 1 - s.first_segment_id)
            .sum();
        let bytes: usize = ranges.iter().map(|s| s.end_byte - s.start_byte).sum();
        let within_limit =
            count <= MAX_SELECTED && bytes <= engine.policy.input_budget.saturating_mul(12);
        let repeated = retrieval(engine)
            .rounds
            .last()
            .is_some_and(|r| r.ranges == *ranges);
        retrieval(engine).rounds.push(RetrievalRound {
            sent: false,
            ranges: selection.ranges.clone(),
        });
        if repeated {
            return fallback(engine, "no_new_excerpts");
        }
        if !within_limit {
            return fallback(engine, "packet_too_large");
        }
        let parts: Vec<_> = ranges
            .iter()
            .map(|r| (r.start_byte, &source[r.start_byte..r.end_byte]))
            .collect();
        let excerpts: Vec<_> = ranges.iter().zip(&parts).map(|(r, (_, text))| {
            json!({"document":r.document_id, "first_segment":r.first_segment_id, "last_segment":r.last_segment_id, "text":text})
        }).collect();
        let packet = json!({"indexed_excerpts":excerpts});
        let request = compact::reader(engine.policy.compact, question, &packet);
        if request.prompt.len() > engine.policy.input_budget.saturating_mul(12)
            || engine.count(&request)? > window_budget(engine)
        {
            return fallback(engine, "packet_exceeds_budget");
        }
        let calls_before = engine.metrics.calls;
        let reading = if engine
            .policy
            .compact
            .is_some_and(ContextMode::checks_decisions)
        {
            super::checked::read(engine, question, &parts, request, false, source).map(|f| {
                let explicit_unset = f.state == "unset";
                (f.output(), explicit_unset)
            })
        } else {
            compact::read(engine, question, &packet, &parts, request, source)
                .map(|out| (out, false))
        };
        let sent = engine.metrics.calls > calls_before;
        retrieval(engine).rounds.last_mut().unwrap().sent = sent;
        let (output, explicit_unset) = match reading {
            Ok(out) => out,
            Err(Fail::Held(_)) if engine.metrics.calls < engine.policy.max_calls => {
                last_reason = "invalid_or_unconfirmed_reading";
                continue;
            }
            Err(e) => return Err(e),
        };
        if explicit_unset && selection.complete {
            let usage = engine.metrics.context.as_mut().unwrap();
            usage.method = "indexed_excerpts";
            usage.retained_segments = count;
            return Ok(Some(output));
        }
        if let Some(value) = output.value["answer"]["value"].as_str() {
            let unseen = lines.iter().any(|s| {
                s.text.contains(value)
                    && !ranges
                        .iter()
                        .any(|r| (r.first_segment_id..=r.last_segment_id).contains(&s.id))
            });
            if selection.complete && !unseen {
                let usage = engine.metrics.context.as_mut().unwrap();
                usage.method = "indexed_excerpts";
                usage.retained_segments = count;
                return Ok(Some(output));
            }
            last_reason = "additional_source_mentions";
            candidate = Some(value.to_owned());
        } else {
            last_reason = "insufficient_evidence";
        }
    }
    fallback(engine, last_reason)
}
