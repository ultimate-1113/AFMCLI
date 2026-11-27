"""Print one published synthetic input, its grading evidence or its measured responses."""
import argparse
import json

import study


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("track",choices=list(study.MODES))
    p.add_argument("case_id")
    p.add_argument("--split",choices=list(study.SIZES),default="test")
    p.add_argument("--mode")
    p.add_argument("--followup",action="store_true")
    p.add_argument("--show",choices=("input","gold","observed"),default="input")
    args=p.parse_args()
    study.lock_check()
    case=next((c for c in study.cases(args.track,args.split) if c["id"]==args.case_id),None)
    if case is None:
        p.error("unknown case")
    if args.followup:
        if not case.get("askable"):
            p.error("case has no follow-up")
        case=study.panel(args.track).followup(case)
    mode=args.mode or ("cautious" if args.followup else study.MODES[args.track][0])
    if mode not in study.MODES[args.track]:
        p.error("unknown mode for this track")
    if args.followup and mode!="cautious":
        p.error("follow-up observations use the cautious mode")
    if args.show=="input":
        obj=study.exp_job(case,args.track,mode)
    elif args.show=="gold":
        obj={key:case[key] for key in ("id","gold","coverage","reply") if key in case}
    else:
        file=study.HERE/"results"/args.split/(args.track+".jsonl")
        obj=[r for r in study.load_rows(file).values() if r["id"]==args.case_id and r["mode"]==mode
             and r["stage"]==("followup" if args.followup else "initial")]
    print(json.dumps(obj,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
