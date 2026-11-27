//! Translation trials. Source IDs and literal protection can be switched independently.
use super::{LIMITATION, Translation, TranslationMode, align, check, held, language, line_break};
use crate::{
    contract,
    engine::{Engine, Output, Run, request},
    fm::Request,
    json,
};
use serde_json::{Value, json};

mod direction;
mod protection;

#[cfg(test)]
mod tests;

type RawRow = Result<String, String>;
type CheckedRow = Result<String, Vec<String>>;

struct Batch<'a> {
    input: &'a Translation,
    ids: bool,
    protection: bool,
    namespace: String,
    rows: Vec<protection::Row>,
}

fn id(index: usize) -> String {
    format!("row{:02}", index + 1)
}

fn one_line(s: &str) -> String {
    s.split(line_break)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

fn clean_model_suffix<'a>(text: &'a str, source: &str) -> &'a str {
    // Guided generation can append a transcript control token or the opening
    // of another JSON fence *inside* its final string. Remove only this suffix
    // when the corresponding literal never appeared in source material.
    static TAIL: std::sync::LazyLock<regex::Regex> = std::sync::LazyLock::new(|| {
        regex::Regex::new(
            r#"(?is)(?:[」』”"]\s*[\]}]\s*)?(?:<ctrl[0-9]+>|\x60{3}(?:json)?)[\s{}\[\]"\x60]*$"#,
        )
        .unwrap()
    });
    let mut text = text.trim();
    if !source.contains("<ctrl")
        && !source.contains("\x60\x60\x60")
        && let Some(m) = TAIL.find(text)
    {
        text = text[..m.start()].trim_end();
    }
    if !source.contains(r"\n") {
        text = text.trim_start_matches(r"\n").trim_start();
    }
    text
}

impl<'a> Batch<'a> {
    fn new(input: &'a Translation, ids: bool, protection: bool) -> Self {
        let namespace = protection::namespace(input);
        let mut next_marker = 1;
        let rows = input
            .sentences
            .iter()
            .map(|s| {
                if protection {
                    protection::mask(input, s, &namespace, &mut next_marker)
                } else {
                    protection::Row {
                        text: s.clone(),
                        terms: Vec::new(),
                    }
                }
            })
            .collect();
        Self {
            input,
            ids,
            protection,
            namespace,
            rows,
        }
    }

    fn prompt(&self, which: &[usize], retry: Option<(&[RawRow], &[CheckedRow])>) -> Request {
        let target = language(&self.input.target_language);
        let mut task = format!(
            "Translate each source block from {} into natural {target}. Preserve every claim, condition, action, negation, comparison and time relationship. Do not explain the translation. ",
            language(&self.input.source_language)
        );
        if self.ids {
            task.push_str("Fill each output field with the complete translation of the source block with the same ID. Sentence breaks within a field are fine. ");
        } else {
            task.push_str("Return just the translations, one complete block on each line, in the given order. ");
        }
        if self.protection {
            task.push_str("Use the provided target terms verbatim. Copy each stated measurement exactly, including its unit symbol. Keep any protected markup symbol intact. ");
        } else {
            task.push_str("Keep each number as written, its unit and each exact proper name. Use the specified glossary translations literally. ");
        }
        if retry.is_some() {
            task.push_str("Retranslate the supplied blocks from their original text, correcting the issues listed for each block.");
        }
        let blocks = which.iter().map(|&i| {
            let row = &self.rows[i];
            let mut block = json!({"text":row.text});
            if !row.terms.is_empty() {
                block["terminology"] = row.requirements();
            }
            let meaning = direction::guide(&self.input.sentences[i], self.input);
            if !meaning.is_empty() {
                block["meaning_to_preserve"] = json!(meaning);
            }
            if let Some((drafts, checks)) = retry {
                if let Ok(raw) = &drafts[i] {
                    block["previous_translation"] = json!(raw);
                }
                block["issues"] = json!(checks[i].as_ref().err());
            }
            (id(i), block)
        });
        let (source, schema) = if self.ids {
            (
                Value::Object(blocks.collect()),
                Some(contract::object(
                    which.iter().map(|&i| (id(i), json!({"type":"string"}))),
                )),
            )
        } else {
            (Value::Array(blocks.map(|(_, v)| v).collect()), None)
        };
        let mut payload = json!({"source_blocks":source});
        if !self.protection {
            payload["glossary"] = json!(self.input.glossary);
            payload["exact_names"] = json!(self.input.names);
            payload["unit_symbols"] = json!(self.input.units);
        }
        request(&task, payload, schema)
    }

    fn read(&self, which: &[usize], raw: &str) -> Vec<RawRow> {
        let all_failed = |reason: &str| vec![Err(reason.to_owned()); which.len()];
        if self.ids {
            let Ok(Value::Object(mut fields)) = json::parse(raw) else {
                return all_failed("expected a translation object with unique source IDs");
            };
            if fields.keys().any(|k| !which.iter().any(|&i| id(i) == *k)) {
                return all_failed("unexpected source ID");
            }
            which
                .iter()
                .map(|&i| match fields.remove(&id(i)) {
                    Some(Value::String(s)) if !s.trim().is_empty() => Ok(one_line(&s)),
                    _ => Err("missing or empty translation for this ID".into()),
                })
                .collect()
        } else {
            let lines: Vec<_> = raw
                .split(line_break)
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .collect();
            if lines.len() == which.len() {
                return lines.into_iter().map(|s| Ok(s.to_owned())).collect();
            }
            if which.len() == 1 && !lines.is_empty() {
                return vec![Ok(lines.join(" "))];
            }
            align(raw, which.len()).map_or_else(
                || all_failed("source and translation blocks cannot be aligned"),
                |lines| lines.into_iter().map(Ok).collect(),
            )
        }
    }

    fn verify(&self, i: usize, raw: &RawRow, mode: TranslationMode) -> CheckedRow {
        let raw = raw.as_ref().map_err(|e| vec![e.clone()])?;
        let protected_syntax = self
            .input
            .glossary
            .values()
            .chain(&self.input.names)
            .chain(&self.input.units)
            .any(|s| s.contains("<ctrl") || s.contains("\x60\x60\x60") || s.contains(r"\n"));
        let raw = if protected_syntax {
            raw.trim()
        } else {
            clean_model_suffix(raw, &self.input.sentences[i])
        };
        if raw.is_empty() {
            return Err(vec!["missing translation text".into()]);
        }
        let output = if self.protection {
            self.rows[i]
                .restore(raw, &self.namespace)
                .map_err(|e| vec![e])?
        } else {
            raw.to_owned()
        };
        let source = &self.input.sentences[i];
        let mut issues = Vec::new();
        if self.protection || mode == TranslationMode::Checked {
            issues = check(self.input, source, &output).failed;
            if mode == TranslationMode::Draft {
                issues.retain(|issue| issue != "direction");
            }
        }
        if mode == TranslationMode::Checked {
            issues.extend(direction::issues(source, &output, self.input));
        }
        if issues.is_empty() {
            Ok(output)
        } else {
            Err(issues)
        }
    }
}

pub(super) fn run(
    engine: &mut Engine<'_>,
    input: &Translation,
    mode: TranslationMode,
    ids: bool,
    protection: bool,
) -> Run<Output> {
    let batch = Batch::new(input, ids, protection);
    let all: Vec<_> = (0..input.sentences.len()).collect();
    let first = engine.generate(&batch.prompt(&all, None))?;
    let mut drafts = batch.read(&all, &first);
    let mut checks: Vec<_> = all
        .iter()
        .map(|&i| batch.verify(i, &drafts[i], mode))
        .collect();
    let failed: Vec<_> = all
        .iter()
        .copied()
        .filter(|&i| checks[i].is_err())
        .collect();
    if !failed.is_empty()
        && mode == TranslationMode::Checked
        && engine.policy.repairs > 0
        && engine.metrics.calls < engine.policy.max_calls
    {
        let fix = batch.prompt(&failed, Some((&drafts, &checks)));
        let before = engine.metrics.calls;
        let second = engine.generate(&fix);
        engine.metrics.repairs += engine.metrics.calls - before;
        for (&i, replacement) in failed.iter().zip(batch.read(&failed, &second?)) {
            drafts[i] = replacement;
        }
        checks = all
            .iter()
            .map(|&i| batch.verify(i, &drafts[i], mode))
            .collect();
    }
    let mut lines = Vec::with_capacity(all.len());
    for (i, row) in checks.into_iter().enumerate() {
        match row {
            Ok(line) => lines.push(line),
            Err(issues) => {
                return Ok(held(format!(
                    "source block {} failed {}",
                    i + 1,
                    issues.join(", ")
                )));
            }
        }
    }
    let (status, note) = match mode {
        TranslationMode::Draft => (
            "unverified",
            "Draft translation. Semantic accuracy has not been verified.",
        ),
        TranslationMode::Checked => ("ok", LIMITATION),
    };
    Ok(Output {
        status,
        value: lines.join("\n").into(),
        notes: vec![note.into()],
    })
}
