//! English/Japanese translation with optional sentence-local checks and repair.
use crate::engine::{Engine, Fail, Output, Run, request, rewrite_request};
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::LazyLock,
};
use unicode_normalization::UnicodeNormalization;

mod trial;

static NUMBER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?").unwrap());
static JAPANESE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"[\u3040-\u30ff\u3400-\u9fff]").unwrap());
static ENGLISH: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[a-zA-Z]{2}").unwrap());
const LIMITATION: &str = "Literal and sentence checks cannot detect every meaning reversal, including errors in negation, comparison or timing.";

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Deserialize, Serialize, clap::ValueEnum)]
#[serde(rename_all = "lowercase")]
pub enum TranslationMode {
    /// Generate a single unverified full-text draft.
    Draft,
    /// Check protected literals sentence by sentence and hold unresolved translations.
    #[default]
    Checked,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Translation {
    pub source_language: String,
    pub target_language: String,
    pub sentences: Vec<String>,
    #[serde(default)]
    pub glossary: BTreeMap<String, String>,
    #[serde(default)]
    pub names: Vec<String>,
    #[serde(default)]
    pub units: Vec<String>,
}

impl Translation {
    pub fn validate(&self) -> Result<(), String> {
        if !matches!(
            (self.source_language.as_str(), self.target_language.as_str()),
            ("en", "ja") | ("ja", "en")
        ) {
            return Err(
                "source_language and target_language must be the en/ja pair in opposite directions"
                    .into(),
            );
        }
        if !(1..=48).contains(&self.sentences.len())
            || self
                .sentences
                .iter()
                .any(|s| s.trim().is_empty() || s.contains(line_break))
        {
            return Err("supply 1 to 48 nonempty source sentences, one line per item".into());
        }
        let terms = self
            .glossary
            .keys()
            .chain(self.glossary.values())
            .chain(&self.names)
            .chain(&self.units);
        if terms.clone().count() > 256 || terms.into_iter().any(|s| s.trim().is_empty()) {
            return Err(
                "supply at most 256 nonempty glossary keys, values, names and units in total"
                    .into(),
            );
        }
        Ok(())
    }

    fn instructions(&self) -> String {
        let terms = json!({"glossary":self.glossary,"names":self.names,"units":self.units});
        format!(
            "Translate every source sentence from {} to {}. Return only the translations, one sentence per original line in the original order. Preserve obligations, direction of comparisons, negation and timing. Keep each number as written, its unit symbol and exact proper name. Use the supplied target glossary terms literally (English capitalization may follow context). {terms}",
            language(&self.source_language),
            language(&self.target_language)
        )
    }
}

fn language(code: &str) -> &'static str {
    if code == "ja" { "Japanese" } else { "English" }
}

fn line_break(c: char) -> bool {
    matches!(
        c,
        '\r' | '\n' | '\u{000b}' | '\u{000c}' | '\u{0085}' | '\u{2028}' | '\u{2029}'
    )
}

fn normalize(text: &str) -> String {
    text.nfkc()
        .map(|ch| if ch == '−' { '-' } else { ch })
        .collect()
}

/// Normalized literals have boundaries beside ASCII letters, so `m` is distinct
/// from `mm` while `5m` and Japanese text immediately before a term are valid.
fn literal_spans<'a>(
    text: &'a str,
    term: &'a str,
) -> impl Iterator<Item = std::ops::Range<usize>> + 'a {
    let bytes = text.as_bytes();
    let mut cursor = 0;
    std::iter::from_fn(move || {
        let first = term.chars().next()?;
        while let Some(relative) = text[cursor..].find(term) {
            let start = cursor + relative;
            let end = start + term.len();
            let left = term.as_bytes()[0].is_ascii_alphabetic()
                && start > 0
                && bytes[start - 1].is_ascii_alphabetic();
            let right = term.as_bytes()[term.len() - 1].is_ascii_alphabetic()
                && bytes.get(end).is_some_and(u8::is_ascii_alphabetic);
            if left || right {
                cursor = start + first.len_utf8();
            } else {
                cursor = end;
                return Some(start..end);
            }
        }
        None
    })
}

fn occurrences(text: &str, term: &str) -> usize {
    literal_spans(text, term).count()
}

fn significant(text: &str) -> impl Iterator<Item = char> + '_ {
    text.chars()
        .filter(|c| c.is_alphanumeric())
        .flat_map(char::to_lowercase)
}

/// Mask only complete source-required literals that already passed their own
/// checks. Case-sensitive names/units retain their positions even when Unicode
/// lowercase expansion changes a later byte offset in the glossary text.
fn english_direction(output: &str, exact: &[String], glossary: &[String]) -> bool {
    if !JAPANESE.is_match(output) {
        return ENGLISH.is_match(output);
    }
    let mut exact_mask = vec![false; output.len()];
    for required in exact {
        for span in literal_spans(output, required) {
            exact_mask[span].fill(true);
        }
    }
    let mut lower = String::new();
    let mut covered = Vec::new();
    for (offset, ch) in output.char_indices() {
        lower.extend(ch.to_lowercase());
        covered.resize(lower.len(), exact_mask[offset]);
    }
    for target in glossary {
        for span in literal_spans(&lower, target) {
            covered[span].fill(true);
        }
    }
    let prose: String = lower
        .char_indices()
        .map(|(i, ch)| if covered[i] { ' ' } else { ch })
        .collect();
    ENGLISH.is_match(&prose) && !JAPANESE.is_match(&prose)
}

fn numbers<'a>(text: &'a str, unit: Option<&str>) -> Vec<&'a str> {
    let mut values = Vec::new();
    for number in NUMBER.find_iter(text) {
        if let Some(unit) = unit {
            let suffix = text[number.end()..].trim_start();
            let Some(after) = suffix.strip_prefix(unit) else {
                continue;
            };
            if unit.as_bytes().last().is_some_and(u8::is_ascii_alphabetic)
                && after
                    .as_bytes()
                    .first()
                    .is_some_and(u8::is_ascii_alphabetic)
            {
                continue;
            }
        }
        values.push(number.as_str());
    }
    values.sort_unstable();
    values
}

struct SentenceCheck<'a> {
    failed: Vec<String>,
    glossary: BTreeMap<&'a str, &'a str>,
    names: Vec<&'a str>,
    units: Vec<&'a str>,
}

fn check<'a>(input: &'a Translation, source: &str, output: &str) -> SentenceCheck<'a> {
    let source = normalize(source);
    let output = normalize(output);
    let mut result = SentenceCheck {
        failed: Vec::new(),
        glossary: BTreeMap::new(),
        names: Vec::new(),
        units: Vec::new(),
    };
    let mut allowed_exact = Vec::new();
    let mut allowed_glossary = Vec::new();
    let translate_to_english = input.target_language == "en";
    let mixed_script = translate_to_english && JAPANESE.is_match(&output);
    if numbers(&source, None) != numbers(&output, None) {
        result.failed.push("numbers".into());
    }

    for name in input.names.iter().collect::<BTreeSet<_>>() {
        let needle = normalize(name);
        let (want, got) = (occurrences(&source, &needle), occurrences(&output, &needle));
        if want + got > 0 {
            result.names.push(name);
        }
        if want != got {
            result.failed.push(format!("name {name}"));
        } else if mixed_script && want > 0 {
            allowed_exact.push(needle);
        }
    }
    let src_folded = source.to_lowercase();
    let out_folded = output.to_lowercase();
    // Several source words may share a target spelling. Count their total once,
    // including repeated mentions, and deduplicate case variants of one key.
    let mut by_target: BTreeMap<String, BTreeMap<String, usize>> = BTreeMap::new();
    for (from, to) in &input.glossary {
        let source_term = normalize(from).to_lowercase();
        let target_term = normalize(to).to_lowercase();
        let want = occurrences(&src_folded, &source_term);
        let got = occurrences(&out_folded, &target_term);
        if want + got > 0 {
            result.glossary.insert(from, to);
            by_target
                .entry(target_term)
                .or_default()
                .insert(source_term, want);
        }
    }
    for (target, sources) in by_target {
        let want = sources.values().sum::<usize>();
        if want != occurrences(&out_folded, &target) {
            result.failed.push(format!("term {target}"));
        } else if mixed_script && want > 0 {
            allowed_glossary.push(target);
        }
    }
    for unit in input.units.iter().collect::<BTreeSet<_>>() {
        let needle = normalize(unit);
        let (want, got) = (occurrences(&source, &needle), occurrences(&output, &needle));
        if want + got > 0 {
            result.units.push(unit);
        }
        if want != got || numbers(&source, Some(&needle)) != numbers(&output, Some(&needle)) {
            result.failed.push(format!("unit {unit}"));
        } else if mixed_script && want > 0 {
            allowed_exact.push(needle);
        }
    }
    let changed = !significant(&source).eq(significant(&output));
    let direction = if translate_to_english {
        english_direction(&output, &allowed_exact, &allowed_glossary)
    } else {
        JAPANESE.is_match(&output)
    };
    if !changed || !direction {
        result.failed.insert(0, "direction".into());
    }
    result
}

fn abbreviation(prefix: &str) -> bool {
    let word = prefix
        .rsplit(|c: char| !c.is_ascii_alphabetic() && c != '.')
        .next()
        .unwrap_or("");
    let stem = word.trim_end_matches('.');
    matches!(
        stem.to_ascii_lowercase().as_str(),
        "mr" | "mrs" | "ms" | "dr" | "prof" | "e.g" | "i.e" | "vs"
    ) || (!stem.is_empty()
        && stem
            .split('.')
            .all(|p| p.len() == 1 && p.as_bytes()[0].is_ascii_uppercase()))
}

fn sentences(line: &str) -> Vec<&str> {
    let mut parts = Vec::new();
    let mut start = 0;
    for (i, c) in line.char_indices() {
        let end = i + c.len_utf8();
        let following = line[end..].chars().next();
        let stop = match c {
            '。' | '！' | '？' => !following
                .is_some_and(|next| matches!(next, '！' | '？' | '。' | '」' | '』' | '”' | '）')),
            '.' | '!' | '?' => {
                following.is_none_or(char::is_whitespace)
                    && (c != '.' || !abbreviation(&line[..end]))
            }
            _ => false,
        };
        if stop {
            let part = line[start..end].trim();
            if !part.is_empty() {
                parts.push(part);
            }
            start = end;
        }
    }
    let rest = line[start..].trim();
    if !rest.is_empty() {
        parts.push(rest);
    }
    parts
}

fn align(draft: &str, count: usize) -> Option<Vec<String>> {
    let lines: Vec<_> = draft
        .split(line_break)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .collect();
    if lines.len() == count {
        return lines
            .iter()
            .all(|s| sentences(s).len() == 1)
            .then(|| lines.into_iter().map(str::to_owned).collect());
    }
    let joined = lines.join(" ");
    let parts = sentences(&joined);
    (parts.len() == count).then(|| parts.into_iter().map(str::to_owned).collect())
}

fn held(reason: String) -> Output {
    Output {
        status: "held",
        value: Value::Null,
        notes: vec![reason, LIMITATION.into()],
    }
}

pub fn run(engine: &mut Engine<'_>, input: &Translation, mode: TranslationMode) -> Run<Output> {
    input.validate().map_err(Fail::Invalid)?;
    let (ids, protection) = match mode {
        TranslationMode::Draft => (
            cfg!(feature = "trial-draft-ids"),
            cfg!(feature = "trial-draft-protection"),
        ),
        TranslationMode::Checked => (
            cfg!(feature = "trial-checked-ids"),
            cfg!(feature = "trial-checked-protection"),
        ),
    };
    if ids || protection {
        return trial::run(engine, input, mode, ids, protection);
    }
    baseline(engine, input, mode)
}

fn baseline(engine: &mut Engine<'_>, input: &Translation, mode: TranslationMode) -> Run<Output> {
    let draft_mode = mode == TranslationMode::Draft;
    let first = rewrite_request(
        &input.instructions(),
        &input.sentences.join("\n"),
        true,
        None,
        true,
        draft_mode,
    );
    let draft = engine.generate(&first)?;
    if draft_mode {
        if draft.trim().is_empty() {
            return Err(Fail::Held(
                "the model returned an empty translation draft".into(),
            ));
        }
        return Ok(Output { status: "unverified", value: draft.into(), notes: vec!["Draft translation. Sentence alignment and protected literals have not been checked.".into()] });
    }
    let Some(mut lines) = align(&draft, input.sentences.len()) else {
        return Ok(held(
            "source and translation sentences cannot be aligned".into(),
        ));
    };
    let broken =
        input
            .sentences
            .iter()
            .zip(&lines)
            .enumerate()
            .find_map(|(index, (source, output))| {
                let current = check(input, source, output);
                (!current.failed.is_empty()).then_some((index, current))
            });
    if let Some((index, current)) = broken {
        if engine.policy.repairs == 0 || engine.metrics.calls >= engine.policy.max_calls {
            return Ok(held(format!(
                "sentence {} failed {} and no repair call is available",
                index + 1,
                current.failed.join(", ")
            )));
        }
        let guide = format!(
            "Correct this single {} translation against its original. Return only the corrected sentence. Apply required terminology, number-unit pairs and names; preserve negation, timing and comparison direction. Do not add commentary.",
            language(&input.target_language)
        );
        let payload = json!({"original":input.sentences[index],"draft":lines[index],"failed_checks":current.failed,
            "glossary":current.glossary,"names":current.names,"units":current.units});
        let request = request(
            "Carry out the task.",
            json!({"task":guide,"source":payload.to_string()}),
            None,
        );
        let before = engine.metrics.calls;
        let correction = engine.generate(&request);
        engine.metrics.repairs += engine.metrics.calls - before;
        let correction = correction?;
        let replacement = correction.trim();
        if replacement.contains(line_break) || sentences(replacement).len() != 1 {
            return Ok(held(format!(
                "the repair for sentence {} did not return one sentence",
                index + 1
            )));
        }
        lines[index] = replacement.to_owned();
        for (i, (source, output)) in input.sentences.iter().zip(&lines).enumerate() {
            let issues = check(input, source, output).failed;
            if !issues.is_empty() {
                return Ok(held(format!(
                    "sentence {} still fails {}",
                    i + 1,
                    issues.join(", ")
                )));
            }
        }
    }
    Ok(Output {
        status: "ok",
        value: lines.join("\n").into(),
        notes: vec![LIMITATION.into()],
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        engine::Policy,
        fm::{Model, Request},
    };
    use std::collections::VecDeque;

    const FIRST: &str = "Letoのポンプ圧を6.5 kPaに保つ。";
    const SECOND: &str = "対照試料は4 h保管する。";
    const BAD: &str = "Letoのポンプ圧を6.5 hに保つ。";

    fn example() -> Translation {
        serde_json::from_value(json!({"source_language":"en","target_language":"ja",
            "sentences":["Leto keeps pump pressure at 6.5 kPa.","Keep the control sample for 4 h."],
            "glossary":{"pump pressure":"ポンプ圧","control sample":"対照試料"},"names":["Leto"],"units":["kPa","h"]})).unwrap()
    }

    #[derive(Default)]
    struct Fake {
        replies: VecDeque<String>,
        sent: Vec<Request>,
        counts: VecDeque<usize>,
    }
    impl Model for Fake {
        fn ready(&mut self) -> Result<(), String> {
            Ok(())
        }
        fn count(&mut self, _: &Request) -> Result<usize, String> {
            Ok(self.counts.pop_front().unwrap_or(10))
        }
        fn generate(&mut self, r: &Request) -> Result<String, String> {
            self.sent.push(r.clone());
            self.replies
                .pop_front()
                .ok_or_else(|| "unexpected extra generation".into())
        }
    }

    fn simulate(
        input: &Translation,
        mode: TranslationMode,
        policy: Policy,
        replies: Vec<String>,
    ) -> (Output, Vec<Request>, usize) {
        let mut fake = Fake {
            replies: replies.into(),
            ..Fake::default()
        };
        let mut engine = Engine::new(&mut fake, policy);
        let result = baseline(&mut engine, input, mode).unwrap();
        let repairs = engine.metrics.repairs;
        let calls = engine.metrics.calls;
        assert_eq!(calls, fake.sent.len());
        (result, fake.sent, repairs)
    }

    #[test]
    fn draft_releases_a_single_unverified_response_and_checked_returns_the_clean_text() {
        let input = example();
        let wrong = "a draft missing a source sentence";
        let (draft, calls, repairs) = simulate(
            &input,
            TranslationMode::Draft,
            Policy::default(),
            vec![wrong.into()],
        );
        assert_eq!(
            (draft.status, draft.value, calls.len(), repairs),
            ("unverified", json!(wrong), 1, 0)
        );
        let full = format!("{FIRST}\n{SECOND}");
        let (ok, calls, repairs) = simulate(
            &input,
            TranslationMode::Checked,
            Policy::default(),
            vec![full.clone()],
        );
        assert_eq!(
            (ok.status, ok.value, calls.len(), repairs),
            ("ok", json!(full), 1, 0)
        );
        assert_eq!(ok.notes, [LIMITATION]);
    }

    #[test]
    fn repairs_only_the_first_failed_sentence_and_includes_only_relevant_obligations() {
        let input = example();
        let (out, calls, repairs) = simulate(
            &input,
            TranslationMode::Checked,
            Policy::default(),
            vec![format!("{BAD}\n{SECOND}"), FIRST.into()],
        );
        assert_eq!(
            (out.status, out.value, calls.len(), repairs),
            ("ok", json!(format!("{FIRST}\n{SECOND}")), 2, 1)
        );
        let (_, raw) = calls[1].prompt.split_once('\n').unwrap();
        let ask: Value = serde_json::from_str(raw).unwrap();
        let src: Value = serde_json::from_str(ask["source"].as_str().unwrap()).unwrap();
        assert_eq!(src["original"], input.sentences[0]);
        assert_eq!(src["draft"], BAD);
        assert_eq!(src["names"], json!(["Leto"]));
        assert_eq!(src["glossary"], json!({"pump pressure":"ポンプ圧"}));
        assert!(src["units"].as_array().unwrap().contains(&json!("h")));
        assert!(!calls[1].prompt.contains("control sample"));
    }

    #[test]
    fn caps_local_generation_and_rechecks_both_repaired_and_untouched_sentences() {
        let input = example();
        let policy = Policy {
            repairs: 8,
            max_calls: 9,
            ..Policy::default()
        };
        for (draft, patch) in [
            (format!("{BAD}\n{SECOND}"), BAD.into()),
            (format!("{BAD}\n対照試料は6 h保管する。"), FIRST.into()),
            (format!("{BAD}\n{SECOND}"), format!("{FIRST}別の文。")),
            (format!("{BAD}\n{SECOND}"), format!("{FIRST}\n追加")),
        ] {
            let (out, calls, repairs) =
                simulate(&input, TranslationMode::Checked, policy, vec![draft, patch]);
            assert_eq!(
                (out.status, out.value, calls.len(), repairs),
                ("held", Value::Null, 2, 1)
            );
        }
        for policy in [
            Policy {
                repairs: 0,
                ..policy
            },
            Policy {
                max_calls: 1,
                ..policy
            },
        ] {
            let (out, calls, repairs) = simulate(
                &input,
                TranslationMode::Checked,
                policy,
                vec![format!("{BAD}\n{SECOND}")],
            );
            assert_eq!(
                (out.status, out.value, calls.len(), repairs),
                ("held", Value::Null, 1, 0)
            );
        }
        let (out, calls, repairs) =
            simulate(&input, TranslationMode::Checked, policy, vec![FIRST.into()]);
        assert_eq!(
            (out.status, out.value, calls.len(), repairs),
            ("held", Value::Null, 1, 0)
        );
    }

    #[test]
    fn preflight_limits_apply_before_counting_a_repair_as_sent() {
        let input = example();
        let mut fake = Fake {
            replies: [format!("{BAD}\n{SECOND}")].into(),
            counts: [8, 30].into(),
            ..Fake::default()
        };
        let mut engine = Engine::new(
            &mut fake,
            Policy {
                input_budget: 16,
                ..Policy::default()
            },
        );
        assert!(matches!(
            run(&mut engine, &input, TranslationMode::Checked),
            Err(Fail::Held(_))
        ));
        assert_eq!(
            (
                engine.metrics.calls,
                engine.metrics.repairs,
                engine.metrics.token_checks
            ),
            (1, 0, 2)
        );
        let mut engine = Engine::new(
            &mut fake,
            Policy {
                max_calls: 0,
                ..Policy::default()
            },
        );
        assert!(matches!(
            run(&mut engine, &input, TranslationMode::Draft),
            Err(Fail::Held(_))
        ));
        assert_eq!((engine.metrics.calls, engine.metrics.token_checks), (0, 0));
    }

    #[test]
    fn checks_width_case_multiplicity_unit_bindings_and_standalone_units() {
        let mut input = example();
        input.names = vec!["Aster".into()];
        input.units = vec!["kg".into(), "h".into(), "m".into()];
        input.glossary =
            BTreeMap::from([("crate".into(), "箱".into()), ("case".into(), "箱".into())]);
        let source = "Ａｓｔｅｒ: crate and CASE, −１，２００．５㎏, ４ h; display m.";
        assert!(
            check(
                &input,
                source,
                "Asterが箱と箱を記す。-1,200.5 kg、4 hで、表示はm。"
            )
            .failed
            .is_empty()
        );
        let failed = check(
            &input,
            source,
            "asterが箱を記す。-1,200.5 h、4 kgで、表示はmm。",
        )
        .failed;
        for name in ["name Aster", "term 箱", "unit kg", "unit h", "unit m"] {
            assert!(failed.contains(&name.to_string()));
        }
        assert!(!failed.contains(&"numbers".to_string()));
        assert!(
            check(&input, "Aster, 2 kg, 2 kg.", "Asterは2 kgを取る。")
                .failed
                .contains(&"numbers".to_string())
        );
        assert_eq!(occurrences("pastAster AsterのAster5", "Aster"), 2);
    }

    #[test]
    fn alignment_and_language_have_explicit_limits() {
        assert_eq!(
            align("Dr. Fox set it to 2.5. Next run!", 2).unwrap(),
            ["Dr. Fox set it to 2.5.", "Next run!"]
        );
        assert_eq!(align("一文。次の文。", 2).unwrap(), ["一文。", "次の文。"]);
        assert!(align("一文。追記。\n次。", 2).is_none());
        let mut input = example();
        input.source_language = "ja".into();
        input.target_language = "en".into();
        input.sentences = vec!["Letoは2 h以上保管する。".into()];
        assert!(
            check(&input, &input.sentences[0], "Letoが2 h保存。")
                .failed
                .contains(&"direction".into())
        );
        let (out, _, _) = simulate(
            &input,
            TranslationMode::Checked,
            Policy::default(),
            vec!["Leto keeps it for less than 2 h.".into()],
        );
        assert_eq!(out.status, "ok");
        assert_eq!(out.notes, [LIMITATION]);
    }

    #[test]
    fn english_translation_keeps_only_literals_required_for_this_sentence() {
        let src = "青木は 5 件の町内便を受け取った。";
        let input: Translation =
            serde_json::from_value(json!({"source_language":"ja","target_language":"en",
            "sentences":[src], "names":["青木", "岩田"], "units":["件"],
            "glossary":{"町内便":"地域便 Express", "返送":"返送票"}}))
            .unwrap();
        let policy = Policy {
            repairs: 0,
            ..Policy::default()
        };
        let good = "青木 received 5 件 of 地域便 EXPRESS.";
        let (result, calls, repairs) =
            simulate(&input, TranslationMode::Checked, policy, vec![good.into()]);
        assert_eq!(
            (result.status, result.value, calls.len(), repairs),
            ("ok", json!(good), 1, 0)
        );
        for (bad, issue) in [
            (
                "青木 received 5 件 of 地域便 EXPRESS todayです。",
                "direction",
            ),
            ("青木 received 5 件 of 地域便 EXPRESS at 岩田.", "name 岩田"),
            (
                "青木 received 5 件 of 地域便 Express with 返送票.",
                "term 返送票",
            ),
            ("青木 received 5 units of 地域便 Express.", "unit 件"),
            ("青木 received 6 件 of 地域便 Express.", "numbers"),
            ("青木 青木 received 5 件 of 地域便 Express.", "name 青木"),
            ("青木 5 件 地域便 Express", "direction"),
        ] {
            assert!(
                check(&input, src, bad).failed.contains(&issue.to_owned()),
                "{bad}"
            );
            let (held, _, _) = simulate(&input, TranslationMode::Checked, policy, vec![bad.into()]);
            assert_eq!((held.status, held.value), ("held", Value::Null));
        }
        let (held, _, _) = simulate(&input, TranslationMode::Checked, policy, vec![src.into()]);
        assert_eq!((held.status, held.value), ("held", Value::Null));
    }

    #[test]
    fn direction_exemptions_preserve_case_boundaries_overlaps_and_noncopy_requirement() {
        let mut input: Translation = serde_json::from_value(json!({"source_language":"ja", "target_language":"en",
            "sentences":["青木Ａが東京の品を扱う。"], "names":["青木Ａ", "東"], "glossary":{"東京":"東京"}})).unwrap();
        let source = &input.sentences[0];
        assert!(
            check(&input, source, "青木A handles goods in 東京.")
                .failed
                .is_empty()
        );
        assert!(
            !check(&input, source, "青木a handles goods in 東京.")
                .failed
                .is_empty()
        );
        assert!(
            !check(&input, source, "青木A handles goods via 青木a in 東京.")
                .failed
                .is_empty()
        );
        assert!(
            !check(&input, source, "青木Atlas handles goods in 東京.")
                .failed
                .is_empty()
        );
        input.sentences[0] = "青木A runs today！".into();
        assert!(
            check(&input, &input.sentences[0], "青木A runs today.")
                .failed
                .contains(&"direction".into())
        );
    }

    #[test]
    fn japanese_translation_with_a_japanese_name_still_requires_a_changed_sentence() {
        let input: Translation =
            serde_json::from_value(json!({"source_language":"en","target_language":"ja",
            "sentences":["Keep 東京 for 5 h."],"names":["東京"],"units":["h"]}))
            .unwrap();
        let policy = Policy {
            repairs: 0,
            ..Policy::default()
        };
        for copy in ["Keep 東京 for 5 h.", "ＫＥＥＰ 東京　for ５ h！"] {
            let (out, _, _) = simulate(&input, TranslationMode::Checked, policy, vec![copy.into()]);
            assert_eq!((out.status, out.value), ("held", Value::Null));
        }
        let translated = "東京を5 h保持する。";
        let (out, _, _) = simulate(
            &input,
            TranslationMode::Checked,
            policy,
            vec![translated.into()],
        );
        assert_eq!((out.status, out.value), ("ok", json!(translated)));
    }

    #[test]
    fn input_defaults_limits_and_unknown_fields() {
        let basic = json!({"source_language":"en","target_language":"ja","sentences":["Hello."]});
        let mut input: Translation = serde_json::from_value(basic.clone()).unwrap();
        assert!(
            input.validate().is_ok()
                && input.glossary.is_empty()
                && input.names.is_empty()
                && input.units.is_empty()
        );
        input.sentences = vec!["line\rbreak".into()];
        assert!(input.validate().is_err());
        input.sentences = vec!["x".into(); 49];
        assert!(input.validate().is_err());
        input.sentences.truncate(1);
        input.names = vec!["X".into(); 257];
        assert!(input.validate().is_err());
        input.names.clear();
        input.source_language = "ja".into();
        assert!(input.validate().is_err());
        let mut unknown = basic;
        unknown["unexpected"] = json!(true);
        assert!(serde_json::from_value::<Translation>(unknown).is_err());
    }
}
