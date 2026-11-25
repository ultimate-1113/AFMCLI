use super::{excerpt, window};
use crate::{
    contract,
    document::{self, Evidence, Segment},
    engine::{ContextUsage, Engine, Fail, Output, Run, request},
    fm::Request,
};
use serde::Serialize;
use serde_json::{Value, json};
use std::collections::{BTreeSet, HashMap};

// A string in the sequence is literal. [index, count] expands to count copies of
// dictionary[index]. The slices include whitespace, so expansion is byte-for-byte.
#[derive(Serialize)]
#[serde(untagged)]
enum Piece<'a> {
    Literal(&'a str),
    Repeat([usize; 2]),
}

#[derive(Serialize)]
struct Packed<'a> {
    dictionary: Vec<&'a str>,
    sequence: Vec<Piece<'a>>,
}

fn pack<'a>(source: &'a str, lines: &[Segment<'_>], byte_limit: usize) -> Option<Packed<'a>> {
    let pieces = || {
        let mut start = 0;
        lines
            .iter()
            .skip(1)
            .map(|l| l.start)
            .chain([source.len()])
            .map(move |end| {
                let piece = &source[start..end];
                start = end;
                piece
            })
    };
    let mut counts = HashMap::new();
    let mut distinct_bytes = 0;
    for piece in pieces() {
        let count = counts.entry(piece).or_insert(0usize);
        if *count == 0 {
            distinct_bytes += piece.len();
            if distinct_bytes > byte_limit {
                return None;
            }
        }
        *count += 1;
    }
    if counts.len() >= lines.len() {
        return None;
    }
    let mut dictionary = Vec::new();
    let mut indices = HashMap::new();
    let mut sequence = Vec::new();
    for piece in pieces() {
        if sequence.len() > byte_limit {
            return None;
        }
        if counts[piece] == 1 {
            sequence.push(Piece::Literal(piece));
            continue;
        }
        let id = *indices.entry(piece).or_insert_with(|| {
            dictionary.push(piece);
            dictionary.len() - 1
        });
        if let Some(Piece::Repeat([prev, count])) = sequence.last_mut()
            && *prev == id
        {
            *count += 1;
        } else {
            sequence.push(Piece::Repeat([id, 1]));
        }
    }
    Some(Packed {
        dictionary,
        sequence,
    })
}

fn reader(question: &str, context: &Value) -> Request {
    request(
        "Read the supplied material in order. For encoded material, each sequence string is literal; a pair [i,n] repeats dictionary[i] n times (zero-based). Excerpts are in original order with gaps between them. Return the shortest verbatim answer in the source language. Respect speaker, scope, corrections and cancellations. If the fact is absent, withdrawn without replacement, or conflicting without a resolution, return an empty answer.",
        json!({"question":question,"material":context}),
        Some(contract::object([(
            "answer".into(),
            json!({"type":"string"}),
        )])),
    )
}

fn read(
    engine: &mut Engine<'_>,
    question: &str,
    context: &Value,
    parts: &[(usize, &str)],
    r: Request,
) -> Run<Output> {
    let evidence = engine.structured(r, |v| {
        let value = v["answer"].as_str().unwrap();
        if value.is_empty() {
            return Ok(None);
        }
        Ok(Some(Evidence {
            value: value.to_owned(),
            spans: document::locate_parts(parts, value)?,
        }))
    })?;
    let Some(evidence) = evidence else {
        return Ok(Output::new("abstained", json!({"answer":null})));
    };
    if engine.policy.check_support {
        let verify = request(
            "Check whether the source establishes this exact candidate for the question. Read ALL the supplied material in order, including distant amendments, different speakers and unresolved alternatives. A withdrawn or superseded candidate is unsupported. In encoded material [i,n] repeats dictionary[i] n times; in excerpts gaps separate original ranges.",
            json!({"question":question,"candidate":evidence.value,"material":context}),
            Some(contract::object([(
                "supported".into(),
                json!({"type":"boolean"}),
            )])),
        );
        if !engine.structured(verify, |v| Ok(v["supported"].as_bool().unwrap()))? {
            let mut out = Output::new("abstained", json!({"answer":null}));
            out.notes
                .push("the combined source material did not confirm the candidate".into());
            return Ok(out);
        }
    }
    Ok(Output::new("ok", json!({"answer":evidence})))
}

const MAX_SELECTION: usize = 8;

fn selector(question: &str, lines: &[Segment<'_>]) -> Request {
    let rows: Vec<_> = lines.iter().map(|s| json!([s.id, s.text])).collect();
    request(
        "Read this part of a longer document or conversation. Select original sentence IDs containing evidence for the question, including earlier proposals, corrections, withdrawals, qualifications and contradictions even when this part has no final answer. Do not answer the question. For no relevant evidence return []. If more than eight sentences matter or the relevant range cannot be isolated, set keep_all=true.",
        json!({"question":question,"sentences":rows}),
        Some(contract::object([
            (
                "ids".into(),
                json!({"type":"array","items":{"type":"integer"}}),
            ),
            ("keep_all".into(), json!({"type":"boolean"})),
        ])),
    )
}

fn selected_indices(value: &Value, block: &[Segment<'_>]) -> Result<Vec<usize>, String> {
    if value["keep_all"] == true {
        return Ok(block.iter().map(|s| s.id - 1).collect());
    }
    let mut unique = BTreeSet::new();
    let bounds = block[0].id..=block.last().unwrap().id;
    for v in value["ids"].as_array().unwrap() {
        let id = v
            .as_u64()
            .and_then(|n| usize::try_from(n).ok())
            .ok_or("invalid sentence ID")?;
        if !bounds.contains(&id) || !unique.insert(id - 1) || unique.len() > MAX_SELECTION {
            return Err(
                "select up to eight distinct IDs from this window, or set keep_all=true".into(),
            );
        }
    }
    Ok(unique.into_iter().collect())
}

fn original_ranges<'a>(
    source: &'a str,
    lines: &[Segment<'_>],
    selected: &[bool],
) -> Vec<(usize, &'a str)> {
    let mut ranges = Vec::new();
    let mut start = 0;
    while start < lines.len() {
        if !selected[start] {
            start += 1;
            continue;
        }
        let end = start + selected[start..].iter().take_while(|&&v| v).count();
        ranges.push((lines[start].start, excerpt(source, &lines[start..end])));
        start = end;
    }
    ranges
}

pub(super) fn answer(
    engine: &mut Engine<'_>,
    source: &str,
    question: &str,
    lines: &[Segment<'_>],
) -> Run<Option<Output>> {
    let mut usage = ContextUsage {
        method: "repeat_dictionary",
        source_segments: lines.len(),
        retained_segments: lines.len(),
        selection_windows: 0,
    };
    let byte_limit = engine.policy.input_budget.saturating_mul(12);
    if let Some(packed) = pack(source, lines, byte_limit) {
        let context = json!(packed);
        let r = reader(question, &context);
        // Avoid passing multi-megabyte encodings to the tokenizer. Exact token
        // checks still decide whether a candidate can be sent to the model.
        if r.prompt.len() < source.len()
            && r.prompt.len() <= byte_limit
            && engine.count(&r)? <= engine.policy.input_budget
        {
            engine.metrics.context = Some(usage);
            return read(engine, question, &context, &[(0, source)], r).map(Some);
        }
    }
    usage.method = "evidence_windows";
    usage.retained_segments = 0;
    engine.metrics.context = Some(usage);
    let mut kept = vec![false; lines.len()];
    let mut pos = 0;
    while pos < lines.len() {
        let rest = &lines[pos..];
        let n = match window(engine, rest, |group| selector(question, group)) {
            Ok(n) => n,
            Err(Fail::Held(_)) => break,
            Err(e) => return Err(e),
        };
        let block = &rest[..n];
        let indices =
            engine.structured(selector(question, block), |v| selected_indices(v, block))?;
        engine.metrics.context.as_mut().unwrap().selection_windows += 1;
        for i in indices {
            // Keep the adjoining source sentences even across window boundaries.
            kept[i.saturating_sub(1)..(i + 2).min(lines.len())].fill(true);
        }
        pos += n;
    }
    let retained = kept.iter().filter(|&&v| v).count();
    engine.metrics.context.as_mut().unwrap().retained_segments = retained;
    let ranges = original_ranges(source, lines, &kept);
    if pos == lines.len() && ranges.is_empty() {
        return Ok(Some(Output::new("abstained", json!({"answer":null}))));
    }
    if pos == lines.len() && ranges.iter().map(|(_, s)| s.len()).sum::<usize>() <= byte_limit {
        let context = json!({"excerpts":ranges.iter().map(|(_, text)| *text).collect::<Vec<_>>()});
        let r = reader(question, &context);
        if engine.count(&r)? <= engine.policy.input_budget {
            return read(engine, question, &context, &ranges, r).map(Some);
        }
    }
    let usage = engine.metrics.context.as_mut().unwrap();
    usage.method = "full_scan_fallback";
    usage.retained_segments = lines.len();
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn dictionary_roundtrip_keeps_whitespace_order_and_counts() {
        let source = " \t通知。\r\n監視中。\n監視中。\n撤回。\r\n監視中。\n終了。  ";
        let lines = document::segments(source);
        let packed = pack(source, &lines, 4096).unwrap();
        let mut restored = String::new();
        for piece in packed.sequence {
            match piece {
                Piece::Literal(s) => restored.push_str(s),
                Piece::Repeat([id, n]) => restored.push_str(&packed.dictionary[id].repeat(n)),
            }
        }
        assert_eq!(restored, source);
        assert!(pack("unique", &document::segments("unique"), 4096).is_none());
    }
}
