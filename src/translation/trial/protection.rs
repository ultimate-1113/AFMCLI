use super::super::{NUMBER, Translation, line_break, literal_spans, normalize, occurrences};
use regex::Regex;
use serde_json::{Value, json};
use std::{collections::BTreeMap, ops::Range, sync::LazyLock};
use unicode_normalization::UnicodeNormalization;

static CLOCK_OR_DATE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\b[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?").unwrap()
});

pub(super) fn namespace(input: &Translation) -> String {
    let text = input
        .sentences
        .iter()
        .chain(&input.names)
        .chain(&input.units)
        .chain(input.glossary.keys())
        .chain(input.glossary.values())
        .map(|s| normalize(s).to_lowercase())
        .collect::<Vec<_>>()
        .join("\n");
    let mut namespace = "mtref".to_owned();
    while text.contains(&namespace) {
        namespace.push('x');
    }
    namespace.to_ascii_uppercase()
}

/// Search in compatibility-decomposed text while retaining original UTF-8
/// offsets. A match cannot consume half of a ligature or case expansion.
struct View {
    text: String,
    offsets: Vec<Option<usize>>,
}

impl View {
    fn new(source: &str, fold: bool) -> Self {
        let mut text = String::with_capacity(source.len());
        let mut offsets = vec![Some(0)];
        for (offset, ch) in source.char_indices() {
            for decomposed in std::iter::once(ch).nfkd() {
                let c = if decomposed == '−' { '-' } else { decomposed };
                if fold {
                    text.extend(c.to_lowercase());
                } else {
                    text.push(c);
                }
            }
            offsets.resize(text.len() + 1, None);
            offsets[text.len()] = Some(offset + ch.len_utf8());
        }
        Self { text, offsets }
    }

    fn original(&self, range: Range<usize>) -> Option<Range<usize>> {
        Some(self.offsets[range.start]?..self.offsets[range.end]?)
    }
}

#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum Kind {
    Glossary,
    Name,
    Number,
    Unit,
}

pub(super) struct Term {
    tag: String,
    source: String,
    target: String,
    kind: Kind,
}

pub(super) struct Row {
    pub text: String,
    pub terms: Vec<Term>,
}

struct Candidate {
    span: Range<usize>,
    kind: Kind,
    target: String,
}

pub(super) fn mask(
    input: &Translation,
    source: &str,
    namespace: &str,
    next_marker: &mut usize,
) -> Row {
    let exact = View::new(source, false);
    let fold = View::new(source, true);
    let mut candidates = Vec::new();
    for (from, to) in &input.glossary {
        let needle = View::new(from, true).text;
        for span in literal_spans(&fold.text, &needle).filter_map(|r| fold.original(r)) {
            candidates.push(Candidate {
                span,
                kind: Kind::Glossary,
                target: to.clone(),
            });
        }
    }
    for (list, kind) in [(&input.names, Kind::Name), (&input.units, Kind::Unit)] {
        for value in list {
            let needle = View::new(value, false).text;
            for span in literal_spans(&exact.text, &needle).filter_map(|r| exact.original(r)) {
                candidates.push(Candidate {
                    span,
                    kind,
                    target: value.clone(),
                });
            }
        }
    }
    let mut units: Vec<_> = input
        .units
        .iter()
        .map(|s| View::new(s, false).text)
        .collect();
    // International symbols can be attached even when the caller did not list
    // a unit. Language-specific counters remain translatable unless declared.
    units.extend(
        [
            "%", "°C", "°F", "kg", "mg", "µg", "g", "km", "cm", "mm", "m", "mL", "L", "h", "ms",
            "s", "kPa", "Pa", "kW", "W", "Hz", "kHz", "MHz", "V", "mA",
        ]
        .into_iter()
        .map(|s| View::new(s, false).text),
    );
    units.sort_by_key(|u| std::cmp::Reverse(u.len()));
    units.dedup();
    for m in NUMBER.find_iter(&exact.text) {
        let suffix = &exact.text[m.end()..];
        let trimmed = suffix.trim_start();
        let mut end = m.end();
        if let Some(unit) = units.iter().find(|unit| {
            trimmed.strip_prefix(unit.as_str()).is_some_and(|rest| {
                !(unit.as_bytes().last().is_some_and(u8::is_ascii_alphabetic)
                    && rest.as_bytes().first().is_some_and(u8::is_ascii_alphabetic))
            })
        }) {
            end += suffix.len() - trimmed.len() + unit.len();
        }
        if let Some(span) = exact.original(m.start()..end) {
            candidates.push(Candidate {
                target: source[span.clone()].to_owned(),
                span,
                kind: Kind::Number,
            });
        }
    }
    for m in CLOCK_OR_DATE.find_iter(&exact.text) {
        if let Some(span) = exact.original(m.range()) {
            candidates.push(Candidate {
                target: source[span.clone()].to_owned(),
                span,
                kind: Kind::Number,
            });
        }
    }
    candidates.sort_by_key(|c| (c.span.start, std::cmp::Reverse(c.span.len()), c.kind));
    let mut text = String::new();
    let mut terms = Vec::new();
    let mut cursor = 0;
    for c in candidates {
        if c.span.start < cursor {
            continue;
        }
        let tag = format!("[[{namespace}{}]]", *next_marker);
        *next_marker += 1;
        text.push_str(&source[cursor..c.span.start]);
        text.push_str(&tag);
        cursor = c.span.end;
        terms.push(Term {
            tag,
            source: source[c.span].to_owned(),
            target: c.target,
            kind: c.kind,
        });
    }
    text.push_str(&source[cursor..]);
    Row { text, terms }
}

impl Row {
    pub fn meanings(&self) -> Value {
        Value::Object(
            self.terms
                .iter()
                .map(|t| (t.tag.clone(), json!(t.source)))
                .collect(),
        )
    }

    pub fn restore(&self, raw: &str, namespace: &str) -> Result<String, String> {
        let known: BTreeMap<_, _> = self
            .terms
            .iter()
            .enumerate()
            .map(|(i, t)| (t.tag.as_str(), i))
            .collect();
        let mut seen = vec![false; self.terms.len()];
        let mut output = String::new();
        let mut unmarked = String::new();
        let mut rest = raw;
        let prefix = format!("[[{namespace}");
        while let Some(index) = rest.find(&prefix) {
            let (before, after) = rest.split_at(index);
            let Some((token_body, tail)) = after.split_once("]]") else {
                return Err("incomplete protected symbol".into());
            };
            let tag = &after[..token_body.len() + 2];
            let i = *known
                .get(tag)
                .ok_or("unexpected protected symbol in this block")?;
            if std::mem::replace(&mut seen[i], true) {
                return Err(format!("protected symbol {tag} was repeated"));
            }
            output.push_str(before);
            output.push_str(&self.terms[i].target);
            unmarked.push_str(before);
            unmarked.push(' ');
            rest = tail;
        }
        output.push_str(rest);
        unmarked.push_str(rest);
        if normalize(&output).to_uppercase().contains(namespace) || output.contains(line_break) {
            return Err("malformed protected symbol or embedded line break".into());
        }
        // Some models spell out the exact target. Accept it only when the raw,
        // unmarked part pays for every absent occurrence locally. The caller
        // then checks all target counts and number/unit bindings after restoration.
        let mut missing = BTreeMap::new();
        for (i, term) in self.terms.iter().enumerate() {
            if !seen[i] {
                let folded = term.kind == Kind::Glossary;
                let target = normalize(&term.target);
                let key = (
                    folded,
                    if folded {
                        target.to_lowercase()
                    } else {
                        target
                    },
                );
                *missing.entry(key).or_insert(0) += 1;
            }
        }
        let original = normalize(&unmarked);
        let folded = original.to_lowercase();
        for ((fold, target), count) in missing {
            if occurrences(if fold { &folded } else { &original }, &target) < count {
                return Err(format!("missing protected expression {target}"));
            }
        }
        Ok(output)
    }
}
