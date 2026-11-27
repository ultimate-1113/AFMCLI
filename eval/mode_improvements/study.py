"""Freeze panels, run the real CLI on matched cases, and independently replay every observation."""
import argparse
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "combinations"))
sys.path.insert(0, str(HERE.parents[1]))
from eval.combinations.study import system_lock
import criteria

BASE = "beeb6944946471e12c5de43834f87a84c32ca9f4"
MODEL = "AFM 3 Core Advanced"
MODES = {"qa": ("preview","lookup","cautious"), "translation": ("draft","checked"), "calc": ("formula",)}
SIZES = {"dev": (12,12,8), "test": (48,40,24)}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",",":"), sort_keys=True, allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def panel(track):
    return importlib.import_module(track+"_panel")


def cases(track, split):
    records = list(panel(track).make(split))
    require(len(records)==SIZES[split][list(MODES).index(track)], "wrong panel size")
    require(len({c["id"] for c in records})==len(records), "duplicate case id")
    for c in records:
        require(c["language"] in ("ja","en"), "unsupported language")
        require("gold" in c and isinstance(c["job"],dict), "incomplete frozen case")
    return records


def freeze_data():
    files = [HERE/"protocol.md", HERE/"criteria.py"]
    files += sorted(HERE.glob("*_panel.py")) + sorted(HERE.glob("*_fixtures.*"))
    files += sorted((HERE/"qa_sources").glob("*.py")) + sorted((HERE/"qa_sources").glob("*_lengths.json"))
    data = {"baseline":BASE, "model":MODEL, "files":{p.relative_to(HERE).as_posix():sha(p.read_bytes()) for p in files}, "panels":{}}
    for track in MODES:
        by_split = {}
        for split in SIZES:
            rows = cases(track, split)
            follow = {c["id"]:sha(encode(panel(track).followup(c))) for c in rows if c.get("askable")}
            by_split[split] = {"cases":{c["id"]:sha(encode(c)) for c in rows}, "followups":follow}
        require(not (by_split["dev"]["cases"].keys() & by_split["test"]["cases"].keys()), "split ID overlap")
        data["panels"][track] = by_split
    return data


def lock_check():
    frozen = json.loads((HERE/"lock.json").read_text())
    require(frozen==freeze_data(),"fixture, scoring or threshold fingerprint differs from frozen manifest")
    return sha(encode(frozen))


def exp_job(case, track, mode):
    job = dict(case["job"])
    if track!="calc":
        job["mode"] = mode
    return job


def retrieval(case, record):
    if "coverage" not in case:
        return None
    context = record.get("metrics",{}).get("context") or {}
    evidence = context.get("retrieval") or {}
    ranges = [(x["start_byte"],x["end_byte"]) for r in evidence.get("rounds",[]) if r.get("sent")
              for x in r.get("ranges",[])]
    if not ranges:
        return "unobserved"
    needed = case["coverage"].get("state_changes") or case["coverage"].get("primary") or []
    if not needed:
        return "unobserved"
    def present(span):
        a,b = (span["start_byte"],span["end_byte"]) if isinstance(span,dict) else span
        return any(x<=a and b<=y for x,y in ranges)
    if any(present(span) for span in needed):
        return "decisive_passage_sent"
    return "unobserved_fallback" if evidence.get("fallback") else "decisive_passage_not_in_recorded_ranges"


def observe(binary, job):
    start = perf_counter()
    try:
        proc = subprocess.run([str(binary),"--timeout","45","run"],input=encode(job),capture_output=True,timeout=360)
        require(proc.returncode in (0,2,3) and proc.stdout, "missing CLI JSON")
        record = json.loads(proc.stdout)
    except subprocess.TimeoutExpired:
        record = dict(task=job["task"],status="error",result=None,notes=["study wall-time limit"],metrics={})
    return record, round((perf_counter()-start)*1000,3)


def decorate(case, track, mode, record):
    require(record["task"]==case["job"]["task"],"CLI task mismatch")
    m=record["metrics"]
    if m or record["status"]!="error":
        if track!="calc":
            require(m.get("answer_mode" if track=="qa" else "translation_mode")==mode,"CLI mode mismatch")
        require(0<=m["peak_sent_tokens"]<=6200 and m["sent_input_tokens"]>=m["peak_sent_tokens"],"token accounting")
    grade = panel(track).grade(case,record,mode)
    require(all(isinstance(grade[k],bool) for k in ("correct","answered","serious")),"grade must be boolean")
    return dict(grade=grade, retrieval=retrieval(case,record), positive=case.get("positive",True),
                askable=case.get("askable",False), language=case["language"])


def key(row):
    return (row["id"],row["mode"],row["stage"],row["arm"])


def load_rows(path):
    if not path.exists():
        return {}
    rows={}
    for line in path.read_bytes().splitlines():
        row=json.loads(line)
        require(key(row) not in rows,"duplicate trial")
        rows[key(row)]=row
    return rows


def save_rows(path, rows):
    temporary=path.with_suffix(".new")
    with temporary.open("wb") as f:
        for row in rows.values():
            f.write(encode(row)+b"\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary,path)


def schedule(track, split, modes, arms):
    for i,c in enumerate(cases(track,split)):
        shift=i%len(modes)
        for j,mode in enumerate(modes[shift:]+modes[:shift]):
            stages=[("initial",c)]
            if mode=="cautious" and c.get("askable"):
                stages.append(("followup",panel(track).followup(c)))
            for si,(stage,current) in enumerate(stages):
                n=(i+j+si)%len(arms)
                for arm in arms[n:]+arms[:n]:
                    yield c["id"], current, mode, stage, arm


def validate(row, c, job, track, split, fingerprint, lock):
    require(row["binary_sha256"]==fingerprint and row["job_sha256"]==sha(encode(job)),"trial fingerprint mismatch")
    require(row["track"]==track and row["split"]==split and row["model"]==MODEL and row["lock"]==lock,"wrong trial panel")
    require(math.isfinite(row["elapsed_ms"]) and row["elapsed_ms"]>=0,"invalid duration")
    for field,expected in decorate(c,track,row["mode"],row["record"]).items():
        require(row[field]==expected,"observation failed replay: "+field)


def run(args, lock, path):
    bins={}
    for parameter in args.arm:
        arm,separator,value=parameter.partition("=")
        require(separator and arm and arm not in bins,"supply unique arm=executable")
        binary=Path(value).resolve(strict=True)
        preflight=json.loads(subprocess.check_output([str(binary),"doctor"],timeout=45))
        require(preflight["available"] and preflight["target"]==MODEL and preflight["route"]=="system","unexpected model")
        bins[arm]=(binary,sha(binary.read_bytes()))
    require(bins,"at least one arm is required")
    if args.split=="test":
        require(list(bins)==["base","candidate"],"test must use both base and candidate in that order")
        choices=json.loads((args.out.parent/"selection.json").read_text())
        require(choices["lock"]==lock,"wrong development selection")
        for mode in args.modes:
            require(choices["modes"][mode]["binary_sha256"]==bins["candidate"][1],"candidate differs from pre-test selection")
        require(choices["base_sha256"]==bins["base"][1],"wrong baseline")
    args.out.mkdir(parents=True,exist_ok=True)
    for cid,c,mode,stage,arm in schedule(args.track,args.split,args.modes,list(bins)):
        binary,fingerprint=bins[arm]; job=exp_job(c,args.track,mode)
        with system_lock():
            rows=load_rows(path)
            k=(cid,mode,stage,arm)
            if k in rows:
                validate(rows[k],c,job,args.track,args.split,fingerprint,lock)
                continue
            record,duration=observe(binary,job)
            row=dict(id=cid,mode=mode,stage=stage,arm=arm,track=args.track,split=args.split,
                     lock=lock, model=MODEL, job_sha256=sha(encode(job)),binary_sha256=fingerprint,
                     elapsed_ms=duration,record=record,**decorate(c,args.track,mode,record))
            validate(row,c,job,args.track,args.split,fingerprint,lock)
            rows[k]=row
            save_rows(path,rows)
        print(cid,mode,stage,arm,record["status"],flush=True)


def replay(track,split,modes,lock,path,arms):
    rows=load_rows(path)
    require(bool(rows),"no saved trials")
    fingerprints={}
    expected=set()
    selection = json.loads((path.parent.parent/"selection.json").read_text()) if split=="test" else None
    if selection:
        require(selection["lock"]==lock and arms==["base","candidate"],"unexpected test selection")
    for cid,c,mode,stage,arm in schedule(track,split,modes,arms):
        k=(cid,mode,stage,arm)
        require(k in rows,"missing case or arm "+str(k))
        r=rows[k]
        stable=fingerprints.setdefault((arm,mode),r["binary_sha256"])
        if selection:
            fixed = selection["base_sha256"] if arm=="base" else selection["modes"][mode]["binary_sha256"]
            require(stable==fixed, "replay used a different binary from the frozen selection")
        validate(r,c,exp_job(c,track,mode),track,split,stable,lock)
        expected.add(k)
    require(set(rows)==expected,"unplanned extra trials")
    return criteria.summary(rows.values())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=["freeze","verify-lock","run","replay","decide"])
    p.add_argument("--track",choices=list(MODES))
    p.add_argument("--split",choices=list(SIZES),default="test")
    p.add_argument("--out",type=Path,default=HERE/"results"/"test")
    p.add_argument("--arm",action="append",default=[])
    p.add_argument("--modes",nargs="+")
    args=p.parse_args()
    if args.action=="freeze":
        data=freeze_data()
        with (HERE/"lock.json").open("x",encoding="utf-8") as f:
            f.write(json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
        print(sha(encode(data)))
        return
    lock=lock_check()
    if args.action=="verify-lock":
        print(lock)
        return
    if not args.track:
        p.error("--track is required")
    args.modes=list(args.modes or MODES[args.track])
    require(len(set(args.modes))==len(args.modes) and set(args.modes)<=set(MODES[args.track]),"unsupported modes")
    path=args.out/(args.track+".jsonl")
    if args.action=="run":
        run(args,lock,path)
    else:
        arms=args.arm or ["base","candidate"]
        if args.action=="decide":
            require(args.split=="test" and arms==["base","candidate"] and tuple(args.modes)==MODES[args.track],"incomplete decision panel")
        measured=replay(args.track,args.split,args.modes,lock,path,arms)
        result=criteria.decisions(measured) if args.action=="decide" else measured
        print(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2))


if __name__=="__main__":
    main()
