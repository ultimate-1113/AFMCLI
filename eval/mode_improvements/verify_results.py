"""Verify the complete release evidence without a model or a network connection."""
import argparse
import json
from pathlib import Path

import criteria
import diagnose
import study


def read(path):
    return json.loads(path.read_bytes())


def save_or_verify(path, value, write):
    if write:
        path.write_text(json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    else:
        study.require(read(path)==value,"published summary differs: "+path.name)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results",type=Path,default=study.HERE/"results")
    p.add_argument("--write-summaries",action="store_true")
    args=p.parse_args()
    root=args.results
    lock=study.lock_check()
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
        counts[track]=[len(study.load_rows(dev)),len(observed)]
    save_or_verify(root/"differences.json",differences,args.write_summaries)
    save_or_verify(root/"decisions.json",final_decisions,args.write_summaries)
    warm=read(root/"search"/"warm.json")
    cold=read(root/"search"/"cold.json")
    previous=read(study.HERE.parents[1]/"tools"/"search"/"verification-final.json")
    study.require(warm["ordered_score_and_hit_hashes"]==previous["ordered_score_and_hit_hashes"]
                  and warm["checked_scores_per_mode"]==8_392_704
                  and warm["routes"]["index_via_parent_alias"]==["metal","coreml_ane_preferred"],
                  "search scores, order, or integrity recovery differs")
    study.require(cold["ordered_hits_sha256"]==read(study.HERE.parents[1]/"tools"/"search"/"cold.json")["ordered_hits_sha256"]
                  and len(cold["rounds"])==3 and len(warm["warm_request_rounds"])==6,"search regression incomplete")
    print(json.dumps({"dev_and_test_rows":counts,"adopted":[m for m in final_decisions if final_decisions[m]["adopt"]]},
                     ensure_ascii=False))


if __name__=="__main__":
    main()
