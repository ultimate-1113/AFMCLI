//! Two separately generated readings of the same original evidence.
use super::{arbiter, compact, excerpt, window};
use crate::{
    contract,
    document::{self, Evidence, Segment, Span},
    engine::{AnswerCheck, ContextMode, Engine, Output, Run, request},
    fm::Request,
    json as wire,
};
use regex::Regex;
use serde_json::{Value, json};
use std::{collections::BTreeSet, sync::LazyLock};

pub(super) struct Finding {
    pub state: &'static str,
    pub value: String,
    pub clause: String,
    pub spans: BTreeSet<Span>,
    pub value_spans: BTreeSet<Span>,
}
impl Finding {
    fn missing() -> Self {
        Self {
            state: "missing",
            value: String::new(),
            clause: String::new(),
            spans: BTreeSet::new(),
            value_spans: BTreeSet::new(),
        }
    }
    pub(super) fn output(self) -> Output {
        if self.state == "set" {
            Output::new(
                "ok",
                json!({"answer":Evidence{value:self.value, spans:self.value_spans}}),
            )
        } else {
            Output::new("abstained", json!({"answer":null}))
        }
    }
    fn reported_value(&self) -> Option<String> {
        (self.state == "set").then(|| self.value.clone())
    }
}

static STATUS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?ix)
    \b(pending|tentative(?:ly)?|propos(?:al|ed)|suggested|withdraw\w*|withdrew|retract\w*|revok\w*|
    cancell?ed|unconfirmed|undecided|unassigned|unspecified|unapproved|awaiting|unknown|
    on\s+hold|under\s+(?:review|discussion|consideration)|not\s+(?:yet\s+)?(?:approved|decided|confirmed|assigned|known|available)|
    without\s+(?:a\s+)?replacement|no\s+(?:replacement|decision|approved|assignment|longer))\b
    |審議|検討中|未定|未承認|未確定|未発行|未割当|未決|不明|保留|撤回|取り消|取消|見送|案の段階|候補|提案|まだ.{0,12}ない|決まっていない|承認されていない").unwrap()
});
pub(super) static CHANGE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?ix)
    \b(amend\w*|replac\w*|corrected|correction|updat\w*|supersed\w*|confirm\w*|
    approv\w*|assigned|reassigned|moved|instead|finaliz\w*|ratified|adopted)\b
    |再決定|再指定|訂正|変更|確定|承認|発行|決定|指定|割り当て|改定|成立",
    )
    .unwrap()
});
static WANTS_STATE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?ix)
    \b(status|state\s+of|initial(?:ly)?|original(?:ly)?|propos(?:ed|al)|tentative|previous|former|before)\b
    |(?:状況|状態|当初|初回|最初|以前|仮称|仮案|提案された|候補だった)").unwrap()
});
static NEG_VALUE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?ix)
    ^(?:pending(?:\s+approval)?|unknown|undecided|unassigned|not\s+(?:yet\s+)?(?:approved|assigned|decided|confirmed)|
    (?:under\s+(?:review|discussion)|no\s+(?:venue|code|value|replacement)\s+(?:has|was|is)\b).*)[.。]?$|
    ^(?:審議中|検討中|未定|未確定|未承認|保留|未発行|未割当|不明)(?:[。.]|(?:で|のまま|です|となって|につき).*)?$|
    (?:されていない|まだない|決まっていない|承認されておらず)[。.]?$").unwrap()
});

fn literal_name(clause: &str, value: &str) -> bool {
    [
        ("「", "」"),
        ("『", "』"),
        ("“", "”"),
        ("\"", "\""),
        ("'", "'"),
    ]
    .iter()
    .any(|(a, b)| clause.contains(&format!("{a}{value}{b}")))
}

// Restrict the predicate to the mention itself. A negative term inside an assigned
// proper name or a separate notice about another entity is not a withdrawal.
fn tentative_mention(clause: &str, value: &str) -> bool {
    static BEFORE: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?ix)
        \b(?:proposed|suggested|considered|nominated|revoked|withdrew|cancelled|canceled)\s+(?:the\s+)?$|
        \b(?:candidate|proposal|provisional|tentative)(?:\s+(?:code|venue|value))?\s*[:=]\s*$|
        (?:仮案|候補地|候補会場)(?:は|として|：)\s*$").unwrap()
    });
    static AFTER: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?ix)
        ^\s+(?:is|was|remains|has\s+been)\s+(?:(?:only|still)\s+)?(?:a\s+)?(?:proposal|candidate|tentative|withdrawn|unconfirmed|revoked|not\s+approved)\b|
        ^(?:を提案|を候補|を撤回|を取り消|は保留|は未定|は未承認|は仮案)").unwrap()
    });
    let mut found = false;
    for (at, _) in clause.match_indices(value) {
        let before = clause[..at].trim_end_matches(['「', '『', '“', '"']);
        let after = clause[at + value.len()..].trim_start_matches(['」', '』', '”', '"']);
        found = true;
        if !BEFORE.is_match(before) && !AFTER.is_match(after) {
            return false;
        }
    }
    found
}
fn field_conflict(question: &str, clause: &str, value: &str) -> bool {
    static TYPES: LazyLock<Vec<Regex>> = LazyLock::new(|| {
        [
        r"\b(?:code|identifier|serial|password|pin|reference number)\b|符号|コード|暗証|識別子|受付番号|管理番号",
        r"\b(?:colou?r|hue|tint)\b|識別色|何色|色名|色彩",
        r"\b(?:venue|location|hall|room|site|address)\b|会場|場所|所在地|部屋|住所",
        r"\b(?:weight|height|length|width|temperature)\b|重量|重さ|高さ|長さ|温度",
        r"\b(?:deadline|date|time)\b|期限|締切|日時|時刻|日付",
    ].into_iter().map(|p| Regex::new(&format!("(?i){p}")).unwrap()).collect()
    });
    let wanted: Vec<_> = TYPES
        .iter()
        .enumerate()
        .filter(|(_, r)| r.is_match(question))
        .map(|(i, _)| i)
        .collect();
    let [want] = wanted.as_slice() else {
        return false;
    };
    let surrounding = if value.is_empty() {
        clause.to_owned()
    } else {
        clause.replace(value, " ")
    };
    !TYPES[*want].is_match(&surrounding)
        && TYPES
            .iter()
            .enumerate()
            .any(|(i, r)| i != *want && r.is_match(&surrounding))
}

// Reject an explicit different named entity in the same grammatical role. Leave
// pronouns, omitted subjects, and clauses mentioning multiple entities to the readers.
fn subject_conflict(question: &str, clause: &str, value: &str) -> bool {
    static NAMED: LazyLock<Vec<Regex>> = LazyLock::new(|| {
        [
            r"\b([A-Z][A-Za-z0-9-]*)\s+(team|group|division|department)\b",
            r"\b([Pp]roject|[Tt]eam|[Uu]nit|[Cc]ase)\s+([A-Z][A-Za-z0-9-]*|[A-Z]*-?[0-9]+)\b",
            r"([\p{Han}\p{Katakana}A-Za-z0-9-]{1,16})(班|課|便|組)",
        ]
        .into_iter()
        .map(|p| Regex::new(p).unwrap())
        .collect()
    });
    let surrounding = if value.is_empty() {
        clause.to_owned()
    } else {
        clause.replace(value, " ")
    };
    for (i, re) in NAMED.iter().enumerate() {
        let names = |s: &str| {
            re.captures_iter(s)
                .map(|m| {
                    let (name, role) = if i == 1 {
                        (&m[2], &m[1])
                    } else {
                        (&m[1], &m[2])
                    };
                    (name.to_ascii_lowercase(), role.to_ascii_lowercase())
                })
                .filter(|(name, _)| {
                    !matches!(
                        name.as_str(),
                        "the"
                            | "which"
                            | "whose"
                            | "what"
                            | "this"
                            | "that"
                            | "our"
                            | "当"
                            | "各"
                            | "同"
                            | "何"
                            | "全"
                    )
                })
                .collect::<BTreeSet<_>>()
        };
        let asked = names(question);
        let described = names(&surrounding);
        if asked.len() == 1 && described.len() == 1 {
            let (a, ar) = asked.first().unwrap();
            let (b, br) = described.first().unwrap();
            if ar == br && a != b {
                return true;
            }
        }
    }
    false
}

pub(super) fn source_finding(v: &Value, parts: &[(usize, &str)], question: &str) -> Finding {
    let clause = v["clause"].as_str().unwrap_or("").trim();
    let value = v["value"].as_str().unwrap_or("").trim();
    let Ok(spans) = document::locate_parts(parts, clause) else {
        return Finding::missing();
    };
    let state = match v["state"].as_str() {
        Some("unset") if value.is_empty() && STATUS.is_match(clause) => "unset",
        Some("set") if !value.is_empty() && value != clause => "set",
        _ => return Finding::missing(),
    };
    if field_conflict(question, clause, value) || subject_conflict(question, clause, value) {
        return Finding::missing();
    }
    let mut value_spans = BTreeSet::new();
    if state == "set" {
        if !WANTS_STATE.is_match(question)
            && ((NEG_VALUE.is_match(value) && !literal_name(clause, value))
                || tentative_mention(clause, value))
        {
            return Finding::missing();
        }
        for s in &spans {
            if let Ok(located) = document::locate(clause, s.start_byte, value) {
                value_spans.extend(located);
            }
        }
        if value_spans.is_empty() {
            return Finding::missing();
        }
    }
    Finding {
        state,
        value: value.into(),
        clause: clause.into(),
        spans,
        value_spans,
    }
}

fn columns(draft: bool) -> Value {
    let mut fields = vec![
        (
            "state".into(),
            json!({"type":"string","enum":["set","unset","missing"]}),
        ),
        ("clause".into(), json!({"type":"string"})),
        ("value".into(), json!({"type":"string"})),
    ];
    if draft {
        for k in ["subject", "field", "old"] {
            fields.push((k.into(), json!({"type":"string"})));
        }
        fields.push((
            "change".into(),
            json!({"type":"string","enum":["assigned","replaced","withdrawn","pending","none"]}),
        ));
    }
    contract::object(fields)
}

pub(super) fn reader(question: &str, material: &Value) -> Request {
    request(
        "Track the requested subject and field to the time asked. state=set for an established value, unset for an explicit withdrawal or pending decision, missing for no matching fact. A proposal alone is unconfirmed. Copy the deciding clause exactly. Copy only its value when set, otherwise empty. Give subject, field, earlier value (old) and change briefly; use empty for unknown. Encoded log/dictionary rows preserve source order.",
        json!({"question":question,"material":material}),
        Some(columns(true)),
    )
}

fn second(question: &str, material: &Value, handoff: Option<&Value>) -> Request {
    let mut data = json!({"question":question,"source":material});
    if let Some(h) = handoff {
        data["handoff"] = h.clone();
    }
    request(
        "Determine the final answer from source. A handoff, if present, is a hypothesis, not evidence. Check the exact subject, requested property, and time. A different property cannot fill the requested one. Read later applicable amendments; a change applying elsewhere does not supersede this fact. state=set only for an established value, unset for a pending/withdrawn decision, missing if no matching fact. Copy the deciding source clause verbatim, and ONLY the requested value for set. Else value is empty.",
        data,
        Some(columns(false)),
    )
}

struct Draft {
    raw: Value,
    bytes: usize,
    attempt: Option<usize>,
    finding: Finding,
}

fn first(
    engine: &mut Engine<'_>,
    question: &str,
    parts: &[(usize, &str)],
    req: Request,
) -> Run<Draft> {
    let schema = req.schema.clone().unwrap();
    let (raw, bytes) = engine.checked(req, |text| {
        let v = wire::parse(text)?;
        contract::validate(&schema, &v)?;
        Ok((v, text.len()))
    })?;
    let attempt = engine.policy.trace.then_some(engine.metrics.calls);
    let finding = source_finding(&raw, parts, question);
    Ok(Draft {
        raw,
        bytes,
        attempt,
        finding,
    })
}

fn capsule(draft: &Draft, question: &str, parts: &[(usize, &str)]) -> Value {
    let f = &draft.finding;
    let mut out = json!({"state":f.state});
    for key in ["subject", "field", "old", "value"] {
        let v = draft.raw[key].as_str().unwrap_or("").trim();
        if !v.is_empty()
            && v.chars().count() <= 40
            && (question.contains(v) || parts.iter().any(|(_, s)| s.contains(v)))
        {
            out[key] = json!(v);
        }
    }
    let change = draft.raw["change"].as_str().unwrap_or("").trim();
    if !change.is_empty() && change.chars().count() <= 20 {
        out["change"] = json!(change);
    }
    let short: String = f.clause.chars().take(42).collect();
    if !short.is_empty() {
        out["excerpt"] = json!(short);
    }
    for key in ["excerpt", "change", "old", "field", "subject"] {
        if out.to_string().len() < draft.bytes {
            break;
        }
        out.as_object_mut().unwrap().remove(key);
    }
    out
}

fn material(parts: &[(usize, &str)]) -> Value {
    if let [(_, one)] = parts {
        json!(one)
    } else {
        json!(parts.iter().map(|(_, s)| s).collect::<Vec<_>>())
    }
}

// For a packed input exceeding the window, return verbatim original decisions and nearby
// qualifiers. Every explicit change is retained, or the packet is declined on budget.
fn focus<'a>(
    parts: &[(usize, &'a str)],
    anchor: &Finding,
    extra: &[Span],
) -> Vec<(usize, &'a str)> {
    let mut selected = Vec::new();
    for &(base, text) in parts {
        let lines = document::segments(text);
        let mut keep = BTreeSet::new();
        for (i, s) in lines.iter().enumerate() {
            if anchor
                .spans
                .iter()
                .chain(extra)
                .any(|a| a.start_byte < base + s.end() && base + s.start < a.end_byte)
                || STATUS.is_match(s.text)
                || CHANGE.is_match(s.text)
            {
                keep.extend(i.saturating_sub(1)..(i + 2).min(lines.len()));
            }
        }
        let mut run: Option<(usize, usize)> = None;
        for i in keep {
            if let Some((_, end)) = run.as_mut()
                && *end == i
            {
                *end = i + 1;
                continue;
            }
            if let Some((a, b)) = run {
                selected.push((base + lines[a].start, excerpt(text, &lines[a..b])));
            }
            run = Some((i, i + 1));
        }
        if let Some((a, b)) = run {
            selected.push((base + lines[a].start, excerpt(text, &lines[a..b])));
        }
    }
    selected
}

fn resolution(first: &Finding, second: &Finding) -> (&'static str, bool) {
    if first.state == second.state && first.value == second.value {
        return ("agreement", true);
    }
    if second.state == "unset" {
        let first_is_later = first
            .spans
            .first()
            .zip(second.spans.last())
            .is_some_and(|(f, s)| f.start_byte >= s.end_byte);
        if first.state == "set" && first_is_later && CHANGE.is_match(&first.clause) {
            return ("retained_later_assignment", false);
        }
        return ("source_unset", true);
    }
    let later = second
        .spans
        .first()
        .zip(first.spans.last())
        .is_some_and(|(s, f)| s.start_byte >= f.end_byte);
    if second.state == "set" && later {
        ("later_assignment", true)
    } else {
        ("disagreement", false)
    }
}

fn checked(
    engine: &mut Engine<'_>,
    question: &str,
    parts: &[(usize, &str)],
    d: Draft,
    whole: bool,
    extra: &[Span],
    original: &str,
) -> Run<Finding> {
    let mut log = AnswerCheck {
        first_state: d.finding.state,
        first_value: d.finding.reported_value(),
        draft_bytes: d.bytes,
        draft_attempt: d.attempt,
        ..Default::default()
    };
    if d.finding.state == "missing" {
        log.resolution = "no_grounded_draft";
        engine.metrics.answer_checks.push(log);
        return Ok(Finding::missing());
    }
    let handoff = capsule(&d, question, parts);
    let may_send = matches!(
        engine.policy.compact,
        Some(ContextMode::IndexHandoff | ContextMode::IndexHandoffReview)
    );
    let cap = engine.policy.input_budget;
    // Use the very same original ranges in the blinded and handoff arms, including at the cap.
    let mut offered = parts.to_vec();
    let raw_bytes: usize = parts.iter().map(|(_, s)| s.len()).sum();
    if !whole && raw_bytes > cap.saturating_mul(6) {
        offered = focus(parts, &d.finding, extra);
    }
    let mut data = material(&offered);
    let mut with = second(question, &data, Some(&handoff));
    if with.prompt.len() > cap.saturating_mul(12) || engine.count(&with)? > cap {
        offered = focus(parts, &d.finding, extra);
        data = material(&offered);
        with = second(question, &data, Some(&handoff));
        if offered.is_empty()
            || with.prompt.len() > cap.saturating_mul(12)
            || engine.count(&with)? > cap
        {
            log.resolution = "second_input_budget";
            engine.metrics.answer_checks.push(log);
            return Ok(Finding::missing());
        }
    }
    let req = if may_send {
        with
    } else {
        second(question, &data, None)
    };
    let before = engine.metrics.sent_input_tokens;
    let outcome = engine.structured(req, |v| Ok(source_finding(v, &offered, question)));
    log.second_input_tokens = engine.metrics.sent_input_tokens - before;
    if may_send && log.second_input_tokens > 0 {
        log.handoff_bytes = handoff.to_string().len();
        log.handoff = Some(handoff);
        log.handoff_sent = true;
    }
    let second = match outcome {
        Ok(f) => f,
        Err(e) => {
            log.resolution = "second_failed";
            engine.metrics.answer_checks.push(log);
            return Err(e);
        }
    };
    log.second_state = Some(second.state);
    log.second_value = second.reported_value();
    let (reason, accept) = resolution(&d.finding, &second);
    log.resolution = reason;
    let mut answer = Finding::missing();
    if reason != "agreement" && engine.policy.compact == Some(ContextMode::IndexHandoffReview) {
        if engine
            .metrics
            .answer_checks
            .iter()
            .any(|a| a.review_triggered)
        {
            log.review_note = Some("job_limit");
        } else {
            log.review_triggered = true;
            let spans: Vec<_> = d.finding.spans.union(&second.spans).cloned().collect();
            let calls = engine.metrics.calls;
            let reviewed = arbiter::decide(engine, question, &[(0, original)], &spans);
            log.review_sent = engine.metrics.calls > calls;
            match reviewed {
                Ok(Some(third)) => {
                    log.review_state = Some(third.state);
                    log.review_value = third.reported_value();
                    if third.state != "missing"
                        && [&d.finding, &second]
                            .iter()
                            .any(|f| f.state == third.state && f.value == third.value)
                    {
                        log.resolution = "independent_confirmation";
                        engine.metrics.answer_checks.push(log);
                        return Ok(third);
                    }
                    // The independent reader can find a still later decision missed by both
                    // earlier readings. Require the original clause to follow both citations.
                    let later = third.spans.first().is_some_and(|t| {
                        [&d.finding, &second]
                            .iter()
                            .all(|f| f.spans.last().is_some_and(|p| p.end_byte <= t.start_byte))
                    });
                    if third.state != "missing"
                        && later
                        && (CHANGE.is_match(&third.clause) || third.state == "unset")
                    {
                        log.resolution = "independent_later_decision";
                        engine.metrics.answer_checks.push(log);
                        return Ok(third);
                    }
                    log.resolution = "independent_disagreement";
                    engine.metrics.answer_checks.push(log);
                    return Ok(Finding::missing());
                }
                Ok(None) => {
                    log.review_note = Some("unavailable_or_budget");
                }
                Err(e) => {
                    log.review_note = Some("review_failed");
                    engine.metrics.answer_checks.push(log);
                    return Err(e);
                }
            }
        }
    }
    if accept {
        answer = second;
    } else if reason == "retained_later_assignment" {
        answer = d.finding;
    }
    engine.metrics.answer_checks.push(log);
    Ok(answer)
}

pub(super) fn read(
    engine: &mut Engine<'_>,
    question: &str,
    parts: &[(usize, &str)],
    req: Request,
    whole: bool,
    original: &str,
) -> Run<Finding> {
    let draft = first(engine, question, parts, req)?;
    checked(engine, question, parts, draft, whole, &[], original)
}

pub(super) fn extract(
    engine: &mut Engine<'_>,
    source: &str,
    q: &str,
    lines: &[Segment<'_>],
) -> Run<Output> {
    let make = |ls: &[Segment<'_>]| reader(q, &json!(excerpt(source, ls)));
    let n = window(engine, lines, make)?;
    if n == lines.len() {
        let full = excerpt(source, lines);
        return read(
            engine,
            q,
            &[(lines[0].start, full)],
            reader(q, &json!(full)),
            true,
            source,
        )
        .map(Finding::output);
    }
    if let Some(out) = compact::answer(engine, source, q, lines)? {
        return Ok(out);
    }
    // The lossless, ranked and exhaustive selection paths could not construct a packet.
    // Read each remaining window. A claim is usable only with evidence in its own window.
    let mut drafts = Vec::new();
    let mut i = 0;
    while i < lines.len() {
        let n = window(engine, &lines[i..], make)?;
        let block = &lines[i..i + n];
        let parts = [(block[0].start, excerpt(source, block))];
        let d = first(engine, q, &parts, make(block))?;
        if d.finding.state != "missing" {
            drafts.push(d);
        }
        i += n;
    }
    let Some(last) = drafts.pop() else {
        return Ok(Finding::missing().output());
    };
    let extra: Vec<_> = drafts.into_iter().flat_map(|d| d.finding.spans).collect();
    checked(engine, q, &[(0, source)], last, false, &extra, source).map(Finding::output)
}
