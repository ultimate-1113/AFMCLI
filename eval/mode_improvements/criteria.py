"""Group independently scored observations and evaluate the preregistered deployment gates."""
from collections import Counter, defaultdict
from math import ceil
from statistics import median


def summary(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["arm"], row["mode"]].append(row)
    result = {}
    for (arm, mode), group in sorted(groups.items()):
        first = [r for r in group if r["stage"] == "initial"]
        if not first:
            continue
        followups = [r for r in group if r["stage"] == "followup"]
        scored = lambda key: sum(bool(r["grade"].get(key)) for r in first)
        false_numbers = sum(not r["positive"] and r["grade"]["answered"] for r in first)
        asked = {r["id"] for r in first if r["grade"].get("right_question")}
        tokens = [r["record"]["metrics"].get("sent_input_tokens") for r in first]
        calls = [r["record"]["metrics"].get("calls") for r in first]
        received = Counter(r["language"] for r in first if r["grade"]["answered"])
        obs = dict(n=len(first), correct=scored("correct"), delivered=scored("answered"),
                   serious=scored("serious"), positive=sum(r["positive"] for r in first),
                   correct_positive=sum(r["positive"] and r["grade"]["correct"] for r in first),
                   unsupported=false_numbers,
                   correct_null=sum(not r["positive"] and r["grade"]["correct"] for r in first),
                   need_correct=scored("right_missing"),
                   correct_question=sum(r["askable"] and r["grade"].get("right_question",False) for r in first),
                   unnecessary_question=sum(not r["askable"] and r["grade"].get("any_question",False) for r in first),
                   followup_n=len(followups), followup_correct=sum(r["grade"]["correct"] for r in followups),
                   asked_then_answered=sum(r["id"] in asked and r["grade"]["correct"] for r in followups),
                   by_direction=dict(sorted(received.items())),
                   status=dict(sorted(Counter(r["record"]["status"] for r in first).items())),
                   max_calls=max(calls) if all(isinstance(x,int) for x in calls) else None,
                   total_calls=sum(calls) if all(isinstance(x,int) for x in calls) else None,
                   input_tokens=sum(tokens) if all(isinstance(x,int) for x in tokens) else None,
                   median_ms=median(r["elapsed_ms"] for r in first),
                   retrieval=dict(sorted(Counter(r["retrieval"] for r in first if r["retrieval"]).items())))
        result.setdefault(arm,{})[mode] = obs
    return result


def choose_translation(measured):
    """Rank development variants only; penalize misses in units of documents."""
    choices = {}
    for mode in ("draft", "checked"):
        def cost(arm):
            c = measured[arm][mode]
            n = c["n"]
            if mode == "draft":
                deficit = max(0, c["serious"] - measured["base"][mode]["serious"]/2)
                deficit += max(0, ceil(.9*n) - c["delivered"])
            else:
                deficit = c["serious"] + max(0, ceil(.6*n) - c["delivered"])
                deficit += sum(max(0, ceil(n/4) - c["by_direction"].get(lang,0)) for lang in ("en","ja"))
            calls = c["total_calls"]
            return (deficit, -c["correct"], calls if calls is not None else float("inf"), c["median_ms"], arm)
        choices[mode] = min(("ids","protection","both"), key=cost)
    return choices


def decisions(measured):
    base, candidate = measured["base"], measured["candidate"]
    results = {}
    for mode, c in candidate.items():
        b = base[mode]
        if mode == "preview":
            checks = dict(unsupported_halved=2*c["unsupported"]<=b["unsupported"],
                          confirmed_within_one=c["correct_positive"]+1>=b["correct_positive"],
                          at_most_one_call=c["max_calls"] is not None and c["max_calls"]<=1,
                          token_budget=c["input_tokens"] is not None and b["input_tokens"] is not None
                              and 100*c["input_tokens"]<=105*b["input_tokens"],
                          latency=100*c["median_ms"]<=110*b["median_ms"])
        elif mode == "lookup":
            checks = dict(confirmed_preserved=c["correct_positive"]>=b["correct_positive"],
                          unsupported_reduced=c["unsupported"]<b["unsupported"],
                          latency=100*c["median_ms"]<=110*b["median_ms"])
        elif mode == "cautious":
            checks = dict(unsupported=c["unsupported"]<=min(b["unsupported"],2),
                          confirmed_plus_two=c["correct_positive"]>=b["correct_positive"]+2,
                          useful_questions=c["correct_question"]>=10,
                          unnecessary_questions=c["unnecessary_question"]<=1,
                          latency=100*c["median_ms"]<=125*b["median_ms"])
        elif mode == "draft":
            checks = dict(delivery=c["delivered"]>=36, serious_halved=2*c["serious"]<=b["serious"],
                          at_most_one_call=c["max_calls"] is not None and c["max_calls"]<=1)
        elif mode == "checked":
            checks = dict(delivery=c["delivered"]>=24, each_direction=all(c["by_direction"].get(k,0)>=10 for k in ("en","ja")),
                          serious=c["serious"]==0, precision=c["delivered"]>0 and 10*c["correct"]>=9*c["delivered"],
                          bounded_calls=c["max_calls"] is not None and c["max_calls"]<=2)
        elif mode == "formula":
            checks = dict(confirmed=c["correct_positive"]>=8, unsupported=c["unsupported"]==0,
                          named_missing=c["need_correct"]>=10)
        else:
            raise ValueError("Unexpected evaluation mode " + mode)
        results[mode] = dict(adopt=all(checks.values()), criteria=checks)
    return results
