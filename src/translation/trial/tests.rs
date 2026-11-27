use super::*;
use crate::{engine::Policy, fm::Model};
use std::collections::VecDeque;

#[derive(Default)]
struct Fake {
    replies: VecDeque<String>,
    sent: Vec<Request>,
}

impl Model for Fake {
    fn ready(&mut self) -> Result<(), String> {
        Ok(())
    }
    fn count(&mut self, _: &Request) -> Result<usize, String> {
        Ok(10)
    }
    fn generate(&mut self, request: &Request) -> Result<String, String> {
        self.sent.push(request.clone());
        self.replies
            .pop_front()
            .ok_or("unexpected generation".into())
    }
}

fn input(sentences: &[&str]) -> Translation {
    serde_json::from_value(
        json!({"source_language":"en","target_language":"ja","sentences":sentences}),
    )
    .unwrap()
}

fn simulate(
    input: &Translation,
    mode: TranslationMode,
    flags: (bool, bool),
    replies: &[Value],
    policy: Policy,
) -> (Output, Vec<Request>, usize) {
    let mut fake = Fake {
        replies: replies
            .iter()
            .map(|v| v.as_str().map_or_else(|| v.to_string(), str::to_owned))
            .collect(),
        ..Fake::default()
    };
    let mut engine = Engine::new(&mut fake, policy);
    let output = run(&mut engine, input, mode, flags.0, flags.1).unwrap();
    let repairs = engine.metrics.repairs;
    assert_eq!(engine.metrics.calls, fake.sent.len());
    (output, fake.sent, repairs)
}

#[test]
fn ids_reorder_fields_keep_natural_splits_and_reject_ambiguous_membership() {
    let t = input(&["It rained, so we stayed inside.", "The clouds cleared."]);
    let row = json!({"row02":"雲は晴れた。","row01":"雨が降った。\nそのため屋内にいた。"});
    let (draft, requests, repairs) = simulate(
        &t,
        TranslationMode::Draft,
        (true, false),
        &[row],
        Policy::default(),
    );
    assert_eq!(
        (draft.status, draft.value, repairs),
        (
            "unverified",
            json!("雨が降った。 そのため屋内にいた。\n雲は晴れた。"),
            0
        )
    );
    assert_eq!(requests.len(), 1);
    contract::check(requests[0].schema.as_ref().unwrap()).unwrap();
    for bad in [
        json!({"row01":"一行だけ。"}),
        json!({"row01":"雨。","row02":"晴れ。","other":"余分。"}),
        json!(r#"{"row01":"雨。","row01":"風。","row02":"晴れ。"}"#),
        json!({"row01":"","row02":"晴れ。"}),
        json!({"row01":["a","b"],"row02":"晴れ。"}),
    ] {
        let (out, requests, repairs) = simulate(
            &t,
            TranslationMode::Draft,
            (true, false),
            &[bad],
            Policy::default(),
        );
        assert_eq!(
            (out.status, out.value, requests.len(), repairs),
            ("held", Value::Null, 1, 0)
        );
    }
}

#[test]
fn protected_terms_preserve_width_binding_counts_and_local_ownership() {
    let mut t = input(&[
        "ＭＴＲＥＦ reports −１，２００．５㎏ of TEST SAMPLE for Zoë.",
        "Zoë reports 2 kg.",
    ]);
    t.names = vec!["Zoë".into()];
    t.units = vec!["kg".into()];
    t.glossary.insert("test sample".into(), "試料".into());
    let batch = Batch::new(&t, false, true);
    assert_eq!(batch.namespace, "MTREFX");
    let row = &batch.rows[0];
    assert_eq!(
        row.text,
        "ＭＴＲＥＦ reports [[MTREFX1]] of [[MTREFX2]] for [[MTREFX3]]."
    );
    let correct = "[[MTREFX3]]向けの[[MTREFX2]]を[[MTREFX1]]と報告。";
    assert_eq!(
        row.restore(correct, &batch.namespace).unwrap(),
        "Zoë向けの試料を−１，２００．５㎏と報告。"
    );
    assert!(
        row.restore("Zoë向けの試料を−１，２００．５㎏と報告。", &batch.namespace)
            .is_ok()
    );
    for wrong in [
        "[[MTREFX3]]向けの[[MTREFX2]]を報告。",
        "[[MTREFX4]]向けの[[MTREFX2]]を[[MTREFX1]]と報告。",
        "[[MTREFX3]]向けの[[MTREFX2]]を[[MTREFX1]][[MTREFX1]]と報告。",
        "[[MTREFX3]]向けの[[MTREFX2]]を[[MTREFX1]]と報告。ＭＴＲＥＦＸ７",
    ] {
        assert!(row.restore(wrong, &batch.namespace).is_err(), "{wrong}");
    }
    let mut overlapping = input(&["Luna Park met Luna Park."]);
    overlapping.names = vec!["Luna".into(), "Luna Park".into()];
    let b = Batch::new(&overlapping, true, true);
    assert_eq!(b.rows[0].text, "[[MTREF1]] met [[MTREF2]].");
    assert!(b.rows[0].restore("[[MTREF1]]のみ。", &b.namespace).is_err());
    assert!(
        b.rows[0]
            .restore("[[MTREF1]]とLuna Park。", &b.namespace)
            .is_ok()
    );
}

#[test]
fn draft_checks_direct_spellings_and_cannot_leak_a_foreign_marker() {
    let mut t = input(&["Move 3 kg to the shed.", "Move 7 kg to the yard."]);
    t.units = vec!["kg".into()];
    for raw in [
        "納屋に3 lbを移す。\n庭に7 kgを移す。",
        "納屋に[[MTREF2]]を移す。\n庭に[[MTREF1]]を移す。",
        "納屋に3 kgと3 kgを移す。\n庭に7 kgを移す。",
    ] {
        let (out, requests, repairs) = simulate(
            &t,
            TranslationMode::Draft,
            (false, true),
            &[json!(raw)],
            Policy::default(),
        );
        assert_eq!(
            (out.status, out.value, requests.len(), repairs),
            ("held", Value::Null, 1, 0)
        );
    }
    let raw = "納屋に[[MTREF1]]を移す。\n庭に7 kgを移す。";
    let (out, requests, _) = simulate(
        &t,
        TranslationMode::Draft,
        (false, true),
        &[json!(raw)],
        Policy::default(),
    );
    assert_eq!(
        (out.status, out.value),
        ("unverified", json!("納屋に3 kgを移す。\n庭に7 kgを移す。"))
    );
    assert!(requests[0].schema.is_none());
}

#[test]
fn checked_repairs_every_failed_row_in_one_call_and_rechecks() {
    let t = input(&[
        "Do not close the gate.",
        "We saw a cloud.",
        "Open the valve after the inspection.",
    ]);
    let draft = json!({"row01":"門を閉める。","row02":"雲を見た。","row03":"検査の前に弁を開く。"});
    let patch = json!({"row03":"検査の後に弁を開く。","row01":"門を閉めてはならない。"});
    let policy = Policy {
        repairs: 5,
        max_calls: 9,
        ..Policy::default()
    };
    let (out, calls, repairs) = simulate(
        &t,
        TranslationMode::Checked,
        (true, true),
        &[draft.clone(), patch],
        policy,
    );
    assert_eq!(
        (out.status, out.value, calls.len(), repairs),
        (
            "ok",
            json!("門を閉めてはならない。\n雲を見た。\n検査の後に弁を開く。"),
            2,
            1
        )
    );
    assert_eq!(
        calls[1].schema.as_ref().unwrap()["required"],
        json!(["row01", "row03"])
    );
    assert!(!calls[1].prompt.contains("cloud"));
    let bad_patch = json!({"row01":"門を開けない。","row03":"検査の後に弁を開く。"});
    let (out, calls, repairs) = simulate(
        &t,
        TranslationMode::Checked,
        (true, true),
        &[draft.clone(), bad_patch],
        policy,
    );
    assert_eq!(
        (out.status, out.value, calls.len(), repairs),
        ("held", Value::Null, 2, 1)
    );
    for policy in [
        Policy {
            repairs: 0,
            ..policy
        },
        Policy {
            max_calls: 1,
            ..policy
        },
    ] {
        let (out, calls, repairs) = simulate(
            &t,
            TranslationMode::Checked,
            (true, true),
            std::slice::from_ref(&draft),
            policy,
        );
        assert_eq!((out.status, calls.len(), repairs), ("held", 1, 0));
    }
}

#[test]
fn explicit_bounds_actions_and_events_are_checked_in_both_languages() {
    let to_ja = input(&["x"]);
    let to_en = Translation {
        source_language: "ja".into(),
        target_language: "en".into(),
        ..to_ja.clone()
    };
    for (en, ja, wrong) in [
        (
            "Use at least 5 kg.",
            "5 kg以上を使用する。",
            "5 kg以下を使用する。",
        ),
        (
            "Use no more than 5 kg.",
            "5 kg以下を使用する。",
            "5 kg未満を使用する。",
        ),
        (
            "Use less than 5 kg.",
            "5 kg未満を使用する。",
            "5 kgを超えて使用する。",
        ),
        (
            "Use at least 3 kg and less than 9 kg.",
            "3 kg以上、9 kg未満を使う。",
            "3 kg未満、9 kg以上を使う。",
        ),
        (
            "Do not open the gate.",
            "門を開けてはいけない。",
            "門を開ける。",
        ),
        (
            "Open the gate after the inspection.",
            "検査の後に門を開ける。",
            "検査の前に門を開ける。",
        ),
        ("Increase the voltage.", "電圧を上げる。", "電圧を下げる。"),
        (
            "Open the gate before starting the pump.",
            "門を開けてからポンプを起動する。",
            "門を開ける前にポンプを起動する。",
        ),
        (
            "Nova is taller than Vega.",
            "VegaはNovaより低い。",
            "NovaはVegaより低い。",
        ),
        (
            "The roof is higher than the fence.",
            "屋根は柵より高い。",
            "屋根は柵より低い。",
        ),
        (
            "The meeting is before 09:15 and the test is after 18:40.",
            "会議は09:15より前で、試験は18:40より後だ。",
            "会議は09:15より後で、試験は18:40より前だ。",
        ),
        (
            "Open the gate after Monday and close it after Friday.",
            "月曜日以降に門を開け、金曜日以降に門を閉める。",
            "金曜日以降に門を開け、月曜日以降に門を閉める。",
        ),
    ] {
        assert!(
            direction::issues(en, ja, &to_ja).is_empty(),
            "{en} / {ja}: {:?}",
            direction::issues(en, ja, &to_ja)
        );
        assert!(
            direction::issues(ja, en, &to_en).is_empty(),
            "{ja} / {en}: {:?}",
            direction::issues(ja, en, &to_en)
        );
        assert!(
            !direction::issues(en, wrong, &to_ja).is_empty(),
            "{en} / {wrong}"
        );
        assert!(
            !direction::issues(wrong, en, &to_en).is_empty(),
            "{wrong} / {en}"
        );
    }
}
