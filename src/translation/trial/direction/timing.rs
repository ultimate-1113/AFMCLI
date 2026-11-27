use super::{CLAUSE, TIMES, Translation, hits, re, semantic_text};
use crate::translation::literal_spans;
use regex::Regex;
use std::{collections::BTreeSet, sync::LazyLock};

static DURATION: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"(?P<n>[0-9]+(?:\.[0-9]+)?)\s*-?\s*(?P<u>milliseconds?|minutes?|seconds?|hours?|weeks?|days?|hrs?|mins?|secs?|ms|h|s|ミリ秒|時間|分間|秒間|日間|週間|分|秒|日)",
    )
});
static EN_NEXT: LazyLock<Regex> = LazyLock::new(|| {
    re(r"^\s*(?:immediately\s+)?(?P<cue>leading up to|prior to|ahead of|before|following|after)\b")
});
static EN_WINDOW: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"\b(?:during|throughout|for|over|in)\s+(?:(?:the|a|that|those|entire|full|whole|next|first|last|preceding|following)\s+)*$",
    )
});
static WAIT: LazyLock<Regex> = LazyLock::new(|| re(r"\bwait(?:s|ed|ing)?(?:\s+for)?\s*$"));
static JA_NEXT: LazyLock<Regex> =
    LazyLock::new(|| re(r"^\s*(?:より|の|が)?(?P<cue>前|まえ|後|あと|経過し|経過後|たっ|経っ)"));
static JA_PREVIOUS: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?P<cue>前|まえ|後|あと)(?:の|から)?\s*$"));

#[derive(Clone, PartialEq, Eq)]
pub(super) struct Offset {
    number: String,
    unit: &'static str,
    after: bool,
    window: bool,
}

fn unit(s: &str) -> &'static str {
    if matches!(s, "時間" | "h" | "hr" | "hrs" | "hour" | "hours") {
        "h"
    } else if s.starts_with("min") || s.starts_with('分') {
        "min"
    } else if s.starts_with("millis") || matches!(s, "ms" | "ミリ秒") {
        "ms"
    } else if s.starts_with("week") || s == "週間" {
        "week"
    } else if s.starts_with("day") || s.starts_with('日') {
        "day"
    } else {
        "s"
    }
}

fn offsets(text: &str, ja: bool) -> Vec<Offset> {
    DURATION
        .captures_iter(text)
        .filter_map(|m| {
            let amount = m.get(0).unwrap();
            let pre = &text[..amount.start()];
            let post = &text[amount.end()..];
            if m["u"].is_ascii() && post.as_bytes().first().is_some_and(u8::is_ascii_alphabetic) {
                return None;
            }
            let (after, window) = if ja {
                if let Some(c) = JA_NEXT.captures(post) {
                    let rest = &post[c.get(0).unwrap().end()..];
                    (
                        !matches!(&c["cue"], "前" | "まえ"),
                        rest.starts_with("の間") || rest.starts_with("にわた"),
                    )
                } else {
                    let c = JA_PREVIOUS.captures(pre)?;
                    if !["は", "の間", "間", "にわた", "に限"]
                        .iter()
                        .any(|p| post.trim_start().starts_with(p))
                    {
                        return None;
                    }
                    (!matches!(&c["cue"], "前" | "まえ"), true)
                }
            } else {
                if WAIT.is_match(pre) {
                    return None;
                }
                let c = EN_NEXT.captures(post)?;
                (
                    matches!(&c["cue"], "following" | "after"),
                    EN_WINDOW.is_match(pre),
                )
            };
            Some(Offset {
                number: m["n"].to_owned(),
                unit: unit(&m["u"]),
                after,
                window,
            })
        })
        .collect()
}

fn references(
    text: &str,
    ja: bool,
    input: &Translation,
    in_source: bool,
) -> BTreeSet<(usize, String)> {
    let mut terms = Vec::new();
    for (source, target) in &input.glossary {
        let spelling = semantic_text(if in_source { source } else { target });
        for span in literal_spans(text, &spelling) {
            terms.push((span, target.to_owned()));
        }
    }
    let mut links = BTreeSet::new();
    for cue in hits(&*TIMES, text, ja) {
        let nearest = terms
            .iter()
            .filter_map(|(span, term)| {
                let gap = if ja && span.end <= cue.start {
                    &text[span.end..cue.start]
                } else if !ja && cue.end <= span.start {
                    &text[cue.end..span.start]
                } else {
                    return None;
                };
                let distance = gap.chars().count();
                (distance <= 26 && !CLAUSE.is_match(gap)).then_some((distance, term))
            })
            .min_by_key(|(distance, _)| *distance);
        if let Some((_, term)) = nearest {
            links.insert((cue.kind, term.clone()));
        }
    }
    links
}

pub(super) fn guide(text: &str, ja: bool, input: &Translation) -> Vec<String> {
    let refs = references(text, ja, input, true);
    let mut guidance: Vec<_> = offsets(text, ja)
        .iter()
        .map(|o| {
            let phase = if o.after { "after" } else { "before" };
            let role = if o.window {
                "the instruction applies throughout the entire interval"
            } else {
                "the action occurs at this time offset"
            };
            let events: Vec<_> = refs
                .iter()
                .filter(|(kind, _)| *kind == usize::from(o.after))
                .map(|(_, term)| term.as_str())
                .collect();
            let event = if events.len() == 1 {
                events[0]
            } else {
                "the reference event"
            };
            format!("{} {} {phase} {event}: {role}.", o.number, o.unit)
        })
        .collect();
    for m in DURATION.captures_iter(text) {
        let a = m.get(0).unwrap();
        if (!ja && WAIT.is_match(&text[..a.start()]))
            || (ja && text[a.end()..].trim_start().starts_with('待'))
        {
            guidance.push(format!(
                "Keep the waiting period of {} {} before the subsequent action.",
                &m["n"],
                unit(&m["u"])
            ));
        }
    }
    guidance
}

pub(super) fn conflicts(source: &str, target: &str, ja: bool, input: &Translation) -> bool {
    let required = offsets(source, ja);
    let actual = offsets(target, !ja);
    if required.iter().any(|r| {
        actual
            .iter()
            .any(|a| a.number == r.number && a.unit == r.unit)
            && !actual.iter().any(|a| a == r)
    }) {
        return true;
    }
    let required = references(source, ja, input, true);
    let actual = references(target, !ja, input, false);
    required
        .iter()
        .any(|r| actual.iter().any(|a| a.0 == r.0) && !actual.contains(r))
}
