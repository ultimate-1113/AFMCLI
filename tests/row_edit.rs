use afmcli::{edit, job::Job};
use serde_json::{Value, json};
use std::io::Write;
use std::process::{Command, Stdio};

fn preview(document: &Value, selector: Value, changes: Value) -> Result<Value, String> {
    let row = serde_json::from_value(selector).unwrap();
    let operations: Vec<edit::Change> = serde_json::from_value(changes).unwrap();
    edit::apply_to_row(document, &row, &operations)
}

fn selector() -> Value {
    json!({"collection":"","id_field":"key/~","id":1,"revision_field":"v/~","revision":2})
}

#[test]
fn row_identity_is_type_sensitive_and_json_pointer_escaping_is_explicit() {
    let original = json!([{"key/~":"1","v/~":2,"q/u":4},{"key/~":1,"v/~":2,"q/u":4}]);
    let op = json!([{"path":"/q~1u","op":"add","value":3,"expect":4}]);
    let expected = json!([{"key/~":"1","v/~":2,"q/u":4},{"key/~":1,"v/~":3,"q/u":7}]);
    assert_eq!(preview(&original, selector(), op).unwrap(), expected);
    for path in ["/key~1~0", "/v~1~0", "/q~2u", "/nested/0"] {
        assert!(
            preview(
                &original,
                selector(),
                json!([{"path":path,"op":"set","expect":2,"value":1}])
            )
            .is_err()
        );
    }
}

#[test]
fn no_op_preserves_the_last_revision_and_exhaustion_cannot_partially_write() {
    let original = json!([{"key/~":1,"v/~":u64::MAX,"a":0,"b":3}]);
    let mut row = selector();
    row["revision"] = json!(u64::MAX);
    let noop = json!([{"path":"/a","op":"set","expect":0,"value":0}]);
    assert_eq!(preview(&original, row.clone(), noop).unwrap(), original);
    let change = json!([{"path":"/a","op":"add","expect":0,"value":2}]);
    assert!(preview(&original, row, change).is_err());
}

#[test]
fn an_invalid_selector_never_degrades_to_a_positional_edit() {
    let job = json!({"task":"edit","document":{"x":1},"changes":[{"path":"/x","op":"set","expect":1,"value":2}]});
    assert!(serde_json::from_value::<Job>(job.clone()).is_ok());
    for row in [
        Value::Null,
        json!({}),
        json!({"collection":"","id_field":"id","id":1,"revision_field":"v","revision":-1}),
    ] {
        let mut invalid = job.clone();
        invalid["row"] = row;
        assert!(serde_json::from_value::<Job>(invalid).is_err());
    }
    let original = json!([{"key/~":1,"v/~":2,"a":5}]);
    for field in ["id", "id_field", "revision_field", "collection"] {
        let mut bad = selector();
        bad[field] = match field {
            "id" => json!(true),
            "id_field" => json!(""),
            "revision_field" => json!("key/~"),
            _ => json!("/bad~2"),
        };
        assert!(
            preview(
                &original,
                bad,
                json!([{"path":"/a","op":"set","expect":5,"value":8}])
            )
            .is_err()
        );
    }
}

#[test]
fn cli_batch_keeps_held_document_and_continues_in_order() {
    let row = selector();
    let document = json!([{"key/~":1,"v/~":2,"q":9}]);
    let mut job = json!({"id":"one","task":"edit","document":document,"row":row,"changes":[{"path":"/q","op":"add","value":1,"expect":9}]});
    let mut child = Command::new(env!("CARGO_BIN_EXE_afmcli"))
        .arg("batch")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut input = child.stdin.take().unwrap();
    writeln!(input, "{job}").unwrap();
    job["id"] = json!("two");
    job["row"]["revision"] = json!(1);
    writeln!(input, "{job}").unwrap();
    drop(input);
    let output = child.wait_with_output().unwrap();
    assert_eq!(output.status.code(), Some(3));
    let records: Vec<Value> = String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .map(|s| serde_json::from_str(s).unwrap())
        .collect();
    assert_eq!(records.len(), 2);
    assert_eq!(records[0]["id"], "one");
    assert_eq!(records[0]["result"]["document"][0]["q"], 10);
    assert_eq!(records[0]["result"]["document"][0]["v/~"], 3);
    assert_eq!(records[1]["status"], "held");
    assert_eq!(records[1]["result"]["document"], document);
}
