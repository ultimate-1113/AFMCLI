//! Conservative checks for explicit relations. Unrecognized paraphrases can be held.
use super::super::{NUMBER, Translation, normalize};
use regex::Regex;
use std::{collections::BTreeMap, sync::LazyLock};

mod relations;

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("static translation pattern")
}

struct Pair {
    en: Regex,
    ja: Regex,
}

impl Pair {
    fn new(en: &str, ja: &str) -> Self {
        Self {
            en: re(en),
            ja: re(ja),
        }
    }
    fn for_lang(&self, ja: bool) -> &Regex {
        if ja { &self.ja } else { &self.en }
    }
}

// Longest match wins. For example, "no more than" is an inclusive upper
// bound, even though it also ends in the strict lower bound "more than".
struct Bound {
    prefix: Pair,
    suffix: Pair,
}

impl Bound {
    fn new(symbol: &str, en_pre: &str, ja_pre: &str, en_post: &str, ja_post: &str) -> Self {
        Self {
            prefix: Pair::new(
                &format!(r"(?:\b(?:{en_pre})|{symbol})\s*$"),
                &format!(r"(?:{ja_pre}|{symbol})\s*$"),
            ),
            suffix: Pair::new(
                &format!(r"^\s*(?:{en_post})\b"),
                &format!(r"^\s*(?:{ja_post})"),
            ),
        }
    }
}

static BOUNDS: LazyLock<[Bound; 4]> = LazyLock::new(|| {
    [
        Bound::new(
            r">=|≥",
            r"at least|(?:no|not) (?:less|fewer|lower|smaller) than|a minimum of|minimum(?: of)?|not (?:below|under)",
            r"少なくと[も]|最低(?:でも)?|最小(?:でも)?|下限(?:は|が)?",
            r"or (?:more|greater|above|higher)|at (?:a |the )?minimum",
            r"以上|(?:を|より)?下回(?:らない|ってはならない)|(?:を)?割らない|を下限",
        ),
        Bound::new(
            r"<=|≤",
            r"at most|(?:no|not) (?:more|greater|higher|larger) than|a maximum of|maximum(?: of)?|(?:do(?:es)? not|must not|cannot|never) exceed|not (?:above|over)|up to|within|capped at",
            r"多くとも|最大(?:でも)?|上限(?:は|が)?|高々",
            r"or (?:less|fewer|below|lower)|at (?:a |the )?maximum",
            r"以下|以内|(?:を|より)?(?:超|越)(?:えない|えてはならない|えず)|を上限",
        ),
        Bound::new(
            ">",
            r"more than|greater than|higher than|above|over|exceed(?:s|ed|ing)?",
            r"より多く|超過(?:する)?",
            r"and (?:above that|higher than that)",
            r"(?:を|より)?(?:超|越)(?:え|す|し|過)?|より(?:多|大き|高|長|重|上)|を上回",
        ),
        Bound::new(
            "<",
            r"less than|fewer than|lower than|shorter than|under|below",
            r"より少なく",
            r"and (?:below that|lower than that)",
            r"未満|より(?:少な|小さ|低|短|軽|下)|を下回",
        ),
    ]
});

static UNIT: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"^\s*(?:[a-zµμ°%/]+|パーセント|キログラム|ミリ秒|時間|分間|秒間|日間|週間|か月|ヶ月|[年月日時分秒回件個本枚台度歳名人箱組週])\s*",
    )
});

fn strongest_bound(text: &str, ja: bool, prefix: bool) -> Option<usize> {
    BOUNDS
        .iter()
        .enumerate()
        .filter_map(|(kind, bound)| {
            let pattern = if prefix { &bound.prefix } else { &bound.suffix };
            let m = pattern.for_lang(ja).find(text)?;
            Some((m.len(), kind))
        })
        .max_by_key(|&(len, _)| len)
        .map(|(_, kind)| kind)
}

fn bounds(text: &str, ja: bool) -> BTreeMap<(String, usize), usize> {
    let mut found = BTreeMap::new();
    for n in NUMBER.find_iter(text) {
        let left = strongest_bound(&text[..n.start()], ja, true);
        let tail = &text[n.end()..];
        let right = strongest_bound(tail, ja, false).or_else(|| {
            let unit = UNIT.find(tail)?;
            strongest_bound(&tail[unit.end()..], ja, false)
        });
        for kind in [left, right]
            .into_iter()
            .flatten()
            .collect::<std::collections::BTreeSet<_>>()
        {
            *found.entry((n.as_str().to_owned(), kind)).or_default() += 1;
        }
    }
    found
}

static NEGATED_BOUND: LazyLock<Pair> = LazyLock::new(|| {
    Pair::new(
        r"\b(?:(?:no|not) (?:more|less|greater|fewer|lower|higher|larger|smaller|later|earlier) than|(?:do(?:es)? not|must not|cannot|never) exceed|not (?:above|below|under|over))\b",
        r"(?:超|越)え(?:ない|てはならない|ず)|下回(?:らない|ってはならない)|割らない",
    )
});
static NEGATION: LazyLock<Pair> = LazyLock::new(|| {
    Pair::new(
        r"\b(?:no|not|never|without|neither|cannot|can't|won't|(?:do|does|did|is|are|was|were|would|has|have|had|should|must|could)n['’]t|forbid(?:s|den)?|prohibit(?:s|ed)?|refrain|avoid(?:s|ed)?)\b",
        r"ない|なく|なかっ|なけれ|ません|せんでした|せず|[らわけかさしれ]ず|するな|すべからず|てはなら|ではなら|てはいけ|禁[止じ]|禁ず|不可|無用|避け|控え|未(?:確認|完了|承認|登録|実施|着|稼働)",
    )
});

// These are directional action pairs. Word boundaries and Japanese verb
// stems keep unrelated words such as "restartable" from implying an action.
static ACTIONS: LazyLock<Vec<Pair>> = LazyLock::new(|| {
    [
    (r"open(?:s|ed|ing)?|unseal(?:s|ed|ing)?", r"開(?:く|け|き|い|か|放|封|栓|弁|門)"),
    (r"close(?:s|d)?|closing|shut(?:s|ting)?|seal(?:s|ed|ing)?", r"閉[じめま鎖塞栓弁門]|密閉|封鎖|締め"),
    (r"start(?:s|ed|ing)?|begin(?:s|ning)?|began|initiat(?:e|es|ed|ing)|resum(?:e|es|ed|ing)|restart(?:s|ed|ing)?", r"開始|起動|始[めま]|始動|再開|着手|稼働"),
    (r"stop(?:s|ped|ping)?|halt(?:s|ed|ing)?|paus(?:e|es|ed|ing)|ceas(?:e|es|ed|ing)|suspend(?:s|ed|ing)?", r"停止|止[めま]|中止|中断|休止|終了|打ち切"),
    (r"increas(?:e|es|ed|ing)|rais(?:e|es|ed|ing)|boost(?:s|ed|ing)?", r"上[げが昇]|増[えや加大]|高め|引き上げ"),
    (r"decreas(?:e|es|ed|ing)|reduc(?:e|es|ed|ing)|lower(?:s|ed|ing)?", r"下[げが降]|減[らり少]|低[下め]|引き下げ"),
    (r"enabl(?:e|es|ed|ing)|activat(?:e|es|ed|ing)|turn(?:s|ed)? on|switch(?:es|ed)? on", r"有効|作動|オン|通電|電源を入"),
    (r"disabl(?:e|es|ed|ing)|deactivat(?:e|es|ed|ing)|turn(?:s|ed)? off|switch(?:es|ed)? off", r"無効|オフ|電源を切|作動を解除"),
    (r"send(?:s|ing)?|sent|transmit(?:s|ted|ting)?|dispatch(?:es|ed|ing)?", r"送[信付るりっら]|発[送信]"),
    (r"receiv(?:e|es|ed|ing)|accept(?:s|ed|ing)? delivery", r"受[信領取]|受け取"),
    (r"approv(?:e|es|ed|ing)|authoriz(?:e|es|ed|ing)|grant(?:s|ed|ing)? permission", r"承認|認可|許可"),
    (r"reject(?:s|ed|ing)?|den(?:y|ies|ied|ying)|refus(?:e|es|ed|ing)", r"却下|拒[否絶]|不許可|不承認|認めない"),
    (r"add(?:s|ed|ing)?|insert(?:s|ed|ing)?|attach(?:es|ed|ing)?", r"加[えわ]|追加|挿入|添付|取り付"),
    (r"remov(?:e|es|ed|ing)|delet(?:e|es|ed|ing)|detach(?:es|ed|ing)?", r"除[去く]|外[すし]|取り外|取り除|削除|撤去|消[去すし]"),
    (r"lock(?:s|ed|ing)?", r"施錠|ロック(?:する|し|をかけ)|鍵をかけ"),
    (r"unlock(?:s|ed|ing)?", r"解錠|ロックを解除|鍵を開"),
].into_iter().map(|(en, ja)| Pair::new(&format!(r"\b(?:{en})\b"), ja)).collect()
});

#[derive(Clone, Copy)]
struct Hit {
    start: usize,
    end: usize,
    kind: usize,
}

fn hits(patterns: &[Pair], text: &str, ja: bool) -> Vec<Hit> {
    let mut hits: Vec<_> = patterns
        .iter()
        .enumerate()
        .flat_map(|(kind, p)| {
            p.for_lang(ja).find_iter(text).map(move |m| Hit {
                start: m.start(),
                end: m.end(),
                kind,
            })
        })
        .collect();
    hits.sort_by_key(|h| (h.start, std::cmp::Reverse(h.end - h.start)));
    let mut end = 0;
    hits.retain(|h| {
        if h.start < end {
            return false;
        }
        end = h.end;
        true
    });
    hits
}

static TIMES: LazyLock<[Pair; 3]> = LazyLock::new(|| {
    [
        Pair::new(
            r"\b(?:before|prior to|ahead of|earlier than|no later than|by (?:the (?:time|end|start|deadline)|noon|midnight|dawn|dusk|close of|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|by\s+[0-9]+)\b",
            r"以前|より前|前(?:に|まで|の|で|は|、|。|$)|先立|先に|までに|より早",
        ),
        Pair::new(
            r"\b(?:after|following|once|later than|as soon as|no earlier than)\b",
            r"以[降後]|より後|後(?:に|で|は|の|、|。|$)|てから|たら|次に|その後|より遅",
        ),
        Pair::new(r"\b(?:until|till)\b", r"までは|まで"),
    ]
});

static CLAUSE: LazyLock<Regex> =
    LazyLock::new(|| re(r"[,;.!?、。！？；]|\b(?:but|then|and)\b|一方|ただし"));

fn explicitly_negative_action(text: &str, ja: bool, hit: Hit) -> bool {
    let clause = if ja {
        CLAUSE.split(&text[hit.start..]).next().unwrap_or("")
    } else {
        CLAUSE.split(&text[..hit.start]).last().unwrap_or("")
    };
    if ja {
        let nearby: String = clause.chars().take(14).collect();
        NEGATION.ja.is_match(&nearby)
    } else {
        let nearby: String = clause
            .split_whitespace()
            .rev()
            .take(4)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect::<Vec<_>>()
            .join(" ");
        NEGATION.en.is_match(&nearby)
    }
}

// Resolve the roles where both events are recognizable. "B after A" and
// "A before B" should agree even though their surface connectives differ.
fn event_order(links: &[Hit], actions: &[Hit], ja: bool) -> Vec<(usize, usize)> {
    let mut order = Vec::new();
    for link in links.iter().filter(|c| c.kind < 2) {
        let previous = actions.iter().rev().find(|a| a.end <= link.start);
        let mut following = actions.iter().filter(|a| a.start >= link.end);
        let Some(next) = following.next() else {
            continue;
        };
        let (left, right, reverse) = if let Some(prev) = previous {
            (prev.kind, next.kind, ja)
        } else if !ja {
            let Some(main) = following.next() else {
                continue;
            };
            (main.kind, next.kind, false)
        } else {
            continue;
        };
        if left != right {
            order.push(if (link.kind == 1) ^ reverse {
                (right, left)
            } else {
                (left, right)
            });
        }
    }
    order.sort_unstable();
    order
}

fn semantic_text(text: &str) -> String {
    normalize(text)
        .to_lowercase()
        .replace('’', "'")
        .replace("午前", "am")
        .replace("午後", "pm")
        .replace("lower than", "less than")
}

pub(super) fn issues(source: &str, output: &str, input: &Translation) -> Vec<String> {
    let src = semantic_text(source);
    let dst = semantic_text(output);
    let ja = input.source_language == "ja";
    let mut issues = Vec::new();
    let required = bounds(&src, ja);
    let actual = bounds(&dst, !ja);
    if let Some(((number, kind), _)) = required.iter().find(|(k, n)| actual.get(k) != Some(*n)) {
        let relation = ["at least", "at most", "more than", "less than"][*kind];
        issues.push(format!("preserve the source bound '{relation} {number}'"));
    }
    let s = NEGATED_BOUND.for_lang(ja).replace_all(&src, " ");
    let t = NEGATED_BOUND.for_lang(!ja).replace_all(&dst, " ");
    if !relations::comparisons_agree(&s, &t, ja, input) {
        issues.push("comparison direction or participants".into());
    }
    let neg_source = NEGATION.for_lang(ja).is_match(&s);
    let neg_target = NEGATION.for_lang(!ja).is_match(&t);
    if neg_source != neg_target {
        issues.push("explicit negation or prohibition".into());
    }
    let src_actions = hits(&ACTIONS, &s, ja);
    let dst_actions = hits(&ACTIONS, &t, !ja);
    for action in &src_actions {
        let wanted = explicitly_negative_action(&s, ja, *action);
        let agrees = dst_actions
            .iter()
            .any(|a| a.kind == action.kind && (!wanted || explicitly_negative_action(&t, !ja, *a)));
        if !agrees {
            issues.push(format!(
                "preserve the action '{}' and its polarity",
                &s[action.start..action.end]
            ));
            break;
        }
    }
    let mut src_times = hits(&*TIMES, &src, ja);
    let mut dst_times = hits(&*TIMES, &dst, !ja);
    let anchors_agree = relations::time_agrees(&src, &dst, ja, &mut src_times, &mut dst_times);
    // Retain offsets from the corresponding, unmodified text for event roles.
    let a = event_order(&src_times, &hits(&ACTIONS, &src, ja), ja);
    let b = event_order(&dst_times, &hits(&ACTIONS, &dst, !ja), !ja);
    let ordered = !a.is_empty() && !b.is_empty();
    if !anchors_agree
        || (ordered && a != b)
        || (!ordered
            && src_times
                .iter()
                .any(|x| !dst_times.iter().any(|y| y.kind == x.kind)))
    {
        issues.push("before/after, deadline or until relationship".into());
    }
    issues
}
