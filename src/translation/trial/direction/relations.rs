use super::{ACTIONS, CLAUSE, Hit, NUMBER, Pair, hits, re, semantic_text};
use crate::translation::{Translation, literal_spans};
use regex::Regex;
use std::{collections::BTreeSet, sync::LazyLock};

struct TimeAnchor {
    cue: Hit,
    when: String,
    action: Option<usize>,
}

static TIME_ANCHOR: LazyLock<Pair> = LazyLock::new(|| {
    Pair::new(
        r"\b(?P<cue>before|prior to|earlier than|no later than|by|after|following|later than|no earlier than|until|till)\s+(?P<when>[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?|[0-9]+(?:\.[0-9]+)?|monday|tuesday|wednesday|thursday|friday|saturday|sunday|noon|midnight)\b",
        r"(?P<when>[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?|[0-9]{1,2}時\s*[0-9]{1,2}分(?:\s*[0-9]{1,2}秒)?|[0-9]+(?:\.[0-9]+)?|[月火水木金土日]曜(?:日)?|正午|真夜中)\s*(?:[a-z°%/]+|時間|日間|週間|[年月日時分秒])?\s*(?:の|より(?:も)?)?(?P<cue>以前|前|までに|以降|以後|後|までは|まで)",
    )
});

fn when_key(text: &str) -> String {
    for (ja, en) in [
        ("月曜", "monday"),
        ("火曜", "tuesday"),
        ("水曜", "wednesday"),
        ("木曜", "thursday"),
        ("金曜", "friday"),
        ("土曜", "saturday"),
        ("日曜", "sunday"),
        ("正午", "noon"),
        ("真夜中", "midnight"),
    ] {
        if text.starts_with(ja) {
            return en.to_owned();
        }
    }
    if text.contains('時') {
        return NUMBER
            .find_iter(text)
            .map(|m| m.as_str())
            .collect::<Vec<_>>()
            .join(":");
    }
    text.to_owned()
}

fn time_anchors(text: &str, ja: bool) -> Vec<TimeAnchor> {
    let actions = hits(&ACTIONS, text, ja);
    TIME_ANCHOR
        .for_lang(ja)
        .captures_iter(text)
        .map(|m| {
            let c = m.name("cue").unwrap();
            let kind = match c.as_str() {
                "after" | "following" | "later than" | "no earlier than" | "以降" | "以後"
                | "後" => 1,
                "until" | "till" | "までは" | "まで" => 2,
                _ => 0,
            };
            let span = m.get(0).unwrap().range();
            let start = CLAUSE
                .find_iter(&text[..span.start])
                .last()
                .map_or(0, |m| m.end());
            let end = CLAUSE
                .find(&text[span.end..])
                .map_or(text.len(), |m| span.end + m.start());
            let local: BTreeSet<_> = actions
                .iter()
                .filter(|a| a.start >= start && a.end <= end)
                .map(|a| a.kind)
                .collect();
            TimeAnchor {
                cue: Hit {
                    start: c.start(),
                    end: c.end(),
                    kind,
                },
                when: when_key(&m["when"]),
                action: (local.len() == 1).then(|| *local.first().unwrap()),
            }
        })
        .collect()
}

pub(super) fn time_agrees(
    source: &str,
    target: &str,
    ja: bool,
    source_times: &mut Vec<Hit>,
    target_times: &mut Vec<Hit>,
) -> bool {
    let required = time_anchors(source, ja);
    let actual = time_anchors(target, !ja);
    let mut used = vec![false; actual.len()];
    let agrees = required.iter().all(|r| {
        let matched = actual.iter().enumerate().position(|(i, a)| {
            !used[i]
                && r.when == a.when
                && r.cue.kind == a.cue.kind
                && r.action.zip(a.action).is_none_or(|(x, y)| x == y)
        });
        if let Some(i) = matched {
            used[i] = true;
            true
        } else {
            false
        }
    });
    // A matched time or weekday is the argument of its own connective and
    // cannot also establish an order between the surrounding actions.
    let overlaps = |t: &Hit, a: &TimeAnchor| a.cue.start < t.end && t.start < a.cue.end;
    source_times.retain(|t| !required.iter().any(|a| overlaps(t, a)));
    target_times.retain(|t| !actual.iter().zip(&used).any(|(a, u)| *u && overlaps(t, a)));
    agrees
}

static RELATIVE: LazyLock<[Pair; 2]> = LazyLock::new(|| {
    let build = |en: &str, ja: &str, tail: &str| {
        Pair::new(
            &format!(r"\b(?:{en})\s+than\b"),
            &format!(r"(?:より(?:も)?|の(?:方|ほう)が)[^、。！？,;!?]{{0,18}}?(?:{ja})|{tail}"),
        )
    };
    [
        build(
            "more|larger|bigger|greater|higher|taller|longer|heavier|faster|stronger|wider|warmer|hotter|older|more expensive",
            "多|大き|高|長|重|速|強|広|暖か|温か|暑|古|上",
            "を上回",
        ),
        build(
            "less|fewer|smaller|lower|shorter|lighter|slower|weaker|narrower|cooler|colder|younger|cheaper|less expensive",
            "少な|小さ|低|短|軽|遅|弱|狭|涼し|冷た|寒|若|安|下",
            "を下回",
        ),
    ]
});
static SHARED_WORD: LazyLock<Regex> = LazyLock::new(|| re(r"[a-z][a-z0-9_-]*"));
static NUMERIC_REFERENCE: LazyLock<Pair> = LazyLock::new(|| {
    Pair::new(
        r"^\s*[-+]?[0-9]",
        r"[0-9]\s*(?:[a-z°%/]+|[年月日時分秒回件個本枚台度歳名人箱組週])?\s*$",
    )
});

struct Relative {
    direction: usize,
    // Canonical participant IDs in descending order, independent of which one
    // was the grammatical subject or whether an opposite adjective was used.
    participants: Option<(usize, usize)>,
}

fn relatives(text: &str, ja: bool, terms: &[(String, String)], source_side: bool) -> Vec<Relative> {
    let mut entities: Vec<_> = terms
        .iter()
        .enumerate()
        .flat_map(|(i, (s, t))| {
            let term = if source_side { s } else { t };
            literal_spans(text, term).map(move |span| (span, i))
        })
        .collect();
    entities.sort_by_key(|(span, _)| (span.start, std::cmp::Reverse(span.len())));
    hits(&*RELATIVE, text, ja)
        .into_iter()
        .filter_map(|cue| {
            let before = &text[..cue.start];
            let after = &text[cue.end..];
            if NUMERIC_REFERENCE
                .for_lang(ja)
                .is_match(if ja { before } else { after })
            {
                return None; // Amount bounds, including strictness, are checked separately.
            }
            let start = CLAUSE.find_iter(before).last().map_or(0, |m| m.end());
            let end = CLAUSE
                .find(after)
                .map_or(text.len(), |m| cue.end + m.start());
            let mut preceding = entities
                .iter()
                .rev()
                .filter(|(s, _)| s.start >= start && s.end <= cue.start);
            let mut following = entities
                .iter()
                .filter(|(s, _)| s.start >= cue.start && s.end <= end);
            let pair = preceding.next().and_then(|(_, near)| {
                if ja {
                    let other = following
                        .find(|(_, i)| i != near)
                        .or_else(|| preceding.find(|(_, i)| i != near))?
                        .1;
                    if text[cue.start..].starts_with('の') {
                        Some((*near, other))
                    } else {
                        Some((other, *near))
                    }
                } else {
                    Some((*near, following.find(|(_, i)| i != near)?.1))
                }
            });
            Some(Relative {
                direction: cue.kind,
                participants: pair.map(|(a, b)| if cue.kind == 0 { (a, b) } else { (b, a) }),
            })
        })
        .collect()
}

pub(super) fn comparisons_agree(s: &str, t: &str, ja: bool, input: &Translation) -> bool {
    if !RELATIVE.iter().any(|p| p.for_lang(ja).is_match(s)) {
        return true;
    }
    let mut terms: BTreeSet<_> = input
        .names
        .iter()
        .map(|n| (semantic_text(n), semantic_text(n)))
        .chain(
            input
                .glossary
                .iter()
                .map(|(s, t)| (semantic_text(s), semantic_text(t))),
        )
        .filter(|(a, b)| !a.is_empty() && !b.is_empty())
        .collect();
    let english = if ja { t } else { s };
    let japanese = if ja { s } else { t };
    for m in SHARED_WORD.find_iter(english) {
        if literal_spans(japanese, m.as_str()).next().is_some() {
            terms.insert((m.as_str().to_owned(), m.as_str().to_owned()));
        }
    }
    let terms: Vec<_> = terms.into_iter().collect();
    let required = relatives(s, ja, &terms, true);
    let actual = relatives(t, !ja, &terms, false);
    let mut used = vec![false; actual.len()];
    required.iter().all(|r| {
        let matched = actual.iter().enumerate().position(|(i, a)| {
            !used[i]
                && match (r.participants, a.participants) {
                    (Some(x), Some(y)) => x == y,
                    _ => r.direction == a.direction,
                }
        });
        if let Some(i) = matched {
            used[i] = true;
            true
        } else {
            false
        }
    })
}
