//! Named QA trials. Each independent reading receives only the question and original passages.
use super::{
    checked, choice, indexed,
    passages::{self, Corpus},
};
use crate::{
    contract,
    document::{self, Evidence},
    engine::{AnswerCheck, AnswerMode, Engine, Fail, Output, Run, request},
    fm::Request,
};
use regex::Regex;
use serde_json::{Value, json};
use std::sync::LazyLock;

static DATE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?ix)
    \d{4}|\d{1,2}[/月-]\d{1,2}|\b(?:january|february|march|april|may|june|july|august|september|october|november|december|
    spring|summer|fall|autumn|winter|before|after|until|since|effective|from|through|weekdays?|weekends?|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|
    日付|施行|適用日|以降|以前|まで|から|[年月日]度?|平日|週末").unwrap()
});
static BINDING: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?ix)
    \b(?:is|was|are|were|uses?|lists?|listed|records?|reads?|has|held\s+at|allocated|operates?)\b|
    [:：=]|用いる|使う|を使用|とする|である|です|に記載|に決ま|を割り当て",
    )
    .unwrap()
});

enum Reading {
    Set { evidence: Evidence, row: usize },
    Unset(usize),
    Clarify { kind: String, question: String },
    Missing,
}
impl Reading {
    fn state(&self) -> &'static str {
        match self {
            Self::Set { .. } => "set",
            Self::Unset(_) => "unset",
            Self::Clarify { .. } => "clarify",
            Self::Missing => "missing",
        }
    }
    fn reported(&self) -> Option<String> {
        if let Self::Set { evidence, .. } = self {
            Some(evidence.value.clone())
        } else {
            None
        }
    }
    fn output(self) -> Output {
        match self {
            Self::Set { evidence, .. } => Output::new("ok", json!({"answer":evidence})),
            Self::Clarify { question, .. } => {
                Output::new("abstained", json!({"answer":null,"clarification":question}))
            }
            _ => Output::new("abstained", json!({"answer":null})),
        }
    }
}

fn reader(q: &str, c: &Corpus<'_>, ids: &[usize], cautious: bool, independent: bool) -> Request {
    let rows: Vec<_> = ids
        .iter()
        .enumerate()
        .map(|(k, &i)| json!([k + 1, c.rows[i].text]))
        .collect();
    let mut instructions=if independent {
        "Independently resolve the question from the original records. Find the applicable decision for this exact subject, property and effective time. Distinguish an actual decision from a plan, other subject, expired entry or conditional fallback. Check whether a later applicable record withdraws OR restores it. Read explicit historical and hypothetical questions at the time/condition asked."
    }else{
        "Read the numbered original records in order; gaps may exist. Match BOTH the entity and the property at the time asked. A proposal, example or fallback alone is not current approval. Only a change to this entity and property counts; a later reinstatement restores the value. For an explicit earlier time or hypothetical, use the stated record for that case."
    }.to_owned();
    instructions.push_str(" First establish which record, edition or condition applies; then read the property requested from THAT record. A selector used to identify the record is not its property value. For set, copy only the shortest exact property value and cite its original passage. For a withdrawn/pending fact use unset and its passage with empty value. For no matching fact use missing, passage 0 and empty value.");
    let states = if cautious {
        json!(["set", "unset", "missing", "date", "target", "condition"])
    } else {
        json!(["set", "unset", "missing"])
    };
    let mut fields = vec![
        ("state".into(), json!({"type":"string","enum":states})),
        ("passage".into(), json!({"type":"integer"})),
        ("value".into(), json!({"type":"string"})),
    ];
    if cautious {
        instructions.push_str(" Only if the user must supply a date, intended target or condition to select among source rules, set state to date/target/condition. Copy a short selector from its passage into value, and a second source selector into alternative if present. Write ONE specific clarification question in the user's language. Pending approval or absent evidence is not a clarification. For all other states leave alternative and clarification empty.");
        if independent && ids.iter().any(|&i| choice::axis(c.rows[i].text).is_some()) {
            instructions.push_str(" The records mention an unspecified selection criterion. Check whether the question or a later record supplies it. If two branches give different properties and neither applies uniquely, ask which branch the user means. Cite the passage that contains the first branch selector.");
        }
        fields.extend([
            ("alternative".into(), json!({"type":"string"})),
            ("clarification".into(), json!({"type":"string"})),
        ]);
    }
    request(
        &instructions,
        json!({"question":q,"requested_field":c.field,"passages":rows}),
        Some(contract::object(fields)),
    )
}

fn clarification(v: &Value, q: &str, c: &Corpus<'_>, ids: &[usize], i: usize) -> Option<Reading> {
    let kind = v["state"].as_str()?;
    let a = v["value"].as_str()?.trim();
    let b = v["alternative"].as_str()?.trim();
    let short =
        |s: &str| !s.is_empty() && s.chars().count() <= 72 && !s.contains(['\r', '\n', '?', '？']);
    let row = c.rows[i].text;
    if !short(a)
        || document::locate(row, 0, a).is_err()
        || a.eq_ignore_ascii_case(b)
        || (!b.is_empty()
            && (!short(b)
                || !ids
                    .iter()
                    .any(|&j| document::locate(c.rows[j].text, 0, b).is_ok())))
    {
        return None;
    }
    if c.different(q, i, "")
        || c.pending(q, ids)
        || (passages::closed(row) && !passages::affirmed(row))
    {
        return None;
    }
    let q_lower = q.to_lowercase();
    let specified = |s: &str| !s.is_empty() && q_lower.contains(&s.to_lowercase());
    // A copied selector already supplied by the question is not a missing user choice.
    if specified(a) != specified(b) {
        return None;
    }
    match kind {
        "date" if DATE.is_match(a) || DATE.is_match(b) => {}
        "target" if !b.is_empty() => {}
        "condition" if passages::condition(row) || !b.is_empty() => {}
        _ => return None,
    }
    let japanese = q.chars().any(|ch| matches!(ch as u32, 0x3040..=0x30ff));
    let english = choice::english_question(q);
    let raw = v["clarification"].as_str().unwrap_or("").trim();
    let valid_single = raw.chars().count() <= 180
        && raw.ends_with(['?', '？'])
        && raw.matches(['?', '？']).count() == 1
        && !raw.contains(['\n', '\r'])
        && (raw.contains(a) || (!b.is_empty() && raw.contains(b)));
    let native = if japanese {
        raw.chars().any(|ch| matches!(ch as u32, 0x3040..=0x30ff))
    } else if english {
        raw.split_whitespace().count() >= 3
            && !raw.chars().any(|ch| matches!(ch as u32, 0x3040..=0x30ff))
    } else {
        true
    };
    let question = if valid_single && native {
        raw.to_owned()
    } else if japanese {
        match kind {
            "date" if !b.is_empty() => {
                format!("確認したい適用時期は「{a}」と「{b}」のどちらですか？")
            }
            "date" => format!("資料の「{a}」を基準に、何月何日時点の扱いを確認しますか？"),
            "target" => format!("対象は「{a}」と「{b}」のどちらですか？"),
            _ if b.is_empty() => format!("今回の状況は「{a}」に当てはまりますか？"),
            _ => format!("今回当てはまる条件は「{a}」と「{b}」のどちらですか？"),
        }
    } else if english {
        match kind {
            "date" if !b.is_empty() => {
                format!("Which date or period applies to your case, “{a}” or “{b}”?")
            }
            "date" => format!("Which date should I use to apply the rule covering “{a}”?"),
            "target" => format!("Which target do you mean, “{a}” or “{b}”?"),
            _ if b.is_empty() => format!("Does the condition “{a}” apply to your case?"),
            _ => format!("Which condition applies to your case, “{a}” or “{b}”?"),
        }
    } else {
        return None;
    };
    Some(Reading::Clarify {
        kind: kind.into(),
        question,
    })
}

fn interpret(v: &Value, q: &str, c: &Corpus<'_>, ids: &[usize]) -> Reading {
    let state = v["state"].as_str().unwrap_or("");
    let n = v["passage"]
        .as_u64()
        .and_then(|n| usize::try_from(n).ok())
        .and_then(|n| n.checked_sub(1));
    let Some(&i) = n.and_then(|n| ids.get(n)) else {
        return Reading::Missing;
    };
    let value = v["value"].as_str().unwrap_or("").trim();
    let row = c.rows[i];
    match state {
        "set" if !value.is_empty() && value.chars().count() <= 384 => {
            let found = document::locate(row.text, row.start, value)
                .ok()
                .map(|spans| (i, spans))
                .or_else(|| {
                    // A reinstatement can refer to a prior value without spelling it out.
                    // Also recover one unambiguous verbatim value if the reader misnumbered the row.
                    if c.different(q, i, "")
                        || (passages::closed(row.text) && !passages::affirmed(row.text))
                    {
                        return None;
                    }
                    let mut matches = ids.iter().filter_map(|&j| {
                        let l = c.rows[j];
                        document::locate(l.text, l.start, value)
                            .ok()
                            .map(|spans| (j, spans))
                    });
                    let hit = matches.next()?;
                    (matches.next().is_none()
                        && (passages::affirmed(row.text) || i.abs_diff(hit.0) <= 1))
                        .then_some(hit)
                });
            let Some((basis, mut spans)) = found else {
                return Reading::Missing;
            };
            if let Some((row, evidence)) = c.selected_property(q, ids, basis, value) {
                return Reading::Set { row, evidence };
            }
            let decisive = c.decisive(q, ids, basis, value);
            if decisive != basis {
                let row = c.rows[decisive];
                if let Ok(exact) = document::locate(row.text, row.start, value) {
                    spans = exact;
                }
            }
            if (c.rows[basis].text == value && c.rows.len() != 1) || !c.supports(q, basis, value) {
                return Reading::Missing;
            }
            Reading::Set {
                evidence: Evidence {
                    value: value.into(),
                    spans,
                },
                row: if passages::affirmed(row.text) {
                    i.max(decisive)
                } else {
                    decisive
                },
            }
        }
        "unset" if value.is_empty() && !c.different(q, i, "") => Reading::Unset(i),
        "date" | "target" | "condition" => {
            clarification(v, q, c, ids, i).unwrap_or(Reading::Missing)
        }
        _ => Reading::Missing,
    }
}

fn read(
    engine: &mut Engine<'_>,
    q: &str,
    c: &Corpus<'_>,
    wide: bool,
    cautious: bool,
    second: bool,
) -> Run<(Reading, Vec<usize>)> {
    let cap = engine.policy.input_budget;
    let limits = if wide {
        [(28, 8000), (14, 4200), (7, 1900), (2, 700)]
    } else {
        [(14, 4200), (8, 2300), (4, 1150), (1, 700)]
    };
    for (count, bytes) in limits {
        let ids = c.select(count, bytes.min(cap.saturating_mul(8)));
        if ids.is_empty() {
            continue;
        }
        let req = reader(q, c, &ids, cautious, second);
        if engine.count(&req)? > cap {
            continue;
        }
        let calls = engine.metrics.calls;
        let repairs = std::mem::replace(&mut engine.policy.repairs, 0);
        let result = engine.structured(req, |v| Ok(interpret(v, q, c, &ids)));
        engine.policy.repairs = repairs;
        passages::record(engine, ids.iter().map(|&i| c.range(i)).collect(), calls);
        return result.map(|r| (r, ids));
    }
    Err(Fail::Held(
        "no source passage and reading instructions fit the input budget".into(),
    ))
}

fn resolve(a: Reading, b: Reading, c: &Corpus<'_>, q: &str) -> (Reading, &'static str) {
    let strong = |i: usize| {
        passages::affirmed(c.rows[i].text)
            || BINDING.is_match(c.rows[i].text)
            || Corpus::firsthand(c.rows[i].text)
    };
    let later = |set: usize, unset: usize| {
        set > unset
            && passages::affirmed(c.rows[set].text)
            && !passages::closed(c.rows[set].text)
            && !passages::historical_or_hypothetical(q)
    };
    match (&a, &b) {
        (
            Reading::Set {
                evidence: x,
                row: i,
            },
            Reading::Set {
                evidence: y,
                row: j,
            },
        ) if x.value == y.value => (
            if i > j && !passages::historical_or_hypothetical(q) {
                a
            } else {
                b
            },
            "independent_agreement",
        ),
        (Reading::Set { row: i, .. }, Reading::Set { row: j, .. })
            if i != j && !passages::historical_or_hypothetical(q) =>
        {
            let newer = (*i).max(*j);
            if (passages::revision(c.rows[newer].text) || Corpus::firsthand(c.rows[newer].text))
                && strong(newer)
            {
                (if i > j { a } else { b }, "scoped_later_assignment")
            } else {
                (Reading::Missing, "conflicting_values")
            }
        }
        (Reading::Set { row: i, .. }, Reading::Unset(j)) if later(*i, *j) => {
            (a, "scoped_reinstatement")
        }
        (Reading::Unset(i), Reading::Set { row: j, .. }) if later(*j, *i) => {
            (b, "scoped_reinstatement")
        }
        (Reading::Missing, Reading::Set { row, evidence })
            if strong(*row) || c.bound_value(q, *row, &evidence.value) =>
        {
            (b, "independent_source_assignment")
        }
        (Reading::Set { row, evidence }, Reading::Missing)
            if strong(*row) || c.bound_value(q, *row, &evidence.value) =>
        {
            (a, "grounded_source_assignment")
        }
        (Reading::Clarify { kind: x, .. }, Reading::Clarify { kind: y, .. }) if x == y => {
            (b, "source_requires_user_choice")
        }
        (Reading::Missing, Reading::Clarify { .. }) => (b, "source_requires_user_choice"),
        (Reading::Clarify { .. }, Reading::Missing) => (a, "source_requires_user_choice"),
        (Reading::Clarify { .. }, Reading::Set { .. }) => (a, "source_requires_user_choice"),
        (Reading::Set { .. }, Reading::Clarify { .. }) => (b, "source_requires_user_choice"),
        (Reading::Unset(_), _) | (_, Reading::Unset(_)) => {
            (Reading::Missing, "unconfirmed_current_value")
        }
        _ => (Reading::Missing, "no_independent_resolution"),
    }
}

/// Aggregate questions need the existing exhaustive source traversal.
pub(super) fn answer(engine: &mut Engine<'_>, source: &str, q: &str) -> Run<Option<Output>> {
    if source.len() > passages::BYTE_LIMIT || q.len() > 4096 || indexed::AGGREGATE.is_match(q) {
        return Ok(None);
    }
    let lines = document::segments(source);
    if lines.is_empty() || lines.len() > passages::ROW_LIMIT {
        return Ok(None);
    }
    let cautious = engine.metrics.answer_mode == Some(AnswerMode::Cautious);
    let c = Corpus::new(lines, q);
    passages::init_trace(engine, "scoped_passages", c.rows.len());
    let (first, initial) = read(engine, q, &c, false, cautious, false)?;
    let mut check = AnswerCheck {
        first_state: first.state(),
        first_value: first.reported(),
        draft_attempt: engine.policy.trace.then_some(engine.metrics.calls),
        ..Default::default()
    };
    let incomplete = initial.len() < c.rows.len();
    if let Some((kind, question)) = choice::question(&c, q, &initial) {
        check.resolution = "explicit_open_selector";
        engine.metrics.answer_checks.push(check);
        return Ok(Some(
            if cautious {
                Reading::Clarify {
                    kind: kind.into(),
                    question,
                }
            } else {
                Reading::Missing
            }
            .output(),
        ));
    }
    let need = cautious
        || match &first {
            Reading::Set { .. } => c.conflict_in(q, &initial),
            Reading::Missing => incomplete,
            Reading::Unset(i) => {
                incomplete
                    && !checked::STATUS.is_match(c.rows[*i].text)
                    && !passages::closed(c.rows[*i].text)
            }
            Reading::Clarify { .. } => false,
        };
    if !need {
        check.resolution = "direct_passage";
        engine.metrics.answer_checks.push(check);
        return Ok(Some(first.output()));
    }
    let before = engine.metrics.sent_input_tokens;
    let (second, final_ids) = read(engine, q, &c, incomplete, cautious, true)?;
    check.second_input_tokens = engine.metrics.sent_input_tokens - before;
    check.second_state = Some(second.state());
    check.second_value = second.reported();
    let (result, resolution) = if let Some((kind, question)) = choice::question(&c, q, &final_ids) {
        (
            if cautious {
                Reading::Clarify {
                    kind: kind.into(),
                    question,
                }
            } else {
                Reading::Missing
            },
            "explicit_open_selector",
        )
    } else if matches!(first, Reading::Missing)
        && matches!(second, Reading::Missing)
        && let Some((row, evidence)) = c.witness(q, &final_ids)
        && !c.pending(q, &final_ids)
    {
        (Reading::Set { row, evidence }, "single_explicit_witness")
    } else {
        resolve(first, second, &c, q)
    };
    check.resolution = resolution;
    engine.metrics.answer_checks.push(check);
    Ok(Some(result.output()))
}
