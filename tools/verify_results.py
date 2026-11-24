"""Replay all public synthetic scores and reproduce the aggregate tables, offline."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from statistics import median
import sys
import subprocess

ROOT=Path(__file__).resolve().parents[1]
EVAL=ROOT/"eval"
OUT=EVAL/"results"
sys.path.insert(0,str(EVAL))
from bench import assess,freeze,lines

def status(r):
    return r["response"]["status"] if "response" in r else r["status"]
def metrics(r):
    return r["response"]["metrics"] if "response" in r else r["metrics"]
def summarize(rows):
    result={}
    names=sorted({r.get("dataset",r["task"]) for r in rows})
    for name in [*names,"total"]:
        group=[r for r in rows if name=="total" or r.get("dataset",r["task"])==name]
        base={r["id"]:r["score"]["pass"] for r in group if r["mode"]=="direct"}
        modes={}
        for mode in ("direct","verified","fast"):
            sel=[r for r in group if r["mode"]==mode]
            if not sel:continue
            modes[mode]=dict(n=len(sel),passed=sum(r["score"]["pass"] for r in sel),
                gained=sum(r["score"]["pass"] and not base[r["id"]] for r in sel),
                lost=sum(not r["score"]["pass"] and base[r["id"]] for r in sel),
                calls=sum(metrics(r)["calls"] for r in sel),median_ms=median(r["elapsed_ms"] for r in sel),
                statuses=dict(Counter(status(r) for r in sel)),
                span_records=sum(r["score"]["spans_valid"] is not None for r in sel),
                span_failures=sum(r["score"]["spans_valid"] is False for r in sel))
        result[name]=modes
    return result

def validate():
    expected=json.loads((EVAL/"lock.json").read_text())
    current=freeze()
    for path, digest in expected.items():
        if current.get(path)==digest:
            continue
        if path.startswith("eval/"):
            raise ValueError(f"archived evaluator or input changed: {path}")
        try:
            original=subprocess.check_output(
                ["git","show",f"52b10f6833690ed6dd40d55757a57519abe65d42:{path}"],cwd=ROOT,
                stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError as error:
            raise RuntimeError("Full Git history or the v0.1.0 source tree is required to verify the historical implementation.") from error
        if hashlib.sha256(original).hexdigest()!=digest:
            raise ValueError(f"historical implementation differs from the evaluation lock: {path}")
    cases={x["id"]:x for x in lines(EVAL/"heldout.jsonl")}
    held=lines(OUT/"heldout.jsonl");ext=lines(OUT/"external.jsonl")
    all_sha={r["binary_sha256"] for r in held+ext}
    assert len(all_sha)==1 and len(next(iter(all_sha)))==64
    for rows,cohort in [(held,"synthetic"),(ext,"external")]:
        keys=[(r["id"],r["mode"]) for r in rows];assert len(set(keys))==len(keys)
        expected={(r["id"],m) for r in rows for m in (("direct","verified","fast") if r["task"]=="answer" else ("direct","verified"))}
        assert set(keys)==expected and len({r["run_key"] for r in rows})==1
        if cohort=="synthetic":
            assert set(cases)=={r["id"] for r in rows}
            for r in rows:assert r["score"]==assess(cases[r["id"]],r["mode"],r["response"]),r["id"]
        else:
            assert Counter(r["dataset"] for r in rows if r["mode"]=="direct")=={"jsquad":16,"squad":16,"jnli":15}
            allowed={"binary_sha256","id","mode","dataset","task","run_key","elapsed_ms","score","status","metrics"}
            for r in rows:assert set(r)==allowed and isinstance(r["score"]["pass"],bool)
    dev={r["id"]:r for r in lines(EVAL/"dev.jsonl")}
    assert not (set(cases)&set(dev))
    development=lines(OUT/"development.jsonl")
    for r in development:
        assert r["score"]==assess(dev[r["id"]],r["mode"],r["response"])
    cand={}
    for c in sorted({r["candidate"] for r in development}):
        als=[r for r in development if r["candidate"]==c and r["mode"]=="verified"]
        cand[c]=[sum(r["score"]["pass"] for r in als),len(als)]
    paired={}
    for mode in ("direct","verified"):
        values={r["id"]:r["score"]["pass"] for r in held if r["mode"]==mode}
        paired[mode]={p:all(values[i] for i,c in cases.items() if c.get("pair")==p) for p in sorted({c["pair"] for c in cases.values() if "pair" in c})}
    probes=json.loads((OUT/"probes.json").read_text())
    assert len(probes)==12
    for r in probes:
        response=r["response"]
        if "expected_route" in r:
            assert r["route"]==(response.get("result") or {}).get("route")
            passed=r["route"]==r["expected_route"]
        elif r["id"]=="image-and-text":
            passed=response["result"]=={"marker":"QL-639","color":"red"}
        elif r["id"]=="long-verified":
            value=(response.get("result") or {}).get("answer") or {}
            passed=value.get("value")=="QMX-4921"
            if passed:assert all(s["end_byte"]-s["start_byte"]==8 for s in value["spans"])
        else:
            assert r["id"] in ("long-direct","long-call-limit")
            passed=response["status"]=="held" and response["result"] is None
        assert passed==r["passed"]
    return dict(binary_sha256=next(iter(all_sha)),protocol_sha256=hashlib.sha256((EVAL/"lock.json").read_bytes()).hexdigest(),
                synthetic=summarize(held),external=summarize(ext),behavior_groups=paired,development=cand,
                probe_checks={r["id"]:r["passed"] for r in probes})

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--write",action="store_true");args=p.parse_args()
    report=validate();dest=OUT/"summary.json"
    if args.write:dest.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
    else:assert json.loads(dest.read_text())==report
    print("Verified frozen sources, 114 synthetic trial records, 126 external measurement records and 12 integration probes.")
