//! For recognizable questions ask AFM for the operands of that relation directly.
//! A price definition, an out-of-scope observation, or a quantity in the wrong
//! dimension is never offered as a substitute for a missing role.
use super::*;
use regex::Regex;
use std::sync::LazyLock;

const FOREIGN: &[&str] = &[
    "adjoining",
    "adjacent",
    "neighboring",
    "neighbouring",
    "next door",
    "other room",
    "another ",
    "different ",
    "別の",
    "別会場",
    "隣",
    "他の",
    "ほかの",
    "よその",
    "別件",
    "対象外",
    "参考値",
    "無関係",
    "unrelated",
    "for reference only",
];
const IN: &[&str] = &[
    "returned",
    "received",
    "restocked",
    "replenish",
    "added",
    "arrived",
    "gained",
    "brought back",
    "inflow",
    "返却",
    "戻",
    "入荷",
    "補充",
    "追加",
    "入庫",
    "搬入",
    "受け取",
    "受け入",
    "納品",
    "届",
    "入場",
    "入館",
];
const OUT: &[&str] = &[
    "lent",
    "loaned",
    "borrowed",
    "shipped",
    "sold",
    "dispatched",
    "removed",
    "gave away",
    "used",
    "lost",
    "withdrew",
    "departed",
    "outflow",
    "貸し出",
    "貸出",
    "出荷",
    "発送",
    "出庫",
    "搬出",
    "売れ",
    "売却",
    "販売",
    "消費",
    "使っ",
    "使用",
    "持ち出",
    "退場",
    "退館",
];
const OPEN: &[&str] = &[
    "initial",
    "at first",
    "before",
    "starting",
    "original",
    "began",
    "opening",
    "当初",
    "最初",
    "初め",
    "はじめ",
    "開始",
    "前の",
    "当初",
    "もともと",
    "元は",
];
const WHOLE: &[&str] = &[
    "out of",
    "total",
    "overall",
    "in all",
    "there are",
    "there were",
    "registered",
    "enrolled",
    "entire",
    "altogether",
    "全体",
    "総数",
    "全員",
    "登録",
    "全数",
    "母数",
    "全",
    "定員",
    "対象者",
];
static WORD: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[a-z]+").unwrap());
static OF_GROUP: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)(?:percent(?:age)?|proportion|share)\s+of\s+(?:the\s+)?(.+?)\s+(?:are|were|is|was|have|has|did|do|does|who|that)\s+(.+?)[?.!]*$").unwrap()
});

fn scope_match(line: &str, goal: &str) -> bool {
    !FOREIGN
        .iter()
        .any(|w| line.contains(w) && !goal.contains(w))
}
fn words(text: &str) -> Vec<String> {
    WORD.find_iter(text)
        .map(|m| m.as_str())
        .filter(|w| {
            w.len() > 2
                && ![
                    "the",
                    "what",
                    "which",
                    "how",
                    "all",
                    "much",
                    "many",
                    "did",
                    "was",
                    "were",
                    "does",
                    "are",
                    "has",
                    "had",
                    "have",
                    "will",
                    "this",
                    "that",
                    "those",
                    "their",
                    "there",
                    "altogether",
                    "together",
                    "combined",
                    "both",
                    "two",
                    "three",
                    "four",
                    "one",
                    "total",
                    "sum",
                    "and",
                    "for",
                    "with",
                    "from",
                    "money",
                    "price",
                    "cost",
                    "amount",
                    "actually",
                    "please",
                    "calculate",
                    "find",
                    "determine",
                    "dollars",
                    "euros",
                    "percentage",
                    "percent",
                    "share",
                    "overall",
                ]
                .contains(w)
        })
        .map(|w| w.trim_end_matches('s').to_owned())
        .collect()
}
fn topical(line: &str, terms: &[String]) -> bool {
    terms.iter().any(|s| line.contains(s))
}
fn quantity_ids(l: &Ledger<'_>, filter: impl Fn(&Quantity<'_>) -> bool) -> Vec<usize> {
    l.quantities
        .iter()
        .enumerate()
        .filter_map(|(i, q)| filter(q).then_some(i))
        .collect()
}
fn currency(q: &Quantity<'_>) -> bool {
    matches!(q.unit, Some((Unit::Money(_), _)))
}
fn countable(q: &Quantity<'_>) -> bool {
    matches!(q.unit, Some((Unit::Count(_) | Unit::Measure(_), _)))
}

pub(super) fn price_definition(l: &Ledger<'_>, price: usize) -> Option<usize> {
    let p = &l.quantities[price];
    let line = folded(l.spans[p.span].text);
    let earlier = price.checked_sub(1)?;
    let n = &l.quantities[earlier];
    // A succinct number+counter followed by a price (e.g. “1 box 4 euros”)
    // states the basis of the price even without a separate “each” word.
    if n.span == p.span && n.literal == "1" && countable(n) {
        let gap = p.range.start.saturating_sub(n.range.end);
        if gap <= 18 || has(&line, PRICE) {
            return Some(earlier);
        }
    }
    None
}
fn money_goal(ask: &str) -> bool {
    has(
        ask,
        &[
            "how much money",
            "cost",
            "price",
            "paid",
            "spend",
            "spen",
            "dollar",
            "euro",
            "代金",
            "料金",
            "金額",
            "合計額",
            "総額",
            "何円",
            "費用",
            "売上",
            "支払",
        ],
    )
}

struct Slot {
    key: &'static str,
    role: &'static str,
    candidates: Vec<usize>,
    minimum: usize,
    many: bool,
    absent: String,
}
impl Slot {
    fn one(
        key: &'static str,
        role: &'static str,
        candidates: Vec<usize>,
        absent: impl Into<String>,
    ) -> Self {
        Self {
            key,
            role,
            candidates,
            minimum: 1,
            many: false,
            absent: absent.into(),
        }
    }
}
pub(super) struct Plan {
    op: &'static str,
    relation: &'static str,
    slots: Vec<Slot>,
}
impl Plan {
    pub fn detect(l: &Ledger<'_>) -> Option<Self> {
        let ask = folded(l.spans[l.requested].text);
        let src = l
            .spans
            .iter()
            .map(|s| folded(s.text))
            .collect::<Vec<_>>()
            .join(" ");
        if has(&ask, PERCENT_ASK) {
            return Some(Self::share(l, &ask));
        }
        if has(&ask, PROFIT)
            || has(
                &ask,
                &[
                    "平均",
                    "average",
                    "単価は",
                    "単価を",
                    "per unit",
                    "unit price",
                ],
            )
        {
            return None;
        }
        if l.quantities
            .iter()
            .any(|q| matches!(q.unit, Some((Unit::Percent, _))))
            || has(&ask, &["割引", "税込", "discount", "percent", "率"])
        {
            return None;
        }
        if money_goal(&ask)
            && (has(&src, PRICE)
                || l.quantities
                    .iter()
                    .enumerate()
                    .any(|(i, q)| currency(q) && price_definition(l, i).is_some()))
        {
            return Some(Self::price(l, &ask));
        }
        if !money_goal(&ask)
            && (has(&src, IN) || has(&src, OUT))
            && (has(
                &ask,
                &[
                    "残",
                    "今",
                    "在庫",
                    "現在",
                    "いま",
                    "あと",
                    "remaining",
                    "left",
                    "now",
                    "after",
                    "end",
                    "何冊ある",
                    "何台ある",
                    "何個ある",
                ],
            ) || has(&src, OPEN))
        {
            return Some(Self::balance(l, &ask, &src));
        }
        if money_goal(&ask) && has(&ask, SUM) {
            return Some(Self::sum_money(l, &ask));
        }
        None
    }
    fn share(l: &Ledger<'_>, ask: &str) -> Self {
        let in_scope = quantity_ids(l, |q| {
            q.span != l.requested
                && scope_match(&folded(q.neighborhood), ask)
                && !matches!(q.unit, Some((Unit::Percent, _)))
        });
        let cap = OF_GROUP.captures(ask);
        let group = cap.as_ref().and_then(|v| v.get(1)).map(|m| m.as_str());
        let property = cap
            .as_ref()
            .and_then(|v| v.get(2))
            .map_or(vec![], |m| words(m.as_str()));
        let marked_part: Vec<_> = in_scope
            .iter()
            .copied()
            .filter(|i| topical(&folded(l.quantities[*i].neighborhood), &property))
            .collect();
        let explicit_whole: Vec<_> = in_scope
            .iter()
            .copied()
            .filter(|i| {
                has(&folded(l.quantities[*i].neighborhood), WHOLE) && !marked_part.contains(i)
            })
            .collect();
        let part = if !marked_part.is_empty() {
            marked_part.clone()
        } else if !explicit_whole.is_empty() {
            in_scope
                .iter()
                .copied()
                .filter(|i| !explicit_whole.contains(i))
                .collect()
        } else {
            in_scope.clone()
        };
        let whole = if !explicit_whole.is_empty() {
            explicit_whole
        } else {
            in_scope
                .into_iter()
                .filter(|i| !marked_part.contains(i))
                .collect()
        };
        let denominator=group.map_or_else(||"the total number or reference amount for the population asked about (対象集団の総数・母数)".into(),
            |g|format!("the total number of {g}"));
        Self {
            op: "percent_share",
            relation: "the part as a percentage of its matching whole",
            slots: vec![
                Slot::one(
                    "part",
                    "part",
                    part,
                    "missing_numerator: the number possessing the requested property is missing.",
                ),
                Slot::one(
                    "whole",
                    "whole",
                    whole,
                    format!("missing_denominator: {denominator} is required."),
                ),
            ],
        }
    }
    fn price(l: &Ledger<'_>, ask: &str) -> Self {
        let rates: Vec<_> = l
            .quantities
            .iter()
            .enumerate()
            .filter(|(i, q)| {
                currency(q)
                    && scope_match(&folded(q.neighborhood), ask)
                    && (price_definition(l, *i).is_some() || has(&folded(q.neighborhood), PRICE))
            })
            .map(|(i, _)| i)
            .collect();
        let bases: Vec<_> = rates
            .iter()
            .filter_map(|i| price_definition(l, *i))
            .collect();
        let quantities: Vec<_> = l
            .quantities
            .iter()
            .enumerate()
            .filter(|(i, q)| {
                countable(q)
                    && !bases.contains(i)
                    && scope_match(&folded(q.neighborhood), ask)
                    && (bases.is_empty()
                        || bases.iter().any(|j| {
                            l.quantities[*j].unit.as_ref().map(|(u, _)| u)
                                == q.unit.as_ref().map(|(u, _)| u)
                        }))
            })
            .map(|(i, _)| i)
            .collect();
        let kind = bases
            .first()
            .and_then(|i| l.quantities[*i].unit.as_ref())
            .map_or("", |(_, s)| s);
        Self {
            op: "price_times_count",
            relation: "the unit price multiplied by the actual ordered or used quantity",
            slots: vec![
                Slot::one(
                    "unit_price",
                    "unit_price",
                    rates,
                    "missing_unit_price: an applicable price for one unit of the requested item (商品の単価) is required.",
                ),
                Slot::one(
                    "purchased_quantity",
                    "count",
                    quantities,
                    format!(
                        "missing_count: the quantity of the priced item actually ordered or used is required (対象商品の注文個数・購入数量, 単位「{kind}」)."
                    ),
                ),
            ],
        }
    }
    fn balance(l: &Ledger<'_>, ask: &str, src: &str) -> Self {
        let material = |q: &Quantity<'_>| {
            q.span != l.requested && countable(q) && scope_match(&folded(q.neighborhood), ask)
        };
        let mut opening = quantity_ids(l, |q| material(q) && has(&folded(q.neighborhood), OPEN));
        let mut added = vec![];
        let mut removed = vec![];
        for (i, q) in l.quantities.iter().enumerate().filter(|(_, q)| material(q)) {
            if opening.contains(&i) {
                continue;
            }
            let ctx = folded(q.neighborhood);
            if has(&ctx, IN) {
                added.push(i);
            } else if has(&ctx, OUT) {
                removed.push(i);
            } else if opening.is_empty() {
                opening.push(i);
            }
        }
        let mut slots = vec![Slot::one(
            "opening_quantity",
            "start",
            opening,
            "missing_start: the opening quantity for the requested stock or population (対象の開始時点の数量) is required.",
        )];
        for (needed, key, role, ids, field) in [
            (
                has(src, OUT),
                "quantities_removed",
                "subtract",
                removed,
                "missing_outflow: the quantity removed, loaned or shipped from the requested place (対象の場所から貸し出した数・出庫数) is required.",
            ),
            (
                has(src, IN),
                "quantities_added",
                "add",
                added,
                "missing_inflow: the quantity added or returned to the requested place (対象の場所へ戻した数・入庫数) is required.",
            ),
        ] {
            if needed {
                slots.push(Slot {
                    key,
                    role,
                    candidates: ids,
                    minimum: 1,
                    many: true,
                    absent: field.into(),
                });
            }
        }
        Self {
            op: "balance",
            relation: "opening stock minus all removals plus all returns or additions for this subject",
            slots,
        }
    }
    fn sum_money(l: &Ledger<'_>, ask: &str) -> Self {
        let topic = words(ask);
        let relevant =
            |q: &Quantity<'_>| q.span != l.requested && scope_match(&folded(q.neighborhood), ask);
        let all = quantity_ids(l, |q| relevant(q) && currency(q));
        let marked: Vec<_> = all
            .iter()
            .copied()
            .filter(|i| topical(&folded(l.quantities[*i].neighborhood), &topic))
            .collect();
        let money = if marked.is_empty() { all } else { marked };
        let miss = l.quantities.iter().find(|q| {
            relevant(q)
                && !currency(q)
                && topical(&folded(q.neighborhood), &topic)
                && q.unit
                    .as_ref()
                    .is_some_and(|(u, _)| matches!(u, Unit::Measure(_)))
        });
        let absent=miss.map_or_else(||"missing_addend: a contributing monetary amount for one of the named subjects is missing.".into(),
            |q|format!("missing_amount: the monetary amount for the subject of «{}» is missing; {} is a measurement, not its amount of money.",l.spans[q.span].text,q.text));
        Self {
            op: "sum",
            relation: "sum the separate money amounts belonging to the named subjects",
            slots: vec![Slot {
                key: "money_amounts",
                role: "addend",
                candidates: money,
                minimum: 2,
                many: true,
                absent,
            }],
        }
    }
    pub fn read(&self, engine: &mut Engine<'_>, l: &Ledger<'_>) -> Run<Selection> {
        let spans = id_choices(l.spans.len(), 's');
        let mut fields = vec![
            (
                "requested_quantity".into(),
                json!({"type":"string","enum":spans,"description":"Which source span asks the final question?"}),
            ),
            (
                "relation_span".into(),
                json!({"type":"string","enum":spans,"description":"Source span stating the relation, the motion, the rate, or the final question."}),
            ),
        ];
        for slot in &self.slots {
            let mut ids: Vec<_> = slot.candidates.iter().map(|i| format!("q{i}")).collect();
            ids.push("missing".into());
            let choice = json!({"type":"string","enum":ids});
            fields.push((
                slot.key.into(),
                if slot.many {
                    json!({"type":"array","items":choice})
                } else {
                    choice
                },
            ));
        }
        let prompt = format!(
            "Read the source. Identify the final question and cite the statement showing its arithmetic relationship. This question asks for {}. For each named field choose its source number ID, with the same item, location and unit as the final question. For multiple quantities include each relevant ID once. Select missing only when there is no applicable quantity. Do not calculate.",
            self.relation
        );
        let evidence = engine.structured(
            request(&prompt, l.payload(), Some(contract::object(fields))),
            |v| Ok(v.clone()),
        )?;
        let mut selection = Selection {
            requested_quantity: evidence["requested_quantity"].as_str().unwrap().into(),
            relation_span: evidence["relation_span"].as_str().unwrap().into(),
            operation: self.op.into(),
            operands: vec![],
            missing: String::new(),
        };
        if let Some(slot) = self
            .slots
            .iter()
            .find(|slot| slot.candidates.len() < slot.minimum)
        {
            return Err(missing(l, &selection, &slot.absent));
        }
        for slot in &self.slots {
            let value = &evidence[slot.key];
            let mut ids: Vec<_> = if let Some(arr) = value.as_array() {
                arr.iter().filter_map(|v| index(v.as_str()?, 'q')).collect()
            } else {
                index(value.as_str().unwrap(), 'q').into_iter().collect()
            };
            ids.sort_unstable();
            ids.dedup();
            if ids.len() < slot.minimum {
                return Err(missing(l, &selection, &slot.absent));
            }
            for id in ids {
                let q = &l.quantities[id];
                selection.operands.push(Operand {
                    number: format!("q{id}"),
                    role: slot.role.into(),
                    unit: q.unit.as_ref().map_or(String::new(), |(_, s)| s.clone()),
                });
            }
        }
        Ok(selection)
    }
}
