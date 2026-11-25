use crate::document::Segment;
use regex::Regex;
use serde_json::{Value, json};
use std::{collections::BTreeSet, sync::LazyLock};

static DIGITS: LazyLock<Regex> = LazyLock::new(|| Regex::new("[0-9]+").unwrap());

fn fields(line: &str) -> Option<(Vec<&str>, String)> {
    let mut chunks = Vec::new();
    let mut values = String::new();
    let mut end = 0;
    for token in DIGITS.find_iter(line) {
        if chunks.len() == 16 {
            return None;
        }
        chunks.push(&line[end..token.start()]);
        if !values.is_empty() {
            values.push(' ');
        }
        values.push_str(token.as_str());
        end = token.end();
    }
    if chunks.is_empty() {
        return None;
    }
    chunks.push(&line[end..]);
    Some((chunks, values))
}

/// Keep the literal text of a run once, and every decimal field in every row.
/// Separators belong to the literal chunks, so zeroes, signs and CRLF survive.
pub(super) fn encode(
    source: &str,
    lines: &[Segment<'_>],
    question: &str,
    limit: usize,
) -> Option<Value> {
    let edge = |n: usize| match n {
        0 => 0,
        n if n == lines.len() => source.len(),
        n => lines[n].start,
    };
    let piece = |i| &source[edge(i)..edge(i + 1)];
    // Keep a small set of matching identifiers in their original row layout.
    // Common numbers still use the table. No field or other row is discarded.
    let identifiers: BTreeSet<_> = DIGITS.find_iter(question).map(|m| m.as_str()).collect();
    let mut visible = BTreeSet::new();
    if !identifiers.is_empty() {
        for (i, line) in lines.iter().enumerate() {
            if DIGITS
                .find_iter(line.text)
                .any(|m| identifiers.contains(m.as_str()))
            {
                visible.insert(i);
                if visible.len() > 8 {
                    visible.clear();
                    break;
                }
            }
        }
    }
    let mut output = Vec::new();
    let mut bytes = 0;
    let mut grouped = false;
    let mut i = 0;
    while i < lines.len() {
        let start = i;
        i += 1;
        if !visible.contains(&start)
            && let Some((pattern, mut rows)) = fields(piece(start))
        {
            let fixed_bytes: usize = pattern.iter().map(|s| s.len()).sum();
            while i < lines.len() && !visible.contains(&i) {
                let Some((other, digits)) = fields(piece(i)) else {
                    break;
                };
                if other != pattern {
                    break;
                }
                rows.push('\n');
                rows.push_str(&digits);
                i += 1;
                if bytes + rows.len() + fixed_bytes > limit {
                    return None;
                }
            }
            if i - start >= 4 {
                bytes += rows.len() + fixed_bytes;
                output.push(json!({"parts":pattern, "rows":rows}));
                grouped = true;
            } else {
                let literal = &source[edge(start)..edge(i)];
                bytes += literal.len();
                output.push(json!(literal));
            }
        } else {
            bytes += piece(start).len();
            output.push(json!(piece(start)));
        }
        if bytes + output.len() > limit {
            return None;
        }
    }
    grouped.then(|| json!({"log":output}))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::document;

    fn unpack(log: &Value) -> String {
        let mut text = String::new();
        for item in log["log"].as_array().unwrap() {
            if let Some(s) = item.as_str() {
                text.push_str(s);
            } else {
                let parts = item["parts"].as_array().unwrap();
                for row in item["rows"].as_str().unwrap().lines() {
                    let fields: Vec<_> = row.split(' ').collect();
                    assert_eq!(parts.len(), fields.len() + 1);
                    for (part, field) in parts.iter().zip(fields) {
                        text.push_str(part.as_str().unwrap());
                        text.push_str(field);
                    }
                    text.push_str(parts.last().unwrap().as_str().unwrap());
                }
            }
        }
        text
    }

    #[test]
    fn decimal_fields_and_matching_rows_round_trip_all_source_bytes() {
        let mut source = String::from(" 🕰\t\r\n");
        for n in 0..28 {
            if n == 13 {
                source.push_str("別の記録。\n");
            }
            source.push_str(&format!("採番 {n:03}: 記録 -{n}.05 / Ａ３ \r\n"));
        }
        source.push_str("末尾  \t");
        let lines = document::segments(&source);
        for question in ["", "採番 017 は？", "05", "番号 314159"] {
            let packet = encode(&source, &lines, question, 10_000).unwrap();
            assert_eq!(unpack(&packet), source);
            if question.contains("017") {
                assert!(packet["log"].as_array().unwrap().iter().any(|v| {
                    v.as_str()
                        .is_some_and(|s| s.contains("採番 017: 記録 -17.05"))
                }));
            }
        }
        assert!(encode(&source, &lines, "", 12).is_none());
        let short = "a1\na2\na3\n";
        assert!(encode(short, &document::segments(short), "", 500).is_none());
    }
}
