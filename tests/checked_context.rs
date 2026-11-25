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

#[test]
fn a_unique_verbatim_value_can_reach_a_second_independent_read() {
    let decision = "Atlas officially assigned the room to Hazel Hall.";
    let other = "The clerks signed the envelope.";
    let source = ["点検票。", decision, other].join("\n");
    for mode in [
        ContextMode::IndexChecked,
        ContextMode::IndexHandoff,
        ContextMode::IndexHandoffReview,
    ] {
        for weak in ["Hazel Hall", "Hazel Hall.", other] {
            let out = run(
                &source,
                "Which room did Atlas assign?",
                mode,
                2100,
                |r, p| {
                    if p.get("source").is_some() {
                        if let Some(h) = p.get("handoff") {
                            assert_ne!(h["excerpt"], decision);
                            assert_ne!(h["excerpt"], other);
                        }
                        response(r, "set", decision, "Hazel Hall")
                    } else {
                        response(r, "set", weak, "Hazel Hall")
                    }
                },
            );
            assert_eq!(out.status, "ok", "{mode:?} {weak}");
            let at = source.find("Hazel Hall").unwrap();
            assert_eq!(
                out.result["answer"]["spans"],
                json!([{"start_byte":at,"end_byte":at+10}])
            );
            assert_eq!(out.metrics.calls, 2);
            let check = &out.metrics.answer_checks[0];
            assert!(check.draft_clause_recovered && !check.review_sent);
        }
    }
}

#[test]
fn weak_citations_for_ambiguous_or_unrelated_values_stop_at_the_first_read() {
    for (src, candidate) in [
        (
            "Atlas assigned room Stone Hall. A second office also assigned room Stone Hall.",
            "Stone Hall",
        ),
        ("Lumen team assigned room Stone Hall.", "Stone Hall"),
        ("Atlas team assigned the colour taupe.", "taupe"),
        ("Atlas team proposed Stone Hall for the room.", "Stone Hall"),
        ("The clerks stacked tins beside the door.", "tins"),
    ] {
        let out = run(
            src,
            "Which room did the Atlas team assign?",
            ContextMode::IndexChecked,
            2000,
            |r, _| response(r, "set", candidate, candidate),
        );
        assert_eq!(out.status, "abstained", "{src}");
        assert_eq!(out.metrics.calls, 1, "{src}");
        assert!(!out.metrics.answer_checks[0].draft_clause_recovered);
    }
}

#[test]
fn a_recovered_assignment_needs_an_independent_confirming_clause() {
    let old = "Atlas has not yet confirmed a room.";
    let new = "Atlas then officially assigned the room to Hazel Hall.";
    let src = [old, new].join("\n");
    for (state, clause, value) in [("unset", old, ""), ("set", "Hazel Hall.", "Hazel Hall")] {
        let out = run(
            &src,
            "Which room did Atlas assign?",
            ContextMode::IndexHandoff,
            1900,
            |r, p| {
                if p.get("source").is_some() {
                    response(r, state, clause, value)
                } else {
                    response(r, "set", "Hazel Hall", "Hazel Hall")
                }
            },
        );
        assert_eq!(out.status, "abstained", "{clause}");
        assert!(out.metrics.answer_checks[0].draft_clause_recovered);
        assert_eq!(out.metrics.calls, 2);
    }
}

#[test]
fn ordinary_initial_capitalization_uses_the_source_spelling_of_the_independent_decision() {
    let assignment = "The panel finish that Atlas approved is cloud grey.";
    let mention = "Cloud grey was the finish visible on the panel.";
    let src = [assignment, mention].join("\n");
    for mode in [
        ContextMode::IndexChecked,
        ContextMode::IndexHandoff,
        ContextMode::IndexHandoffReview,
    ] {
        let out = run(
            &src,
            "Which panel finish did Atlas approve?",
            mode,
            1800,
            |r, p| {
                if p.get("source").is_some() {
                    response(r, "set", assignment, "cloud grey")
                } else {
                    response(r, "set", mention, "Cloud grey")
                }
            },
        );
        assert_eq!(out.metrics.calls, 2);
        assert_eq!(out.result["answer"]["value"], "cloud grey");
        assert_eq!(
            out.result["answer"]["spans"][0]["start_byte"],
            src.find("cloud grey").unwrap()
        );
        assert_eq!(
            out.metrics.answer_checks[0].resolution,
            "source_spelling_agreement"
        );
    }
}

#[test]
fn capitalization_of_identifiers_and_an_unrelated_citation_remain_disagreements() {
    for (q, earlier, later, lower, upper) in [
        (
            "Which reference code did Atlas issue?",
            "The official code Atlas issued is cloud grey.",
            "Cloud grey was another written code.",
            "cloud grey",
            "Cloud grey",
        ),
        (
            "What panel finish was chosen?",
            "The chosen panel finish is bc-5.",
            "Bc-5 was recorded on the sample.",
            "bc-5",
            "Bc-5",
        ),
        (
            "What panel finish was chosen?",
            "The clerks noticed cloud grey.",
            "Cloud grey was the finish on the panel.",
            "cloud grey",
            "Cloud grey",
        ),
        (
            "What panel finish was chosen?",
            "The chosen panel finish is cloud grey.",
            "CLOUD GREY was printed on the sample.",
            "cloud grey",
            "CLOUD GREY",
        ),
    ] {
        let src = [earlier, later].join("\n");
        let out = run(&src, q, ContextMode::IndexChecked, 1950, |r, p| {
            if p.get("source").is_some() {
                response(r, "set", earlier, lower)
            } else {
                response(r, "set", later, upper)
            }
        });
        assert_eq!(out.status, "abstained", "{q} {upper}");
        assert_eq!(out.metrics.answer_checks[0].resolution, "disagreement");
    }
}
