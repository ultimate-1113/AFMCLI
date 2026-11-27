#![cfg(any(
    feature = "trial-qa-preview",
    feature = "trial-qa-lookup",
    feature = "trial-qa-cautious"
))]
use afmcli::{
    engine::{AnswerMode, Engine, Policy, Record},
    fm::{Model, Request},
    job::Job,
};
use serde_json::{Value, json};

struct Mock<F> {
    answer: F,
    sent: Vec<Request>,
}
impl<F: FnMut(usize, &Request) -> Value> Model for Mock<F> {
    fn ready(&mut self) -> Result<(), String> {
        Ok(())
    }
    fn count(&mut self, _: &Request) -> Result<usize, String> {
        Ok(200)
    }
    fn generate(&mut self, r: &Request) -> Result<String, String> {
        let answer = (self.answer)(self.sent.len(), r);
        self.sent.push(r.clone());
        Ok(answer.to_string())
    }
}
fn run(
    q: &str,
    s: &str,
    mode: AnswerMode,
    answer: impl FnMut(usize, &Request) -> Value,
) -> (Record, Vec<Request>) {
    let mut model = Mock {
        answer,
        sent: Vec::new(),
    };
    let rec = Engine::new(
        &mut model,
        Policy {
            trace: true,
            ..Policy::default()
        },
    )
    .execute_with_answer_mode(
        &Job::Answer {
            question: q.into(),
            source: s.into(),
        },
        Some(mode),
    );
    (rec, model.sent)
}
#[cfg(any(feature = "trial-qa-preview", feature = "trial-qa-cautious"))]
fn payload(r: &Request) -> Value {
    serde_json::from_str(r.prompt.split_once('\n').unwrap().1).unwrap()
}
#[cfg(any(feature = "trial-qa-lookup", feature = "trial-qa-cautious"))]
fn ruling(r: &Request, state: &str, row: usize, value: &str) -> Value {
    let mut v = json!({"state":state,"passage":row,"value":value});
    if r.schema.as_ref().unwrap()["properties"]
        .get("clarification")
        .is_some()
    {
        v["alternative"] = json!("");
        v["clarification"] = json!("");
    }
    v
}

#[cfg(feature = "trial-qa-preview")]
#[test]
fn preview_balances_distant_linked_changes_and_obeys_both_caps() {
    let mut text: Vec<_> = (0..140)
        .map(|i| {
            format!(
                "Ledger item {i}: an unrelated dispatch was withdrawn pending an inventory count."
            )
        })
        .collect();
    text[8] = "The shale test for Project Linnet approved the amber seal.".into();
    text[66] = "Subject: Project Linnet shale test".into();
    text[67] = "That approval was withdrawn without replacement.".into();
    text[128] = "Project Linnet reinstated the amber seal for the shale test.".into();
    let s = text.join("\n");
    let (out, sent) = run(
        "Which seal was approved for the shale test for Project Linnet?",
        &s,
        AnswerMode::Preview,
        |_, r| {
            let data = payload(r);
            let rows = data["passages"].as_array().unwrap();
            let bytes = rows
                .iter()
                .map(|p| p[1].as_str().unwrap().len())
                .sum::<usize>();
            assert!(rows.len() <= 14 && bytes <= 5600);
            for at in [8, 66, 67, 128] {
                assert!(
                    rows.iter().any(|p| p[1] == text[at]),
                    "missing {at}: {rows:?}"
                );
            }
            let id = rows.iter().find(|p| p[1] == text[8]).unwrap()[0].clone();
            json!({"state":"set","passage":id,"quote":text[8],"value":"amber"})
        },
    );
    assert_eq!((out.status.as_str(), sent.len()), ("review", 1));
    assert_eq!(
        out.result["answer"]["spans"][0]["start_byte"],
        s.rfind("amber").unwrap()
    );
    let rounds = &out
        .metrics
        .context
        .as_ref()
        .unwrap()
        .retrieval
        .as_ref()
        .unwrap()
        .rounds;
    assert_eq!(rounds.len(), 1);
    assert!(rounds[0].sent);
    for (p, r) in payload(&sent[0])["passages"]
        .as_array()
        .unwrap()
        .iter()
        .zip(&rounds[0].ranges)
    {
        assert_eq!(p[1], s[r.start_byte..r.end_byte]);
        assert_eq!(r.first_segment_id, r.last_segment_id);
    }
}

#[cfg(feature = "trial-qa-lookup")]
#[test]
fn lookup_distinguishes_a_withdrawal_another_field_and_a_reinstatement() {
    let q = "Which receipt code is current for the Topaz team?";
    let initial = "The Topaz team was assigned receipt code TZ-101.";
    for (change, expects) in [
        ("The Topaz team receipt code was withdrawn.", false),
        ("The Topaz team shipping code was withdrawn.", true),
        ("The Onyx team receipt code was withdrawn.", true),
        (
            "The Topaz team receipt code was withdrawn.\nThe Topaz team reinstated receipt code TZ-101.",
            true,
        ),
    ] {
        let s = format!("{initial}\n{change}");
        let (out, _) = run(q, &s, AnswerMode::Lookup, |_, r| {
            ruling(r, "set", 1, "TZ-101")
        });
        if expects {
            assert_eq!(out.status, "ok", "{change}");
            assert_eq!(
                out.result["answer"]["spans"][0]["start_byte"],
                s.rfind("TZ-101").unwrap()
            );
        } else {
            assert_eq!(out.status, "abstained");
            assert_eq!(out.result, json!({"answer":null}));
        }
    }
}

#[cfg(feature = "trial-qa-cautious")]
#[test]
fn cautious_recovers_a_supported_assignment_with_independent_source_and_exact_ranges() {
    let q = "Which tray is assigned to the Marlin team?";
    let s = "The Marlin team was assigned tray L-28.\nThe Plover team was assigned tray R-39.";
    let (out, sent) = run(q, s, AnswerMode::Cautious, |k, r| {
        if k == 0 {
            ruling(r, "missing", 0, "")
        } else {
            ruling(r, "set", 1, "L-28")
        }
    });
    assert_eq!(out.status, "ok");
    assert_eq!(out.result["answer"]["value"], "L-28");
    assert_eq!(sent.len(), 2);
    let rounds = out.metrics.context.unwrap().retrieval.unwrap().rounds;
    assert_eq!(rounds.len(), 2);
    for (r, round) in sent.iter().zip(&rounds) {
        let p = payload(r);
        assert!(p.get("handoff").is_none() && p.get("candidate").is_none());
        let rows = p["passages"].as_array().unwrap();
        assert!(round.sent);
        assert_eq!(rows.len(), round.ranges.len());
        for (entry, range) in rows.iter().zip(&round.ranges) {
            assert_eq!(entry[1], s[range.start_byte..range.end_byte]);
        }
    }
}
#[cfg(feature = "trial-qa-cautious")]
#[test]
fn anchored_historical_and_explicit_hypothetical_have_source_values() {
    for (q, s, value) in [
        (
            "What code did the Heron team use before the withdrawal?",
            "The Heron team used code HG-72.\nThe Heron team code was withdrawn.",
            "HG-72",
        ),
        (
            "If the indoor schedule is selected, which room applies?",
            "If the indoor schedule is selected, the room is Fern Hall.",
            "Fern Hall",
        ),
    ] {
        let (out, _) = run(q, s, AnswerMode::Cautious, |_, r| {
            ruling(r, "set", 1, value)
        });
        assert_eq!(out.status, "ok", "{q}");
        assert_eq!(out.result["answer"]["value"], value);
    }
}
#[cfg(feature = "trial-qa-cautious")]
#[test]
fn clarification_requires_original_user_resolvable_selectors_and_excludes_pending() {
    for (q, s, state, first, alt, expected) in [
        (
            "Which checkpoint should I use for my visit?",
            "Before 1 March, use checkpoint A.\nFrom 1 March, use checkpoint B.",
            "date",
            "Before 1 March",
            "From 1 March",
            true,
        ),
        (
            "Which desk should I use?",
            "The north office uses desk 18.\nThe south office uses desk 41.",
            "target",
            "north office",
            "south office",
            true,
        ),
        (
            "受付の窓口はどこですか？",
            "通常の場合は東窓口を使う。\n夜間の場合は西窓口を使う。",
            "condition",
            "通常",
            "夜間",
            true,
        ),
        (
            "Which desk should I use for the north office?",
            "The north office uses desk 18.\nThe south office uses desk 41.",
            "target",
            "north office",
            "south office",
            false,
        ),
        (
            "Which desk should I use?",
            "The north office uses desk 18.\nThe south office uses desk 41.",
            "target",
            "north office",
            "absent office",
            false,
        ),
        (
            "Which permit was approved for the Heron team?",
            "The Heron team permit is pending until 1 March.",
            "date",
            "1 March",
            "",
            false,
        ),
    ] {
        let (out, _) = run(
            q,
            s,
            AnswerMode::Cautious,
            |_, _| json!({"state":state,"passage":1,"value":first,"alternative":alt,"clarification":""}),
        );
        assert_eq!(out.status, "abstained", "{q}");
        assert!(out.result["answer"].is_null());
        assert_eq!(
            out.result.get("clarification").is_some(),
            expected,
            "{q}: {}",
            out.result
        );
        if expected {
            let ask = out.result["clarification"].as_str().unwrap();
            assert!(ask.contains(first));
            assert_eq!(ask.matches(['?', '？']).count(), 1);
        }
    }
}

#[cfg(feature = "trial-qa-cautious")]
#[test]
fn an_identified_selector_joins_the_requested_property_and_an_open_selector_asks_once() {
    let q = "I have the reservation mentioned in the report. Which pickup point did the reservation use?";
    let a = "For winter reservations, the pickup point was Cedar Station.";
    let b = "For summer reservations, the pickup point was Osprey Gate.";
    let settled = format!("{a}\nThe clerk identified the reservation as the winter edition.\n{b}");
    let (out, _) = run(q, &settled, AnswerMode::Cautious, |k, r| {
        if k == 0 {
            ruling(r, "set", 2, "winter edition")
        } else {
            ruling(r, "set", 1, "Cedar Station")
        }
    });
    assert_eq!(out.status, "ok");
    assert_eq!(out.result["answer"]["value"], "Cedar Station");
    assert_eq!(
        out.result["answer"]["spans"][0]["start_byte"],
        settled.find("Cedar Station").unwrap()
    );
    let open = format!("{a}\nThe season of the reservation was not recorded.\n{b}");
    let (out, _) = run(q, &open, AnswerMode::Cautious, |k, r| {
        if k == 0 {
            ruling(r, "set", 1, "Cedar Station")
        } else {
            ruling(r, "set", 3, "Osprey Gate")
        }
    });
    assert_eq!(out.status, "abstained");
    let ask = out.result["clarification"].as_str().unwrap();
    assert!(ask.contains("winter") && ask.contains("summer") && ask.contains("season"));
    let clarified = format!("{q} My reservation is for winter.");
    let (out, _) = run(&clarified, &open, AnswerMode::Cautious, |_, r| {
        ruling(r, "set", 1, "Cedar Station")
    });
    assert_eq!(out.status, "ok");
    assert_eq!(out.result["answer"]["value"], "Cedar Station");
}
