use afmcli::{
    document,
    engine::{ContextMode, Engine, Policy, Record},
    fm::{Model, Request},
    job::Job,
};
use serde_json::{Value, json};
use std::collections::BTreeSet;

struct Fake<F>(F);
impl<F: FnMut(&Request, &Value) -> Value> Model for Fake<F> {
    fn ready(&mut self) -> Result<(), String> {
        Ok(())
    }
    fn count(&mut self, r: &Request) -> Result<usize, String> {
        Ok(r.prompt.chars().count())
    }
    fn generate(&mut self, r: &Request) -> Result<String, String> {
        let payload = r.prompt.split_once('\n').unwrap().1;
        let payload = serde_json::from_str(payload).unwrap();
        Ok(self.0(r, &payload).to_string())
    }
}

fn run(
    source: &str,
    question: &str,
    mode: ContextMode,
    answer: impl FnMut(&Request, &Value) -> Value,
) -> Record {
    let job = Job::Answer {
        source: source.into(),
        question: question.into(),
    };
    let mut model = Fake(answer);
    let p = Policy {
        compact: Some(mode),
        input_budget: 1950,
        repairs: 0,
        trace: true,
        ..Policy::default()
    };
    Engine::new(&mut model, p).execute(&job)
}

// A number is embedded in changing, non-periodic complete sentences.
fn paperwork(n: usize) -> Vec<String> {
    let departments = ["Foyer", "Pier", "Gate", "Annex", "Lodge"];
    (0..n)
        .map(|i| {
            format!(
                "{} clerk {} delivered the labeled package before the office closed.",
                departments[i % departments.len()],
                i * 7 + 11
            )
        })
        .collect()
}

#[test]
fn corrections_and_scope_keep_original_utf8_ranges_and_ids() {
    let mut rows = vec!["# East register".to_owned(), "件名：受取場所".to_owned()];
    rows.extend(paperwork(58));
    rows[4] = "桔梗班が資料の受取場所に指定したのは白雲館。".into();
    rows[40] = "# West register".into();
    rows[41] = "日付：2025年12月11日".into();
    rows[42] = "発言者：受付当番".into();
    rows[47] = "他班の受取場所は朝霧館に変更した。".into();
    rows[56] = "桔梗班の資料受取場所を百合館に訂正する。".into();
    let source = rows.join("\r\n");
    for mode in [ContextMode::IndexOnce, ContextMode::Indexed] {
        let result = run(
            &source,
            "桔梗班の資料の受取場所は？",
            mode,
            |request, payload| {
                let excerpts = payload["material"]["indexed_excerpts"].as_array().unwrap();
                assert!(request.prompt.contains("白雲館") && request.prompt.contains("百合館"));
                assert!(request.prompt.contains("朝霧館") && request.prompt.contains("発言者"));
                assert!(excerpts.iter().any(|e| e["document"] == 1));
                assert!(excerpts.iter().any(|e| e["document"] == 2));
                if payload.get("candidate").is_some() {
                    json!({"supported":true})
                } else {
                    json!({"answer":"百合館"})
                }
            },
        );
        assert_eq!(result.status, "ok", "{:?}", result.notes);
        let ctx = result.metrics.context.unwrap();
        assert_eq!(ctx.method, "indexed_excerpts");
        let lookup = ctx.retrieval.unwrap();
        assert!(lookup.fallback.is_none());
        assert_eq!(lookup.rounds.len(), 1);
        let segments = document::segments(&source);
        for r in &lookup.rounds[0].ranges {
            assert_eq!(r.start_byte, segments[r.first_segment_id - 1].start);
            assert_eq!(r.end_byte, segments[r.last_segment_id - 1].end());
        }
        let offset = source.find("百合館").unwrap();
        assert_eq!(
            result.result["answer"]["spans"],
            json!([{"start_byte":offset,"end_byte":offset+"百合館".len()}])
        );
        assert!(result.metrics.peak_sent_tokens <= 1950);
    }
}

#[test]
fn empty_reading_expands_once_or_reads_every_window() {
    let mut rows = paperwork(89);
    rows[21] = "Clover's briefing will be held in Iris Hall.".into();
    let src = rows.join("\n");
    let segments = document::segments(&src);
    for mode in [ContextMode::IndexOnce, ContextMode::Indexed] {
        let mut ranked_reads = 0;
        let mut seen = BTreeSet::new();
        let record = run(
            &src,
            "Which hall is the Clover briefing in?",
            mode,
            |_, payload| {
                if let Some(group) = payload["sentences"].as_array() {
                    let mut ids = vec![];
                    for row in group {
                        let id = row[0].as_u64().unwrap();
                        assert!(seen.insert(id));
                        if row[1].as_str().unwrap().contains("Clover") {
                            ids.push(id);
                        }
                    }
                    json!({"ids":ids})
                } else if payload.get("candidate").is_some() {
                    json!({"supported":true})
                } else if payload["material"].get("indexed_excerpts").is_some() {
                    ranked_reads += 1;
                    json!({"answer":if ranked_reads == 1 { "" } else { "Iris Hall" }})
                } else {
                    assert_eq!(seen.len(), segments.len());
                    json!({"answer":"Iris Hall"})
                }
            },
        );
        assert_eq!(record.status, "ok", "{:?}", record.notes);
        let ctx = record.metrics.context.unwrap();
        let retrieval = ctx.retrieval.unwrap();
        if mode == ContextMode::IndexOnce {
            assert_eq!((ranked_reads, ctx.method), (1, "evidence_windows"));
            assert_eq!(retrieval.fallback, Some("insufficient_evidence"));
            assert_eq!(seen.len(), segments.len());
        } else {
            assert_eq!((ranked_reads, ctx.method), (2, "indexed_excerpts"));
            assert!(retrieval.fallback.is_none());
            assert!(seen.is_empty());
        }
        assert_eq!(retrieval.rounds.len(), ranked_reads);
        assert!(retrieval.rounds.iter().all(|r| r.sent));
        assert!(record.metrics.peak_sent_tokens <= 1950);
    }
}

#[test]
fn whole_document_question_visits_every_segment_in_original_order() {
    let src = paperwork(95).join("\r\n");
    let expected = document::segments(&src);
    for question in [
        "Across all entries, what was the highest received weight?",
        "全ての観測所の最大値は？",
    ] {
        let mut inspected = 0;
        let r = run(&src, question, ContextMode::Indexed, |_, payload| {
            let rows = payload["sentences"]
                .as_array()
                .expect("whole-document fallback");
            for row in rows {
                assert_eq!(row[0], expected[inspected].id);
                assert_eq!(row[1], expected[inspected].text);
                inspected += 1;
            }
            json!({"ids":[]})
        });
        assert_eq!(r.status, "abstained");
        assert_eq!(inspected, expected.len());
        let usage = r.metrics.context.unwrap().retrieval.unwrap();
        assert_eq!(usage.fallback, Some("whole_document_question"));
        assert!(usage.rounds.is_empty());
        assert!(r.metrics.peak_sent_tokens <= 1950);
    }
}

#[test]
fn an_omitted_span_cannot_be_published_and_absence_requires_full_scan() {
    let mut rows = paperwork(85);
    rows[15] = "The Cinnabar group submitted the weight proposal.".into();
    rows[65] = "The hidden label is ORCHID.".into();
    let src = rows.join("\n");
    let expected = document::segments(&src);
    let mut visited = 0;
    let mut reads = 0;
    let result = run(
        &src,
        "What height did the Cinnabar group confirm?",
        ContextMode::Indexed,
        |r, payload| {
            if let Some(rows) = payload["sentences"].as_array() {
                visited += rows.len();
                json!({"ids":[]})
            } else {
                reads += 1;
                assert!(!r.prompt.contains("ORCHID"));
                json!({"answer":"ORCHID"})
            }
        },
    );
    assert_eq!(result.status, "abstained", "{:?}", result.notes);
    assert_eq!(result.result["answer"], Value::Null);
    assert_eq!(reads, 2);
    assert_eq!(visited, expected.len());
    assert_eq!(
        result.metrics.context.unwrap().retrieval.unwrap().fallback,
        Some("invalid_or_unconfirmed_reading")
    );
}

#[test]
fn short_and_losslessly_packable_sources_do_not_use_an_index() {
    let short = run(
        "The room is B12.",
        "Which room?",
        ContextMode::Indexed,
        |_, p| {
            if p.get("candidate").is_some() {
                json!({"supported":true})
            } else {
                json!({"answerable":true,"answer":"B12"})
            }
        },
    );
    assert_eq!(short.status, "ok");
    assert!(short.metrics.context.is_none());
    for (src, method) in [
        (
            "Unit logged.\n".repeat(350) + "The room is B12.",
            "repeat_dictionary",
        ),
        (
            (0..95)
                .map(|i| {
                    format!(
                        "Patrol {i:03} visited bay {:03} and filed the completed paperwork.",
                        3 * i
                    )
                })
                .chain(["The room is B12.".into()])
                .collect::<Vec<_>>()
                .join("\n"),
            "numbered_rows",
        ),
    ] {
        let r = run(&src, "Which room?", ContextMode::Indexed, |_, p| {
            if p.get("candidate").is_some() {
                json!({"supported":true})
            } else {
                json!({"answer":"B12"})
            }
        });
        assert_eq!(r.status, "ok", "{:?}", r.notes);
        let ctx = r.metrics.context.unwrap();
        assert_eq!(ctx.method, method);
        assert!(ctx.retrieval.is_none());
    }
}
