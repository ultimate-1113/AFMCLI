"""Locate quality losses without inferring what an uninstrumented model request contained."""
from collections import Counter, defaultdict


def qa(rows, cases_by_id, panel):
    groups=defaultdict(Counter)
    for row in rows:
        if row["stage"]!="initial" or row["grade"]["correct"]:
            continue
        case=cases_by_id[row["id"]]
        context=row["record"].get("metrics",{}).get("context") or {}
        reading=context.get("retrieval") or {}
        rounds=[q.get("ranges",[]) for q in reading.get("rounds",[]) if q.get("sent") and q.get("ranges")]
        def complete(ranges):
            counts=panel.retrieval_coverage(case,ranges)
            relevant=[v for k,v in counts.items() if k in ("primary","state_changes") and v["total"]]
            return bool(relevant) and all(v["total"]==v["seen"] for v in relevant)
        if not rounds:
            reason="no_range_telemetry"
        elif any(complete(ranges) for ranges in rounds):
            reason="all_annotated_primary_and_change_spans_in_one_read"
        elif complete([v for ranges in rounds for v in ranges]):
            reason="all_annotated_spans_across_reads_only"
        elif reading.get("fallback"):
            reason="incomplete_recorded_ranges_with_fallback"
        else:
            reason="annotated_spans_missing_from_recorded_reads"
        groups[row["arm"],row["mode"]][reason]+=1
    return {arm:{m:dict(sorted(values.items())) for (a,m),values in sorted(groups.items()) if a==arm}
            for arm in sorted({a for a,m in groups})}


def paired(rows):
    groups=defaultdict(dict)
    for r in rows:
        if r["stage"]=="initial":
            groups[r["mode"],r["id"]][r["arm"]]=r
    result=defaultdict(lambda:defaultdict(list))
    for (mode,ident),pair in sorted(groups.items()):
        b,c=(pair[a] for a in ("base","candidate"))
        for key in ("correct","serious","answered"):
            old,new=b["grade"][key],c["grade"][key]
            if old!=new:
                result[mode][key+("_gained" if new else "_removed")].append(ident)
    return {mode:dict(rows) for mode,rows in result.items()}


def qa_groups(rows, cases):
    result=defaultdict(lambda:defaultdict(Counter))
    for row in rows:
        if row["stage"]!="initial":
            continue
        case=cases[row["id"]]
        key=case["language"]+"-"+case["tier"]
        counts=result[row["arm"]][row["mode"]+"-"+key]
        positive=case["positive"]
        counts["confirmed" if positive else "unresolved"]+=1
        counts["correct_confirmed"]+=int(positive and row["grade"]["correct"])
        counts["unsupported"]+=int(not positive and row["grade"]["answered"])
        counts["correct_question"]+=int(row["grade"]["right_question"])
    return {arm:{key:dict(counts) for key,counts in cohort.items()} for arm,cohort in result.items()}


def translation_errors(rows):
    result=defaultdict(lambda:defaultdict(Counter))
    for row in rows:
        grade=row["grade"]
        counts=result[row["arm"]][row["mode"]+"-"+row["language"]]
        counts["documents"]+=1
        if not grade["answered"]:
            continue
        counts["delivered"]+=1
        counts["fully_correct"]+=int(grade["correct"])
        counts["serious"]+=int(grade["serious"])
        for key in ("action_ok","comparison_ok","direction_ok","format_ok","glossary_ok","names_ok",
                    "numbers_ok","time_ok","units_ok","window_ok"):
            counts[key.removesuffix("_ok")+"_failed"]+=int(not grade[key])
    return {arm:{key:dict(counts) for key,counts in cohort.items()} for arm,cohort in result.items()}
