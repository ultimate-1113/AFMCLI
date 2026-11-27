use super::*;
use crate::{
    engine::{Policy, Record},
    fm::{Model, Request},
    job::Job,
};

struct Stub(Option<Result<Value, String>>);
impl Model for Stub {
    fn ready(&mut self) -> Result<(), String> {
        unreachable!()
    }
    fn count(&mut self, _: &Request) -> Result<usize, String> {
        Ok(100)
    }
    fn generate(&mut self, req: &Request) -> Result<String, String> {
        let reply = self.0.take().expect("unexpected model call");
        if req.schema.is_some() {
            assert!(req.prompt.contains("source_quantities"));
        }
        reply.map(|v| {
            let Some(schema)=&req.schema else{return v.to_string()};
            let props=&schema["properties"];
            if !props["operation"].is_null() || v["operands"].is_null(){return v.to_string();}
            let mut out=json!({"requested_quantity":v["requested_quantity"],"relation_span":v["relation_span"]});
            if out["relation_span"]=="none"{out["relation_span"]=v["requested_quantity"].clone();}
            for (key,role) in [("part","part"),("whole","whole"),("unit_price","unit_price"),
                ("purchased_quantity","count"),("opening_quantity","start"),("quantities_added","add"),
                ("quantities_removed","subtract"),("money_amounts","addend")] {
                if props[key].is_null(){continue;}
                let many=props[key]["type"]=="array";
                let choices=if many{&props[key]["items"]["enum"]}else{&props[key]["enum"]};
                let ids:Vec<_>=v["operands"].as_array().unwrap().iter().filter(|x|x["role"]==role&&choices.as_array().unwrap().contains(&x["number"])).map(|x|x["number"].clone()).collect();
                out[key]=if many{json!(ids)}else{ids.into_iter().next().unwrap_or(json!("missing"))};
            }
            out.to_string()
        })
    }
}
fn operand(number: &str, role: &str, unit: &str) -> Value {
    json!({"number":number,"role":role,"unit":unit})
}
fn selection(op: &str, requested: &str, relation: &str, operands: Vec<Value>) -> Value {
    json!({"requested_quantity":requested,"relation_span":relation,"operation":op,"operands":operands,"missing":""})
}
fn execute(source: &str, reply: Option<Result<Value, String>>, direct: bool) -> Record {
    let job = Job::Calculate {
        expression: None,
        source: Some(source.into()),
        angle: Angle::Rad,
    };
    Engine::new(
        &mut Stub(reply),
        Policy {
            direct,
            repairs: 0,
            ..Policy::default()
        },
    )
    .execute(&job)
}
fn checked(source: &str, reply: Value) -> Record {
    execute(source, Some(Ok(reply)), false)
}

#[test]
fn local_expression_and_direct_mode_keep_their_contracts() {
    let rec = execute("3/4", None, false);
    assert_eq!((rec.status.as_str(), rec.metrics.calls), ("ok", 0));
    assert_eq!(rec.result["value"], "0.75");
    let rec = execute(
        "半径が足りません。",
        Some(Ok(json!({"expression":""}))),
        true,
    );
    assert_eq!((rec.status.as_str(), rec.metrics.calls), ("unverified", 1));
    assert!(rec.result.is_string());
}

#[test]
fn derives_cost_from_decimal_or_spelled_source_number_and_rate() {
    let input = "Three spools cost four euros each. What is the total cost?";
    let rec = checked(
        input,
        selection(
            "price_times_count",
            "s1",
            "s0",
            vec![
                operand("q1", "unit_price", "euros"),
                operand("q0", "count", "spools"),
            ],
        ),
    );
    assert_eq!(
        (rec.status.as_str(), rec.metrics.calls),
        ("review", 1),
        "{:?}",
        rec.notes
    );
    assert_eq!(rec.result["value"], "12");
    assert_eq!(rec.result["unit"], "euros");
    for fact in rec.result["operands"].as_array().unwrap() {
        let lo = fact["start_byte"].as_u64().unwrap() as usize;
        let hi = fact["end_byte"].as_u64().unwrap() as usize;
        assert_eq!(input.get(lo..hi), fact["source"].as_str());
    }
}

#[test]
fn handles_sum_difference_and_percentage_changes_in_normal_job_path() {
    let tests = [
        (
            "午前は１１箱。午後は７箱。合計は何箱？",
            selection(
                "sum",
                "s2",
                "s2",
                vec![operand("q0", "addend", "箱"), operand("q1", "addend", "箱")],
            ),
            "18",
            "箱",
        ),
        (
            "在庫は28台。3台を出荷した。残りは何台？",
            selection(
                "difference",
                "s2",
                "s2",
                vec![
                    operand("q0", "start", "台"),
                    operand("q1", "subtract", "台"),
                ],
            ),
            "25",
            "台",
        ),
        (
            "The list price is 80 dollars. The discount is 15%. What is the final discounted price?",
            selection(
                "percent_decrease_total",
                "s2",
                "s1",
                vec![operand("q0", "base", "USD"), operand("q1", "percent", "%")],
            ),
            "68",
            "dollars",
        ),
        (
            "2,400円のうち8%を寄付する。寄付額はいくら？",
            selection(
                "percent_of",
                "s1",
                "s0",
                vec![operand("q0", "base", "円"), operand("q1", "percent", "%")],
            ),
            "192",
            "円",
        ),
        (
            "登録者は全体で60人。そのうち9人が返信した。返信した人の割合は？",
            selection(
                "percent_share",
                "s2",
                "s2",
                vec![operand("q1", "part", "人"), operand("q0", "whole", "人")],
            ),
            "15",
            "%",
        ),
    ];
    for (input, proposal, want, unit) in tests {
        let rec = checked(input, proposal);
        assert_eq!(rec.status, "review", "{input}: {:?}", rec.notes);
        assert_eq!(rec.result["value"], want);
        assert_eq!(rec.result["unit"], unit);
    }
}

#[test]
fn insufficient_and_invalid_numeric_subtotals_never_produce_answers() {
    let input = "The unit price is 40 dollars each. We sold 8 boxes. What is the profit?";
    let rec = checked(
        input,
        selection(
            "price_times_count",
            "s2",
            "s0",
            vec![
                operand("q0", "unit_price", "dollars"),
                operand("q1", "count", "boxes"),
            ],
        ),
    );
    assert_eq!(rec.status, "held");
    assert!(rec.result.is_null());
    assert!(rec.notes[0].contains("costs"));
    let input = "参加者が9人です。出席率は何%？";
    let rec = checked(
        input,
        selection(
            "percent_share",
            "s1",
            "s1",
            vec![operand("q0", "part", "人")],
        ),
    );
    assert!(rec.result.is_null());
    assert!(rec.notes[0].contains("denominator"));
    let mut proposal = selection("insufficient", "s1", "none", vec![]);
    proposal["missing"] = json!("発送対象の荷物数");
    let rec = checked("送料は1個につき40円。発送分の支払額は？", proposal);
    assert_eq!(rec.status, "held");
    assert!(rec.notes[0].contains("missing_count") && rec.notes[0].contains("発送"));
}

#[test]
fn rejects_different_units_source_ids_and_unsupported_role_assignments() {
    let input = "We received 10 kg. Another batch was 8 m. What is the total?";
    for operands in [
        vec![operand("q0", "addend", "kg"), operand("q1", "addend", "m")],
        vec![operand("q0", "addend", "kg"), operand("q0", "addend", "kg")],
        vec![operand("q0", "addend", "kg"), operand("q1", "addend", "kg")],
        vec![operand("q0", "addend", "kg"), operand("q1", "percent", "m")],
    ] {
        let rec = checked(input, selection("sum", "s2", "s2", operands));
        assert_eq!(rec.status, "held");
        assert!(rec.result.is_null());
    }
    let rec = checked(
        "参考値は5枚。今朝は12枚。合計は何枚？",
        selection(
            "sum",
            "s2",
            "s2",
            vec![operand("q0", "addend", "枚"), operand("q1", "addend", "枚")],
        ),
    );
    assert!(rec.result.is_null());
    assert!(rec.notes[0].contains("excluded"));
}

#[test]
fn backend_errors_are_not_reported_as_missing_arithmetic_evidence() {
    let rec = execute(
        "What percentage responded?",
        Some(Err("fm timed out".into())),
        false,
    );
    assert_eq!(rec.status, "error");
    assert!(rec.result.is_null());
    assert_eq!(rec.notes, ["fm timed out"]);
}

#[test]
fn signed_movements_require_the_requested_site_for_each_change() {
    let proposal = || {
        selection(
            "balance",
            "s4",
            "s1",
            vec![
                operand("q0", "start", "人"),
                operand("q1", "subtract", "人"),
                operand("q2", "add", "人"),
            ],
        )
    };
    let input = "開始時の人数は44人。入館と退館があった。この施設で退館したのは6人。入館したのは4人。現在の人数は？";
    let rec = checked(input, proposal());
    assert_eq!(rec.status, "review", "{:?}", rec.notes);
    assert_eq!(rec.result["expression"], "44-6+4");
    let rec = checked(&input.replace("この施設", "別の施設"), proposal());
    assert_eq!(rec.status, "held");
    assert!(rec.result.is_null() && rec.notes[0].contains("missing_outflow"));
}

#[test]
fn percentage_denominator_belongs_to_the_same_population() {
    let input = "The west plant had 12 leaking valves. There were 80 valves inspected in this plant. What percentage of the valves inspected in the west plant are leaking?";
    let proposal = || {
        selection(
            "percent_share",
            "s2",
            "s2",
            vec![
                operand("q0", "part", "valves"),
                operand("q1", "whole", "valves"),
            ],
        )
    };
    let rec = checked(input, proposal());
    assert_eq!(rec.status, "review", "{:?}", rec.notes);
    assert_eq!(rec.result["value"], "15");
    let rec = checked(
        &input.replace("this plant", "a different plant"),
        proposal(),
    );
    assert!(rec.result.is_null());
    assert!(rec.notes[0].contains("total number of valves inspected in the west plant"));
}

#[test]
fn a_separate_price_or_percentage_cannot_replace_a_group_members_balance() {
    assert!(!has("copper wire", &["per "]));
    assert!(has("seven euros per meter", &["per "]));
    let source = "The amber purse held 15 euros. The linen purse held 19 euros. Copper wire costs 4 euros per meter and carries a 5% fee. What is the total amount of money in both purses?";
    let amounts = || {
        selection(
            "sum",
            "s3",
            "s0",
            vec![
                operand("q0", "addend", "euros"),
                operand("q1", "addend", "euros"),
            ],
        )
    };
    let rec = checked(source, amounts());
    assert_eq!(rec.status, "review", "{:?}", rec.notes);
    assert_eq!(rec.result["value"], "34");
    let rec = checked(
        &source.replace("held 19 euros", "weighed 19 ounces"),
        amounts(),
    );
    assert_eq!(rec.status, "held");
    assert!(rec.result.is_null());
    assert!(
        rec.notes[0]
            .to_lowercase()
            .contains("amount of money in the linen purse")
    );
}

#[test]
fn recovers_only_a_single_explicit_movement_and_reports_its_origin() {
    let source = "開始時の人数は44人。この施設で6人が退館した。現在の人数は？";
    let rec = checked(
        source,
        selection("balance", "s2", "s1", vec![operand("q1", "subtract", "人")]),
    );
    assert_eq!(rec.status, "review", "{:?}", rec.notes);
    assert_eq!(rec.result["value"], "38");
    assert_eq!(
        rec.result["operands"][0]["selection"],
        "unique_explicit_source_role"
    );
    let rec = checked(
        "Tables cost 9 dollars per table. There are 2 lights. What is the total cost of the tables?",
        selection(
            "price_times_count",
            "s2",
            "s0",
            vec![
                operand("q0", "unit_price", "dollars"),
                operand("q1", "count", "lights"),
            ],
        ),
    );
    assert!(rec.result.is_null() && rec.notes[0].contains("missing_count"));
}
