"""Paired evaluation through the installed Rust CLI. Python 3.9+, standard library."""
import argparse
from collections import Counter
import fcntl
import hashlib
import json
from pathlib import Path
import re
from statistics import median
import subprocess
import time
import unicodedata
from external import fixtures

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent

def lines(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]

def compact(v):
    return json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(",",":"))

def sha(data):
    return hashlib.sha256(data).hexdigest()

def digest(v):
    return sha(compact(v).encode())

def freeze():
    files=sorted([*ROOT.glob("src/*.rs"),ROOT/"Cargo.toml",ROOT/"Cargo.lock",*HERE.glob("*.py"),HERE/"dev.jsonl",HERE/"heldout.jsonl",HERE/"protocol.md"])
    return {str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in files}

def unguided(text):
    s=text.strip()
    match=re.fullmatch(r"```(?:json)?\s*(.*?)\s*```",s,re.S|re.I)
    return json.loads(match[1] if match else s)

def evidence(job,data):
    raw=job.get("source","").encode()
    spans=data["spans"]
    return len(spans)>0 and all(type(v["start_byte"]) is int and type(v["end_byte"]) is int and
        0<=v["start_byte"]<v["end_byte"]<=len(raw) and raw[v["start_byte"]:v["end_byte"]].decode()==data["value"] for v in spans)

def norm(s):
    return " ".join(unicodedata.normalize("NFC",s).casefold().strip().split())

def overlap(a,b,ja):
    def units(s):
        return list(norm(s).replace(" ","")) if ja else re.findall(r"\w+|[^\w\s]",norm(s))
    x,y=units(a),units(b)
    both=sum((Counter(x)&Counter(y)).values())
    return (2*both/(len(x)+len(y))) if x or y else 1.0

def assess(case,mode,record):
    job,gold=case["job"],case["gold"]
    kind=job["task"]
    result=record.get("result")
    status=record.get("status")
    direct=mode=="direct"
    score={"pass":False,"format":False,"spans_valid":None,"f1":None}
    if status in ("refused","error"):return score
    if status=="held" and kind!="edit":return score
    try:
        if direct and kind not in ("ask","rewrite"):result=unguided(result)
        correct=False
        if kind=="answer":
            val=result["answer"]
            if direct:
                if not isinstance(val,str):return score
                val=val.strip()
            elif val is None:val=""
            else:
                score["spans_valid"]=evidence(job,val)
                val=val["value"]
            correct=norm(val) in {norm(s) for s in (gold or [""])}
            score["f1"]=max(overlap(val,g,case.get("dataset")=="jsquad" or any("ぁ"<=c<="鿿" for c in job["question"])) for g in (gold or [""]))
        elif kind=="extract":
            if set(result)!=set(gold):return score
            spellings={}
            checks=[]
            for k,items in result.items():
                if not isinstance(items,list):return score
                vals=[]
                for item in items:
                    if direct:
                        if not isinstance(item,str):return score
                        vals.append(item)
                    else:
                        checks.append(evidence(job,item));vals.append(item["value"])
                spellings[k]=sorted(vals)
            score["spans_valid"]=all(checks) if checks else None
            correct=spellings=={k:sorted(v) for k,v in gold.items()}
        elif kind=="summarize":
            if direct:
                ids=result["ids"]
            else:
                rows=result["segments"]
                raw=job["source"].encode()
                score["spans_valid"]=all(0<=r["start_byte"]<r["end_byte"]<=len(raw) and raw[r["start_byte"]:r["end_byte"]].decode()==r["text"] for r in rows) if rows else None
                ids=[r["id"] for r in rows]
            if not isinstance(ids,list) or not all(type(i) is int for i in ids):return score
            correct=sorted(ids)==sorted(gold)
        elif kind=="classify":
            if set(result)!={"label"}:return score
            label=result["label"]
            if not (isinstance(label,str) or (label is None and not direct)):return score
            correct=(label or "")==gold
        elif kind=="solve":
            if "source" in job and not direct:
                score["interpretation_exact"]=compact(result["interpretation"])==compact(gold["interpretation"])
                result=result["solution"]
            correct=compact(result)==compact(gold["solution"])
        elif kind=="structure":
            correct=compact(result)==compact(gold)
        elif kind=="edit":
            correct=compact(result["document"])==compact(gold["document"]) and result["changed"] is (gold["document"]!=job["document"]) and (direct or (status=="ok")==gold["accepted"])
        elif kind in ("ask","rewrite"):
            if not isinstance(result,str):return score
            text=norm(result)
            correct=(all(norm(x) in text for x in gold.get("all",[]))
                     and all(any(norm(x) in text for x in a) for a in gold.get("any",[]))
                     and all(norm(x) not in text for x in gold.get("forbid",[]))
                     and (not gold.get("different") or text!=norm(job.get("source","")))
                     and gold.get("min",0)<=len(result.strip())<=gold.get("max",job.get("max_chars") or 1000000))
            if job.get("keep_numbers"):
                tokens=lambda s: sorted(re.findall(r"[+＋\-−－]?[0-9０-９]+(?:[,，.．][0-9０-９]+)*",s))
                correct &= tokens(result)==tokens(job["source"])
        score.update({"pass":bool(correct) and score["spans_valid"] is not False,"format":True})
    except (TypeError,KeyError,AttributeError,ValueError,UnicodeError):
        pass
    return score

def run(args):
    cases=fixtures(args.data) if args.split=="external" else lines(HERE/(args.split+".jsonl"))
    if args.split!="dev" and json.loads((HERE/"lock.json").read_text())!=freeze():
        raise SystemExit("source, evaluator or fixture changed after freeze")
    modes=("direct","verified") if args.modes in ("both","all") else (args.modes,)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    bin_sha=sha(args.binary.read_bytes())
    settings=digest([cases,bin_sha,args.repairs,freeze()])
    with args.output.open("a+") as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        old=lines(args.output)
        if any(r["run_key"]!=settings for r in old):raise SystemExit("output belongs to a different run")
        done={(r["id"],r["mode"]) for r in old}
        for case in sorted(cases,key=lambda c:digest(c["id"])):
            chosen=modes+(("fast",) if args.modes=="all" and case["job"]["task"]=="answer" else ())
            order=chosen if int(digest(case["id"])[0],16)%2 else tuple(reversed(chosen))
            for mode in order:
                if (case["id"],mode) in done:continue
                command=[str(args.binary.resolve()),"--trace","--repairs",str(args.repairs),"run"]
                if mode=="direct":command.append("--direct")
                if mode=="fast":command.append("--fast-answer")
                start=time.perf_counter()
                proc=subprocess.run(command,input=compact(case["job"]),capture_output=True,text=True,timeout=600)
                if proc.returncode not in (0,3):raise RuntimeError(f"CLI error on {case['id']} {mode}: {proc.stdout[:240]} {proc.stderr[:100]}")
                rec=json.loads(proc.stdout)
                row=dict(id=case["id"],task=case["job"]["task"],mode=mode,run_key=settings,binary_sha256=bin_sha,elapsed_ms=round(1000*(time.perf_counter()-start)),response=rec,score=assess(case,mode,rec))
                if "dataset" in case:row["dataset"]=case["dataset"]
                f.write(compact(row)+"\n");f.flush()
    print("completed",args.split,len(cases),"cases")

def verify(path,split,data):
    cases=fixtures(data) if split=="external" else lines(HERE/(split+".jsonl"))
    c={r["id"]:r for r in cases}
    rows=lines(path)
    expected={(i,m) for i in c for m in ("direct","verified")}
    if any(r["mode"]=="fast" for r in rows):expected.update((i,"fast") for i,v in c.items() if v["job"]["task"]=="answer")
    assert len(rows)==len(expected) and {(r["id"],r["mode"]) for r in rows}==expected
    for r in rows:
        if "response" in r:assert r["score"]==assess(c[r["id"]],r["mode"],r["response"])
    for family in sorted({r.get("dataset",r["task"]) for r in rows}):
        print(family,end=" ")
        for mode in ("direct","verified","fast"):
            rs=[r for r in rows if r.get("dataset",r["task"])==family and r["mode"]==mode]
            if not rs:continue
            print(f"{mode} {sum(r['score']['pass'] for r in rs)}/{len(rs)} ({median(r['elapsed_ms'] for r in rs):.0f} ms)",end=" ")
        print()

def main():
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=["run","verify","freeze","redact"])
    p.add_argument("--split",choices=["dev","heldout","external"],default="dev")
    p.add_argument("--binary",type=Path,default=ROOT/"target/release/afmcli")
    p.add_argument("--data",type=Path,default=HERE/"work/data")
    p.add_argument("--output",type=Path,default=HERE/"work/results.jsonl")
    p.add_argument("--public",type=Path)
    p.add_argument("--repairs",type=int,choices=[0,1],default=1)
    p.add_argument("--modes",choices=["direct","verified","both","all"],default="both")
    a=p.parse_args()
    if a.action=="freeze":
        with (HERE/"lock.json").open("x") as f:json.dump(freeze(),f,indent=2);f.write("\n")
    elif a.action=="run":run(a)
    elif a.action=="verify":verify(a.output,a.split,a.data)
    else:
        if a.split!="external" or a.public is None:raise SystemExit("redact requires --split external and --public")
        rows=lines(a.output)
        verify(a.output,a.split,a.data)
        with a.public.open("x") as f:
            for r in rows:
                safe={k:r[k] for k in ("id","mode","dataset","task","run_key","binary_sha256","elapsed_ms","score")}
                safe.update(status=r["response"]["status"],metrics=r["response"]["metrics"])
                f.write(compact(safe)+"\n")
if __name__=="__main__": main()
