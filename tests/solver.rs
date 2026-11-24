use afmcli::{
    engine::{Engine, Policy},
    fm::{Model, Request},
    job::Job,
    solve::Problem,
};
use serde_json::{Value, json};

struct Replies(Vec<Value>);
impl Model for Replies {
    fn ready(&mut self) -> Result<(), String> {
        Ok(())
    }
    fn count(&mut self, _: &Request) -> Result<usize, String> {
        Ok(100)
    }
    fn generate(&mut self, _: &Request) -> Result<String, String> {
        if self.0.is_empty() {
            panic!("unexpected generation")
        }
        Ok(self.0.remove(0).to_string())
    }
}
fn problem() -> Value {
    json!({"variables":[{"name":"a","min":0,"max":4},{"name":"b","min":0,"max":4}],
    "constraints":[{"weights":[1,1],"relation":"ge","rhs":2}],"objectives":[{"weights":[1,1],"sense":"min"}]})
}
fn solve(v: Value) -> Result<Value, String> {
    serde_json::from_value::<Problem>(v).unwrap().solve()
}
#[test]
fn ties_strict_boundaries_and_priority_order() {
    let mut p = problem();
    let a = solve(p.clone()).unwrap();
    assert_eq!(a["assignment"], json!({"a":0,"b":2}));
    assert_eq!(a["optimal_count"], 3);
    p["constraints"][0]["relation"] = json!("gt");
    p["objectives"]
        .as_array_mut()
        .unwrap()
        .push(json!({"weights":[0,1],"sense":"min"}));
    let a = solve(p.clone()).unwrap();
    assert_eq!(a["assignment"], json!({"a":3,"b":0}));
    assert_eq!(a["objective_values"], json!([3, 0]));
    assert_eq!(a["optimal_count"], 1);
    p["constraints"][0]["rhs"] = json!(8);
    let a = solve(p).unwrap();
    assert_eq!(a["feasible"], false);
    assert!(a["assignment"].is_null());
}
#[test]
fn huge_ranges_duplicate_names_and_arithmetic_overflow_are_explicit() {
    let mut p = problem();
    p["variables"][0]["max"] = json!(i64::MAX);
    assert!(solve(p).is_err());
    let mut p = problem();
    p["variables"][0]["name"] = json!("b");
    assert!(solve(p).is_err());
    let mut p = problem();
    p["variables"][0]["min"] = json!(i64::MAX);
    p["variables"][0]["max"] = json!(i64::MAX);
    p["constraints"][0]["weights"] = json!([2, 0]);
    assert!(solve(p).is_err());
}
#[test]
fn interpreted_and_confirmed_problems_have_distinct_statuses() {
    let p = problem();
    let typed: Job = serde_json::from_value(json!({"task":"solve","problem":p})).unwrap();
    let natural: Job = serde_json::from_value(
        json!({"task":"solve","source":"a,b in 0..4; a+b >= 2, minimize a+b."}),
    )
    .unwrap();
    let mut m = Replies(vec![p.clone()]);
    let a = Engine::new(&mut m, Policy::default()).execute(&typed);
    assert_eq!(a.status, "ok");
    assert_eq!(a.metrics.calls, 0);
    let b = Engine::new(&mut m, Policy::default()).execute(&natural);
    assert_eq!(b.status, "review");
    assert_eq!(b.result["solution"], a.result);
    let mut invented = p;
    invented["constraints"][0]["rhs"] = json!(17);
    let mut m = Replies(vec![invented]);
    let b = Engine::new(
        &mut m,
        Policy {
            repairs: 0,
            ..Default::default()
        },
    )
    .execute(&natural);
    assert_eq!(b.status, "held");
    assert!(b.result.is_null());
}
#[test]
fn auto_route_never_executes_a_generated_edit_or_unknown_tool() {
    let job: Job = serde_json::from_value(
        json!({"task":"auto","source":"red or blue","instruction":"which color?"}),
    )
    .unwrap();
    let mut m = Replies(vec![json!({"route":"edit"})]);
    let out = Engine::new(
        &mut m,
        Policy {
            repairs: 0,
            ..Default::default()
        },
    )
    .execute(&job);
    assert_eq!(out.status, "held");
    let mut m = Replies(vec![
        json!({"route":"answer"}),
        json!({"answerable":true,"answer":"red"}),
        json!({"supported":true}),
    ]);
    let out = Engine::new(&mut m, Policy::default()).execute(&job);
    assert_eq!(out.result["route"], "answer");
    assert_eq!(out.result["output"]["answer"]["value"], "red");
}
#[test]
fn unsupported_grounded_answer_abstains_after_an_opt_in_second_read() {
    let j: Job = serde_json::from_value(
        json!({"task":"answer","question":"Who?","source":"Not yet announced."}),
    )
    .unwrap();
    let mut m = Replies(vec![
        json!({"answer":"Not yet announced","answerable":true}),
        json!({"supported":false}),
    ]);
    let out = Engine::new(
        &mut m,
        Policy {
            check_support: true,
            ..Default::default()
        },
    )
    .execute(&j);
    assert_eq!(out.status, "abstained");
    assert_eq!(out.metrics.calls, 2);
}
