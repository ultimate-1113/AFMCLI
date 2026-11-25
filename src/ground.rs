use crate::{
    contract,
    document::{self, Evidence, Segment, Span},
    engine::{ContextMode, Engine, Fail, Output, Run, request},
    fm::Request,
    job::{Field, Job},
};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

mod compact;
mod indexed;
mod numbered;
mod review;

fn window_budget(engine: &Engine<'_>) -> usize {
    // Leave room for validation feedback when a long compact-mode job has to
    // split its source. A single segment may still use the full model budget.
    let cap = engine.policy.input_budget;
    let reserve = if engine
        .policy
        .compact
        .is_some_and(|mode| mode != ContextMode::Repeats)
        && engine.metrics.context.is_some()
        && engine.policy.repairs > 0
    {
        (cap / 8).min(64)
    } else {
        0
    };
    cap - reserve
}

/// Find a contiguous prefix that fits. Every portion is processed or the entire task is held.
fn window<'a>(
    engine: &mut Engine<'_>,
    s: &[Segment<'a>],
    make: impl Fn(&[Segment<'a>]) -> Request,
) -> Run<usize> {
    let cap = engine.policy.input_budget;
    let budget = window_budget(engine);
    let byte_limit = budget.saturating_mul(6);
    let mut bytes = 0;
    let mut high = 0;
    for line in s {
        bytes += line.text.len() + 16;
        if bytes > byte_limit && high > 0 {
            break;
        }
        high += 1;
    }
    if engine.count(&make(&s[..high]))? <= budget {
        return Ok(high);
    }
    let mut lo = 0;
    while lo + 1 < high {
        let mid = lo + (high - lo) / 2;
        if engine.count(&make(&s[..mid]))? <= budget {
            lo = mid;
        } else {
            high = mid;
        }
    }
    if lo == 0 {
        if budget < cap && engine.count(&make(&s[..1]))? <= cap {
            return Ok(1);
        }
        return Err(Fail::Held(
            "one source segment and the task instructions exceed the input budget".into(),
        ));
    }
    Ok(lo)
}
fn excerpt<'a>(source: &'a str, s: &[Segment<'_>]) -> &'a str {
    &source[s[0].start..s.last().unwrap().end()]
}
fn field_request(fields: &[Field], source: &str) -> Request {
    let s = contract::object(fields.iter().map(|f| {
        (
            f.name.clone(),
            json!({"type":"array","items":{"type":"string"}}),
        )
    }));
    request(
        "Extract source text for each field. Copy each requested value verbatim and as briefly as possible. Use [] for absent or unsupported information. For many=false return at most one value. Observe corrections, cancellations and negation.",
        json!({"fields":fields,"source":source}),
        Some(s),
    )
}
fn answer_request(question: &str, source: &str, direct: bool) -> Request {
    if !direct {
        return request(
            "Decide whether the passage explicitly supplies the requested fact. If missing, answerable=false and answer is the empty string. Otherwise return the shortest exact answer span: for who, where, when or how many, copy just the name, place, time or quantity. Example: passage 'The fabric was linen.' / question 'Which material?' gives linen. If the passage gives a different attribute, abstain. For example, a package weight does not answer a question about its height. 資料に質問の答えが書いていない場合は空文字を返す。Preserve the source language.",
            json!({"question":question,"passage":source}),
            Some(contract::object([
                ("answerable".into(), json!({"type":"boolean"})),
                ("answer".into(), json!({"type":"string"})),
            ])),
        );
    }
    request(
        "Answer the question using the shortest exact quote from the source that answers it. Consider corrections and negation. If the source does not establish an answer, return an empty string.",
        json!({"question":question,"source":source}),
        Some(contract::object([(
            "answer".into(),
            json!({"type":"string"}),
        )])),
    )
}
type Located = BTreeMap<String, BTreeMap<String, BTreeSet<Span>>>;
fn support_request(question: &str, lines: &[Segment<'_>]) -> Request {
    let evidence: Vec<_> = lines
        .iter()
        .map(|s| json!({"id":s.id,"text":s.text}))
        .collect();
    request(
        "Select up to three sentence IDs that together give the requested fact. Include corrections or qualifications that change the answer. Select [] when this passage provides no evidence for the specific question.",
        json!({"question":question,"sentences":evidence}),
        Some(contract::object([(
            "ids".into(),
            json!({"type":"array","items":{"type":"integer"}}),
        )])),
    )
}

fn read_selected(engine: &mut Engine<'_>, question: &str, lines: &[Segment<'_>]) -> Run<Located> {
    let selected = if lines.len() == 1 {
        lines.to_vec()
    } else {
        engine.structured(support_request(question, lines), |v| {
            let mut picked = BTreeSet::new();
            for n in v["ids"].as_array().unwrap() {
                let id = n
                    .as_u64()
                    .and_then(|i| usize::try_from(i).ok())
                    .ok_or("invalid sentence ID")?;
                if !lines.iter().any(|s| s.id == id) || !picked.insert(id) || picked.len() > 3 {
                    return Err("select up to three distinct IDs from the passage".into());
                }
            }
            Ok(lines
                .iter()
                .copied()
                .filter(|s| picked.contains(&s.id))
                .collect())
        })?
    };
    if selected.is_empty() {
        return Ok(Located::new());
    }
    let passages: Vec<_> = selected.iter().map(|s| s.text).collect();
    let reader = answer_request(question, &passages.join("\n"), false);
    engine.structured(reader, |v| {
        let answer = v["answer"].as_str().unwrap();
        if v["answerable"].as_bool() != Some(!answer.is_empty()) {
            return Err("answerable and the proposed answer must agree".into());
        }
        if answer.is_empty() {
            return Ok(Located::new());
        }
        let mut spans = BTreeSet::new();
        for s in &selected {
            if let Ok(hits) = document::locate(s.text, s.start, answer) {
                spans.extend(hits);
            }
        }
        if spans.is_empty() {
            return Err(
                "copy an exact answer from a selected sentence or return an empty string".into(),
            );
        }
        Ok(BTreeMap::from([(
            "answer".into(),
            BTreeMap::from([(answer.into(), spans)]),
        )]))
    })
}
fn gather(
    fields: &[Field],
    is_answer: bool,
    v: &Value,
    source: &str,
    offset: usize,
) -> Result<Located, String> {
    let mut collected = Located::new();
    for f in fields {
        let proposed: Vec<&str> = if is_answer {
            let answer = v["answer"].as_str().ok_or("answer must be a string")?;
            if v["answerable"].as_bool() != Some(!answer.is_empty()) {
                return Err(
                    "answerable must agree with the presence of a verbatim answer span".into(),
                );
            }
            if answer.is_empty() {
                vec![]
            } else {
                vec![answer]
            }
        } else {
            v[&f.name]
                .as_array()
                .ok_or("field must be a list")?
                .iter()
                .map(|x| x.as_str().ok_or("value must be text"))
                .collect::<Result<_, _>>()?
        };
        if !f.many && proposed.len() > 1 {
            return Err(format!("field {} accepts at most one value", f.name));
        }
        let values = collected.entry(f.name.clone()).or_default();
        for value in proposed {
            if values.contains_key(value) {
                return Err("return distinct values per field".into());
            }
            values.insert(value.to_owned(), document::locate(source, offset, value)?);
        }
    }
    Ok(collected)
}
fn extract(
    engine: &mut Engine<'_>,
    source: &str,
    fields: &[Field],
    question: Option<&str>,
) -> Run<Output> {
    let direct = engine.policy.direct;
    let make = |part: &str| match question {
        Some(q) => answer_request(q, part, direct),
        None => field_request(fields, part),
    };
    if engine.policy.direct {
        return engine.raw(make(source));
    }
    let lines = document::segments(source);
    let mut first_window = None;
    if let Some(q) =
        question.filter(|_| engine.policy.compact.is_some() && !engine.policy.decompose)
    {
        let budget = window_budget(engine);
        let n = window(engine, &lines, |ls| make(excerpt(source, ls)))?;
        if n < lines.len()
            && let Some(result) = compact::answer(engine, source, q, &lines)?
        {
            return Ok(result);
        }
        if window_budget(engine) == budget {
            first_window = Some(n);
        }
    }
    let mut offset = 0;
    let mut merged = Located::new();
    while offset < lines.len() {
        let rest = &lines[offset..];
        let (n, local) = if let Some(q) = question.filter(|_| engine.policy.decompose) {
            let n = window(engine, rest, |ls| support_request(q, ls))?;
            (n, read_selected(engine, q, &rest[..n])?)
        } else {
            let n = match first_window.take() {
                Some(n) => n,
                None => window(engine, rest, |ls| make(excerpt(source, ls)))?,
            };
            let block = excerpt(source, &rest[..n]);
            (
                n,
                engine.structured(make(block), |v| {
                    gather(fields, question.is_some(), v, block, rest[0].start)
                })?,
            )
        };
        for (field, values) in local {
            let dest = merged.entry(field).or_default();
            for (value, spans) in values {
                dest.entry(value).or_default().extend(spans);
            }
        }
        offset += n;
    }
    let mut notes = vec![];
    let mut result = serde_json::Map::new();
    let mut populated = 0;
    for f in fields {
        let values = merged.remove(&f.name).unwrap_or_default();
        let mut entries = if !f.many && values.len() > 1 {
            notes.push(format!(
                "{}: source windows produced conflicting values",
                f.name
            ));
            vec![]
        } else {
            let mut entries: Vec<_> = values
                .into_iter()
                .map(|(value, spans)| Evidence { value, spans })
                .collect();
            entries.sort_by_key(|e| e.spans.first().map(|s| s.start_byte));
            entries
        };
        if engine.policy.check_support
            && let (Some(q), Some(item)) = (question, entries.first())
        {
            let mut passages = Vec::new();
            for span in item.spans.iter().take(3) {
                let first = lines.partition_point(|l| l.end() <= span.start_byte);
                let end = lines.partition_point(|l| l.start < span.end_byte);
                if first < end {
                    let piece = excerpt(
                        source,
                        &lines[first.saturating_sub(1)..(end + 1).min(lines.len())],
                    );
                    if !passages.contains(&piece) {
                        passages.push(piece);
                    }
                }
            }
            let r = request(
                "Does this exact candidate actually supply the specific fact the question asks for, according to the excerpts? A different attribute, a superseded fact, or an announcement that the fact is unknown is insufficient. Return true only when the excerpts support the answer.",
                json!({"question":q,"candidate":item.value,"excerpts":passages}),
                Some(contract::object([(
                    "supported".into(),
                    json!({"type":"boolean"}),
                )])),
            );
            let supported = engine.structured(r, |v| Ok(v["supported"].as_bool().unwrap()))?;
            if !supported {
                entries.clear();
                notes.push("a separate model reading did not confirm the proposed answer".into());
            }
        }
        if !entries.is_empty() {
            populated += 1;
        }
        result.insert(
            f.name.clone(),
            if question.is_some() {
                json!(entries.first())
            } else {
                json!(entries)
            },
        );
    }
    let status = if populated == 0 {
        "abstained"
    } else if populated == fields.len() {
        "ok"
    } else {
        "partial"
    };
    Ok(Output {
        status,
        value: Value::Object(result),
        notes,
    })
}
fn selection(s: &[Segment<'_>], n: usize, focus: &str) -> Request {
    let rows: Vec<_> = s.iter().map(|l| json!({"id":l.id,"text":l.text})).collect();
    let schema = contract::object([(
        "ids".into(),
        json!({"type":"array","items":{"type":"integer"}}),
    )]);
    request(
        "Select the source segments most important for the requested summary. Return their integer ids once each, at most limit. With a focus, choose only relevant segments; [] means there is no relevant segment. Source order will be restored.",
        json!({"focus":focus,"limit":n,"segments":rows}),
        Some(schema),
    )
}
fn select<'a>(
    engine: &mut Engine<'_>,
    s: &[Segment<'a>],
    n: usize,
    focus: &str,
) -> Run<Vec<Segment<'a>>> {
    if s.len() <= n && focus.trim().is_empty() {
        return Ok(s.to_vec());
    }
    let ids = engine.structured(selection(s, n, focus), |v| {
        let ids = v["ids"].as_array().ok_or("provide an ids array")?;
        if ids.len() > n || (ids.is_empty() && focus.trim().is_empty()) {
            return Err(format!(
                "return between {} and {n} distinct segment ids",
                usize::from(focus.trim().is_empty())
            ));
        }
        let mut picked = BTreeSet::new();
        for v in ids {
            let id = v
                .as_u64()
                .and_then(|v| usize::try_from(v).ok())
                .ok_or("id must be positive")?;
            if !s.iter().any(|l| l.id == id) || !picked.insert(id) {
                return Err("select distinct ids that appear in the offered segments".into());
            }
        }
        Ok(picked)
    })?;
    Ok(s.iter().copied().filter(|l| ids.contains(&l.id)).collect())
}
fn summarize(engine: &mut Engine<'_>, source: &str, n: usize, focus: &str) -> Run<Output> {
    let mut candidates = document::segments(source);
    if engine.policy.direct {
        return engine.raw(selection(&candidates, n, focus));
    }
    loop {
        let before = candidates.len();
        let mut filtered = vec![];
        let mut i = 0;
        while i < candidates.len() {
            let remaining = &candidates[i..];
            let take = window(engine, remaining, |ls| selection(ls, n, focus))?;
            filtered.extend(select(engine, &remaining[..take], n, focus)?);
            i += take;
        }
        if filtered.len() <= n {
            let result: Vec<_> = filtered
                .iter()
                .map(|l| json!({"id":l.id,"text":l.text,"start_byte":l.start,"end_byte":l.end()}))
                .collect();
            return Ok(Output::new(
                if result.is_empty() { "abstained" } else { "ok" },
                json!({"segments":result}),
            ));
        }
        if filtered.len() >= before {
            return Err(Fail::Held(
                "requested summary cannot be reduced within the input budget".into(),
            ));
        }
        candidates = filtered;
    }
}
pub fn execute(engine: &mut Engine<'_>, job: &Job) -> Run<Output> {
    match job {
        Job::Answer { question, source } => extract(
            engine,
            source,
            &[Field {
                name: "answer".into(),
                description: question.clone(),
                many: false,
            }],
            Some(question),
        ),
        Job::Extract { fields, source } => extract(engine, source, fields, None),
        Job::Summarize {
            source,
            focus,
            sentences,
        } => summarize(engine, source, *sentences, focus),
        _ => unreachable!(),
    }
}
