//! A local, bounded selector for the named QA readers. Ranges always refer to original bytes.
use super::{checked, choice, indexed};
#[cfg(feature = "trial-qa-preview")]
use crate::document::Span;
use crate::{
    document::{self, Evidence, Segment},
    engine::{ContextUsage, Engine, RetrievalRound, RetrievalUsage, SourceRange},
};
use regex::Regex;
use std::{
    cmp::Reverse,
    collections::{BTreeSet, HashMap},
    sync::LazyLock,
};

pub(super) const BYTE_LIMIT: usize = 2 * 1024 * 1024;
pub(super) const ROW_LIMIT: usize = 32_768;

static POSITIVE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?ix)
    \b(?:approv\w*|assign\w*|reassign\w*|reinstat\w*|restor\w*|reconfirm\w*|confirm\w*|
    authori[sz]\w*|adopt\w*|issu(?:e|ed)|ratif\w*|designat\w*|select\w*|chosen|chose|
    sign(?:ed|off)|certif\w*|sanction(?:s|ed)?|now\s+valid|back\s+in\s+(?:force|effect))\b|
    正式|承認|決裁|採用|確定|決定|指定|付与|発行|選定|再開|復活|差し戻しを解除|有効と|認めた",
    )
    .unwrap()
});
static CLOSED: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?ix)
    \b(?:withdrawn|withdrew|revok\w*|rescinded|cancel(?:l)?ed|retract\w*|voided|lapsed|
    suspended|unconfirmed|unapproved|unassigned|undecided|pending|awaiting|
    not\s+(?:yet\s+)?(?:approved|confirmed|valid|assigned)|no\s+(?:approved|replacement|longer)|
    still\s+(?:open|undecided)|under\s+(?:review|discussion))\b|
    撤回|取り消|取消|取り下げ|差し戻|無効|失効|見送|保留|未確定|未定|未決|未承認|審議中|検討中|未発行|
    未着|未割当|決まっていない|決まらず|承認されていない|白紙に|印を外|印が外").unwrap()
});
static NEGATED: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?ix)
    \b(?:not|never|no)\s+(?:[\w-]+\s+){0,5}?(?:approved|assigned|confirmed|issued|authorized|signed|sanctioned|adopted|replacement)\b|
    未(?:承認|確定|決定|指定|発行)|(?:承認|確定|決定|指定|発行)(?:していない|されていない|されなかった|されず|には至らず)|
    (?:承認|決定|指定)された[^。；;]{0,12}(?:まだない|なかった)").unwrap()
});
static CONDITIONAL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?ix)
    \b(?:if|unless|provided\s+that|only\s+when|in\s+(?:the\s+)?event\s+of|contingent\s+on|depending\s+on)\b|
    仮に|もし|なら|場合|ときに限|のとき|た場合|という条件|に限り").unwrap()
});
static HISTORY: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?ix)
    \b(?:prior|earlier|historical|formerly|archive[ds]?|obsolete|superseded|for\s+reference|
    example|illustrative|sample\s+(?:entry|only)|proposal|proposed|provisional|tentative|draft)\b|
    以前|当初|過去|旧版|旧案|旧記録|参考例|記入例|見本|当時|仮案|仮称|候補|提案|草案",
    )
    .unwrap()
});
static REFERENCE_ONLY: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?ix)
    \b(?:historically|archived|for\s+reference\s+only|illustrative|for\s+example)\b|
    過去の記録|参考例|記入例|見本|当時の記録",
    )
    .unwrap()
});
static TIMED_QUESTION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?ix)
    \b(?:as\s+of|at\s+the\s+time|on\s+(?:that|the\s+original)\s+date|prior\s+to|before|previously|at\s+first|
    originally|historical|hypothetical|suppos(?:e|ing)|would|in\s+the\s+event|for\s+example|
    january|february|march|april|may\s+\d|june|july|august|september|october|november|december)\b|
    \d{4}[-/年]\d|[0-9０-９]{1,2}月|時点|当時|以前|当初|前に|前の|だった頃|仮定|たとえば").unwrap()
});
static REFER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?ix)
    \b(?:it|its|this|that|the\s+(?:same|earlier|previous|above|decision|assignment|approval|proposal|designation)|
    no\s+replacement|without\s+(?:a\s+)?replacement)\b|
    同じ|この|その|当該|同件|これ|それ|先の|代わり|代替|後任|前記").unwrap()
});
static REASSIGN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?ix)
    \b(?:reinstat\w*|restor\w*|reappoint\w*|reconfirm\w*|reassign\w*|replac(?:ed|es)|supersed(?:es|ing)|
    instead|correct(?:ed|ion)|after\s+all|back\s+in\s+(?:force|effect))\b|
    再承認|再指定|再決定|復活|再開|確定し直|再び|改めて|正式に戻|変更|差し替|訂正").unwrap()
});
static QUALIFIED: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\b([a-z][a-z-]+)\s+(code|identifier|venue|site|room|address|date|deadline|location|colou?r|time|name)\b").unwrap()
});
static PROPER: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b[A-Z][A-Za-z0-9-]*\b").unwrap());
static OLD_SIDE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?ix)
    (?:\b(?:instead\s+of|formerly|previously|replaces|supersedes|formerly\s+called)\s+|(?:旧称|旧案)[：:])$").unwrap()
});

pub(super) fn historical_or_hypothetical(q: &str) -> bool {
    checked::WANTS_STATE.is_match(q) || TIMED_QUESTION.is_match(q) || CONDITIONAL.is_match(q)
}
#[cfg(any(feature = "trial-qa-lookup", feature = "trial-qa-cautious"))]
pub(super) fn condition(s: &str) -> bool {
    CONDITIONAL.is_match(s)
}
pub(super) fn closed(s: &str) -> bool {
    CLOSED.is_match(s)
}
pub(super) fn affirmed(s: &str) -> bool {
    POSITIVE.is_match(&NEGATED.replace_all(s, " "))
}
pub(super) fn revision(s: &str) -> bool {
    REASSIGN.is_match(s)
}

pub(super) fn identifies(s: &str) -> bool {
    static SELECTOR: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(
            r"(?ix)
     \b(?:identif\w*|classif\w*|belongs?\s+to|corresponds?\s+to|is\s+from|came\s+from|
     dated|marked\s+as|applies\s+to|refers?\s+to|under\s+the|assigned\s+to)\b|
     特定|判明|該当|に当たる|に相当|のもの|に属|と確認|と判定",
        )
        .unwrap()
    });
    SELECTOR.is_match(s)
}

fn fields(s: &str) -> BTreeSet<(&str, &str)> {
    QUALIFIED
        .captures_iter(s)
        .filter_map(|c| {
            let a = c.get(1)?.as_str();
            (!matches!(
                a,
                "the"
                    | "a"
                    | "an"
                    | "what"
                    | "which"
                    | "whose"
                    | "its"
                    | "that"
                    | "this"
                    | "new"
                    | "old"
                    | "final"
                    | "approved"
                    | "assigned"
                    | "proposed"
                    | "updated"
                    | "current"
                    | "official"
                    | "same"
                    | "previous"
                    | "original"
                    | "signed"
                    | "valid"
                    | "confirmed"
                    | "effective"
                    | "replacement"
            ))
            .then(|| (a, c.get(2).unwrap().as_str()))
        })
        .collect()
}

struct Row {
    scope: indexed::Scope,
    direct: usize,
    strength: usize,
    hits: usize,
    linked: bool,
    decision: bool,
    closed: bool,
    qualified: bool,
    open_axis: bool,
}

// Preserve the noun phrase the question explicitly requests, separately from its setup.
fn requested_field(q: &str) -> Option<String> {
    static EN: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?i)\b(?:what|which|whose)\s+((?:[a-z][a-z-]*\s+){0,3}[a-z][a-z-]*)\s+(?:was|were|is|are|did|does|do|has|had|will|applies|should|would)\b").unwrap()
    });
    static JA: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"([\p{Han}\p{Katakana}]{1,8}(?:の名(?:称|前)?|名|番号|符号|会場|期限|識別色))(?:を|は|が)").unwrap()
    });
    static IMPERATIVE: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?i)\b(?:name|identify|give|state)\s+(?:the\s+)?([a-z-]+(?:\s+[a-z-]+){0,2}?)\s+(?:currently|formerly|listed|recorded|used|on|in|for)\b").unwrap()
    });
    let f = EN
        .captures_iter(q)
        .last()
        .or_else(|| JA.captures_iter(q).last())
        .or_else(|| IMPERATIVE.captures(q))?;
    let f = f.get(1)?.as_str();
    (!f.split_whitespace().any(|s| {
        matches!(
            s.to_ascii_lowercase().as_str(),
            "of" | "the" | "is" | "was" | "were"
        )
    }))
    .then(|| f.to_owned())
}

fn assigned_name<'s>(s: &'s str, field: &str) -> Option<&'s str> {
    static NAME: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"^(?:\s*(?i:was|is|were|reads?|says?|[:：=]|は|が)\s*|\s+)(?:[「『“"]([^」』”"]{1,70})[」』”"]|([A-Z0-9][A-Za-z0-9-]*(?:[ ]+[A-Z0-9][A-Za-z0-9-]*){0,4})\b)"#).unwrap()
    });
    let at = s.to_ascii_lowercase().find(&field.to_ascii_lowercase())? + field.len();
    let capture = NAME.captures(&s[at..])?;
    capture
        .get(1)
        .or_else(|| capture.get(2))
        .map(|m| m.as_str())
}

pub(super) struct Corpus<'a> {
    pub rows: Vec<Segment<'a>>,
    pub field: Option<String>,
    info: Vec<Row>,
    ranked: Vec<usize>,
    names: Vec<String>,
    max_direct: usize,
}

impl<'a> Corpus<'a> {
    pub fn new(lines: Vec<Segment<'a>>, q: &str) -> Self {
        let terms: HashMap<_, _> = indexed::tokens(q)
            .into_iter()
            .enumerate()
            .map(|(i, t)| (t, i))
            .collect();
        let mut frequency = vec![0; terms.len()];
        let mut hits = Vec::with_capacity(lines.len());
        let mut scope = indexed::Scope::default();
        let mut info = Vec::with_capacity(lines.len());
        for (i, line) in lines.iter().enumerate() {
            scope.advance(line.text, i);
            let matched: Vec<_> = indexed::tokens(line.text)
                .iter()
                .filter_map(|t| terms.get(t).copied())
                .collect();
            for &t in &matched {
                frequency[t] += 1;
            }
            let closed = closed(line.text);
            let qualified = CONDITIONAL.is_match(line.text) || HISTORY.is_match(line.text);
            info.push(Row {
                scope,
                direct: 0,
                strength: 0,
                hits: matched.len(),
                linked: false,
                decision: closed
                    || POSITIVE.is_match(line.text)
                    || indexed::REVISION.is_match(line.text),
                closed,
                qualified,
                open_axis: choice::axis(line.text).is_some(),
            });
            hits.push(matched);
        }
        let weight: Vec<_> = frequency
            .iter()
            .map(|n| {
                let idf = ((lines.len() + 1) / (n + 1)).ilog2() as usize + 1;
                idf * idf
            })
            .collect();
        let direct: Vec<usize> = hits
            .iter()
            .map(|h| h.iter().map(|&t| weight[t]).sum())
            .collect();
        let mut max_direct = 0;
        for i in 0..info.len() {
            let heading = info[i]
                .scope
                .headers()
                .filter(|&s| s != i)
                .map(|s| direct[s])
                .max()
                .unwrap_or(0);
            let referent = REFER.is_match(lines[i].text);
            let short_under_header = lines[i].text.chars().count() <= 48
                && info[i].scope.headers().any(|h| h < i && i - h <= 2);
            let heading = if direct[i] > 0 || referent || short_under_header {
                heading
            } else {
                0
            };
            let referring = i > 0
                && info[i - 1].scope.document() == info[i].scope.document()
                && (referent || short_under_header);
            let previous = if referring { direct[i - 1] } else { 0 };
            let r = &mut info[i];
            r.direct = direct[i];
            r.linked = heading > 0 || previous > 0;
            r.strength = direct[i] * 4
                + if r.decision || r.qualified {
                    (heading * 2).max(previous * 3)
                } else {
                    0
                };
            max_direct = max_direct.max(direct[i]);
        }
        let mut ranked: Vec<_> = (0..info.len()).filter(|&i| info[i].strength > 0).collect();
        ranked.sort_unstable_by_key(|&i| Reverse((info[i].strength, info[i].hits, i)));
        let names = PROPER
            .find_iter(q)
            .map(|m| m.as_str().to_ascii_lowercase())
            .filter(|s| {
                !matches!(
                    s.as_str(),
                    "which"
                        | "what"
                        | "where"
                        | "when"
                        | "who"
                        | "how"
                        | "as"
                        | "at"
                        | "for"
                        | "the"
                        | "is"
                        | "did"
                        | "does"
                        | "in"
                        | "if"
                        | "on"
                        | "under"
                        | "after"
                        | "before"
                        | "please"
                        | "give"
                        | "name"
                        | "project"
                        | "team"
                        | "unit"
                        | "case"
                )
            })
            .collect();
        Self {
            field: requested_field(q),
            rows: lines,
            info,
            ranked,
            names,
            max_direct,
        }
    }

    /// Pick positive and adverse decisions before allocating space to headers and neighbors.
    pub fn select(&self, count: usize, budget: usize) -> Vec<usize> {
        if self.rows.len() <= count
            && self.rows.iter().map(|s| s.text.len()).sum::<usize>() <= budget
        {
            return (0..self.rows.len()).collect();
        }
        let top = self.ranked.first().map_or(0, |&i| self.info[i].strength);
        let relevant = |i: usize| self.info[i].strength.saturating_mul(4) >= top && top > 0;
        let mut centers = Vec::new();
        let add = |v: &mut Vec<usize>, i| {
            if !v.contains(&i) {
                v.push(i);
            }
        };
        for &i in self.ranked.iter().take(2) {
            add(&mut centers, i);
        }
        // Missing selectors and authoritative source records may use different
        // words from either branch. Reserve space before adding ordinary neighbors.
        for &i in self
            .ranked
            .iter()
            .filter(|&&i| self.info[i].open_axis)
            .take(2)
        {
            add(&mut centers, i);
        }
        let gaps: Vec<_> = self
            .info
            .iter()
            .enumerate()
            .filter_map(|(i, r)| r.open_axis.then_some(i))
            .take(3)
            .collect();
        if gaps.len() <= 2 {
            for i in gaps {
                add(&mut centers, i);
            }
        }
        for &i in self
            .ranked
            .iter()
            .filter(|&&i| Self::firsthand(self.rows[i].text))
            .take(3)
        {
            add(&mut centers, i);
        }
        for kind in 0..3 {
            let matching = |i: usize| {
                relevant(i)
                    && match kind {
                        0 => self.info[i].closed,
                        1 => self.info[i].decision && !self.info[i].closed,
                        _ => self.info[i].qualified,
                    }
            };
            for &i in self.ranked.iter().filter(|&&i| matching(i)).take(2) {
                add(&mut centers, i);
            }
            if let Some(i) = (0..self.rows.len()).rfind(|&i| matching(i)) {
                add(&mut centers, i);
            }
        }
        for &i in self.ranked.iter().take((count / 3).max(2)) {
            add(&mut centers, i);
        }
        if centers.is_empty() && !self.rows.is_empty() {
            centers.extend([0, self.rows.len() - 1]);
        }
        let neighbors = centers
            .iter()
            .flat_map(|&i| {
                [i.checked_sub(1), (i + 1 < self.rows.len()).then_some(i + 1)]
                    .into_iter()
                    .flatten()
                    .filter(move |&j| {
                        self.info[i].scope.document() == self.info[j].scope.document()
                    })
            })
            .collect::<Vec<_>>();
        let mut picked = BTreeSet::new();
        let mut bytes = 0;
        let priority = centers
            .iter()
            .copied()
            .chain(centers.iter().flat_map(|&i| self.info[i].scope.headers()))
            .chain(
                neighbors
                    .iter()
                    .copied()
                    .filter(|&i| self.info[i].decision || self.info[i].qualified),
            )
            .chain(neighbors.iter().copied())
            .chain(self.ranked.iter().copied().take_while(|&i| relevant(i)));
        for i in priority {
            let len = self.rows[i].text.len();
            if bytes + len <= budget && picked.insert(i) {
                bytes += len;
                if picked.len() == count {
                    break;
                }
            }
        }
        picked.into_iter().collect()
    }

    pub fn range(&self, i: usize) -> SourceRange {
        let l = self.rows[i];
        SourceRange {
            document_id: self.info[i].scope.document(),
            first_segment_id: l.id,
            last_segment_id: l.id,
            start_byte: l.start,
            end_byte: l.end(),
        }
    }
    #[cfg(feature = "trial-qa-preview")]
    pub fn containing(&self, span: &Span) -> Option<usize> {
        let i = self.rows.partition_point(|r| r.end() <= span.start_byte);
        self.rows
            .get(i)
            .filter(|r| r.start <= span.start_byte && span.end_byte <= r.end())
            .map(|_| i)
    }

    pub fn different(&self, q: &str, i: usize, value: &str) -> bool {
        let row = self.rows[i].text;
        if checked::field_conflict(q, row, value) || checked::subject_conflict(q, row, value) {
            return true;
        }
        let want = fields(q);
        let around = if value.is_empty() {
            row.to_owned()
        } else {
            row.replace(value, " ")
        };
        let seen = fields(&around);
        want.len() == 1 && seen.len() == 1 && {
            let (a, kind) = want.first().unwrap();
            let (b, other) = seen.first().unwrap();
            kind == other && a != b
        }
    }
    fn linked_to_question(&self, i: usize) -> bool {
        let r = &self.info[i];
        let strong = r.direct > 0 && r.direct.saturating_mul(2) >= self.max_direct && r.hits >= 2;
        let inherited = r.linked
            && (r.decision || r.qualified)
            && r.strength.saturating_mul(2) >= self.max_direct * 3;
        if !strong && !inherited {
            return false;
        }
        self.names.is_empty()
            || self.names.iter().any(|name| {
                self.rows[i].text.to_ascii_lowercase().contains(name)
                    || r.scope
                        .headers()
                        .any(|h| self.rows[h].text.to_ascii_lowercase().contains(name))
                    || (inherited
                        && i > 0
                        && self.rows[i - 1].text.to_ascii_lowercase().contains(name))
            })
    }
    #[cfg(any(feature = "trial-qa-lookup", feature = "trial-qa-cautious"))]
    pub fn conflict_in(&self, qs: &str, ids: &[usize]) -> bool {
        ids.iter().any(|&i| {
            self.linked_to_question(i)
                && !self.different(qs, i, "")
                && (self.info[i].closed || self.info[i].qualified || revision(self.rows[i].text))
        })
    }

    pub fn pending(&self, q: &str, ids: &[usize]) -> bool {
        ids.iter()
            .rev()
            .copied()
            .find(|&i| {
                self.info[i].decision
                    && self.linked_to_question(i)
                    && !self.different(q, i, "")
                    && !CONDITIONAL.is_match(self.rows[i].text)
                    && !REFERENCE_ONLY.is_match(self.rows[i].text)
            })
            .is_some_and(|i| self.info[i].closed && !affirmed(self.rows[i].text))
    }

    pub fn decisive(&self, q: &str, ids: &[usize], basis: usize, value: &str) -> usize {
        if historical_or_hypothetical(q) {
            return basis;
        }
        ids.iter()
            .rev()
            .copied()
            .find(|&j| {
                j > basis
                    && self.linked_to_question(j)
                    && !self.different(q, j, value)
                    && self.rows[j].text.contains(value)
                    && affirmed(self.rows[j].text)
                    && !CONDITIONAL.is_match(self.rows[j].text)
                    && !REFERENCE_ONLY.is_match(self.rows[j].text)
                    && !checked::tentative_mention(self.rows[j].text, value)
            })
            .unwrap_or(basis)
    }

    /// A reader may quote a selector ("the winter issue") when the question asks for
    /// a different property. Join only a unique, explicitly named property to the selector.
    pub fn selected_property(
        &self,
        q: &str,
        ids: &[usize],
        basis: usize,
        selector: &str,
    ) -> Option<(usize, Evidence)> {
        let field = self.field.as_deref()?;
        if !identifies(self.rows[basis].text)
            || self.rows[basis]
                .text
                .to_ascii_lowercase()
                .contains(&field.to_ascii_lowercase())
        {
            return None;
        }
        let terms: BTreeSet<_> = indexed::tokens(selector)
            .into_iter()
            .filter(|s| {
                s.chars().count() > 1
                    && !matches!(
                        s.as_str(),
                        "edition"
                            | "release"
                            | "issue"
                            | "version"
                            | "period"
                            | "season"
                            | "case"
                            | "branch"
                            | "office"
                            | "site"
                            | "plan"
                            | "schedule"
                            | "the"
                    )
            })
            .collect();
        if terms.is_empty() {
            return None;
        }
        let mut matching = ids.iter().filter_map(|&i| {
            let row = self.rows[i];
            let name = assigned_name(row.text, field)?;
            if name.eq_ignore_ascii_case(selector)
                || self.different(q, i, name)
                || terms.is_disjoint(&indexed::tokens(&row.text.replace(name, " ")))
            {
                return None;
            }
            Some((i, name))
        });
        let (i, name) = matching.next()?;
        if matching.next().is_some() || !self.supports(q, i, name) {
            return None;
        }
        let row = self.rows[i];
        Some((
            i,
            Evidence {
                value: name.into(),
                spans: document::locate(row.text, row.start, name).ok()?,
            },
        ))
    }

    pub fn branch_context(&self, i: usize) -> Option<String> {
        let field = self.field.as_deref()?;
        let line = self.rows[i].text;
        let value = assigned_name(line, field)?;
        Some(line.replace(value, " "))
    }

    pub fn firsthand(s: &str) -> bool {
        static WITNESS: LazyLock<Regex> = LazyLock::new(|| {
            Regex::new(r"(?ix)\b(?:now\s+(?:records?|shows?|lists?|sanctions?|requires?)|signed\s+(?:the\s+)?(?:new|final|actual)|actually\s+(?:used|recorded)|records?\s+that|recorded\s+on\s+(?:arrival|return)|in\s+force)\b|
        実物|現物|受取(?:帳|簿)|当時の(?:原本|控え)|と読める|だったとある|原本による|の写しで|と明記").unwrap()
        });
        WITNESS.is_match(s)
    }

    pub fn maps_property(&self, q: &str, i: usize) -> bool {
        let row = self.rows[i].text;
        self.field.as_ref().is_some_and(|f| {
            row.to_ascii_lowercase()
                .contains(&f.trim_end_matches("の名").to_ascii_lowercase())
        }) && !self.different(q, i, "")
    }

    /// Recover an explicit quoted value from one firsthand source only. Require
    /// multiple query terms outside the quote, and discard multiple possible bindings.
    pub fn witness(&self, q: &str, ids: &[usize]) -> Option<(usize, Evidence)> {
        static QUOTED: LazyLock<Regex> =
            LazyLock::new(|| Regex::new(r#"[「『“"]([^」』”"\r\n]{1,64})[」』”"]"#).unwrap());
        let tokens = indexed::tokens(q);
        let mut found = ids.iter().filter_map(|&i| {
            let l = self.rows[i];
            if !Self::firsthand(l.text)
                || checked::STATUS.is_match(l.text)
                || CONDITIONAL.is_match(l.text)
                || !self.maps_property(q, i)
                || (REFERENCE_ONLY.is_match(l.text) && !historical_or_hypothetical(q))
            {
                return None;
            }
            let mut quotes = QUOTED.captures_iter(l.text);
            let cap = quotes.next()?;
            if quotes.next().is_some() {
                return None;
            }
            let v = cap.get(1)?.as_str();
            if !self.supports(q, i, v)
                || indexed::tokens(&l.text.replace(v, " "))
                    .intersection(&tokens)
                    .take(2)
                    .count()
                    != 2
            {
                return None;
            }
            Some((
                i,
                Evidence {
                    value: v.into(),
                    spans: document::locate(l.text, l.start, v).ok()?,
                },
            ))
        });
        let one = found.next()?;
        found.next().is_none().then_some(one)
    }

    pub fn bound_value(&self, q: &str, i: usize, value: &str) -> bool {
        let row = self.rows[i].text;
        if self.different(q, i, value) || !self.supports(q, i, value) {
            return false;
        }
        Self::firsthand(row)
            || affirmed(row)
            || self.maps_property(q, i)
                && (checked::literal_name(row, value) || self.branch_context(i).is_some())
            || checked::literal_name(row, value)
                && (row.contains("は「") || row.contains("が「") || row.contains("に「"))
    }

    /// Veto only linked source statements. A literal name or an unrelated proposal is not a change.
    pub fn supports(&self, q: &str, i: usize, value: &str) -> bool {
        let line = self.rows[i].text;
        if self.different(q, i, value) {
            return false;
        }
        // A title or field label repeated from the question is not its value.
        let label = q.replace(['の', ' ', '　'], "").to_lowercase();
        if label.contains(&value.replace(['の', ' ', '　'], "").to_lowercase())
            && !checked::literal_name(line, value)
            && !affirmed(line)
            && !Self::firsthand(line)
        {
            return false;
        }
        if historical_or_hypothetical(q) {
            return true;
        }
        if checked::NEG_VALUE.is_match(value) && !checked::literal_name(line, value) {
            return false;
        }
        if line
            .match_indices(value)
            .all(|(at, _)| OLD_SIDE.is_match(&line[..at]))
        {
            return false;
        }
        let mut valid = line
            .split([';', '；'])
            .filter(|s| s.contains(value))
            .any(|clause| {
                let around = clause.replace(value, " ");
                !checked::tentative_mention(clause, value)
                    && (!closed(&around) || affirmed(&around))
            });
        for j in i + 1..self.rows.len() {
            let notice = self.rows[j].text;
            if !self.info[j].decision
                || !self.linked_to_question(j)
                || self.different(q, j, "")
                || CONDITIONAL.is_match(notice)
                || REFERENCE_ONLY.is_match(notice)
            {
                continue;
            }
            if self.info[j].closed && !affirmed(notice) {
                valid = false;
            } else if affirmed(notice)
                && (notice.contains(value) || (revision(notice) && REFER.is_match(notice)))
            {
                valid = true;
            } else if revision(notice) && affirmed(notice) && !notice.contains(value) {
                valid = false;
            }
        }
        valid
    }
}

pub(super) fn init_trace(e: &mut Engine<'_>, method: &'static str, total: usize) {
    e.metrics.context = Some(ContextUsage {
        method,
        source_segments: total,
        retained_segments: 0,
        selection_windows: 0,
        retrieval: Some(RetrievalUsage::default()),
    });
}
pub(super) fn record(e: &mut Engine<'_>, ranges: Vec<SourceRange>, before: usize) {
    let ctx = e.metrics.context.as_mut().unwrap();
    let retrieval = ctx.retrieval.as_mut().unwrap();
    let sent = e.metrics.calls > before;
    if sent {
        ctx.retained_segments = ranges
            .iter()
            .map(|r| r.last_segment_id - r.first_segment_id + 1)
            .sum();
    }
    retrieval.rounds.push(RetrievalRound { sent, ranges });
}
