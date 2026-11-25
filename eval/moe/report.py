"""Summarize paired cases using their frozen, replayable scores."""
from collections import Counter
import math
from statistics import median


def choose(rows):
    by_group = []
    for group in sorted({r["group"] for r in rows}):
        relevant = [r for r in rows if r["group"] == group]
        arms = {arm: totals([r for r in relevant if r["arm"] == arm])
                for arm in ("candidate", "current")}
        candidate, current = arms["candidate"], arms["current"]
        if candidate["serious"] <= current["serious"]:
            net = candidate["correct"] - current["correct"]
            by_group.append((-net, candidate["input_tokens"], group))
    return min(by_group)[2] if by_group else None


def totals(rows):
    from study import final_record
    times = sorted(r["elapsed_ms"] for r in rows)
    outcomes = Counter(r["score"].get("outcome") or (
        "correct" if r["score"]["correct"] else "serious_error" if r["score"]["serious"] else "incomplete")
        for r in rows)
    statuses = Counter(final_record(r)["status"] for r in rows)
    return {"n": len(rows), "correct": sum(bool(r["score"]["correct"]) for r in rows),
            "serious": sum(bool(r["score"]["serious"]) for r in rows),
            "unsupported": sum(bool(r["score"].get("unsupported")) or
                               r["score"].get("outcome") in ("unsupported_answer", "wrong_answer", "wrong_citation")
                               for r in rows),
            "input_tokens": sum(r["input_tokens"] for r in rows),
            "peak_tokens": max((r["peak_tokens"] for r in rows), default=0),
            "calls": sum(r["calls"] for r in rows), "token_checks": sum(r["token_checks"] for r in rows),
            "median_ms": median(times) if times else None,
            "p95_ms": times[math.ceil(len(times) * .95) - 1] if times else None,
            "outcomes": dict(sorted(outcomes.items())), "statuses": dict(sorted(statuses.items()))}


def paired(rows, arm, base):
    a = {r["id"]: r for r in rows if r["arm"] == arm}
    b = {r["id"]: r for r in rows if r["arm"] == base}
    if set(a) != set(b):
        raise ValueError("unpaired panels")
    wins, losses, by_group = [], [], Counter()
    for i in a:
        delta = int(a[i]["score"]["correct"]) - int(b[i]["score"]["correct"])
        by_group[a[i]["group"]] += delta
        if delta > 0:
            wins.append(i)
        if delta < 0:
            losses.append(i)
    return {"net": len(wins) - len(losses), "wins": sorted(wins), "losses": sorted(losses),
            "net_by_family": dict(sorted(by_group.items()))}


def diagnostics(track, rows):
    if track == "general":
        chosen = {}
        for r in rows:
            if r["arm"] != "selected":
                continue
            flags = r["steps"][0]["flags"]
            name = flags[flags.index("--skill") + 1] if "--skill" in flags else "no_hint"
            chosen.setdefault(name, set()).add(r["id"])
        return {name: paired([r for r in rows if r["id"] in ids], "selected", "current")
                for name, ids in sorted(chosen.items())}
    if track == "context":
        details = {}
        for arm in sorted({r["arm"] for r in rows}):
            scores = [r["score"] for r in rows if r["arm"] == arm]
            measured = [s["reader_evidence_recall"] for s in scores if s["reader_evidence_recall"] is not None]
            retrieved = [s["retrieval_recall"] for s in scores if s["retrieval_recall"] is not None]
            details[arm] = {"known_reader_coverage_cases": len(measured),
                            "mean_known_reader_evidence_recall": math.fsum(measured) / len(measured) if measured else None,
                            "indexed_cases": len(retrieved),
                            "mean_index_evidence_recall": math.fsum(retrieved) / len(retrieved) if retrieved else None,
                            "fallback_reasons": dict(Counter(s["fallback"] for s in scores if s["fallback"])),
                            "retrieval_rounds": dict(Counter(s["retrieval_rounds"] for s in scores))}
        return details
    keys = ("answered", "repair_attempted", "units_fixed", "good_units_broken",
            "numeric_preservation", "unit_preservation", "name_preservation")
    return {arm: {k: sum(int(r["score"].get(k, 0)) for r in rows
                         if r["group"] == "translation" and r["arm"] == arm) for k in keys}
            for arm in sorted({r["arm"] for r in rows if r["group"] == "translation"})}


def track_report(track, rows):
    arms = sorted({r["arm"] for r in rows})
    # A code-only table control applies only to this group in the development split.
    stats = {a: totals([r for r in rows if r["arm"] == a]) for a in arms}
    by_group = {g: {a: totals([r for r in rows if r["group"] == g and r["arm"] == a])
                    for a in sorted({r["arm"] for r in rows if r["group"] == g})}
                for g in sorted({r["group"] for r in rows})}
    groups = {r["id"]: r["group"] for r in rows}
    comparisons = {}
    for a in arms:
        if a in ("direct", "current"):
            continue
        ids = {r["id"] for r in rows if r["arm"] == a}
        panel = [r for r in rows if r["id"] in ids]
        comparisons[a] = {b: paired(panel, a, b) for b in ("current", "direct")}
    if track == "context":
        for tier in ("4k", "8k", "16k"):
            subset = [r for r in rows if r["id"].split("-")[2] == tier]
            by_group[tier] = {a: totals([r for r in subset if r["arm"] == a]) for a in arms}
    return {"n_cases": len(groups), "arms": stats, "by_group": by_group,
            "paired": comparisons, "diagnostics": diagnostics(track, rows)}


def report(output):
    from study import TRACKS, data_path, read_rows
    parts = {f"{track}_{split}": track_report(track, read_rows(data_path(output, track, split)))
             for track in TRACKS for split in ("dev", "test")}
    gates = {}
    for track, arm in (("general", "selected"), ("context", "indexed"), ("special", "candidate")):
        study = parts[f"{track}_test"]
        if not study["arms"]:
            gates[track] = {"adopt": False, "reason": "no eligible specialty"}
            continue
        candidate, base = study["arms"][arm], study["arms"]["current"]
        delta = study["paired"][arm]["current"]
        if track == "context":
            checks = {"accuracy": delta["net"] >= 0,
                      "unsupported": candidate["unsupported"] <= base["unsupported"],
                      "false_positive_on_absent": candidate["outcomes"].get("unsupported_answer", 0)
                      <= base["outcomes"].get("unsupported_answer", 0),
                      "total_input": candidate["input_tokens"] * 5 <= base["input_tokens"] * 4}
        else:
            checks = {"accuracy": delta["net"] >= 3, "serious": candidate["serious"] <= base["serious"],
                      "median": candidate["median_ms"] <= 1.25 * base["median_ms"]}
            if track == "general":
                checks["families"] = sum(x > 0 for x in delta["net_by_family"].values()) >= 2
        gates[track] = {"adopt": all(checks.values()), "checks": checks}
    return {"specialty": choose(read_rows(data_path(output, "special", "dev"))),
            "gates": gates, "panels": parts}
