"""Retry only operational failures, preserving the original paired trial and its adoption gates."""
import argparse
from collections import Counter
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess

import criteria
import study

ROUNDS=(("same-settings",45),("extended-timeout",90))


def reason(record):
    notes=" ".join(record.get("notes",[])).lower()
    if "timed out" in notes or "wall-time limit" in notes:
        return "reported_timeout"
    if "context window" in notes or "context size" in notes:
        return "reported_context_exhaustion"
    if "fm returned an error" in notes:
        return "fm_nonzero_exit"
    return "other_execution_error"


def primary(root, lock):
    trials={}
    for track in ("calc","translation","qa"):
        path=root/"test"/(track+".jsonl")
        study.replay(track,"test",list(study.MODES[track]),lock,path,["base","candidate"])
        cases={c["id"]:c for c in study.cases(track,"test")}
        for k,row in study.load_rows(path).items():
            case=cases[row["id"]]
            if row["stage"]=="followup":
                case=study.panel(track).followup(case)
            study.require(k not in trials,"duplicate cross-track trial key")
            trials[k]=(row,case)
    return trials


def attempt(root, name, limit, previous, trials, lock, binaries=None):
    path=root/"retries"/(name+".jsonl")
    if binaries is not None:
        path.parent.mkdir(exist_ok=True)
    study.require(binaries is not None or path.exists(),"missing retry round: "+name)
    expected=[k for k,r in previous.items() if r["record"]["status"]=="error"]
    with study.system_lock() if binaries is not None else nullcontext():
        rows=study.load_rows(path)
        if binaries is not None and not expected and not path.exists():
            study.save_rows(path,{})
    for k in expected:
        original,case=trials[k]
        arm,track,mode=original["arm"],original["track"],original["mode"]
        job=study.exp_job(case,track,mode)
        earlier=study.sha(study.encode(previous[k]))
        with study.system_lock() if binaries is not None else nullcontext():
            if binaries is not None:
                rows=study.load_rows(path)
            if k not in rows:
                study.require(binaries is not None,"absent retry: "+str(k))
                exe,fingerprint=binaries[arm]
                study.require(fingerprint==original["binary_sha256"],"build differs from first attempt")
                result,elapsed=study.observe(exe,job,limit)
                row={field:original[field] for field in ("id","mode","stage","arm","track","split","lock","model","job_sha256","binary_sha256")}
                row.update(retry=name,timeout_seconds=limit,previous_sha256=earlier,elapsed_ms=elapsed,record=result,
                           **study.decorate(case,track,mode,result))
                rows[k]=row
                study.save_rows(path,rows)
                print(name,*k,result["status"],flush=True)
            row=rows[k]
            study.require(row["retry"]==name and row["timeout_seconds"]==limit and row["previous_sha256"]==earlier,
                          "retry does not belong to the previous attempt")
            study.validate(row,case,job,track,"test",original["binary_sha256"],lock)
    study.require(list(rows)==expected,"unexpected retry or modified retry order")
    return rows



def investigate(root, binaries=None):
    lock=study.lock_check()
    trials=primary(root,lock)
    if binaries is not None:
        selection=json.loads((root/"selection.json").read_bytes())
        study.require(binaries["base"][1]==selection["base_sha256"] and all(
            m["binary_sha256"]==binaries["candidate"][1] for m in selection["modes"].values()
        ),"retry executables differ from the original selection")
    initial={k:r for k,(r,_) in trials.items()}
    current=initial
    final=dict(initial)
    report={"initial_errors":sum(r["record"]["status"]=="error" for r in initial.values()),
            "initial_error_reasons":dict(Counter(reason(r["record"]) for r in initial.values() if r["record"]["status"]=="error")),
            "rounds":{}}
    for name,limit in ROUNDS:
        current=attempt(root,name,limit,current,trials,lock,binaries)
        final.update(current)
        completed=[r for r in current.values() if r["record"]["status"]!="error"]
        report["rounds"][name]={"n":len(current),"completed":len(completed),"correct_among_completed":sum(r["grade"]["correct"] for r in completed),
             "remaining_errors":len(current)-len(completed),"additional_seconds":round(sum(r["elapsed_ms"] for r in current.values())/1000,3),
             "error_reasons":dict(Counter(reason(r["record"]) for r in current.values() if r["record"]["status"]=="error")),
             "statuses":dict(Counter(r["record"]["status"] for r in current.values()))}
    report["with_retries"]={}
    for track,modes in study.MODES.items():
        results=criteria.summary(r for r in final.values() if r["track"]==track)
        for mode in modes:
            report["with_retries"][mode]={a:{k:results[a][mode][k] for k in (
                "n","correct","correct_positive","unsupported","need_correct","correct_question","followup_correct","status")}
                for a in ("base","candidate")}
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("run","verify"))
    parser.add_argument("--results",type=Path,default=study.HERE/"results")
    parser.add_argument("--base",type=Path)
    parser.add_argument("--candidate",type=Path)
    args=parser.parse_args()
    binaries=None
    if args.action=="run":
        if not (args.base and args.candidate):
            parser.error("supply both original executables")
        binaries={}
        for arm,exe in (("base",args.base),("candidate",args.candidate)):
            exe=exe.resolve(strict=True)
            pre=json.loads(subprocess.check_output([str(exe),"doctor"],timeout=45))
            study.require(pre["available"] and pre["target"]==study.MODEL and pre["route"]=="system","unexpected model")
            binaries[arm]=(exe,study.sha(exe.read_bytes()))
    report=investigate(args.results,binaries)
    path=args.results/"retries"/"summary.json"
    if args.action=="run":
        tmp=path.with_suffix(".new")
        tmp.write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
        tmp.replace(path)
    else:
        study.require(json.loads(path.read_bytes())==report,"retry summary differs")
    print(json.dumps({k:v for k,v in report.items() if k!="with_retries"},ensure_ascii=False))


if __name__=="__main__":
    main()
