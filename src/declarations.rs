//! Translate source declarations independently, then assemble a checked bounded linear model.
use crate::{
    contract, document,
    engine::{Engine, Fail, Output, Run, request},
    solve::{Constraint, Objective, Problem, Relation, Sense, Variable, source_numbers},
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::BTreeSet;

struct Declarations {
    bounds: Vec<String>,
    variables: Vec<Variable>,
    constraints: Vec<String>,
    objectives: Vec<String>,
}

#[derive(Deserialize)]
struct NamedDomain {
    name: String,
    min: i64,
    max: i64,
    evidence: String,
}
#[derive(Deserialize)]
struct Inventory {
    variables: Vec<NamedDomain>,
    constraints: Vec<String>,
    objectives: Vec<String>,
}
fn string() -> Value {
    json!({"type":"string"})
}
fn integer() -> Value {
    json!({"type":"integer"})
}
fn list(items: Value) -> Value {
    json!({"type":"array","items":items})
}
fn object(fields: impl IntoIterator<Item = (&'static str, Value)>) -> Value {
    contract::object(fields.into_iter().map(|(k, v)| (k.into(), v)))
}

fn declarations(engine: &mut Engine<'_>, src: &str) -> Run<Option<Declarations>> {
    let schema = object([
        (
            "variables",
            list(object([
                ("name", string()),
                ("min", integer()),
                ("max", integer()),
                ("evidence", string()),
            ])),
        ),
        ("constraints", list(string())),
        ("objectives", list(string())),
    ]);
    let r = request(
        "Inventory a bounded integer optimization problem. List the distinct decision variables, their literal names and explicitly given inclusive integer domains. For each domain copy its exact supporting phrase into evidence. Copy each additional constraint verbatim from the passage, one condition per quote. Copy each objective verbatim in priority order, retaining its direction. Domain bounds and per-unit prices or yields are background facts; only additional numeric restrictions belong in constraints. Preserve strict inequalities. If the domains or goals are missing, ambiguous or outside this linear problem, return three empty lists.",
        json!({"passage":src}),
        Some(schema),
    );
    engine.structured(r, |v| {
        let d: Inventory = serde_json::from_value(v.clone()).map_err(|e| e.to_string())?;
        if d.variables.is_empty() || d.objectives.is_empty() {
            return Ok(None);
        }
        if !(1..=12).contains(&d.variables.len())
            || !(1..=8).contains(&d.objectives.len())
            || d.constraints.len() > 32
        {
            return Err("provide a complete bounded inventory, or use three empty lists".into());
        }
        let mut names = BTreeSet::new();
        let mut bounds = vec![];
        let mut variables = vec![];
        for v in d.variables {
            document::locate(src, 0, &v.evidence)?;
            let numbers = crate::solve::literal_numbers(&v.evidence);
            if v.name.trim().is_empty()
                || !src.contains(&v.name)
                || !names.insert(v.name.clone())
                || !numbers.contains(&v.min)
                || !numbers.contains(&v.max)
                || v.min > v.max
            {
                return Err(
                    "use distinct literal variables and explicit bounds from the passage".into(),
                );
            }
            bounds.push(v.evidence);
            variables.push(Variable {
                name: v.name,
                min: v.min,
                max: v.max,
            });
        }
        for q in d.constraints.iter().chain(&d.objectives) {
            document::locate(src, 0, q)?;
        }
        if d.objectives.iter().any(|q| {
            let text = q.to_lowercase();
            [
                "未定",
                "未決",
                "未確定",
                "決まっていない",
                "undecided",
                "not yet decided",
                "not yet chosen",
                "unspecified",
            ]
            .iter()
            .any(|word| text.contains(word))
        }) {
            return Ok(None);
        }
        Ok(Some(Declarations {
            variables,
            bounds,
            constraints: d.constraints,
            objectives: d.objectives,
        }))
    })
}

// Understand an entire affine expression with integer constants and named variables.
// A term consumes its complete input or hands the declaration back to the language model.
fn affine(text: &str, vars: &[Variable]) -> Option<(Vec<i64>, i64)> {
    if vars.iter().any(|v| {
        !v.name.starts_with(|c: char| c.is_alphabetic() || c == '_')
            || !v.name.chars().all(|c| c.is_alphanumeric() || c == '_')
    }) {
        return None;
    }
    let mut weights = vec![0i64; vars.len()];
    let mut constant = 0i64;
    let mut rest = text.trim();
    let mut first = true;
    while !rest.is_empty() {
        let negative = rest.starts_with('-');
        if negative || rest.starts_with('+') {
            rest = rest[1..].trim_start();
        } else if !first {
            return None;
        }
        let digits = rest.bytes().take_while(u8::is_ascii_digit).count();
        let magnitude = if digits == 0 {
            1
        } else {
            rest[..digits].parse::<i128>().ok()?
        };
        let coefficient = i64::try_from(if negative { -magnitude } else { magnitude }).ok()?;
        rest = rest[digits..].trim_start();
        let product = rest.starts_with('*');
        if product {
            if digits == 0 {
                return None;
            }
            rest = rest[1..].trim_start();
        }
        let var = vars
            .iter()
            .enumerate()
            .filter(|(_, v)| rest.starts_with(&v.name))
            .max_by_key(|(_, v)| v.name.len());
        if let Some((i, v)) = var {
            weights[i] = weights[i].checked_add(coefficient)?;
            rest = rest[v.name.len()..].trim_start();
        } else if digits != 0 && !product {
            constant = constant.checked_add(coefficient)?;
        } else {
            return None;
        }
        first = false;
    }
    (!first).then_some((weights, constant))
}

fn math(s: &str) -> String {
    s.trim()
        .trim_end_matches(['。', '.', ',', ';', '、'])
        .trim()
        .replace(['×', '·'], "*")
        .replace('−', "-")
}
fn explicit_constraint(q: &str, vars: &[Variable]) -> Option<Constraint> {
    let clause = math(q);
    let clause = clause
        .trim_start_matches("Require ")
        .trim_start_matches("制約は ")
        .trim_end_matches(" を守り")
        .trim();
    for (op, rel, reverse) in [
        (">=", Relation::Ge, Relation::Le),
        ("<=", Relation::Le, Relation::Ge),
        ("≥", Relation::Ge, Relation::Le),
        ("≤", Relation::Le, Relation::Ge),
        (">", Relation::Gt, Relation::Lt),
        ("<", Relation::Lt, Relation::Gt),
        ("=", Relation::Eq, Relation::Eq),
    ] {
        if let Some((l, r)) = clause.split_once(op) {
            let (mut a, la) = affine(l, vars)?;
            let (b, rb) = affine(r, vars)?;
            if a.iter().all(|v| *v == 0) && b.iter().any(|v| *v != 0) {
                return Some(Constraint {
                    weights: b,
                    relation: reverse,
                    rhs: la.checked_sub(rb)?,
                });
            }
            for (x, y) in a.iter_mut().zip(b) {
                *x = x.checked_sub(y)?;
            }
            return Some(Constraint {
                weights: a,
                relation: rel,
                rhs: rb.checked_sub(la)?,
            });
        }
    }
    None
}
fn explicit_objective(q: &str, vars: &[Variable]) -> Option<Objective> {
    let text = math(q);
    let text = text
        .trim_start_matches("First ")
        .trim_start_matches("then ")
        .trim();
    for (en, ja, sense) in [
        ("minimize ", "最小化", Sense::Min),
        ("maximize ", "最大化", Sense::Max),
    ] {
        let phrase = if text
            .get(..en.len())
            .is_some_and(|s| s.eq_ignore_ascii_case(en))
        {
            &text[en.len()..]
        } else if let Some(expr) = text.trim_end_matches("する").strip_suffix(ja) {
            expr.trim_end().strip_suffix('を')?.trim()
        } else {
            continue;
        };
        let (weights, constant) = affine(phrase, vars)?;
        if constant != 0 {
            return None;
        }
        return Some(Objective { weights, sense });
    }
    None
}

fn weight_schema(vars: &[Variable]) -> Value {
    contract::object(vars.iter().map(|v| (v.name.clone(), integer())))
}
fn weights(v: &Value, vars: &[Variable], numbers: &BTreeSet<i64>) -> Result<Vec<i64>, String> {
    vars.iter()
        .map(|var| {
            v[&var.name]
                .as_i64()
                .filter(|n| numbers.contains(n))
                .ok_or_else(|| "use integer coefficients supported by the passage".into())
        })
        .collect()
}
pub fn interpret(engine: &mut Engine<'_>, src: &str) -> Run<Output> {
    let Some(d) = declarations(engine, src)? else {
        return Ok(Output::new("abstained", Value::Null));
    };
    let numbers = source_numbers(src);
    let mut constraints = Vec::new();
    for q in &d.constraints {
        let row = if let Some(c) = explicit_constraint(q, &d.variables) {
            c
        } else {
            let schema = object([
                ("weights", weight_schema(&d.variables)),
                (
                    "relation",
                    json!({"type":"string","enum":["lt","le","eq","ge","gt"]}),
                ),
                ("rhs", integer()),
            ]);
            engine.structured(request("Translate only the quoted constraint. The left side is the sum of the named variables times their coefficients; the right side is a constant. Supply one coefficient for every variable, zero when absent. Keep strict and inclusive bounds distinct; reverse the comparison if swapping sides.",
                json!({"declaration":q,"passage":src}),Some(schema)),|v|{
                    let rhs=v["rhs"].as_i64().filter(|n|numbers.contains(n)).ok_or("use a supported integer threshold")?;
                    Ok(Constraint{weights:weights(&v["weights"],&d.variables,&numbers)?,relation:serde_json::from_value(v["relation"].clone()).map_err(|e|e.to_string())?,rhs})
                })?
        };
        constraints.push(row);
    }
    let mut objectives = Vec::new();
    for q in &d.objectives {
        let row = if let Some(o) = explicit_objective(q, &d.variables) {
            o
        } else {
            let schema = object([
                ("weights", weight_schema(&d.variables)),
                ("sense", json!({"type":"string","enum":["min","max"]})),
            ]);
            engine.structured(request("Translate just this optimization goal. Give the coefficient of each named variable in its objective, zero if absent. Use the passage for unit costs or yields. Preserve the goal's maximize/minimize direction.",
                json!({"goal":q,"passage":src}),Some(schema)),|v|Ok(Objective{weights:weights(&v["weights"],&d.variables,&numbers)?,
                    sense:serde_json::from_value(v["sense"].clone()).map_err(|e|e.to_string())?}))?
        };
        objectives.push(row);
    }
    let problem = Problem {
        variables: d.variables,
        constraints,
        objectives,
    };
    let solution = problem.solve().map_err(Fail::Held)?;
    Ok(Output::new(
        "review",
        json!({"interpretation":problem,"solution":solution,
        "declarations":{"bounds":d.bounds,"constraints":d.constraints,"objectives":d.objectives}}),
    ))
}
