//! Conservative vetoes for a positive answer already produced by the original indexed reader.
//! This module receives the source and output only. It cannot call a model or change retrieval.
use super::checked::{NEG_VALUE, WANTS_STATE, field_conflict, literal_name, tentative_mention};
use crate::{document, engine::Output};
use regex::Regex;
use serde_json::json;
use std::{collections::BTreeSet, sync::LazyLock};

static CONDITIONAL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
    r"(?ix)\b(if|unless|hypothetical|suppos(?:e|ing)|might|would|could|conditionally|depending\s+on)\b|場合|なら|仮に|もし|ときに限|ときは|たら|れば|条件").unwrap()
});
static ASK_STATE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
    r"(?ix)^\s*(?:is|was|has|had|did|can|does|whether)\b|\b(?:whether|decision\s+status|withdrawn\s+(?:value|code)|cancell?ed\s+(?:value|venue))\b|確定して(?:いる|いま)|承認され(?:たか|てい)|承認済みか|未定か|撤回された|取り消された|どうな|どのような決定|可否|有無|という名|何と呼").unwrap()
});
static STATE_SENTENCE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
    r"(?ix)\b(?:no\s+(?:approved\s+)?(?:venue|code|value|replacement|decision)\s+(?:has|is|was)|(?:has|is|remains)\s+(?:not|under\s+(?:review|discussion)))|
    (?:未定|未確定|未承認|保留)(?:です|である|となった)[。.]?$|(?:まだない|されていない|決まっていない|承認されておらず)[。.]?$").unwrap()
});
static LITERAL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
    r"(?ix)\b(?:named|called|literal|official\s+name|assigned\s+the\s+name)\b|命名|という名称|という名前|という名|正式名称").unwrap()
});
static NEGATED: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
    r"(?ix)\bno\s+(?:[\w-]+\s+){0,4}(?:has|had|was|is)\s+(?:yet\s+)?(?:been\s+)?(?:approved|confirmed|assigned|accepted|decided|issued)\b|
    \bnot\s+(?:yet\s+)?(?:been\s+)?(?:approved|confirmed|assigned|accepted|decided|issued)\b|
    (?:未承認|未決定|未指定|未発行|未確定)|(?:承認|確定|指定|発行|決定)(?:されていない|されなかった|された.{0,12}まだない)").unwrap()
});
static AFFIRM: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
    r"(?ix)\b(?:approved|confirmed|assigned|accepted|decided|issued|authorized|ratified|adopted|reinstated|restored|booked|scheduled|chosen|selected|established|finali[sz]ed|valid|instead|after\s+all)\b|
    承認|確定|決定|指定|採用|許可|付与|発行|再開|復活|有効|予約|決裁|成立|割り当て|認め|決まった|訂正|変更").unwrap()
});
static WITHDRAW: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
    r"(?ix)\b(?:was|is|has\s+been|had\s+been)\s+(?:formally\s+)?(?:withdrawn|revoked|cancelled|canceled|rescinded)\b|
    \b(?:revoked|withdrew|rescinded|cancelled|canceled)\s+(?:the|its|this|that)\b|
    (?:を|は|が)(?:正式に)?(?:撤回した|撤回された|失効した|取り消した|取り消された|取消した|無効になった|無効とした)").unwrap()
});
static QUALIFIED_FIELD: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
    r"(?ix)\b([a-z][a-z-]+)\s+(code|identifier|venue|site|room|address|date|deadline|number|location|color|colour|time|name)\b|
    [\p{Han}\p{Katakana}]{1,12}(?:符号|コード|番号|会場|日時|日付|場所|時刻|名称|期限|識別色)").unwrap()
});
static SUBJECT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
    r"\b([A-Z][A-Za-z0-9-]*)\s+(team|group|division|department)\b|\b([Tt]eam|[Pp]roject|[Uu]nit|[Cc]ase)\s+([A-Z][A-Za-z0-9-]*)\b|([\p{Han}\p{Katakana}A-Za-z0-9-]{1,16})(班|組|課|便)").unwrap()
});
static VENUE_NAME: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
    r"(?i)\b(?:hall|room|building|centre|center|theatre|theater|plaza|office|station|auditorium|pavilion)$").unwrap()
});
static DATED_SECTION: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^##\s+[0-9]{4}-[0-9]{2}-[0-9]{2}\s*[―—:：-]?\s*(.*)$").unwrap());
static BOUND_OTHER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
    r"(?ix)\b(?:colou?r|hue|tint)\s*$|\b(?:colou?r|hue|code|identifier|venue|room|weight|height|temperature|deadline|date)\s*(?:is|was|[:=])\s*$|
    (?:識別色|色名|符号|コード|会場|部屋|重量|温度|締切|時刻)(?:として|は|を|[:：=])\s*$").unwrap()
});

fn subjects(text: &str) -> BTreeSet<(String, String)> {
    SUBJECT
        .captures_iter(text)
        .map(|c| {
            let (name, role) = if c.get(1).is_some() {
                (&c[1], &c[2])
            } else if c.get(3).is_some() {
                (&c[4], &c[3])
            } else {
                (&c[5], &c[6])
            };
            (name.to_ascii_lowercase(), role.to_ascii_lowercase())
        })
        .collect()
}

fn fields(text: &str) -> BTreeSet<String> {
    QUALIFIED_FIELD
        .captures_iter(text)
        .filter_map(|c| {
            if c.get(1).is_some()
                && [
                    "the", "a", "which", "what", "current", "final", "new", "old", "approved",
                    "valid", "assigned", "its", "that", "this", "previous", "an",
                ]
                .contains(&c[1].to_ascii_lowercase().as_str())
            {
                None
            } else {
                Some(c[0].to_ascii_lowercase())
            }
        })
        .collect()
}

fn dated_section(text: &str) -> Option<&str> {
    DATED_SECTION.captures(text)?.get(1).map(|s| s.as_str())
}

fn proposal_field(text: &str, field: &str, value: &str) -> bool {
    let known = fields(text);
    if known.contains(field) {
        return true;
    }
    if !known.is_empty() {
        return false;
    }
    // "proposed River Hall for its hearing" binds both the location and the qualified activity.
    let Some((modifier, noun)) = field.split_once(' ') else {
        return false;
    };
    matches!(noun, "venue" | "location" | "site" | "room")
        && VENUE_NAME.is_match(value)
        && text
            .split(|c: char| !c.is_alphanumeric())
            .any(|word| word.eq_ignore_ascii_case(modifier))
}

fn section(text: &str) -> bool {
    let l = text.to_ascii_lowercase();
    if l.starts_with("# ")
        || l.starts_with("document:")
        || text.starts_with("文書：")
        || l.starts_with("subject:")
        || text.starts_with("件名：")
        || l.starts_with("section:")
        || text.starts_with("節：")
        || (text.starts_with('[') && text.ends_with(']'))
    {
        return true;
    }
    text.strip_prefix("## ")
        .is_some_and(|s| !s.starts_with(|c: char| c.is_ascii_digit()))
}

fn reaffirm(text: &str) -> bool {
    AFFIRM.is_match(&NEGATED.replace_all(text, " "))
}

fn wrong_field(q: &str, s: &str, v: &str) -> bool {
    field_conflict(q, s, v)
        && s.match_indices(v)
            .all(|(i, _)| BOUND_OTHER.is_match(&s[..i]))
}

fn veto(q: &str, source: &str, value: &str) -> Option<&'static str> {
    if WANTS_STATE.is_match(q) || ASK_STATE.is_match(q) || CONDITIONAL.is_match(q) {
        return None;
    }
    let spans = document::locate(source, 0, value).ok()?;
    let lines = document::segments(source);
    let mut mentions = BTreeSet::new();
    for span in spans {
        let i = lines.partition_point(|l| l.end() <= span.start_byte);
        let l = lines.get(i)?;
        // Leave quotes that cross a sentence boundary or automated segment limit to the original reader.
        if span.start_byte < l.start || span.end_byte > l.end() {
            return None;
        }
        let physical_lo = source[..l.start].rfind('\n').map_or(0, |v| v + 1);
        let physical_hi = source[l.end()..]
            .find('\n')
            .map_or(source.len(), |v| l.end() + v);
        let physical = &source[physical_lo..physical_hi];
        if CONDITIONAL.is_match(physical)
            || (i > 0 && CONDITIONAL.is_match(lines[i - 1].text))
            || literal_name(physical, value)
            || LITERAL.is_match(physical)
        {
            return None;
        }
        mentions.insert(i);
    }
    let last = *mentions.last()?;
    if NEG_VALUE.is_match(value) && STATE_SENTENCE.is_match(value) {
        return Some("index-guard: quoted an unset-state statement");
    }
    if mentions
        .iter()
        .all(|&i| wrong_field(q, lines[i].text, value))
    {
        return Some("index-guard: source explicitly assigns a different attribute");
    }
    let target = subjects(q);
    let attribute = fields(q);
    if target.len() != 1 || attribute.len() != 1 {
        return None;
    }
    let field = attribute.first()?;
    let compatible = |s: &str| {
        let ns = subjects(s);
        let fs = fields(s);
        (ns.is_empty() || !target.is_disjoint(&ns))
            && (fs.is_empty() || !attribute.is_disjoint(&fs))
    };
    // An unqualified later confirmation may refer back to the proposal without repeating its value.
    let confirms = |from: usize| {
        lines[from..]
            .iter()
            .any(|l| reaffirm(l.text) && compatible(l.text))
    };
    if mentions.iter().all(|&i| {
        let clause = lines[i].text;
        tentative_mention(clause, value)
            && !reaffirm(clause)
            && subjects(clause) == target
            && proposal_field(clause, field, value)
    }) && !confirms(last + 1)
    {
        return Some("index-guard: value only appears as a proposal or withdrawal");
    }
    // Cross-sentence withdrawal needs one explicit subject and the same qualified field on both sides.
    if !mentions
        .iter()
        .all(|&i| subjects(lines[i].text) == target && fields(lines[i].text) == attribute)
    {
        return None;
    }
    let first = *mentions.first()?;
    let daily = lines[..=first]
        .iter()
        .rev()
        .find_map(|l| dated_section(l.text));
    for i in (first + 1)..lines.len() {
        let s = lines[i].text;
        if section(s) || dated_section(s).is_some_and(|name| Some(name) != daily) {
            break;
        }
        if i >= last
            && WITHDRAW.is_match(s)
            && !reaffirm(s)
            && !CONDITIONAL.is_match(s)
            && !CONDITIONAL.is_match(lines[i - 1].text)
            && subjects(s) == target
            && fields(s) == attribute
            && !confirms(i + 1)
        {
            return Some("index-guard: later notice withdraws the same subject and field");
        }
    }
    None
}

pub(super) fn apply(question: &str, source: &str, mut output: Output) -> Output {
    if output.status == "ok"
        && let Some(v) = output.value["answer"]["value"].as_str()
        && let Some(reason) = veto(question, source, v)
    {
        output.status = "abstained";
        output.value = json!({"answer":null});
        output.notes.push(reason.into());
    }
    output
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        engine::{ContextMode, Engine, Policy, Record},
        fm::{Model, Request},
        job::Job,
    };
    use serde_json::Value;

    #[test]
    fn four_distinct_known_nonempty_errors() {
        for (q, s, v) in [
            (
                "葵谷班が使える現在の受領符号は何ですか？",
                "葵谷班に受領符号 QF-225 の使用を認めた。\n葵谷班の受領符号を撤回した。代わりの符号は未定である。",
                "QF-225",
            ),
            (
                "Which briefing venue did the Mahogany team approve?",
                "The Mahogany team proposed Vale Hall for its briefing.\nThe Mahogany briefing venue remains under discussion; no venue has been approved.",
                "Vale Hall",
            ),
            (
                "What receipt code was assigned to the Aster team?",
                "Receipt code SX-404 was issued to the Petrel team.\nThe Aster team was assigned the color grey.",
                "grey",
            ),
            (
                "青嵐班が承認した説明会場はどこですか？",
                "青嵐班は説明会場として翠風館を提案した。\n青嵐班の説明会場は審議中で、承認された会場はまだない。",
                "審議中で、承認された会場はまだない。",
            ),
        ] {
            assert!(veto(q, s, v).is_some(), "{q}");
        }
    }

    #[test]
    fn leave_uncertain_scope_condition_names_status_and_reinstatement_to_reader() {
        let q = "Which receipt code is current for the Cedar team?";
        let first = "The Cedar team was assigned receipt code AC-21.";
        for addition in [
            "The Alder team receipt code was withdrawn.",
            "The Cedar team shipping code was withdrawn.",
            "If weather worsens, the Cedar team receipt code was withdrawn for that scenario.",
            "If the emergency plan takes effect:\nThe Cedar team receipt code was withdrawn.",
            "The Cedar team receipt code was withdrawn, but has now been reinstated.",
            "The Cedar team receipt code was withdrawn.\nThe assignment was reinstated.",
            "# West branch\nThe Cedar team receipt code was withdrawn.",
            "## 2028-03-02 — Different branch\nThe Cedar team receipt code was withdrawn.",
        ] {
            assert!(
                veto(q, &format!("{first}\n{addition}"), "AC-21").is_none(),
                "{addition}"
            );
        }
        for (q, s, v) in [
            (
                "Which briefing venue did the Cedar team approve?",
                "The Cedar team proposed Rain Hall for its briefing and subsequently ratified the choice.",
                "Rain Hall",
            ),
            (
                "Which briefing venue did the Cedar team approve?",
                "The Alder team proposed Rain Hall for its briefing. The Cedar team used the same place.",
                "Rain Hall",
            ),
            (
                "Which briefing venue did the Cedar team approve?",
                "The Cedar team proposed Rain Hall for its lecture. Its briefing used the same place.",
                "Rain Hall",
            ),
            (
                "Which site was chosen?",
                "The site named Under Review Hall was approved.",
                "Under Review Hall",
            ),
            (
                "Which venue was selected?",
                "The label is “under discussion”.",
                "under discussion",
            ),
            (
                "会場の名称は何ですか？",
                "本館は「未確定です」と命名された。",
                "未確定です",
            ),
            (
                "What is the approval status?",
                "The venue is under discussion.",
                "under discussion",
            ),
            (
                "What site was proposed?",
                "The Cedar team proposed Rain Hall.",
                "Rain Hall",
            ),
            (
                "Which hall?",
                "The team proposed Rain Hall.\nThe proposal was subsequently accepted.",
                "Rain Hall",
            ),
            (
                "Which hall?",
                "The team proposed Rain Hall.\nIt booked Rain Hall.",
                "Rain Hall",
            ),
            (
                "Which hall?",
                "If a room is booked, the team proposed Rain Hall.",
                "Rain Hall",
            ),
        ] {
            assert!(veto(q, s, v).is_none(), "{q} {s}");
        }
    }

    #[derive(Default)]
    struct Mock {
        counted: Vec<Request>,
        generated: Vec<Request>,
    }
    impl Model for Mock {
        fn ready(&mut self) -> Result<(), String> {
            Ok(())
        }
        fn count(&mut self, r: &Request) -> Result<usize, String> {
            self.counted.push(r.clone());
            Ok(r.prompt.chars().count())
        }
        fn generate(&mut self, r: &Request) -> Result<String, String> {
            self.generated.push(r.clone());
            let p = &r.schema.as_ref().unwrap()["properties"];
            let v = if p.get("supported").is_some() {
                json!({"supported":true})
            } else if p.get("answerable").is_some() {
                json!({"answerable":true,"answer":"grey"})
            } else if p.get("ids").is_some() {
                let (_, body) = r.prompt.split_once('\n').unwrap();
                let src: Value = serde_json::from_str(body).unwrap();
                let ids: Vec<_> = src["sentences"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .filter(|row| row[1].as_str().unwrap().contains("Aster"))
                    .map(|row| row[0].clone())
                    .collect();
                json!({"ids":ids})
            } else {
                json!({"answer":"grey"})
            };
            Ok(v.to_string())
        }
    }
    fn trial(mode: ContextMode, source: &str, budget: usize) -> (Mock, Record) {
        let mut model = Mock::default();
        let policy = Policy {
            compact: Some(mode),
            input_budget: budget,
            trace: true,
            repairs: 0,
            ..Policy::default()
        };
        let job = Job::Answer {
            source: source.into(),
            question: "What receipt code was assigned to the Aster team?".into(),
        };
        let out = Engine::new(&mut model, policy).execute(&job);
        (model, out)
    }
    #[test]
    fn full_and_ranked_readers_get_identical_requests_and_token_checks() {
        let fact = "The Aster team was assigned the color grey.";
        let mut rows: Vec<_> = (0..85)
            .map(|i| {
                format!(
                    "{} clerk {} cleaned the register and returned the supply box.",
                    ["Cove", "Pier", "Arch", "Dune", "Lane"][i % 5],
                    i * 7 + 13
                )
            })
            .collect();
        rows[19] = "The Petrel team was assigned receipt code XY-017.".into();
        rows[63] = fact.into();
        for (source, budget, ranked) in [
            (fact.to_owned(), 6200, false),
            (rows.join("\n"), 1950, true),
        ] {
            let (before, a) = trial(ContextMode::Indexed, &source, budget);
            let (after, b) = trial(ContextMode::IndexGuard, &source, budget);
            assert_eq!(a.status, "ok", "{:?}", a.notes);
            assert_eq!(b.status, "abstained", "{:?}", b.notes);
            assert_eq!(b.result, json!({"answer":null}));
            assert_eq!(before.generated, after.generated);
            assert_eq!(before.counted, after.counted);
            assert_eq!(a.attempts, b.attempts);
            assert_eq!(a.metrics.calls, b.metrics.calls);
            assert_eq!(a.metrics.sent_input_tokens, b.metrics.sent_input_tokens);
            assert_eq!(
                serde_json::to_value(&a.metrics.context).unwrap(),
                serde_json::to_value(&b.metrics.context).unwrap()
            );
            if ranked {
                assert_eq!(a.metrics.context.unwrap().method, "indexed_excerpts");
            }
        }
    }
}
