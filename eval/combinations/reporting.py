"""Matched outcomes and measured stage usage; all statistics use whole trials."""
from collections import Counter, defaultdict
import math
from statistics import median


def counts(rows, case_map, track):
    def positive(r):
        c = case_map[r["id"]]
        return c["positive"] if track=="qa" else c["expected"] is not None
    true = [r for r in rows if positive(r)]
    missing = [r for r in rows if not positive(r)]
    times = sorted(r["elapsed_ms"] for r in rows)
    pairs = defaultdict(list)
    for r in rows:
        pairs[r["pair"]].append(r["score"]["correct"])
    def total(key):
        return sum(r["usage"][key] for r in rows)
    third = [e["raw"] for r in rows for e in r["events"] if e["kind"]=="read" and e["raw"]["limit"] == 850]
    return {"count":len(rows),"correct":sum(r["score"]["correct"] for r in rows),
            "settled_n":len(true),"settled_correct":sum(r["score"]["correct"] for r in true),
            "missing_n":len(missing),"missing_correct":sum(r["score"]["correct"] for r in missing),
            "missing_nonnull":sum(r["score"]["false_nonnull"] for r in missing),
            "both_in_pair_correct":sum(len(v)==2 and all(v) for v in pairs.values()),
            "outcomes":dict(Counter(r["score"]["outcome"] for r in rows)),
            "routes":dict(Counter(r["notes"].get("decision") or r["notes"].get("policy") or
                                  r["notes"].get("reason") or r["notes"].get("route") or "evidence stages" for r in rows)),
            "trials_with_extra_read":sum(any(e["kind"]=="read" and e["raw"]["sent"] for e in r["events"]) for r in rows),
            "trials_with_native_callback":sum(r["usage"]["native_callbacks"]>0 for r in rows),
            "sent_structured_reads":sum(e["raw"]["sent"] for r in rows for e in r["events"] if e["kind"]=="read"),
            "third_disagreements":sum("third" in r["notes"] for r in rows),
            "third_counted":len(third),"third_sent":sum(r["sent"] for r in third),
            "native_callbacks":total("native_callbacks"),"host_calc":total("host_calc"),
            "requests":total("requests"),"native_requests":total("native_requests"),
            "counted_input_tokens":total("counted_input_tokens"),
            "native_visible_prompt_tokens":total("native_visible_prompt_tokens"),
            "max_counted_input":max(r["usage"]["peak_counted_input_tokens"] for r in rows),
            "median_ms":round(median(times),3),"p95_ms":times[math.ceil(.95*len(times))-1]}


def compare(rows, method, base):
    m={r["id"]:r for r in rows if r["arm"]==method}
    b={r["id"]:r for r in rows if r["arm"]==base}
    wins=[]; losses=[]; prevented=[]; introduced=[]
    for key in sorted(m):
        a,old=m[key]["score"],b[key]["score"]
        if a["correct"] != old["correct"]:
            (wins if a["correct"] else losses).append(key)
        if a["false_nonnull"] != old["false_nonnull"]:
            (introduced if a["false_nonnull"] else prevented).append(key)
    return {"wins":wins,"losses":losses,"prevented_nonnull":prevented,"introduced_nonnull":introduced}


def report(cases,all_rows):
    data={}
    for track,rows in all_rows.items():
        by_id={c["id"]:c for c in cases[track]}
        methods=sorted({r["arm"] for r in rows})
        def group(selected):
            return {arm:counts([r for r in selected if r["arm"]==arm],by_id,track) for arm in methods}
        overall=group(rows)
        base="indexed" if track=="qa" else "native"
        old=overall[base]
        flags={}
        for a in methods:
            new=overall[a]
            if a==base:
                continue
            gates={"fewer_missing_nonnull":new["missing_nonnull"]<old["missing_nonnull"],
                   "settled_preserved":new["settled_correct"]>=old["settled_correct"]}
            if track=="qa":
                gates.update({"total_preserved":new["correct"]>=old["correct"],
                              "input_within_105pct":new["counted_input_tokens"]<=old["counted_input_tokens"]*1.05,
                              "median_within_110pct":new["median_ms"]<=old["median_ms"]*1.10})
            else:
                gates.update({"total_improved":new["correct"]>old["correct"]})
            flags[a]={"passed":all(gates.values()),"conditions":gates}
        contrasts=([(a,base) for a in methods if a!=base]+
                   ([("anchored","packet"),("arbitrate","independent"),("selective","anchored")]
                    if track=="qa" else [("native_value","native"),("evidence_formula","formula"),
                                         ("parallel","evidence_formula"),("selective","parallel")]))
        data[track]={"all":overall,"adoption":flags,
                     "paired":{a+"_vs_"+b:compare(rows,a,b) for a,b in contrasts},
                     "language":{lang:group([r for r in rows if r["language"]==lang]) for lang in ("ja","en")}}
        if track=="qa":
            data[track]["tier"]={t:group([r for r in rows if by_id[r["id"]]["tier"]==t]) for t in ("8k","16k")}
    return data
