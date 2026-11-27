//! Trial interpretation of arithmetic prose. AFM selects source IDs and semantic roles.
//! The host checks the selected evidence and builds every expression from its own ledger.
mod ledger;
mod profile;
#[cfg(test)]
mod tests;
use super::{Angle, evaluate};
use crate::{
    contract,
    engine::{Engine, Fail, Output, Run, request},
};
use ledger::{Ledger, Quantity, Unit, folded};
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::BTreeSet;

const OPERATIONS: &[&str] = &[
    "sum",
    "difference",
    "price_times_count",
    "percent_of",
    "percent_share",
    "percent_decrease_total",
    "percent_increase_total",
    "insufficient",
];
const ROLES: &[&str] = &[
    "addend",
    "start",
    "subtract",
    "unit_price",
    "count",
    "base",
    "percent",
    "part",
    "whole",
];
const INSTRUCTION: &str = "Read the arithmetic problem, including the final question. Select the source span asking for the requested final quantity, and a source span establishing its arithmetic relation. Either may be the question itself. Choose only quantities needed to obtain THAT result. The cited source IDs are references, not numbers in the problem. Do not compute. Each operand's number is its q-ID, and unit is the short unit attached to that number (empty if none). Sum uses addend(s), difference uses start minus subtract(s), price_times_count uses unit_price and count, percent_of uses base and percent, percent_share uses part divided by whole, and percent_decrease_total/increase_total use base and percent for the final amount after a change. A subtotal, an unrelated figure, a count for another population, and a per-item definition of 1 do not fill missing roles. If the asked-for final quantity cannot be determined, choose insufficient and name the specific missing quantity, denominator, or relation in the source language. Set missing to an empty string when the final result is determined.";

#[derive(Deserialize)]
struct Selection {
    requested_quantity: String,
    relation_span: String,
    operation: String,
    operands: Vec<Operand>,
    missing: String,
}
#[derive(Deserialize)]
struct Operand {
    number: String,
    role: String,
    unit: String,
}

fn id_choices(len: usize, prefix: char) -> Vec<String> {
    (0..len).map(|i| format!("{prefix}{i}")).collect()
}

fn schema(ledger: &Ledger<'_>) -> Value {
    let spans = id_choices(ledger.spans.len(), 's');
    let mut relations = spans.clone();
    relations.push("none".into());
    let mut quantities = id_choices(ledger.quantities.len(), 'q');
    quantities.push("none".into());
    let operand = contract::object([
        ("number".into(), json!({"type":"string","enum":quantities})),
        ("role".into(), json!({"type":"string","enum":ROLES})),
        (
            "unit".into(),
            json!({"type":"string","description":"Unit word or symbol of this source number. Use empty text if implicit."}),
        ),
    ]);
    contract::object([
        (
            "requested_quantity".into(),
            json!({"type":"string","enum":spans,"description":"Source span that asks for the FINAL quantity."}),
        ),
        (
            "relation_span".into(),
            json!({"type":"string","enum":relations,"description":"Span that states the operation or rate, including the question if it states the relation."}),
        ),
        (
            "operation".into(),
            json!({"type":"string","enum":OPERATIONS}),
        ),
        ("operands".into(), json!({"type":"array","items":operand})),
        (
            "missing".into(),
            json!({"type":"string","description":"When insufficient, name the specific unavailable quantity or relation. Otherwise empty."}),
        ),
    ])
}

fn index(id: &str, prefix: char) -> Option<usize> {
    id.strip_prefix(prefix)?.parse().ok()
}
fn has(s: &str, words: &[&str]) -> bool {
    words.iter().any(|word| s.contains(word))
}

const PROFIT: &[&str] = &[
    "profit",
    "net income",
    "earnings after",
    "利益",
    "儲け",
    "もうけ",
    "損益",
    "粗利",
];
const COST: &[&str] = &[
    "cost", "expense", "paid", "spent", "purchase", "原価", "経費", "費用", "仕入", "支出", "支払",
];
const REVENUE: &[&str] = &[
    "revenue",
    "sales",
    "sold",
    "received",
    "income",
    "selling",
    "売上",
    "売り上",
    "売価",
    "販売",
    "収入",
];
const SUM: &[&str] = &[
    "total",
    "sum",
    "together",
    "combined",
    "altogether",
    "in all",
    "plus",
    "add",
    "both",
    "合計",
    "総",
    "全",
    "合算",
    "合わせ",
    "あわせ",
    "足し",
    "足す",
    "計いくら",
    "計何",
];
const DIFF: &[&str] = &[
    "difference",
    "minus",
    "subtract",
    "remaining",
    "remain",
    "left",
    "change",
    "spent",
    "sold",
    "gave",
    "lost",
    "used",
    "more",
    "fewer",
    "less",
    "after",
    "差",
    "引",
    "残",
    "減",
    "使",
    "余",
    "不足",
    "多い",
    "少ない",
    "おつり",
    "お釣り",
];
const PRICE: &[&str] = &[
    "unit price",
    "each",
    "per ",
    "apiece",
    "a piece",
    "/",
    "単価",
    "あたり",
    "当たり",
    "につき",
    "ずつ",
];
const PERCENT_ASK: &[&str] = &[
    "what percent",
    "which percent",
    "percentage",
    "as a percent",
    "what share",
    "what proportion",
    "何%",
    "割合",
    "比率",
    "百分率",
    "パーセントですか",
    "何パーセント",
    "達成率",
    "成功率",
    "利用率",
    "利益率",
    "増加率",
    "減少率",
    "正答率",
    "出席率",
    "稼働率",
];
const DEDUCT: &[&str] = &[
    "discount", "reduc", "deduct", "decreas", "off", "割引", "値引", "引き", "減額", "下げ", "減少",
];
const INCREASE: &[&str] = &[
    "increas",
    "markup",
    "mark-up",
    "raised",
    "tax",
    "surcharge",
    "added",
    "増",
    "上乗せ",
    "税込",
    "税を",
    "値上",
    "加算",
];
const UNKNOWN: &[&str] = &[
    "unknown",
    "not given",
    "not provided",
    "not stated",
    "not known",
    "not specified",
    "unspecified",
    "not reported",
    "not recorded",
    "unrecorded",
    "missing",
    "unavailable",
    "不明",
    "未定",
    "未記",
    "未提",
    "未確",
    "未公",
    "記載なし",
    "記載がな",
    "載っていな",
    "わから",
    "分から",
    "書かれていな",
    "示されていな",
];

fn missing_text(value: &str) -> Option<&str> {
    let text = value.trim();
    let key = folded(text);
    if matches!(
        key.trim_matches(['.', '。']),
        "" | "none"
            | "n/a"
            | "na"
            | "null"
            | "nothing"
            | "no"
            | "なし"
            | "無し"
            | "該当なし"
            | "ありません"
            | "ない"
            | "不要"
    ) || key.starts_with("no missing")
        || key.starts_with("nothing is missing")
    {
        None
    } else {
        Some(text)
    }
}

fn missing(ledger: &Ledger<'_>, selection: &Selection, obligation: &str) -> Fail {
    let goal = ledger.spans[ledger.requested].text;
    let quoted = ledger
        .spans
        .iter()
        .find(|s| has(&folded(s.text), UNKNOWN))
        .map(|s| s.text);
    let reason = missing_text(&selection.missing).or(quoted);
    let detail = reason.map_or(String::new(), |s| format!(" {s}"));
    Fail::Held(format!("{obligation} Requested: «{goal}».{detail}"))
}

fn required_for(ask: &str) -> &'static str {
    if has(ask, PROFIT) {
        "missing_profit_component: profit requires both sales revenue and costs (売上と原価・経費)."
    } else if has(ask, PERCENT_ASK) {
        "missing_part_or_denominator: the requested percentage needs the matching part and whole (対象の数と母数)."
    } else if has(
        ask,
        &[
            "average",
            "mean",
            "平均",
            "per ",
            "単価",
            "あたり",
            "当たり",
            "率",
            "ratio",
        ],
    ) {
        "missing_denominator_or_relation: the requested rate needs its matching total and divisor (対応する合計と分母)."
    } else if has(
        ask,
        &[
            "price", "cost", "paid", "spend", "売上", "金額", "支払", "代金", "料金", "総額", "円",
        ],
    ) {
        "missing_amount_or_relation: the requested monetary amount needs its applicable prices, quantities and stated adjustments."
    } else {
        "missing_relation: the given quantities do not establish how to obtain the requested final quantity."
    }
}

struct Fact<'a> {
    id: usize,
    q: &'a Quantity<'a>,
    role: &'a str,
    unit: Option<Unit>,
    display: String,
}

fn checked_unit(q: &Quantity<'_>, claimed: &str) -> Result<(Option<Unit>, String), &'static str> {
    let claimed = claimed.trim();
    let claim = missing_text(claimed).and_then(Unit::parse);
    if let Some((bound, name)) = &q.unit {
        // A model may spell out a symbol, or include the denominator of a unit price.
        let numerator = claimed.split(['/', '／']).next().and_then(Unit::parse);
        if claim.is_some() && claim.as_ref() != Some(bound) && numerator.as_ref() != Some(bound) {
            return Err(
                "operand_unit: selected unit does not match the unit attached to the source number.",
            );
        }
        return Ok((Some(bound.clone()), name.clone()));
    }
    if let Some(unit) = claim {
        if !folded(q.neighborhood).contains(&folded(claimed)) {
            return Err("operand_unit: the claimed unit is absent from the cited number's source.");
        }
        return Ok((Some(unit), claimed.to_owned()));
    }
    Ok((None, String::new()))
}

fn facts<'a>(ledger: &'a Ledger<'a>, selection: &'a Selection) -> Run<Vec<Fact<'a>>> {
    let mut seen = BTreeSet::new();
    let mut out = Vec::new();
    for operand in &selection.operands {
        let Some((id, q)) =
            index(&operand.number, 'q').and_then(|i| ledger.quantities.get(i).map(|q| (i, q)))
        else {
            return Err(missing(
                ledger,
                selection,
                &format!(
                    "missing_{}: no source number fills this arithmetic role.",
                    operand.role
                ),
            ));
        };
        if !seen.insert(id) {
            return Err(missing(
                ledger,
                selection,
                "independent_operands: the same source occurrence cannot fill two roles.",
            ));
        }
        let (unit, display) =
            checked_unit(q, &operand.unit).map_err(|e| missing(ledger, selection, e))?;
        let near = folded(q.neighborhood);
        if has(
            &near,
            &[
                "for reference only",
                "unrelated",
                "not included",
                "excluded",
                "参考値",
                "対象外",
                "別件",
                "無関係",
            ],
        ) {
            return Err(missing(
                ledger,
                selection,
                "relevant_operand: a selected amount belongs to an excluded or unrelated quantity.",
            ));
        }
        out.push(Fact {
            id,
            q,
            role: &operand.role,
            unit,
            display,
        });
    }
    Ok(out)
}

fn price_basis(ledger: &Ledger<'_>, price: &Fact<'_>, count: &Fact<'_>) -> bool {
    if let Some(basis) = profile::price_definition(ledger, price.id) {
        let basis = &ledger.quantities[basis];
        return count.q.range != basis.range
            && count.unit.as_ref() == basis.unit.as_ref().map(|(unit, _)| unit);
    }
    let line = folded(ledger.spans[price.q.span].text);
    let near = folded(price.q.neighborhood);
    if has(
        &near,
        &["subtotal", "小計", "total price", "total cost", "合計額"],
    ) && !has(&near, PRICE)
    {
        return false;
    }
    let same_line: Vec<_> = ledger
        .quantities
        .iter()
        .filter(|q| q.span == price.q.span)
        .collect();
    let one_basis = same_line.iter().find(|q| {
        q.literal == "1"
            && matches!(q.unit, Some((Unit::Count(_) | Unit::Measure(_), _)))
            && (has(&line, PRICE) || has(&line, &["costs ", "cost ", "で", "は", "が"]))
    });
    if let Some(one) = one_basis {
        if one.range == count.q.range || one.unit.as_ref().map(|(u, _)| u) != count.unit.as_ref() {
            return false;
        }
        return true;
    }
    has(&near, PRICE)
        || (same_line
            .iter()
            .filter(|q| matches!(q.unit, Some((Unit::Money(_), _))))
            .count()
            == 1
            && has(&line, PRICE))
}

fn calculate(ledger: &Ledger<'_>, s: &Selection, angle: Angle) -> Run<Output> {
    let ask_span = &ledger.spans[ledger.requested];
    let ask = folded(ask_span.text);
    let fail = |reason: &str| missing(ledger, s, reason);
    if s.operation == "insufficient" {
        return Err(fail(required_for(&ask)));
    }
    if index(&s.requested_quantity, 's') != Some(ledger.requested) {
        return Err(fail(
            "requested_quantity: the selected statement does not identify the final question.",
        ));
    }
    let relation = index(&s.relation_span, 's')
        .and_then(|i| ledger.spans.get(i))
        .ok_or_else(|| fail(required_for(&ask)))?;
    let evidence = folded(relation.text);
    let facts = facts(ledger, s)?;
    let one = |role: &str, obligation: &str| -> Run<&Fact<'_>> {
        let mut found = facts.iter().filter(|f| f.role == role);
        let f = found.next().ok_or_else(|| fail(obligation))?;
        if found.next().is_some() {
            return Err(fail(obligation));
        }
        Ok(f)
    };
    let has_profit = has(&ask, PROFIT);
    let wants_percent = has(&ask, PERCENT_ASK);
    if has_profit && !wants_percent && s.operation != "difference" {
        return Err(fail(required_for(&ask)));
    }
    if wants_percent && s.operation != "percent_share" {
        return Err(fail(
            "missing_percentage_relation: a part and its matching denominator are needed to report a percentage of the whole.",
        ));
    }
    if has(
        &ask,
        &[
            "average",
            " mean ",
            "平均",
            "単価は",
            "単価を",
            "unit price",
            "per unit",
        ],
    ) && s.operation != "percent_share"
    {
        return Err(fail(
            "per_unit_denominator: a total alone does not establish the requested average or amount per unit.",
        ));
    }
    let (expression, result_unit, display) = match s.operation.as_str() {
        "balance" => {
            let start = one(
                "start",
                "missing_start: the opening quantity for the requested place is required.",
            )?;
            let mut expr = start.q.literal.clone();
            for fact in facts.iter().filter(|f| f.id != start.id) {
                if fact.unit != start.unit {
                    return Err(fail(
                        "matching_stock_unit: the opening, outgoing and incoming amounts must have the same unit.",
                    ));
                }
                expr.push(match fact.role {
                    "add"=>'+', "subtract"=>'-', _=>return Err(fail("missing_stock_relation: identify whether this movement adds or removes a quantity.")),
                });
                expr.push_str(&fact.q.literal);
            }
            (expr, start.unit.clone(), start.display.clone())
        }
        "sum" | "difference" => {
            let sum = s.operation == "sum";
            let first = if sum {
                facts.first().ok_or_else(|| {
                    fail("missing_addend: at least two source quantities are needed for the total.")
                })?
            } else {
                one(
                    "start",
                    "missing_start: the original or comparison amount is required before subtraction.",
                )?
            };
            let rest: Vec<_> = facts.iter().filter(|f| f.id != first.id).collect();
            let expected_role = if sum { "addend" } else { "subtract" };
            if rest.is_empty()
                || (sum && first.role != "addend")
                || rest.iter().any(|f| f.role != expected_role)
            {
                return Err(fail(if sum {
                    "missing_addend: the total requires another contributing amount."
                } else {
                    "missing_subtrahend: identify the amount to subtract from the starting amount."
                }));
            }
            if rest.iter().any(|f| f.unit != first.unit) {
                return Err(fail(
                    "matching_operand_units: addition and subtraction require the same units for each operand.",
                ));
            }
            if has_profit
                && (!has(&folded(first.q.neighborhood), REVENUE)
                    || !rest.iter().all(|f| has(&folded(f.q.neighborhood), COST)))
            {
                return Err(fail(required_for(&ask)));
            }
            if !(if sum {
                has(&evidence, SUM)
            } else {
                has(&evidence, DIFF) || has(&evidence, PROFIT)
            }) {
                return Err(fail(
                    "missing_additive_relation: the cited wording must state which amounts combine or differ for the requested result.",
                ));
            }
            let mut expr = first.q.literal.clone();
            for f in rest {
                expr.push(if sum { '+' } else { '-' });
                expr.push_str(&f.q.literal);
            }
            (expr, first.unit.clone(), first.display.clone())
        }
        "price_times_count" => {
            let p = one(
                "unit_price",
                "missing_unit_price: a price per applicable item or unit is required.",
            )?;
            let n = one(
                "count",
                "missing_count: specify how many of the priced items or units are included.",
            )?;
            if facts.len() != 2
                || !matches!(p.unit, Some(Unit::Money(_)))
                || !matches!(n.unit, Some(Unit::Count(_) | Unit::Measure(_)))
            {
                return Err(fail(
                    "price_and_quantity: multiplication needs a monetary unit price and its corresponding quantity.",
                ));
            }
            if !price_basis(ledger, p, n) {
                return Err(fail(
                    "missing_unit_price_relation: the selected price must apply per unit of the selected quantity; a subtotal cannot supply this rate.",
                ));
            }
            (
                format!("{}*{}", p.q.literal, n.q.literal),
                p.unit.clone(),
                p.display.clone(),
            )
        }
        "percent_share" => {
            let part = one(
                "part",
                "missing_numerator: identify the part whose percentage is requested.",
            )?;
            let whole = one(
                "whole",
                "missing_denominator: identify the total population or reference amount (母数・全体数).",
            )?;
            if facts.len() != 2
                || part.unit != whole.unit
                || matches!(whole.unit, Some(Unit::Percent))
            {
                return Err(fail(
                    "matching_denominator: the numerator and its reference whole must measure the same quantity in the same unit.",
                ));
            }
            if !has(&evidence, PERCENT_ASK)
                && !has(
                    &evidence,
                    &[
                        " out of ",
                        " of ",
                        "のうち",
                        "の内",
                        "全体",
                        "全数",
                        "に対",
                        "占め",
                    ],
                )
            {
                return Err(fail(
                    "missing_part_to_whole_relation: the cited wording does not establish a percentage of a reference whole.",
                ));
            }
            if super::Ratio::decimal(whole.q.literal.trim_start_matches(['+', '-']))
                .is_ok_and(|r| r.num == 0)
            {
                return Err(fail(
                    "zero_denominator: the reference whole is zero, so its percentage is undefined.",
                ));
            }
            (
                format!("{} / {} * 100", part.q.literal, whole.q.literal),
                Some(Unit::Percent),
                "%".into(),
            )
        }
        "percent_of" | "percent_decrease_total" | "percent_increase_total" => {
            let base = one(
                "base",
                "missing_base: the percentage needs the amount to which the rate applies (基準量).",
            )?;
            let rate = one(
                "percent",
                "missing_percentage_rate: the applicable percentage rate is required.",
            )?;
            if facts.len() != 2
                || rate.unit != Some(Unit::Percent)
                || base.unit == Some(Unit::Percent)
            {
                return Err(fail(
                    "percentage_rate_and_base: use a source percentage and its matching base amount.",
                ));
            }
            let modifier = match s.operation.as_str() {
                "percent_decrease_total" if has(&evidence, DEDUCT) || has(&ask, DEDUCT) => {
                    Some('-')
                }
                "percent_increase_total" if has(&evidence, INCREASE) || has(&ask, INCREASE) => {
                    Some('+')
                }
                "percent_of" => None,
                _ => {
                    return Err(fail(
                        "missing_percentage_adjustment: the final amount needs a stated increase or decrease.",
                    ));
                }
            };
            if !has(
                &evidence,
                &["%", "percent", "パーセント", "割合", "割引", "値引", "税込"],
            ) && !(has(&ask, DEDUCT) || has(&ask, INCREASE))
            {
                return Err(fail(
                    "missing_percentage_relation: the source must connect the rate to the requested base amount.",
                ));
            }
            let expr = match modifier {
                Some(op) => format!("{} * (1 {op} {} / 100)", base.q.literal, rate.q.literal),
                None => format!("{} * {} / 100", base.q.literal, rate.q.literal),
            };
            (expr, base.unit.clone(), base.display.clone())
        }
        _ => return Err(fail(required_for(&ask))),
    };
    let money_question = has(
        &ask,
        &[
            "金額",
            "代金",
            "費用",
            "料金",
            "総額",
            "利益",
            "何円",
            "何ドル",
            "total cost",
            "total price",
            "how much money",
            "dollars",
            "revenue",
            "profit",
        ],
    );
    let count_question = has(
        &ask,
        &[
            "how many", "人数", "個数", "何個", "何人", "何枚", "何本", "何冊", "何台",
        ],
    );
    if (!wants_percent && money_question && !matches!(result_unit, Some(Unit::Money(_))))
        || (count_question && !money_question && !matches!(result_unit, Some(Unit::Count(_))))
    {
        return Err(fail(
            "requested_dimension: the available calculation produces a different kind of quantity from the final question.",
        ));
    }
    // A specifically reported missing component rules out returning even a valid subtotal.
    if missing_text(&s.missing).is_some_and(|m| {
        let key = folded(m);
        ledger.spans.iter().any(|span| {
            let t = folded(span.text);
            has(&t, UNKNOWN) && t.contains(&key)
        })
    }) {
        return Err(fail(required_for(&ask)));
    }
    let mut value =
        evaluate(&expression, angle).map_err(|e| Fail::Held(format!("arithmetic_domain: {e}")))?;
    value["unit"] = json!(display);
    value["requested_quantity"] = json!(ask_span.text);
    value["operation"] = json!(s.operation);
    value["relation_evidence"] = json!(relation.text);
    value["operands"] = json!(facts.iter().map(|f|json!({
        "role":f.role,"source":f.q.text,"value":f.q.literal,"unit":f.display,
        "start_byte":f.q.range.start,"end_byte":f.q.range.end,"evidence":ledger.spans[f.q.span].text
    })).collect::<Vec<_>>());
    Ok(Output {
        status: "review",
        value,
        notes: vec![
            "Review the cited source quantities and their relationship to the requested result."
                .into(),
        ],
    })
}

pub(super) fn run(engine: &mut Engine<'_>, source: &str, angle: Angle) -> Run<Output> {
    let ledger = Ledger::new(source).map_err(|e| Fail::Invalid(e.into()))?;
    if let Some(plan) = profile::Plan::detect(&ledger) {
        let selection = plan.read(engine, &ledger)?;
        return calculate(&ledger, &selection, angle);
    }
    let req = request(INSTRUCTION, ledger.payload(), Some(schema(&ledger)));
    let selection = engine.structured(req, |v| {
        serde_json::from_value(v.clone()).map_err(|e| format!("calculation extraction: {e}"))
    })?;
    calculate(&ledger, &selection, angle)
}
