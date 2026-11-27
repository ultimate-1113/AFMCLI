"""Verify the complete release evidence without a model or a network connection."""
import argparse
import json
from pathlib import Path
import re

import criteria
import diagnose
import study
import select_methods


def read(path):
    return json.loads(path.read_bytes())


def save_or_verify(path, value, write):
    if write:
        path.write_text(json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    else:
        study.require(read(path)==value,"published summary differs: "+path.name)


def archive(root, lock):
    folder=root/"development-round1"
    manifest=read(folder/"manifest.json")
    study.require(manifest["baseline"]==study.BASE,"incorrect development baseline")
    for track,modes in study.MODES.items():
        panel=manifest["panels"][track]
        path=folder/(track+".jsonl")
        arms=["base","ids","protection","both"] if track=="translation" else ["base","candidate"]
        study.replay(track,"dev",list(modes),lock,path,arms)
        rows=study.load_rows(path)
        study.require(len(rows)==panel["n"] and all(
            row["binary_sha256"]==panel["binary"][row["arm"]] for row in rows.values()
        ),"archived development fingerprint differs: "+track)
    numeric=root/"number-syntax-development"
    for label,meta in read(numeric/"manifest.json").items():
        path=numeric/(label+".jsonl")
        study.require(study.sha(path.read_bytes())==meta["raw_sha256"],"changed numeric development observations")
        study.replay("calc","dev",list(study.MODES["calc"]),lock,path,["base","candidate"])
        rows=study.load_rows(path)
        study.require(len(rows)==meta["n"] and all(
            r["binary_sha256"]==meta["binary_sha256"][r["arm"]] for r in rows.values()
        ),"numeric development build differs")


def check_defaults(selection, decisions):
    flags=[]
    for mode,decision in decisions.items():
        if not decision["adopt"]:
            continue
        if mode in ("draft","checked"):
            flags.extend(f for f in select_methods.flags({mode:selection["modes"][mode]["variant"]})
                         if f.startswith("trial-"+mode+"-"))
        elif mode=="formula":
            flags.append("trial-calculation")
        else:
            flags.append("trial-qa-"+mode)
    text=(study.HERE.parents[1]/"Cargo.toml").read_text()
    match=re.search(r"^default\s*=\s*(\[[^\]]*\])",text,re.MULTILINE)
    default=json.loads(match.group(1)) if match else []
    study.require(sorted(flags)==sorted(f for f in default if f.startswith("trial-")),
                  "shipping trial features differ from held-out decisions")


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results",type=Path,default=study.HERE/"results")
    p.add_argument("--write-summaries",action="store_true")
    p.add_argument("--check-defaults",action="store_true",help="match the production features to the bundled trial")
    args=p.parse_args()
    root=args.results
    lock=study.lock_check()
    archive(root,lock)
    selection=read(root/"selection.json")
    final_decisions={}
    counts={}
    differences={}
    for track,modes in study.MODES.items():
        dev=root/"dev"/(track+".jsonl")
        test=root/"test"/(track+".jsonl")
        study.require(study.sha(dev.read_bytes())==selection["development_sha256"][track],"development data changed")
        arms=["base","ids","protection","both"] if track=="translation" else ["base","candidate"]
        d=study.replay(track,"dev",list(modes),lock,dev,arms)
        if track=="translation":
            learned=criteria.choose_translation(d)
            study.require(all(selection["modes"][m]["variant"]==v for m,v in learned.items()),"dev selection differs")
        m=study.replay(track,"test",list(modes),lock,test,["base","candidate"])
        save_or_verify(root/"dev"/(track+"-summary.json"),d,args.write_summaries)
        save_or_verify(root/"test"/(track+"-summary.json"),m,args.write_summaries)
        final_decisions.update(criteria.decisions(m))
        observed=study.load_rows(test)
        differences[track]=diagnose.paired(observed.values())
        if track=="qa":
            by_id={c["id"]:c for c in study.cases(track,"test")}
            differences["qa_evidence_on_misses"]=diagnose.qa(observed.values(),by_id,study.panel(track))
            differences["qa_by_language_and_length"]=diagnose.qa_groups(observed.values(),by_id)
        if track=="translation":
            differences["translation_by_direction_and_error"]=diagnose.translation_errors(observed.values())
        counts[track]=[len(study.load_rows(dev)),len(observed)]
    save_or_verify(root/"differences.json",differences,args.write_summaries)
    save_or_verify(root/"decisions.json",final_decisions,args.write_summaries)
    if args.check_defaults:
        check_defaults(selection,final_decisions)
    warm=read(root/"search"/"warm.json")
    cold=read(root/"search"/"cold.json")
    previous=read(study.HERE.parents[1]/"tools"/"search"/"verification-final.json")
    study.require(warm["ordered_score_and_hit_hashes"]==previous["ordered_score_and_hit_hashes"]
                  and warm["checked_scores_per_mode"]==8_392_704
                  and warm["routes"]["index_via_parent_alias"]==["metal","coreml_ane_preferred"],
                  "search scores, order, or integrity recovery differs")
    study.require(cold["ordered_hits_sha256"]==read(study.HERE.parents[1]/"tools"/"search"/"cold.json")["ordered_hits_sha256"]
                  and len(cold["rounds"])==3 and len(warm["warm_request_rounds"])==6,"search regression incomplete")
    diagnostics=root/"retry-diagnostics"
    original=read(diagnostics/"context.json")
    lower=read(diagnostics/"headroom.json")
    case=next(c for c in study.cases("qa","test") if c["id"]==lower["id"])
    case=study.panel("qa").followup(case)
    job=study.exp_job(case,"qa","cautious")
    study.require(original["job_sha256"]==lower["job_sha256"]==study.sha(study.encode(job))
                  and original["base_commit"]==study.BASE and lower["binary_sha256"]==selection["base_sha256"],
                  "separate context probes differ from the original request")
    study.require(lower["record"]["metrics"]["peak_sent_tokens"]<=lower["input_budget"]
                  and study.panel("qa").grade(case,lower["record"],"cautious")==lower["grade"],
                  "reduced-input probe failed replay")
    print(json.dumps({"dev_and_test_rows":counts,"adopted":[m for m in final_decisions if final_decisions[m]["adopt"]]},
                     ensure_ascii=False))


if __name__=="__main__":
    main()
