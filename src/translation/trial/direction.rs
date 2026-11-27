//! Conservative checks for explicit relations. Unrecognized paraphrases can be held.
use super::super::{NUMBER, Translation, normalize};
use regex::Regex;
use std::{collections::BTreeMap, sync::LazyLock};

mod relations;
mod timing;

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
            r"at least|(?:no|not) (?:less|fewer|lower|smaller) than|a minimum of|minimum(?: of)?|minimum\b[^,;.!?\d]{1,55}\b(?:at|to|of)|not (?:below|under)",
            r"少なくと[も]|最低(?:でも)?|最小(?:でも)?|下限(?:は|が)?",
            r"or (?:more|greater|above|higher)|at (?:a |the )?minimum",
            r"以上|(?:を|より)?下回(?:らない|っては(?:なら|いけ)ない)|(?:を)?割らない|(?:を|が)下限|未満に(?:しない|なってはならない)",
        ),
        Bound::new(
            r"<=|≤",
            r"at most|(?:no|not) (?:more|greater|higher|larger) than|a maximum of|maximum(?: of)?|maximum\b[^,;.!?\d]{1,55}\b(?:at|to|of)|(?:do(?:es)? not|must not|cannot|never) exceed|not (?:above|over)|up to|within|(?:caps?|capped|limits?|limited)\b[^,;.!?\d]{0,55}?\b(?:at|to)",
            r"多くとも|最大(?:でも)?|上限(?:は|が)?|高々",
            r"or (?:less|fewer|below|lower)|at (?:a |the )?maximum",
            r"以下|以内|(?:を|より)?(?:超|越)(?:えない|えては(?:なら|いけ)ない|えず)|(?:を|が|は)上限|に制限",
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
            r"未満|より(?:少な|小さ|低|短|軽|下)|を下回|に(?:満た|達し|届か)(?:ない|ず)",
        ),
    ]
});

static UNIT: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"^\s*(?:[a-zµμ°%/]+|パーセント|キログラム|ミリ秒|時間|分間|秒間|日間|週間|か月|ヶ月|[年月日時分秒回件個本枚台度歳名人箱組週])\s*",
    )
});

fn strongest_bound(text: &str, ja: bool, prefix: bool) -> Option<(usize, std::ops::Range<usize>)> {
    BOUNDS
        .iter()
        .enumerate()
        .filter_map(|(kind, bound)| {
            let pattern = if prefix { &bound.prefix } else { &bound.suffix };
            let m = pattern.for_lang(ja).find(text)?;
            Some((kind, m.range()))
        })
        .max_by_key(|(_, span)| span.len())
}

#[derive(Default)]
struct Amounts {
    found: BTreeMap<(String, usize), usize>,
    spans: Vec<std::ops::Range<usize>>,
}

fn bounds(text: &str, ja: bool) -> Amounts {
    let mut amounts = Amounts::default();
    for n in NUMBER.find_iter(text) {
        let left = strongest_bound(&text[..n.start()], ja, true);
        let tail = &text[n.end()..];
        let right = strongest_bound(tail, ja, false)
            .map(|x| (x, 0))
            .or_else(|| {
                let unit = UNIT.find(tail)?;
                strongest_bound(&tail[unit.end()..], ja, false).map(|x| (x, unit.end()))
            })
            .map(|((k, span), skip)| (k, n.end() + skip + span.start..n.end() + skip + span.end));
        let mut kinds = std::collections::BTreeSet::new();
        for (kind, span) in [left, right].into_iter().flatten() {
            amounts.spans.push(span);
            if kinds.insert(kind) {
                *amounts
                    .found
                    .entry((n.as_str().to_owned(), kind))
                    .or_default() += 1;
            }
        }
    }
    amounts
}

fn without_comparators(text: &str, amounts: &Amounts) -> String {
    static INTERNAL_NEGATION: LazyLock<Regex> = LazyLock::new(|| {
        re(
            r"(?:no|not) (?:more|less|greater|fewer|lower|higher|larger|smaller) than|(?:do(?:es)? not|must not|cannot|never) exceed|not (?:above|below|under|over)|少なくとも|(?:を|より)?下回(?:らない|っては(?:なら|いけ)ない)|(?:超|越)え(?:ない|ては(?:なら|いけ)ない|ず)|割らない|未満に(?:しない|なってはならない)|に(?:満た|達し|届か)(?:ない|ず)",
        )
    });
    let mut spans = amounts.spans.clone();
    spans.sort_by_key(|span| span.start);
    let mut prose = String::new();
    let mut end = 0;
    for span in spans {
        if span.start >= end {
            prose.push_str(&text[end..span.start]);
            prose.push_str(&INTERNAL_NEGATION.replace_all(&text[span.clone()], " "));
        }
        end = end.max(span.end);
    }
    prose.push_str(&text[end..]);
    prose
        .replace("no later than", "by")
        .replace("no earlier than", "after")
}
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
    (r"enabl(?:e|es|ed|ing)|activat(?:e|es|ed|ing)|turn(?:s|ed)? on|switch(?:es|ed)? on", r"有効|作動|オン|通電|電源を入|点灯|(?:灯|照明|電気|ライト)を(?:つけ|点け)"),
    (r"disabl(?:e|es|ed|ing)|deactivat(?:e|es|ed|ing)|turn(?:s|ed)? off|switch(?:es|ed)? off", r"無効|オフ|電源を切|作動を解除|消灯|(?:灯|照明|電気|ライト)を消"),
    (r"send(?:s|ing)?|sent|transmit(?:s|ted|ting)?|dispatch(?:es|ed|ing)?", r"送[信付るりっら]|発[送信]"),
    (r"receiv(?:e|es|ed|ing)|accept(?:s|ed|ing)? delivery", r"受[信領取]|受け取"),
    (r"approv(?:e|es|ed|ing)|authoriz(?:e|es|ed|ing)|grant(?:s|ed|ing)? permission", r"承認|認可|許可"),
    (r"reject(?:s|ed|ing)?|den(?:y|ies|ied|ying)|refus(?:e|es|ed|ing)", r"却下|拒[否絶]|不許可|不承認|認めない"),
    (r"add(?:s|ed|ing)?|insert(?:s|ed|ing)?|attach(?:es|ed|ing)?", r"加[えわ]|追加|挿入|添付|取り付"),
    (r"remov(?:e|es|ed|ing)|delet(?:e|es|ed|ing)|detach(?:es|ed|ing)?", r"除[去く]|外[すし]|取り外|取り除|削除|撤去|消去"),
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
            r"以前|より前|(?:の)?まえ(?:に|まで|の|は|、)|前(?:に|まで|の|で|は|、|。|$)|先立|先に|までに|より早",
        ),
        Pair::new(
            r"\b(?:after|following|once|later than|as soon as|no earlier than)\b",
            r"以[降後]|より後|(?:の)?あと|後(?:に|で|は|の|、|。|$)|てから|たら|次に|その後|より遅",
        ),
        Pair::new(r"\b(?:until|till)\b", r"までは|まで"),
    ]
});

static CLAUSE: LazyLock<Regex> =
    LazyLock::new(|| re(r"[,;!?、。！？；]|\.(?:\s|$)|\b(?:but|then|and)\b|一方|ただし"));

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

const EN_BOUNDS: [&str; 4] = ["at least", "at most", "more than", "less than"];
const JA_BOUNDS: [&str; 4] = ["以上", "以下", "を超える", "未満"];

pub(super) fn guide(source: &str, input: &Translation) -> Vec<String> {
    let text = semantic_text(source);
    let ja = input.source_language == "ja";
    let amounts = bounds(&text, ja);
    let mut guide: Vec<_> = amounts
        .found
        .keys()
        .map(|(n, k)| {
            let target = if ja { EN_BOUNDS[*k] } else { JA_BOUNDS[*k] };
            format!("{} {n}; target comparison: {target}", EN_BOUNDS[*k])
        })
        .collect();
    guide.extend(timing::guide(&text, ja, input));
    guide
}

pub(super) fn issues(source: &str, output: &str, input: &Translation) -> Vec<String> {
    let src = semantic_text(source);
    let dst = semantic_text(output);
    let ja = input.source_language == "ja";
    let mut issues = Vec::new();
    let required = bounds(&src, ja);
    let actual = bounds(&dst, !ja);
    if let Some(((number, kind), _)) = required
        .found
        .iter()
        .find(|(k, n)| actual.found.get(k) != Some(*n))
    {
        let problem = if actual.found.keys().any(|(n, _)| n == number) {
            "opposite direction or different inclusivity"
        } else {
            "unresolved comparison wording"
        };
        issues.push(format!(
            "{problem}: preserve '{} {number}'",
            EN_BOUNDS[*kind]
        ));
    }
    let s = without_comparators(&src, &required);
    let t = without_comparators(&dst, &actual);
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
        let same: Vec<_> = dst_actions
            .iter()
            .filter(|a| a.kind == action.kind)
            .collect();
        let opposite = action.kind ^ 1;
        let contradicted = same.is_empty() && dst_actions.iter().any(|a| a.kind == opposite)
            || !same.is_empty()
                && wanted
                && !same
                    .iter()
                    .any(|a| explicitly_negative_action(&t, !ja, **a));
        if contradicted {
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
        || timing::conflicts(&src, &dst, ja, input)
        || (ordered && a != b)
        || (!ordered
            && !dst_times.is_empty()
            && src_times
                .iter()
                .any(|x| !dst_times.iter().any(|y| y.kind == x.kind)))
    {
        issues.push("before/after, deadline or until relationship".into());
    }
    issues
}
