//! Classify a user-resolvable selection only when the original material contains opposing branches.
use super::{
    indexed,
    passages::{self, Corpus},
};
use regex::Regex;
use std::{collections::BTreeSet, sync::LazyLock};

fn open_period(c: &Corpus<'_>, q: &str, ids: &[usize], at: usize, label: &str) -> Option<String> {
    static PERIOD: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?ix)\b(?:spring|summer|autumn|fall|winter|monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekdays?|weekends?|\d{4}[-/]\d{1,2}[-/]\d{1,2}|(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}|\d{1,2}\s+(?:january|february|march|april|may|june|july|august|september|october|november|december))\b|(?:春|夏|秋|冬)(?:号|版|期)|[0-9０-９]+月[0-9０-９]+日|平日|休日|週末").unwrap()
    });
    let mut branches = Vec::<String>::new();
    for &i in ids {
        if let Some(context) = c.branch_context(i) {
            let mut periods = PERIOD.find_iter(&context);
            if let Some(p) = periods.next()
                && periods.next().is_none()
                && !branches.iter().any(|b| b.eq_ignore_ascii_case(p.as_str()))
            {
                branches.push(p.as_str().to_owned());
            }
        }
    }
    let [a, b] = branches.as_slice() else {
        return None;
    };
    let mentions = |s: &str| {
        let l = s.to_lowercase();
        [a, b].iter().any(|p| l.contains(&p.to_lowercase()))
    };
    if mentions(q)
        || ids
            .iter()
            .any(|&i| i > at && passages::identifies(c.rows[i].text) && mentions(c.rows[i].text))
    {
        return None;
    }
    let question = if q.chars().any(|ch| matches!(ch as u32, 0x3040..=0x30ff)) {
        format!("資料の{label}について、今回当てはまるのは「{a}」と「{b}」のどちらですか？")
    } else if english_question(q) {
        format!("Which {label} applies to your record, “{a}” or “{b}”?")
    } else {
        return None;
    };
    Some(question)
}

pub(super) fn english_question(q: &str) -> bool {
    static EN: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?i)\b(?:what|which|where|when|how|whose|who|does|did|please|tell me|can you|could you)\b").unwrap()
    });
    EN.is_match(q)
}

static MISSING: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?ix)
   \b(?:missing|unknown|unspecified|unstated|unclear|unrecorded|unmarked|not\s+(?:specified|recorded|stated|indicated|identified)|(?:does|did)\s+not\s+(?:say|specify|identify|record)|cannot\s+(?:tell|determine)|uncertain\s+whether)\b|
   不明|未記入|未記載|書かれていない|記されていない|記されておらず|書かれておらず|特定でき|区別でき|分から|わから|記載がない").unwrap()
});
static AXES: LazyLock<Vec<(&str, Regex)>> = LazyLock::new(|| {
    [
    ("condition",r"(?i)\b(?:whether|condition|scenario|setting|configuration|operating mode|circumstance|weather)\b|条件|運用状況|設定|天候|稼働状態|かどうか|の有無"),
    ("date",r"(?i)\b(?:season|issue date|effective date|date|period|year|month|edition|release)\b|発行日|時期|日付|季節|年度|何月"),
    ("target",r"(?i)\b(?:target|subject|recipient|identity|destination|branch|department|team|office|site|unit|item)\b|対象|宛先|目的地|班名|課名|部署|拠点|所属|支店|機種|個体"),
].into_iter().map(|(k,r)|(k,Regex::new(r).unwrap())).collect()
});

pub(super) fn axis(s: &str) -> Option<(&'static str, &str)> {
    if !MISSING.is_match(s) {
        return None;
    }
    AXES.iter()
        .find_map(|(kind, r)| r.find(s).map(|m| (*kind, m.as_str())))
}

fn target(c: &Corpus<'_>, q: &str, ids: &[usize], at: usize) -> Option<String> {
    static TARGETS: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?x)
        ([\p{Han}\p{Katakana}A-Za-z0-9-]{1,12})(班|課|組|室|支店|営業所|拠点|部署|号機|便)|
        \b([A-Za-z][A-Za-z0-9-]*)\s+(?i:team|office|branch|unit|site|division|department|location)\b|
        \b(?:[Tt]eam|[Pp]roject|[Uu]nit|[Ss]ite)\s+[A-Z0-9][A-Za-z0-9-]*\b").unwrap()
    });
    let mut opts = Vec::<String>::new();
    for &i in ids {
        let row = c.rows[i].text;
        if i == at || !c.maps_property(q, i) {
            continue;
        }
        for m in TARGETS.find_iter(row) {
            let s = m.as_str();
            if [
                "同班",
                "両班",
                "二班",
                "所属班",
                "各班",
                "同組",
                "this team",
                "the team",
                "the office",
                "that branch",
                "each office",
            ]
            .iter()
            .any(|x| s.eq_ignore_ascii_case(x))
            {
                continue;
            }
            if !opts.iter().any(|v| v.eq_ignore_ascii_case(s)) {
                opts.push(s.to_owned());
            }
        }
    }
    let [a, b] = opts.as_slice() else {
        return None;
    };
    if answered(q, c, ids, at, &[a, b]) {
        return None;
    }
    if q.chars().any(|ch| matches!(ch as u32, 0x3040..=0x30ff)) {
        Some(format!("今回の対象は「{a}」と「{b}」のどちらですか？"))
    } else if english_question(q) {
        Some(format!("Which target do you mean, “{a}” or “{b}”?"))
    } else {
        None
    }
}

fn answered(q: &str, c: &Corpus<'_>, ids: &[usize], at: usize, options: &[&str]) -> bool {
    let mentions = |s: &str| {
        let low = s.to_lowercase();
        options.iter().any(|v| low.contains(&v.to_lowercase()))
    };
    mentions(q)
        || ids
            .iter()
            .any(|&i| i > at && passages::identifies(c.rows[i].text) && mentions(c.rows[i].text))
}

fn prerequisite(c: &Corpus<'_>, q: &str, ids: &[usize], at: usize) -> Option<String> {
    static WHETHER: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"(?i)\bwhether\s+([^.!?;]{3,110})").unwrap());
    static BRANCH: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?i)\b(?:if|unless|when|with|without|under|requires?|applies|during|depending)\b|場合|なら|とき|の際|の有無").unwrap()
    });
    let line = c.rows[at].text;
    if q.chars().any(|ch| matches!(ch as u32, 0x3040..=0x30ff)) {
        static PAIR: LazyLock<Regex> = LazyLock::new(|| {
            Regex::new(r"[「『]([^」』]{1,32})[」』](?:なの)?か[、・]?[「『]([^」』]{1,32})[」』]")
                .unwrap()
        });
        let m = PAIR.captures(line)?;
        let options = [m.get(1)?.as_str(), m.get(2)?.as_str()];
        if options[0] == options[1]
            || answered(q, c, ids, at, &options)
            || !options.iter().all(|option| {
                ids.iter().any(|&i| {
                    i != at
                        && c.rows[i].text.contains(option)
                        && (BRANCH.is_match(c.rows[i].text) || c.maps_property(q, i))
                })
            })
        {
            return None;
        }
        return Some(format!(
            "今回適用する条件は「{}」と「{}」のどちらですか？",
            options[0], options[1]
        ));
    }
    let p = WHETHER
        .captures(line)?
        .get(1)?
        .as_str()
        .trim_end_matches([',', ' ']);
    let (left, right) = p.split_once(" or ")?;
    let lhs = left.split_whitespace().next_back()?;
    let rhs = right.split_whitespace().next_back()?;
    let stop = [
        "was", "were", "is", "are", "has", "had", "the", "its", "whether", "this", "that", "on",
        "a", "an", "not",
    ];
    let object: BTreeSet<_> = indexed::tokens(left)
        .into_iter()
        .filter(|w| w != lhs && !stop.contains(&w.as_str()))
        .collect();
    let mut confirmations = 0;
    for &i in ids {
        let row = c.rows[i].text;
        if i != at && BRANCH.is_match(row) && !indexed::tokens(row).is_disjoint(&object) {
            confirmations += 1;
        }
    }
    if confirmations < 2 || answered(q, c, ids, at, &[lhs, rhs]) {
        return None;
    }
    if english_question(q) {
        Some(format!("Could you confirm whether {p}?"))
    } else {
        None
    }
}

pub(super) fn question(c: &Corpus<'_>, q: &str, ids: &[usize]) -> Option<(&'static str, String)> {
    if c.pending(q, ids) {
        return None;
    }
    ids.iter().rev().find_map(|&i| {
        let (kind, label) = axis(c.rows[i].text)?;
        if c.different(q, i, "") {
            return None;
        }
        let ask = match kind {
            "date" => open_period(c, q, ids, i, label),
            "target" => target(c, q, ids, i),
            "condition" => prerequisite(c, q, ids, i),
            _ => None,
        }?;
        Some((kind, ask))
    })
}
