"""Select using the development set, then bind the build used for the fresh test."""
import argparse
import json
from pathlib import Path

import criteria
import study


def flags(variants):
    selected=["trial-qa-preview","trial-qa-lookup","trial-qa-cautious","trial-calculation"]
    for mode,arm in variants.items():
        for variant in ("ids","protection"):
            if arm in (variant,"both"):
                selected.append(f"trial-{mode}-{variant}")
    return selected


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results",type=Path,required=True,help="directory containing the dev subdirectory")
    p.add_argument("--base",type=Path)
    p.add_argument("--candidate",type=Path)
    p.add_argument("--source",help="tested candidate commit")
    p.add_argument("--cargo-features",action="store_true",help="print the selected features for compiling the test binary")
    args=p.parse_args()
    lock=study.lock_check()
    arms=["base","ids","protection","both"]
    data=study.replay("translation","dev",list(study.MODES["translation"]),lock,
                      args.results/"dev"/"translation.jsonl",arms)
    variants=criteria.choose_translation(data)
    if not args.candidate:
        print(",".join(flags(variants)) if args.cargo_features else json.dumps(variants, sort_keys=True))
        return
    if not args.base or not args.source or len(args.source)!=40:
        p.error("locking requires both binaries and the tested source commit")
    for track in ("qa","calc"):
        study.replay(track,"dev",list(study.MODES[track]),lock,args.results/"dev"/(track+".jsonl"),["base","candidate"])
    sourcesha = study.sha(args.candidate.read_bytes())
    methods = {m: {"variant":variants.get(m,"candidate"), "binary_sha256":sourcesha}
               for modes in study.MODES.values() for m in modes}
    record=dict(lock=lock,baseline_commit=study.BASE,base_sha256=study.sha(args.base.read_bytes()),
                candidate_commit=args.source,cargo_features=flags(variants),modes=methods,
                development_sha256={t:study.sha((args.results/"dev"/(t+".jsonl")).read_bytes()) for t in study.MODES})
    for t in study.MODES:
        rows=study.load_rows(args.results/"dev"/(t+".jsonl"))
        study.require(all(r["binary_sha256"]==record["base_sha256"] for r in rows.values() if r["arm"]=="base"),
                      "development used a different baseline")
    with (args.results/"selection.json").open("x",encoding="utf-8") as f:
        json.dump(record,f,ensure_ascii=False,sort_keys=True,indent=2)
        f.write("\n")
    print(json.dumps(variants,sort_keys=True))


if __name__=="__main__":
    main()
