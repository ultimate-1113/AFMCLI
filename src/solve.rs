//! Exhaustive optimization of small bounded integer linear problems.
use crate::{
    contract,
    engine::{Engine, Fail, Output, Run, request},
};
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    cmp::Ordering,
    collections::{BTreeMap, BTreeSet},
    sync::LazyLock,
};

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Problem {
    pub variables: Vec<Variable>,
    pub constraints: Vec<Constraint>,
    pub objectives: Vec<Objective>,
}
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Variable {
    pub name: String,
    pub min: i64,
    pub max: i64,
}
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Constraint {
    pub weights: Vec<i64>,
    pub relation: Relation,
    pub rhs: i64,
}
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Objective {
    pub weights: Vec<i64>,
    pub sense: Sense,
}
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Relation {
    Le,
    Lt,
    Ge,
    Gt,
    Eq,
}
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Sense {
    Min,
    Max,
}
fn dot(w: &[i64], x: &[i64]) -> Result<i64, String> {
    w.iter().zip(x).try_fold(0i64, |acc, (a, b)| {
        a.checked_mul(*b)
            .and_then(|n| acc.checked_add(n))
            .ok_or_else(|| "signed 64-bit linear expression overflow".into())
    })
}
impl Problem {
    pub fn size(&self) -> Result<usize, String> {
        let n = self.variables.len();
        let mut names = BTreeSet::new();
        if !(1..=12).contains(&n)
            || self.constraints.len() > 32
            || !(1..=8).contains(&self.objectives.len())
        {
            return Err("expected 1–12 variables, up to 32 constraints and 1–8 objectives".into());
        }
        let mut size = 1usize;
        for v in &self.variables {
            if v.name.trim().is_empty() || v.min > v.max || !names.insert(&v.name) {
                return Err("invalid or duplicate bounded variable".into());
            }
            let width = usize::try_from(i128::from(v.max) - i128::from(v.min) + 1)
                .map_err(|_| "variable range is too large")?;
            size = size.checked_mul(width).ok_or("search space is too large")?;
        }
        let expr = self
            .constraints
            .iter()
            .map(|r| &r.weights)
            .chain(self.objectives.iter().map(|r| &r.weights));
        if expr.into_iter().any(|w| w.len() != n) {
            return Err("every weight vector must follow the declared variable order".into());
        }
        if size > 250_000
            || size.saturating_mul(n * (self.constraints.len() + self.objectives.len())) > 8_000_000
        {
            return Err("search exceeds the bounded enumeration budget".into());
        }
        Ok(size)
    }
    pub fn solve(&self) -> Result<Value, String> {
        let count = self.size()?;
        let mut x: Vec<_> = self.variables.iter().map(|v| v.min).collect();
        let mut best: Option<(Vec<i64>, Vec<i64>)> = None;
        let mut ties = 0;
        for _ in 0..count {
            let mut feasible = true;
            for row in &self.constraints {
                let a = dot(&row.weights, &x)?;
                let b = row.rhs;
                feasible = match row.relation {
                    Relation::Ge => a >= b,
                    Relation::Gt => a > b,
                    Relation::Le => a <= b,
                    Relation::Lt => a < b,
                    Relation::Eq => a == b,
                };
                if !feasible {
                    break;
                }
            }
            if feasible {
                let vals = self
                    .objectives
                    .iter()
                    .map(|o| dot(&o.weights, &x))
                    .collect::<Result<Vec<_>, _>>()?;
                let order = best.as_ref().map_or(Ordering::Less, |(_, old)| {
                    self.objectives
                        .iter()
                        .zip(vals.iter().zip(old))
                        .map(|(o, (a, b))| match o.sense {
                            Sense::Min => a.cmp(b),
                            Sense::Max => b.cmp(a),
                        })
                        .find(|o| *o != Ordering::Equal)
                        .unwrap_or(Ordering::Equal)
                });
                match order {
                    Ordering::Less => {
                        best = Some((x.clone(), vals));
                        ties = 1;
                    }
                    Ordering::Equal => ties += 1,
                    Ordering::Greater => {}
                }
            }
            for (v, slot) in self.variables.iter().zip(&mut x).rev() {
                if *slot < v.max {
                    *slot += 1;
                    break;
                }
                *slot = v.min;
            }
        }
        let (assignment, values) = match best {
            Some((x, vals)) => {
                let a: BTreeMap<_, _> = self.variables.iter().map(|v| &v.name).zip(x).collect();
                (json!(a), vals)
            }
            None => (Value::Null, vec![]),
        };
        Ok(
            json!({"feasible":!assignment.is_null(),"assignment":assignment,"objective_values":values,"optimal_count":ties}),
        )
    }
}
fn schema() -> Value {
    let i = json!({"type":"integer"});
    let st = json!({"type":"string"});
    let ar = |v: Value| json!({"type":"array","items":v});
    let obj = |p: Vec<(&str, Value)>| contract::object(p.into_iter().map(|(k, v)| (k.into(), v)));
    obj(vec![
        (
            "variables",
            ar(obj(vec![
                ("name", st),
                ("min", i.clone()),
                ("max", i.clone()),
            ])),
        ),
        (
            "constraints",
            ar(obj(vec![
                ("weights", ar(i.clone())),
                (
                    "relation",
                    json!({"type":"string","enum":["ge","gt","le","lt","eq"]}),
                ),
                ("rhs", i.clone()),
            ])),
        ),
        (
            "objectives",
            ar(obj(vec![
                ("weights", ar(i)),
                ("sense", json!({"type":"string","enum":["min","max"]})),
            ])),
        ),
    ])
}
static INTEGERS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[+-]?[0-9]+").unwrap());
pub(crate) fn literal_numbers(src: &str) -> BTreeSet<i64> {
    INTEGERS
        .find_iter(src)
        .filter_map(|m| m.as_str().parse().ok())
        .collect()
}
pub(crate) fn source_numbers(src: &str) -> BTreeSet<i64> {
    let mut allowed = literal_numbers(src);
    allowed.extend([-1, 0, 1]);
    allowed
}
fn supported(p: &Problem, src: &str) -> Result<(), String> {
    let allowed = source_numbers(src);
    for v in &p.variables {
        if !src.contains(&v.name) || !allowed.contains(&v.min) || !allowed.contains(&v.max) {
            return Err("copy literal variable names and numeric bounds from the source".into());
        }
    }
    if p.constraints
        .iter()
        .any(|r| !allowed.contains(&r.rhs) || r.weights.iter().any(|v| !allowed.contains(v)))
        || p.objectives
            .iter()
            .any(|r| r.weights.iter().any(|v| !allowed.contains(v)))
    {
        return Err("a coefficient or threshold is absent from the source".into());
    }
    p.size().map(|_| ())
}
pub fn run(
    engine: &mut Engine<'_>,
    problem: Option<&Problem>,
    source: Option<&str>,
) -> Run<Output> {
    if problem.is_some() == source.is_some() {
        return Err(Fail::Invalid(
            "supply exactly one of problem or source".into(),
        ));
    }
    if let Some(p) = problem {
        p.size().map_err(Fail::Invalid)?;
    }
    if engine.policy.direct {
        return engine.raw(request("Find the optimal integer assignment satisfying every constraint. Weight vectors use declared variable order. Optimize all objectives in order, break remaining ties by smallest assignment in variable order. Return a JSON object with feasible, assignment (null if infeasible), objective_values and optimal_count (count before the final tie break).",json!({"problem":problem,"source":source}),None));
    }
    if let Some(p) = problem {
        return Ok(Output::new("ok", p.solve().map_err(Fail::Held)?));
    }
    let src = source.unwrap();
    if engine.policy.decompose {
        return crate::declarations::interpret(engine, src);
    }
    let r = request(
        "Translate this bounded integer linear problem into the schema. Preserve literal variable names, coefficients, bounds, inequality strictness and preference order. All weight vectors follow variables in order. Do not invent bounds or objective priorities. If insufficient, return three empty arrays. Use ASCII numeric literals from the source.",
        json!({"source":src}),
        Some(schema()),
    );
    let interpreted = engine.structured(r, |v| {
        if ["variables", "constraints", "objectives"]
            .iter()
            .all(|k| v[*k].as_array().is_some_and(Vec::is_empty))
        {
            return Ok(None);
        }
        let p: Problem = serde_json::from_value(v.clone()).map_err(|e| e.to_string())?;
        supported(&p, src)?;
        Ok(Some(p))
    })?;
    if let Some(p) = interpreted {
        let solution = p.solve().map_err(Fail::Held)?;
        Ok(Output::new(
            "review",
            json!({"interpretation":p,"solution":solution}),
        ))
    } else {
        Ok(Output::new("abstained", Value::Null))
    }
}
