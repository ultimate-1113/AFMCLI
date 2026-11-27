"""Project an explicitly unresolved answer to an empty value, and test that rule on new notices."""
import argparse
from contextlib import nullcontext
import fcntl
import json
from pathlib import Path
import subprocess

import handoff as h
from eval.handoff import extra


def project(final):
    if final is not None and final["state"]=="未確定":
        return {**final,"value":""}
    return final


def full_credit(final,status,gold):
    if not final or status!="completed" or final["evidence_id"] not in gold["evidence"]:
        return False
    if gold["value"] is None:
        return final["state"]=="未確定" and final["value"]==""
    return final["state"]=="確定" and final["value"]==gold["value"]


def grade(rows,cases):
    counts={"n":len(rows),"completed":0,"raw_strict":0,"projected_strict":0,"nonempty_on_unresolved_before":0,
            "nonempty_on_unresolved_after":0,"false_unresolved_with_a_known_answer":0,"changed_values":0}
    changes=[]
    for row in rows:
        gold=cases[row["id"]]["gold"]
        before=row["final"]
        after=project(before)
        old=full_credit(before,row["status"],gold)
        new=full_credit(after,row["status"],gold)
        h.primary.require(old==row["correct"],"raw credit differs from the original scorer")
        counts["completed"]+=int(row["status"]=="completed")
        counts["raw_strict"]+=int(old)
        counts["projected_strict"]+=int(new)
        counts["nonempty_on_unresolved_before"]+=int(gold["value"] is None and bool(before and before["value"]))
        counts["nonempty_on_unresolved_after"]+=int(gold["value"] is None and bool(after and after["value"]))
        counts["false_unresolved_with_a_known_answer"]+=int(gold["value"] is not None and bool(after and after["state"]=="未確定"))
        if after!=before:
            counts["changed_values"]+=1
            changes.append({"id":row["id"],"value_as_generated":before["value"],"earned_strict_credit":new})
    return {**counts,"changes":changes}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("freeze","run","verify"))
    p.add_argument("--out",type=Path,default=h.HERE/"results")
    p.add_argument("--pilot-results",type=Path,default=h.HERE/"results")
    p.add_argument("--doctor",type=Path)
    args=p.parse_args()
    cases=list(extra.cases())
    old=h.corpus()
    h.primary.require(len(cases)==4 and len({c["id"] for c in cases})==4
                      and not ({c["id"] for c in cases}&{c["id"] for c in old}),"additional cases must be new")
    for size in ("short","long"):
        group=[c for c in cases if c["size"]==size]
        h.primary.require(len(group)==2 and sum(c["gold"]["value"] is None for c in group)==1,
                          "unbalanced independent cases")
    for c in cases:
        ids=[line["id"] for packet in c["packets"] for line in packet]
        h.primary.require(len(c["packets"])==3 and len(set(ids))==len(ids)
                          and c["gold"]["evidence"] and set(c["gold"]["evidence"])<=set(ids),
                          "source or gold IDs missing")
    files=["lock.json","extra.py","null_guard.py","null-protocol.md"]
    locked={"files":{f:h.primary.sha((h.HERE/f).read_bytes()) for f in files},
            "cases":{c["id"]:h.primary.sha(h.primary.encode(c)) for c in cases}}
    lockfile=h.HERE/"null-lock.json"
    if args.action=="freeze":
        with lockfile.open("x",encoding="utf-8") as f:
            json.dump(locked,f,sort_keys=True,indent=2);f.write("\n")
        print(h.primary.sha(h.primary.encode(locked)));return
    h.primary.require(locked==json.loads(lockfile.read_bytes()),"additional study changed after freeze")
    h.primary.require(h.fingerprint(old)==json.loads((h.HERE/"lock.json").read_bytes()),"first pilot sources changed")
    running=args.action=="run"
    if running:
        if not args.doctor:
            p.error("run requires --doctor")
        env=json.loads(subprocess.check_output([str(args.doctor.resolve()),"doctor"],timeout=45))
        h.primary.require(env["available"] and env["target"]==h.primary.MODEL and env["route"]=="system","unexpected model")
        args.out.mkdir(parents=True,exist_ok=True)
    path=args.out/"null-probe.json"
    with (args.out/".null-writer.lock").open("a") if running else nullcontext() as lease:
        if running:
            fcntl.flock(lease,fcntl.LOCK_EX)
        saved=json.loads(path.read_bytes()) if path.exists() else []
        h.primary.require(len(saved)<=len(cases),"unexpected extra observations")
        rows=[]
        for i,c in enumerate(cases):
            if i<len(saved):
                v=h.execute(c,"reopen",archive=saved[i]["events"])
            else:
                h.primary.require(running,"missing additional observation")
                with h.primary.system_lock():
                    v=h.execute(c,"reopen")
            row={"id":c["id"],"size":c["size"],"arm":"reopen",**v}
            rows.append(row)
            if i<len(saved):
                h.primary.require(row==saved[i],"additional observation failed replay")
            else:
                h.save_json(path,rows)
                print(c["id"],v["status"],flush=True)
        base_rows=json.loads((args.pilot_results/"observations.json").read_bytes())
        old_cases={c["id"]:c for c in old}
        report={"exploratory":{a:grade([r for r in base_rows if r["size"]!="known" and r["arm"]==a],old_cases)
                               for a in h.ARMS},
                "fresh_reopen":grade(rows,{c["id"]:c for c in cases})}
        if running:
            h.save_json(args.out/"null-summary.json",report)
        else:
            h.primary.require(report==json.loads((args.out/"null-summary.json").read_bytes()),"guard summary differs")
        print(json.dumps(report["fresh_reopen"],ensure_ascii=False))


if __name__=="__main__":
    main()
