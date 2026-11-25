"""Paired correctness, coverage, token and time summaries with independent adoption gates."""
from collections import Counter, defaultdict
import math
from statistics import median

ARMS = ("records", "indexed", "guard", "checked", "handoff", "review", "staged")
FAST = ("guard", "checked", "handoff", "review")


def rate(numerator, denominator):
    return round(numerator / denominator, 5) if denominator else None


def tallies(rows, cases):
    outcomes = Counter(r["score"]["outcome"] for r in rows)
    times = sorted(r["elapsed_ms"] for r in rows)
    metrics = [r["record"]["metrics"] for r in rows]
    pairs = defaultdict(list)
    for row in rows:
        pairs[row["pair"]].append(row["score"]["correct"])
    return {
        "n": len(rows), "settled_n": sum(cases[r["id"]]["positive"] for r in rows),
        "correct": sum(r["score"]["correct"] for r in rows),
        "settled": outcomes["correct_settled"], "null": outcomes["correct_null"],
        "false_nonnull": outcomes["false_nonnull"], "outcomes": dict(sorted(outcomes.items())),
        "both_in_pair_correct": sum(len(v) == 2 and all(v) for v in pairs.values()),
        "calls": sum(m["calls"] for m in metrics), "tokens": sum(m["sent_input_tokens"] for m in metrics),
        "peak": max((m["peak_sent_tokens"] for m in metrics), default=0),
        "median_ms": median(times) if times else None,
        "p95_ms": times[math.ceil(.95 * len(times)) - 1] if times else None,
        "extra_nonauthoritative_citations": sum(r["score"]["extra_nonauthoritative_spans"] for r in rows),
    }


def mean_reader_coverage(rows, cases):
    visible, all_decisive, positive, final_values = 0, 0, 0, 0
    for row in rows:
        case = cases[row["id"]]
        usage = row["record"]["metrics"].get("context") or {}
        rounds = (usage.get("retrieval") or {}).get("rounds", [])
        exposed = [rd for rd in rounds if rd["sent"]]
        if not exposed:
            continue
        if usage.get("method") not in ("staged_ledger", "indexed_excerpts"):
            continue
        visible += 1
        source = case["job"]["source"].encode()
        ranges = [(r["start_byte"], r["end_byte"]) for r in exposed[-1]["ranges"]]

        def covered(span):
            lo, hi = span
            uncovered = bytearray(source[lo:hi])
            for left, right in ranges:
                a, b = max(lo, left), min(hi, right)
                if a < b:
                    uncovered[a-lo:b-lo] = b" " * (b-a)
            return not uncovered.strip()

        all_decisive += all(covered(s) for s in case["change_spans"])
        if case["positive"]:
            positive += 1
            final_values += any(covered(s) for s in case["gold_spans"])
    return {"observable_final_packets": visible, "packets_with_whole_decisive_passage": all_decisive,
            "observable_settled": positive, "packets_with_authoritative_value": final_values}


def trace_summary(rows, cases):
    counts = Counter()
    doc_reviews = set()
    for r in rows:
        for check in r["record"]["metrics"].get("answer_checks", []):
            case = cases[r["id"]]
            counts["first_decisions"] += 1
            if check.get("review_triggered"):
                counts["third_triggered"] += 1
            if check.get("review_sent"):
                counts["third_sent"] += 1
                doc_reviews.add(r["id"])
            if not check.get("second_state"):
                continue
            counts["second_readings"] += 1
            if not check.get("handoff_sent"):
                continue
            counts["summaries_sent"] += 1
            counts["raw_draft_bytes"] += check["draft_bytes"]
            counts["summary_bytes"] += check["handoff_bytes"]
            first, second = check["first_state"], check["second_state"]
            if first not in ("set", "unset"):
                continue
            def valid(state, value):
                return ((state == "set" and case["positive"] and value == case["gold"]) or
                        (state == "unset" and not case["positive"]))
            a, b = valid(first, check["first_value"]), valid(second, check.get("second_value"))
            counts["scorable_first_decisions_sent"] += 1
            if not a:
                counts["incorrect_first"] += 1
                if b:
                    counts["corrected_by_second"] += 1
                if first == second and check["first_value"] == check.get("second_value"):
                    counts["same_wrong_decision_repeated"] += 1
            if a and not b:
                counts["correct_first_lost_at_second"] += 1
    counts["cases_with_third_call"] = len(doc_reviews)
    return dict(sorted(counts.items()))


def compare(rows, against, method):
    by_pair = {(r["id"], r["arm"]): r for r in rows}
    wins, losses, ratios = [], [], []
    for i in sorted({r["id"] for r in rows}):
        a, b = by_pair[i, method], by_pair[i, against]
        if a["score"]["correct"] != b["score"]["correct"]:
            (wins if a["score"]["correct"] else losses).append(i)
        if b["elapsed_ms"] > 0:
            ratios.append(a["elapsed_ms"] / b["elapsed_ms"])
    return {"wins": wins, "losses": losses, "net": len(wins)-len(losses),
            "median_case_time_ratio": round(median(ratios), 5)}


def summarize(panel, rows):
    cases = {c["id"]: c for c in panel}
    subsets = {"all": rows, "long": [r for r in rows if r["tier"] != "4k"]}
    for tier in ("4k", "8k", "16k"):
        subsets[tier] = [r for r in rows if r["tier"] == tier]
    for group in sorted({c["group"] for c in panel}):
        subsets[group] = [r for r in rows if cases[r["id"]]["group"] == group]
    for lang in sorted({r["language"] for r in rows}):
        subsets[lang] = [r for r in rows if r["language"] == lang]
    stats = {k: {a: tallies([r for r in v if r["arm"] == a],cases) for a in ARMS}
             for k,v in subsets.items()}
    total, long = stats["all"], stats["long"]
    ix, rec = total["indexed"], total["records"]
    result = {}
    for a in FAST + ("staged",):
        v, l = total[a], long[a]
        quality = {"fewer_false_nonnull_than_indexed": v["false_nonnull"] < ix["false_nonnull"],
                   "settled_answers_preserved": v["settled"] >= ix["settled"],
                   "total_correct_preserved": v["correct"] >= ix["correct"],
                   "false_nonnull_at_most_records": v["false_nonnull"] <= rec["false_nonnull"]}
        if a == "staged":
            quality["total_correct_improved"] = v["correct"] > ix["correct"]
            speed = {}
        else:
            speed = {"tokens_at_most_1.05_indexed": l["tokens"] <= 1.05 * long["indexed"]["tokens"],
                     "median_at_most_1.10_indexed": l["median_ms"] <= 1.1 * long["indexed"]["median_ms"],
                     "tokens_below_records": l["tokens"] < long["records"]["tokens"],
                     "median_below_records": l["median_ms"] < long["records"]["median_ms"]}
        result[a] = {"passes": all(quality.values()) and all(speed.values()), "quality": quality,
                     "efficiency": speed, "long_input_vs_indexed": rate(l["tokens"], long["indexed"]["tokens"]),
                     "long_median_vs_indexed":rate(l["median_ms"], long["indexed"]["median_ms"]),
                     "input_saving_retained":rate(long["records"]["tokens"] - l["tokens"],
                                                   long["records"]["tokens"] - long["indexed"]["tokens"])
                     if long["records"]["tokens"] > long["indexed"]["tokens"] else None,
                     "time_saving_retained":rate(long["records"]["median_ms"] - l["median_ms"],
                                                 long["records"]["median_ms"] - long["indexed"]["median_ms"])
                     if long["records"]["median_ms"] > long["indexed"]["median_ms"] else None}
    pool = [a for a in FAST if result[a]["passes"]] or list(FAST)
    winner = min(pool, key=lambda a: (total[a]["false_nonnull"], -total[a]["settled"],
                                      long[a]["tokens"], long[a]["median_ms"], a))
    pairings = [(a, "indexed") for a in FAST + ("staged",)] + [("handoff", "checked"), ("review", "handoff")]
    return {"counts": stats, "gates": result, "fast_candidate_for_old_cases": winner,
            "paired": {a + "_vs_" + b: compare(rows, b, a) for a,b in pairings},
            "traces": {a: trace_summary([r for r in rows if r["arm"] == a],cases) for a in ARMS},
            "observable_evidence": {a: mean_reader_coverage([r for r in rows if r["arm"] == a],cases)
                                    for a in ARMS}}
