use afmcli::engine::{Engine, Policy, Record};
use afmcli::fm::{Model, Request};
use afmcli::{contract, document, edit, fm, job::Job, json};
use serde_json::{Value, json};
use std::{collections::VecDeque, io::Write, process::Command, process::Stdio};

fn fast() -> Policy {
    Policy {
        check_support: false,
        ..Policy::default()
    }
}
struct Fake<F> {
    answers: F,
    requests: usize,
    counts: usize,
}
impl<F: FnMut(&Request) -> Result<String, String>> Model for Fake<F> {
    fn ready(&mut self) -> Result<(), String> {
        Ok(())
    }
    fn count(&mut self, r: &Request) -> Result<usize, String> {
        self.counts += 1;
        Ok(r.prompt.chars().count())
    }
    fn generate(&mut self, r: &Request) -> Result<String, String> {
        self.requests += 1;
        (self.answers)(r)
    }
}
fn eval(j: Value, p: Policy, answers: impl FnMut(&Request) -> Result<String, String>) -> Record {
    let job = serde_json::from_value(j).unwrap();
    let mut model = Fake {
        answers,
        counts: 0,
        requests: 0,
    };
    Engine::new(&mut model, p).execute(&job)
}
fn stream(values: &[Value]) -> impl FnMut(&Request) -> Result<String, String> {
    let mut queue: VecDeque<_> = values.iter().map(Value::to_string).collect();
    move |_| Ok(queue.pop_front().expect("unexpected extra model call"))
}
#[test]
fn refuse_missing_variant_or_incomplete_transcript() {
    assert!(fm::has_advanced(
        "✓ system (AFM 3 Core Advanced)\n✗ pcc (not available)"
    ));
    for s in [
        "✗ system (AFM 3 Core Advanced)",
        "✓ system (AFM 3 Core)",
        "✓ pcc (AFM 3 Core Advanced)",
        "✓ system (AFM 4 Core Advanced)",
    ] {
        assert!(!fm::has_advanced(s));
    }
    let mut t = json!({"modelName":"system","transcript":{"transcript":{"entries":[{"role":"response","finishReason":"contentCompleted","contents":[{"type":"text","text":"part1"},{"type":"text","text":"part2"}]}]}}});
    assert_eq!(fm::decode(&t).unwrap(), "part1part2");
    t["modelName"] = json!("pcc");
    assert!(fm::decode(&t).is_err());
    t["modelName"] = json!("system");
    t["transcript"]["transcript"]["entries"][0]["finishReason"] = json!("cancelled");
    assert!(fm::decode(&t).is_err());
}
#[test]
fn quotes_refer_to_all_valid_original_utf8_offsets() {
    let s = " \r\n⛺ e\u{301} / 18 / 8 / 8";
    let xs = document::locate(s, 0, "8").unwrap();
    assert_eq!(xs.len(), 2);
    for span in xs {
        assert_eq!(&s[span.start_byte..span.end_byte], "8");
    }
    assert!(document::locate(s, 0, "é").is_err());
    let rows = document::segments(s);
    for r in rows {
        assert_eq!(r.text, &s[r.start..r.end()]);
    }
    let s = "別。\n".repeat(450);
    assert_eq!(document::segments(&s).len(), 450);
}
#[test]
fn input_schema_and_instance_are_checked_independently() {
    for schema in [
        json!({"type":"object","properties":{},"required":[],"additionalProperties":true}),
        json!({"type":"object","properties":{},"required":[],"additionalProperties":false,"oneOf":[]}),
        json!({"type":"object","properties":{},"required":["typo"],"additionalProperties":false}),
    ] {
        assert!(contract::check(&schema).is_err());
    }
    let schema = contract::object([(
        "data".into(),
        json!({"type":"array","items":{"type":"integer"}}),
    )]);
    assert!(contract::check(&schema).is_ok());
    for v in [
        json!({}),
        json!({"data":[true]}),
        json!({"other":[],"data":[]}),
        json!({"data":["1"]}),
    ] {
        assert!(contract::validate(&schema, &v).is_err());
    }
    assert!(json::parse(r#"{"a":[{"v":1,"v":2}]}"#).is_err());
}
#[test]
fn bounded_feedback_repairs_exactly_once_and_preserves_source() {
    let job = json!({"task":"extract","source":"町名は若葉。","fields":[{"name":"town"}]});
    let p = Policy {
        trace: true,
        ..fast()
    };
    let mut index = 0;
    let rec = eval(job.clone(), p, |r| {
        assert!(r.prompt.contains("町名は若葉。"));
        index += 1;
        if index == 2 {
            assert!(r.prompt.contains("verbatim"));
        }
        Ok(json!({"town":if index==1{vec!["架空"]}else{vec!["若葉"]}}).to_string())
    });
    assert_eq!(rec.status, "ok");
    assert_eq!(rec.metrics.calls, 2);
    assert_eq!(rec.attempts.len(), 2);
    assert_eq!(rec.result["town"][0]["value"], "若葉");
    let rec = eval(
        job,
        p,
        stream(&[json!({"town":["架空"]}), json!({"town":["架空"]})]),
    );
    assert_eq!(rec.status, "held");
    assert!(rec.result.is_null());
    assert_eq!(rec.metrics.repairs, 1);
}
#[test]
fn missing_fields_do_not_discard_supported_values() {
    let r = eval(
        json!({"task":"extract","source":"BIN-7 is active.","fields":[{"name":"bin"},{"name":"year"}]}),
        fast(),
        stream(&[json!({"bin":["BIN-7"],"year":[]})]),
    );
    assert_eq!(r.status, "partial");
    assert_eq!(r.result["year"], json!([]));
    assert!(r.attempts.is_empty());
}
#[test]
fn native_schema_errors_and_refusals_do_not_turn_into_facts() {
    let j = json!({"task":"answer","source":"A named thing.","question":"Who?"});
    let out = eval(j.clone(), fast(), |_| Err("fm refused the request".into()));
    assert_eq!(out.status, "refused");
    assert_eq!(out.metrics.calls, 1);
    assert_eq!(out.metrics.repairs, 0);
    let no = eval(
        j.clone(),
        fast(),
        stream(&[json!({"answer":"","answerable":false})]),
    );
    assert_eq!(no.status, "abstained");
    let mismatch = eval(
        j,
        Policy {
            repairs: 0,
            ..fast()
        },
        stream(&[json!({"answer":"thing","answerable":false})]),
    );
    assert_eq!(mismatch.status, "held");
}
#[test]
fn verified_labels_belong_to_the_caller_even_if_the_fallback_name_collides() {
    let r = eval(
        json!({"task":"classify","source":"text","labels":[{"name":"NONE_OF_THE_ABOVE"},{"name":"α"}]}),
        fast(),
        stream(&[json!({"NONE_OF_THE_ABOVE":false,"α":false})]),
    );
    assert_eq!(r.status, "abstained");
    assert!(r.result["label"].is_null());
}
#[test]
fn chunked_reader_checks_the_end_and_holds_on_generation_budget() {
    let src = (0..9)
        .map(|i| {
            if i == 8 {
                "Kestrel ZQ.".into()
            } else {
                format!("Line {i} {}.", "x".repeat(70))
            }
        })
        .collect::<Vec<_>>()
        .join("\n");
    let job = json!({"task":"answer","source":src,"question":"Which code?"});
    let reply = |r: &Request| {
        let hit = r.prompt.contains("Kestrel ZQ.");
        Ok(json!({"answer":if hit{"ZQ"}else{""},"answerable":hit}).to_string())
    };
    let pol = Policy {
        input_budget: 685,
        ..fast()
    };
    let out = eval(job.clone(), pol, reply);
    assert_eq!(out.status, "ok");
    assert_eq!(out.result["answer"]["value"], "ZQ");
    assert!(out.metrics.calls > 1);
    let limited = eval(
        job,
        Policy {
            max_calls: 1,
            ..pol
        },
        reply,
    );
    assert_eq!(limited.status, "held");
    assert!(limited.result.is_null());
}
#[test]
fn contradictory_windows_are_not_resolved_by_return_order() {
    let p = Policy {
        input_budget: 675,
        ..fast()
    };
    let mut words = vec!["first ALPHA.".to_owned()];
    words.extend((0..12).map(|n| format!("Paragraph {n} {}.", "z".repeat(65))));
    words.push("last BETA.".into());
    let j = json!({"task":"answer","source":words.join("\n"),"question":"Which value?"});
    let out = eval(j, p, |r| {
        let a = if r.prompt.contains("first ALPHA.") {
            "ALPHA"
        } else if r.prompt.contains("last BETA.") {
            "BETA"
        } else {
            ""
        };
        Ok(json!({"answer":a,"answerable":!a.is_empty()}).to_string())
    });
    assert_eq!(out.status, "abstained");
    assert!(out.notes[0].contains("conflicting"));
}

#[test]
fn compact_preserves_the_short_reader_and_charges_all_sent_inputs() {
    let job = json!({"task":"answer","question":"Which room?","source":"Room N4."});
    let mut prompts = Vec::new();
    for compact in [false, true] {
        let mut sent = Vec::new();
        let result = eval(
            job.clone(),
            Policy {
                compact,
                ..Policy::default()
            },
            |r| {
                sent.push(r.prompt.clone());
                Ok(if r.schema.as_ref().unwrap()["properties"]
                    .get("supported")
                    .is_some()
                {
                    json!({"supported":true})
                } else {
                    json!({"answer":"N4","answerable":true})
                }
                .to_string())
            },
        );
        assert_eq!(result.status, "ok");
        assert!(result.metrics.context.is_none());
        assert_eq!(
            result.metrics.sent_input_tokens,
            sent.iter().map(|s| s.chars().count()).sum::<usize>()
        );
        prompts.push(sent);
    }
    assert_eq!(prompts[0], prompts[1]);
}

#[test]
fn compact_dictionary_quotes_original_bytes_and_checks_distant_updates() {
    let source = format!(
        "初報：青葉館。\n{}再決定：青葉館。\n終了。",
        "巡回済み、受付票を保存。\n".repeat(240)
    );
    let job = json!({"task":"answer","source":source,"question":"確定した会場は？"});
    let policy = Policy {
        compact: true,
        input_budget: 1200,
        ..Default::default()
    };
    for (supported, status) in [(true, "ok"), (false, "abstained")] {
        let record = eval(job.clone(), policy, |r| {
            assert!(
                r.prompt.contains("dictionary")
                    && r.prompt.contains("初報")
                    && r.prompt.contains("再決定")
            );
            Ok(if r.schema.as_ref().unwrap()["properties"]
                .get("supported")
                .is_some()
            {
                json!({"supported":supported})
            } else {
                json!({"answer":"青葉館"})
            }
            .to_string())
        });
        assert_eq!(record.status, status);
        assert_eq!(record.metrics.context.unwrap().method, "repeat_dictionary");
        if supported {
            let spans = record.result["answer"]["spans"].as_array().unwrap();
            assert_eq!(spans.len(), 2);
            for span in spans {
                let range = span["start_byte"].as_u64().unwrap() as usize
                    ..span["end_byte"].as_u64().unwrap() as usize;
                assert_eq!(&source[range], "青葉館");
            }
        }
    }
    let false_quote = eval(
        job,
        Policy {
            repairs: 0,
            ..policy
        },
        |_| Ok(json!({"answer":"dictionary"}).to_string()),
    );
    assert_eq!(false_quote.status, "held");
}

#[test]
fn compact_reads_every_window_and_cannot_quote_an_omitted_sentence() {
    let mut source = (0..27)
        .map(|i| format!("Report {i} {}.", "stored ".repeat(12)))
        .collect::<Vec<_>>();
    source[1] = "The meeting moved to Room DELTA.".into();
    source[10] = "Unrelated value UNSEEN.".into();
    source[25] = "The venue decision was withdrawn.".into();
    let job = json!({"task":"answer","question":"Which room?","source":source.join("\n")});
    let policy = Policy {
        compact: true,
        input_budget: 1200,
        repairs: 0,
        ..Policy::default()
    };
    for (answer, expected) in [("DELTA", "abstained"), ("UNSEEN", "held")] {
        let mut seen = std::collections::BTreeSet::new();
        let record = eval(job.clone(), policy, |r| {
            let payload: Value =
                serde_json::from_str(r.prompt.split_once('\n').unwrap().1).unwrap();
            if let Some(rows) = payload["sentences"].as_array() {
                let mut ids = Vec::new();
                for row in rows {
                    assert!(seen.insert(row[0].as_u64().unwrap()));
                    let text = row[1].as_str().unwrap();
                    if text.contains("DELTA") || text.contains("withdrawn") {
                        ids.push(&row[0]);
                    }
                }
                return Ok(json!({"ids":ids,"keep_all":false}).to_string());
            }
            assert_eq!(seen.len(), 27);
            assert!(r.prompt.contains("withdrawn") && r.prompt.contains("DELTA"));
            assert!(!r.prompt.contains("UNSEEN"));
            Ok(if payload.get("candidate").is_some() {
                json!({"supported":false})
            } else {
                json!({"answer":answer})
            }
            .to_string())
        });
        assert_eq!(record.status, expected);
        let usage = record.metrics.context.unwrap();
        assert_eq!(usage.method, "evidence_windows");
        assert_eq!(usage.retained_segments, 6);
        assert!(usage.selection_windows > 1);
    }
    let limited = eval(
        job,
        Policy {
            max_calls: 1,
            ..policy
        },
        |_| Ok(json!({"ids":[],"keep_all":false}).to_string()),
    );
    assert_eq!(limited.status, "held");
}

#[test]
fn compact_falls_back_instead_of_truncating_a_dense_source() {
    let source = (0..36)
        .map(|i| format!("Entry {i} {}.", "archive ".repeat(9)))
        .collect::<Vec<_>>()
        .join("\n");
    let job = json!({"task":"answer","source":source,"question":"Which seal?"});
    let record = eval(
        job,
        Policy {
            compact: true,
            input_budget: 1200,
            ..fast()
        },
        |r| {
            Ok(if r.prompt.contains("keep_all") {
                json!({"ids":[],"keep_all":true})
            } else {
                json!({"answerable":false,"answer":""})
            }
            .to_string())
        },
    );
    assert_eq!(record.status, "abstained");
    let usage = record.metrics.context.unwrap();
    assert_eq!(usage.method, "full_scan_fallback");
    assert_eq!(usage.retained_segments, 36);
}
#[test]
fn summary_copies_only_offered_ids_in_original_order() {
    let j =
        json!({"task":"summarize","source":"Bells rang.\r\n   雨が降った。\n終演。","sentences":2});
    let out = eval(
        j,
        fast(),
        stream(&[json!({"ids":[1,123]}), json!({"ids":[3,1]})]),
    );
    assert_eq!(out.status, "ok");
    assert_eq!(out.result["segments"][0]["text"], "Bells rang.");
    assert_eq!(out.result["segments"][1]["text"], "終演。");
}
#[test]
fn constraints_on_numbers_and_length() {
    use afmcli::engine::text_check;
    assert!(text_check("２個、２個、-3.5", "-3.5 / ２、２", true, None).is_ok());
    for s in ["２、-3.5", "２、２、3.5", "2、２、-3.5"] {
        assert!(text_check("２個、２個、-3.5", s, true, None).is_err());
    }
    assert!(text_check("", "氷🧊床", false, Some(2)).is_err());
}
#[test]
fn edit_tests_every_precondition_without_a_partial_write() {
    let original = json!({"x/y":[4,9],"~k":i64::MAX});
    for changes in [
        json!([{"path":"/x~1y/0","expect":4,"op":"add","value":1},{"path":"/~0k","expect":i64::MAX,"op":"add","value":1}]),
        json!([{"path":"/x~1y/0","expect":4,"op":"add","value":1},{"path":"/x~1y/0","expect":4,"op":"add","value":1}]),
        json!([{"path":"/x~2y/0","expect":4,"op":"set","value":1}]),
        json!([{"path":"/x~1y/01","expect":9,"op":"set","value":1}]),
    ] {
        let out = eval(
            json!({"task":"edit","document":original,"changes":changes}),
            fast(),
            |_| panic!("edit must not call fm"),
        );
        assert_eq!(out.status, "held");
        assert_eq!(out.result["document"], original);
    }
    let changes = serde_json::from_value::<Vec<edit::Change>>(
        json!([{"path":"/x~1y/1","expect":9,"op":"add","value":-2}]),
    )
    .unwrap();
    assert_eq!(edit::apply(&original, &changes).unwrap()["x/y"][1], 7);
}
#[test]
fn batch_cli_continues_after_bad_json_and_rejects_duplicate_keys() {
    let mut child = Command::new(env!("CARGO_BIN_EXE_afmcli"))
        .arg("batch")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(b"{bad}\n{\"task\":\"edit\",\"task\":\"ask\"}\n{\"id\":\"x\",\"task\":\"edit\",\"document\":{\"n\":3},\"changes\":[{\"path\":\"/n\",\"expect\":3,\"op\":\"add\",\"value\":1}]}\n").unwrap();
    let out = child.wait_with_output().unwrap();
    assert_eq!(out.status.code(), Some(2));
    let txt = String::from_utf8(out.stdout).unwrap();
    let records: Vec<_> = txt.lines().map(|s| json::parse(s).unwrap()).collect();
    assert_eq!(records.len(), 3);
    assert_eq!(records[0]["status"], "error");
    assert_eq!(records[1]["status"], "error");
    assert_eq!(records[2]["result"]["document"]["n"], 4);
}
#[test]
fn duplicate_labels_and_unknown_task_properties_fail_before_any_call() {
    let r = eval(
        json!({"task":"classify","source":"x","labels":[{"name":"A"},{"name":"A"}]}),
        fast(),
        |_| panic!(),
    );
    assert_eq!(r.status, "error");
    assert_eq!(r.metrics.calls, 0);
    assert!(
        serde_json::from_value::<Job>(json!({"task":"ask","instruction":"x","tyop":1})).is_err()
    );
}
