use afmcli::{
    engine::{Engine, Policy, Record},
    fm::{Model, Request},
    job::Job,
};
use serde_json::{Value, json};
use std::collections::VecDeque;

struct Script(VecDeque<Value>);
impl Model for Script {
    fn ready(&mut self) -> Result<(), String> {
        Ok(())
    }
    fn count(&mut self, _: &Request) -> Result<usize, String> {
        Ok(64)
    }
    fn generate(&mut self, _: &Request) -> Result<String, String> {
        Ok(self
            .0
            .pop_front()
            .expect("unexpected model call")
            .to_string())
    }
}
fn run(job: Value, replies: Vec<Value>, calls: usize) -> Record {
    let j: Job = serde_json::from_value(job).unwrap();
    let mut m = Script(replies.into());
    let r = Engine::new(
        &mut m,
        Policy {
            decompose: true,
            repairs: 0,
            check_support: false,
            max_calls: calls,
            ..Default::default()
        },
    )
    .execute(&j);
    assert!(m.0.is_empty());
    r
}

#[test]
fn selection_reports_only_original_offsets_in_the_chosen_sentence() {
    let source = "旧会場は青葉館。確定会場は青葉館。";
    let job = json!({"task":"answer","source":source,"question":"確定した会場は？"});
    let r = run(
        job.clone(),
        vec![
            json!({"ids":[2]}),
            json!({"answerable":true,"answer":"青葉館"}),
        ],
        2,
    );
    assert_eq!(r.status, "ok");
    let start = source.rfind("青葉館").unwrap();
    assert_eq!(
        r.result["answer"]["spans"],
        json!([{"start_byte":start,"end_byte":start+"青葉館".len()}])
    );
    let empty = run(job.clone(), vec![json!({"ids":[]})], 2);
    assert_eq!(empty.status, "abstained");
    for ids in [json!([0]), json!([1, 1])] {
        assert_eq!(run(job.clone(), vec![json!({"ids":ids})], 2).status, "held");
    }
    let capped = run(job, vec![json!({"ids":[2]})], 1);
    assert_eq!((capped.status.as_str(), capped.metrics.calls), ("held", 1));
}

#[test]
fn quoted_formulas_are_parsed_on_both_sides_with_strict_bounds() {
    let domain = "N is an integer from 0 to 8";
    let job = json!({"task":"solve","source":format!("{domain}. Require 4 > N. Require N + 1 >= 2*N - 3. Maximize N.")});
    let inventory = json!({"variables":[{"name":"N","min":0,"max":8,"evidence":domain}],
        "constraints":["4 > N","N + 1 >= 2*N - 3"],"objectives":["Maximize N"]});
    let r = run(job.clone(), vec![inventory.clone()], 1);
    assert_eq!((r.status.as_str(), r.metrics.calls), ("review", 1));
    assert_eq!(r.result["solution"]["assignment"], json!({"N":3}));
    assert_eq!(
        r.result["declarations"]["constraints"],
        inventory["constraints"]
    );
    let mut fabricated = inventory;
    fabricated["constraints"][0] = json!("4 >= N");
    assert_eq!(run(job, vec![fabricated], 1).status, "held");
}

#[test]
fn absent_bounds_or_goal_yield_no_suggested_solution() {
    let job = json!({"task":"solve","source":"The objective has not yet been chosen."});
    let r = run(
        job,
        vec![json!({"variables":[],"constraints":[],"objectives":[]})],
        1,
    );
    assert_eq!(r.status, "abstained");
    assert!(r.result.get("solution").is_none_or(Value::is_null));
}
