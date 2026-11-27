use afmcli::{
    calc::{Angle, evaluate},
    engine::{Engine, Policy},
    fm::{Model, Request},
    job::Job,
};
use serde_json::{Value, json};
use std::{
    io::Write,
    process::{Command, Stdio},
};

fn check(s: &str, want: &str, approx: bool, unit: Angle) {
    let v = evaluate(s, unit).unwrap();
    assert_eq!(v["value"], want, "{s}");
    assert_eq!(v["approximate"], approx, "{s}");
    assert_eq!(v["angle_unit"], json!(unit));
}

#[test]
fn exact_arithmetic_and_operator_binding() {
    for (e, v) in [
        ("(1+2)^3", "27"),
        ("1/3", "1/3"),
        ("1/40", "0.025"),
        ("1/3 + 1/6", "0.5"),
        ("0.1 + 0.2", "0.3"),
        ("2 ^ 3 ^ 2", "512"),
        ("-2^2", "-4"),
        ("(-2)^2", "4"),
        ("2^-2", "0.25"),
        ("2^-2^2", "0.0625"),
        ("−2 × (6 ÷ 3)", "-4"),
        ("5!", "120"),
        ("-3!", "-6"),
        ("sqrt(9/16)", "0.75"),
        ("Abs(-3/7)", "3/7"),
        ("1.2500e2 + .05E+2", "130"),
        ("8e-2", "0.08"),
        ("nCr(5,2)", "10"),
        ("3!^2", "36"),
        ("nPr(5,2)", "20"),
        ("0!", "1"),
        ("0.000e38", "0"),
    ] {
        check(e, v, false, Angle::Rad);
    }
    let max = i128::MAX;
    check(&format!("({max}/17)*(17/{max})"), "1", false, Angle::Rad);
    let tiny = evaluate("1/2^126", Angle::Rad).unwrap();
    let tiny = tiny["value"].as_str().unwrap();
    assert!(tiny.starts_with("0."));
    assert_eq!(tiny.len(), 128); // 126 fractional digits, even when long division needs >i128 working space.
}

#[test]
fn scientific_functions_and_angle_domain() {
    check("sin(30)", "0.5", false, Angle::Deg);
    check("cos(180)", "-1", false, Angle::Deg);
    check("tan(-45)", "-1", false, Angle::Deg);
    check("asin(1)", "90", true, Angle::Deg);
    check("ln(e)", "1", true, Angle::Rad);
    check("log(1000)", "3", true, Angle::Rad);
    check("log10(100)", "2", true, Angle::Rad);
    check("exp(0)", "1", true, Angle::Rad);
    for (expr, want) in [
        ("sqrt(2)", std::f64::consts::SQRT_2),
        ("sin(π/2)", 1.0),
        ("cos(PI)", -1.0),
        ("acos(0)", std::f64::consts::FRAC_PI_2),
        ("atan(1)", std::f64::consts::FRAC_PI_4),
        ("tanh(0)", 0.0),
        ("sinh(1)", 1.0f64.sinh()),
        ("cosh(1)", 1.0f64.cosh()),
        ("(-8)^(1/3)", -2.0),
    ] {
        let n = evaluate(expr, Angle::Rad).unwrap();
        let x = n["value"].as_str().unwrap().parse::<f64>().unwrap();
        assert!(
            n["approximate"] == true && (x - want).abs() < 1e-12,
            "{expr}"
        );
    }
    assert!(evaluate("tan(90)", Angle::Deg).is_err());
    assert!(evaluate("tan(pi/2)", Angle::Rad).is_err());
    assert!(evaluate("tan(89.999999)", Angle::Deg).is_ok());
}

#[test]
fn syntax_domains_and_resource_limits_are_explicit() {
    for e in [
        "1/0",
        "0^0",
        "0^-1",
        "sqrt(-1)",
        "ln(0)",
        "acos(2)",
        "exp(1000)",
        "exp(-1000)",
        "(-2)^0.5",
        "34!",
        "0.2!",
        "nCr(2,3)",
        "nPr(-1,2)",
        "nCr(5000,2000)",
        "x+3",
        "exec(2)",
        "2x4",
        "2(3)",
        "pi(2)",
        "1+",
        "1e",
        "1e+",
        "()",
        "1..2",
        "1,2",
        "sqrt(1,2)",
        "log(1,2,3)",
        "NaN",
        "inf",
        "2^4097",
        "1e1000",
    ] {
        assert!(evaluate(e, Angle::Rad).is_err(), "accepted {e}");
    }
    assert!(evaluate(&format!("{}+1", i128::MAX), Angle::Rad).is_err());
    assert!(
        evaluate(
            &format!("{}1{}", "(".repeat(60), ")".repeat(60)),
            Angle::Rad
        )
        .is_err()
    );
    assert!(evaluate(&"1+".repeat(300), Angle::Rad).is_err());
    assert!(evaluate(&" ".repeat(4097), Angle::Rad).is_err());
}

#[derive(Default)]
struct Reply(Vec<Value>);
impl Model for Reply {
    fn ready(&mut self) -> Result<(), String> {
        panic!("unexpected model readiness attempt")
    }
    fn count(&mut self, _: &Request) -> Result<usize, String> {
        assert!(!self.0.is_empty(), "unexpected token count");
        Ok(80)
    }
    fn generate(&mut self, r: &Request) -> Result<String, String> {
        if let Some(schema) = &r.schema {
            assert_eq!(schema["required"], json!(["expression"]));
        } else {
            assert!(r.prompt.contains("Return only JSON matching this schema"));
        }
        Ok(self.0.remove(0).to_string())
    }
}
fn execute(input: Value, model: &mut Reply, direct: bool) -> afmcli::engine::Record {
    let job: Job = serde_json::from_value(input).unwrap();
    Engine::new(
        model,
        Policy {
            direct,
            repairs: 0,
            ..Policy::default()
        },
    )
    .execute(&job)
}

#[test]
fn local_and_interpreted_workflows_are_distinct() {
    let mut none = Reply::default();
    for args in [
        json!({"task":"calculate","expression":"3/4"}),
        json!({"task":"calc","source":"3/4"}),
    ] {
        let rec = execute(args, &mut none, true);
        assert_eq!(
            (
                rec.status.as_str(),
                rec.metrics.calls,
                rec.metrics.token_checks
            ),
            ("ok", 0, 0)
        );
    }
    for s in ["tan(90)", "1/0", "unknown(5)", "7 % 2", "2 & 3"] {
        let rec = execute(
            json!({"task":"calculate","source":s,"angle":"deg"}),
            &mut none,
            false,
        );
        assert_eq!((rec.status.as_str(), rec.metrics.calls), ("held", 0));
    }
    let natural =
        json!({"task":"calculate","source":"Three rolls cost four euros each. What is the total?"});
    #[cfg(not(feature = "trial-calculation"))]
    {
        let rec = execute(
            natural.clone(),
            &mut Reply(vec![json!({"expression":"3*4"})]),
            false,
        );
        assert_eq!((rec.status.as_str(), rec.metrics.calls), ("review", 1));
        assert_eq!(rec.result["expression"], "3*4");
        assert_eq!(rec.result["value"], "12");
    }
    let direct = execute(
        natural.clone(),
        &mut Reply(vec![json!({"expression":"3*4"})]),
        true,
    );
    assert_eq!(
        (direct.status.as_str(), direct.metrics.calls),
        ("unverified", 1)
    );
    assert!(direct.result.is_string());
    #[cfg(not(feature = "trial-calculation"))]
    for expr in ["", "wrong(12)", "1/0"] {
        let rec = execute(
            natural.clone(),
            &mut Reply(vec![json!({"expression":expr})]),
            false,
        );
        assert_eq!(rec.status, "held");
        assert!(rec.result.is_null());
    }
    for input in [
        json!({"task":"calculate"}),
        json!({"task":"calculate","expression":"1","source":"one"}),
    ] {
        assert_eq!(execute(input, &mut none, false).status, "error");
    }
}

fn cli(args: &[&str], input: &str) -> (i32, String) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_afmcli"))
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(input.as_bytes())
        .unwrap();
    let out = child.wait_with_output().unwrap();
    (
        out.status.code().unwrap(),
        String::from_utf8(out.stdout).unwrap(),
    )
}

#[test]
fn cli_stdin_file_position_json_and_batch() {
    for (args, source) in [
        (vec!["calc", "--angle", "deg", "sin(30)"], ""),
        (vec!["calc", "--from-text", "--angle", "deg"], "sin(30)\n"),
        (
            vec!["run"],
            r#"{"task":"calculate","expression":"sin(30)","angle":"deg"}"#,
        ),
    ] {
        let (code, data) = cli(&args, source);
        assert_eq!(code, 0);
        let parsed: Value = serde_json::from_str(&data).unwrap();
        assert_eq!(parsed["result"]["value"], "0.5");
        assert_eq!(parsed["metrics"]["calls"], 0);
    }
    let mut tmp = tempfile::NamedTempFile::new().unwrap();
    writeln!(tmp, "1/3").unwrap();
    let (code, data) = cli(&["-i", tmp.path().to_str().unwrap(), "calc"], "");
    assert_eq!(code, 0);
    assert_eq!(
        serde_json::from_str::<Value>(&data).unwrap()["result"]["value"],
        "1/3"
    );
    let (code, data) = cli(
        &["batch"],
        "{\"task\":\"calculate\",\"id\":\"a\",\"expression\":\"2^-2\"}\n{\"task\":\"calculate\",\"id\":\"b\",\"expression\":\"1/0\"}\n",
    );
    assert_eq!(code, 2);
    let lines: Vec<Value> = data
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    assert_eq!(lines[0]["id"], "a");
    assert_eq!(lines[0]["result"]["value"], "0.25");
    assert_eq!(lines[1]["status"], "error");
    assert_eq!(lines[1]["metrics"]["calls"], 0);
    assert_eq!(cli(&["--text", "calc", "1/3"], ""), (0, "1/3\n".into()));
    assert_eq!(cli(&["--text", "calc"], "0.1 + 0.2"), (0, "0.3\n".into()));
    assert_eq!(cli(&["--text", "calc", "1/0"], ""), (2, String::new()));
    assert_eq!(
        cli(&["--text", "calc", "--from-text"], "what is two?"),
        (2, String::new())
    );
}
