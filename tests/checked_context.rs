#![cfg(feature = "experimental-index")]

use afmcli::{
    engine::{ContextMode, Engine, Policy, Record},
    fm::{Model, Request},
    job::Job,
};
use serde_json::{Value, json};

struct Script<F>(F);
impl<F: FnMut(&Request, &Value) -> Value> Model for Script<F> {
    fn ready(&mut self) -> Result<(), String> {
        Ok(())
    }
    fn count(&mut self, req: &Request) -> Result<usize, String> {
        Ok(req.prompt.chars().count())
    }
    fn generate(&mut self, req: &Request) -> Result<String, String> {
        let (_, data) = req.prompt.split_once('\n').unwrap();
        Ok(self.0(req, &serde_json::from_str(data).unwrap()).to_string())
    }
}
fn run(
    source: &str,
    q: &str,
    mode: ContextMode,
    budget: usize,
    f: impl FnMut(&Request, &Value) -> Value,
) -> Record {
    let pol = Policy {
        compact: Some(mode),
        input_budget: budget,
        trace: true,
        repairs: 0,
        ..Policy::default()
    };
    let job = Job::Answer {
        source: source.to_owned(),
        question: q.to_owned(),
    };
    Engine::new(&mut Script(f), pol).execute(&job)
}
fn response(req: &Request, state: &str, clause: &str, value: &str) -> Value {
    let mut out = json!({"state":state,"clause":clause,"value":value});
    if req.schema.as_ref().unwrap()["properties"]
        .get("old")
        .is_some()
    {
        out["subject"] = json!("Atlas");
        out["field"] = json!("room");
        out["old"] = json!("");
        out["change"] = json!(match state {
            "set" => "assigned",
            "unset" => "withdrawn",
            _ => "none",
        });
    }
    out
}

#[test]
fn blinded_and_handoff_calls_differ_only_by_real_shorter_draft() {
    let src =
        "Atlas assigned the room called Pending Review Hall.\nThe lunch deadline is still pending.";
    let q = "What is the currently assigned room for Atlas?";
    let clause = src.lines().next().unwrap();
    let mut common = Vec::new();
    for mode in [
        ContextMode::IndexChecked,
        ContextMode::IndexHandoff,
        ContextMode::IndexHandoffReview,
    ] {
        let mut prompts = vec![];
        let rec = run(src, q, mode, 2400, |r, data| {
            let mut d = data.clone();
            if let Some(h) = d.as_object_mut().unwrap().remove("handoff") {
                assert_ne!(mode, ContextMode::IndexChecked);
                assert_eq!(h["subject"], "Atlas");
                assert_eq!(h["value"], "Pending Review Hall");
            }
            if d.get("source").is_some() {
                assert_eq!(d["source"], src);
            }
            prompts.push((
                r.prompt.split_once('\n').unwrap().0.to_owned(),
                d,
                r.schema.clone(),
            ));
            response(r, "set", clause, "Pending Review Hall")
        });
        assert_eq!(rec.status, "ok", "{:?}", rec.notes);
        assert_eq!(rec.result["answer"]["value"], "Pending Review Hall");
        assert_eq!(rec.metrics.calls, 2);
        let c = &rec.metrics.answer_checks[0];
        assert_eq!(c.first_state, "set");
        assert_eq!(c.second_state, Some("set"));
        assert_eq!(
            c.draft_bytes,
            rec.attempts[0]["response"].as_str().unwrap().len()
        );
        assert_eq!(c.draft_attempt, Some(1));
        assert!(!c.review_triggered);
        assert_eq!(c.handoff_sent, mode != ContextMode::IndexChecked);
        assert!(c.handoff_bytes < c.draft_bytes);
        if let Some(h) = &c.handoff {
            assert_eq!(h.to_string().len(), c.handoff_bytes);
        }
        if common.is_empty() {
            common = prompts;
        } else {
            assert_eq!(common, prompts);
        }
    }
}

#[test]
fn independently_read_final_revocation_uses_whole_source() {
    let original = "Atlas assigned room Pine Hall.";
    let cancel = "Atlas withdrew its room assignment without a replacement.";
    let src = format!(
        "{original}\n{}\n{cancel}",
        "The staff waited at reception.\n".repeat(39)
    );
    let r = run(
        &src,
        "Which room is assigned to Atlas?",
        ContextMode::IndexChecked,
        3000,
        |req, data| {
            if data.get("source").is_some() {
                assert_eq!(data["source"], src);
                response(req, "unset", cancel, "")
            } else {
                response(req, "set", original, "Pine Hall")
            }
        },
    );
    assert_eq!(r.status, "abstained");
    assert_eq!(r.metrics.calls, 2);
    assert_eq!(r.metrics.answer_checks[0].resolution, "source_unset");
}

#[test]
fn valid_reassignment_can_be_recovered_after_a_withdrawal() {
    let cancel = "Atlas withdrew its previous room assignment.";
    let new = "Atlas then assigned room 「未定館」.";
    let other = "The display color for Atlas is still undecided.";
    let src = [cancel, new, other].join("\r\n");
    let r = run(
        &src,
        "Which room is currently assigned to Atlas?",
        ContextMode::IndexHandoff,
        2300,
        |req, p| {
            if p.get("source").is_some() {
                response(req, "set", new, "未定館")
            } else {
                response(req, "unset", cancel, "")
            }
        },
    );
    assert_eq!(r.status, "ok", "{:?}", r.notes);
    assert_eq!(r.metrics.answer_checks[0].resolution, "later_assignment");
    let start = src.find("未定館").unwrap();
    assert_eq!(
        r.result["answer"]["spans"],
        json!([{"start_byte":start,"end_byte":start+"未定館".len()}])
    );
}

#[test]
fn repeated_or_numbered_source_never_supplies_a_status_sentence_as_a_place() {
    let bad = "Atlasの会場は審議中で、承認された会場はまだない。";
    let source = format!("{}{}", "事務局が受付簿を整えた。\n".repeat(330), bad);
    let out = run(
        &source,
        "Atlasの承認済み会場は？",
        ContextMode::IndexHandoff,
        1600,
        |r, _| response(r, "set", bad, "審議中で、承認された会場はまだない。"),
    );
    assert_eq!(out.status, "abstained");
    assert_eq!(out.metrics.context.unwrap().method, "repeat_dictionary");
    assert_eq!(out.metrics.calls, 1);
    assert_eq!(out.metrics.answer_checks[0].resolution, "no_grounded_draft");
}
#[test]
fn unrelated_field_and_tentative_candidate_are_not_values() {
    for (q, clause, value) in [
        (
            "What receipt code did Atlas receive?",
            "Atlas was assigned the color grey.",
            "grey",
        ),
        (
            "Which room did Atlas approve?",
            "Atlas proposed Hazel Hall for the briefing.",
            "Hazel Hall",
        ),
        (
            "Atlas班の確定した会場は？",
            "Atlas班は東風館を候補として提示した。",
            "東風館",
        ),
    ] {
        let out = run(clause, q, ContextMode::IndexChecked, 2200, |r, _| {
            response(r, "set", clause, value)
        });
        assert_eq!(out.status, "abstained", "{q}: {:?}", out.notes);
        assert_eq!(out.metrics.calls, 1);
    }
}

#[test]
fn agreed_unset_does_not_trigger_another_search_or_an_arbiter() {
    let mut rows = (0..120)
        .map(|i| {
            format!(
                "{} clerk filed docket {} and turned the envelope over to the librarian.",
                ["Amber", "Baker", "Crow", "Dune"][i % 4],
                i
            )
        })
        .collect::<Vec<_>>();
    let clause = "Atlas withdrew the dispatch room assignment without a replacement.";
    rows[7] = "Atlas assigned its dispatch room at Pine Hall.".into();
    rows[98] = clause.into();
    let src = rows.join("\n");
    for mode in [
        ContextMode::IndexChecked,
        ContextMode::IndexHandoff,
        ContextMode::IndexHandoffReview,
    ] {
        let out = run(
            &src,
            "Which dispatch room is currently assigned to Atlas?",
            mode,
            2400,
            |r, _| {
                assert!(r.prompt.contains("withdrew"));
                response(r, "unset", clause, "")
            },
        );
        assert_eq!(out.status, "abstained", "{:?}", out.notes);
        assert_eq!(out.metrics.calls, 2);
        let c = out.metrics.context.unwrap();
        assert_eq!(c.method, "indexed_excerpts");
        assert_eq!(c.retrieval.unwrap().rounds.len(), 1);
        assert!(!out.metrics.answer_checks[0].review_triggered);
    }
}
#[test]
fn the_summary_cannot_supply_a_value_absent_from_the_original_clause() {
    let clause = "Atlas selected room River Hall.";
    let src = format!("Obsolete catalog entry: Ember Hall.\n{clause}");
    let out = run(
        &src,
        "Which room did Atlas select?",
        ContextMode::IndexHandoff,
        2400,
        |r, p| {
            if p.get("source").is_some() {
                response(r, "set", "Atlas selected room Ember Hall.", "Ember Hall")
            } else {
                response(r, "set", clause, "River Hall")
            }
        },
    );
    assert_eq!(out.status, "abstained");
    assert_eq!(out.metrics.calls, 2);
    assert_eq!(out.metrics.answer_checks[0].second_state, Some("missing"));
}

#[test]
fn only_disagreement_can_trigger_the_blinded_third_reading() {
    let early = "Atlas assigned room Ash Hall.";
    let late = "Atlas assigned room Cove Hall after the relocation.";
    let src = [early, late].join("\n");
    for mode in [ContextMode::IndexHandoff, ContextMode::IndexHandoffReview] {
        let mut third = 0;
        let out = run(
            &src,
            "What room is assigned to Atlas?",
            mode,
            2400,
            |r, p| {
                if p.get("passages").is_some() {
                    third += 1;
                    assert!(p.get("handoff").is_none() && p.get("candidate").is_none());
                    assert!(r.prompt.contains(early) && r.prompt.contains(late));
                    response(r, "set", late, "Cove Hall")
                } else if p.get("source").is_some() {
                    response(r, "set", early, "Ash Hall")
                } else {
                    response(r, "set", late, "Cove Hall")
                }
            },
        );
        let check = &out.metrics.answer_checks[0];
        if mode == ContextMode::IndexHandoffReview {
            assert_eq!(
                (third, out.metrics.calls, out.status.as_str()),
                (1, 3, "ok")
            );
            assert_eq!(out.result["answer"]["value"], "Cove Hall");
            assert!(check.review_triggered && check.review_sent);
            assert_eq!(check.review_state, Some("set"));
            assert_eq!(check.resolution, "independent_confirmation");
        } else {
            assert_eq!(
                (third, out.metrics.calls, out.status.as_str()),
                (0, 2, "abstained")
            );
        }
    }
}

#[test]
fn an_older_null_or_another_subjects_warning_cannot_remove_a_later_approval() {
    let old = "Atlas team withdrew its previous room assignment.";
    let assignment = "Atlas team then assigned room No Decision Hall.";
    let other = "Beacon team has not yet approved a room.";
    let text = [old, assignment, other].join("\n");
    for (proposal, second_state) in [(old, "unset"), (other, "missing")] {
        let response_qa = run(
            &text,
            "Which room did the Atlas team assign?",
            ContextMode::IndexChecked,
            2350,
            |r, p| {
                if p.get("source").is_some() {
                    response(r, "unset", proposal, "")
                } else {
                    response(r, "set", assignment, "No Decision Hall")
                }
            },
        );
        let check = &response_qa.metrics.answer_checks[0];
        assert_eq!(check.second_state, Some(second_state));
        if second_state == "unset" {
            assert_eq!(response_qa.status, "ok");
            assert_eq!(response_qa.result["answer"]["value"], "No Decision Hall");
        }
    }
}

#[test]
fn a_newer_independent_original_clause_can_recover_a_third_value() {
    let a = "Atlas assigned room Ash Hall.";
    let b = "Atlas moved to Birch Hall.";
    let c = "Atlas confirmed the final room as Cove Hall.";
    let source = [a, b, c].join("\n");
    let out = run(
        &source,
        "Which room did Atlas confirm?",
        ContextMode::IndexHandoffReview,
        2100,
        |r, p| {
            if p.get("passages").is_some() {
                response(r, "set", c, "Cove Hall")
            } else if p.get("source").is_some() {
                response(r, "set", b, "Birch Hall")
            } else {
                response(r, "set", a, "Ash Hall")
            }
        },
    );
    assert_eq!(out.result["answer"]["value"], "Cove Hall");
    assert_eq!(out.metrics.calls, 3);
    assert_eq!(
        out.metrics.answer_checks[0].resolution,
        "independent_later_decision"
    );
}
