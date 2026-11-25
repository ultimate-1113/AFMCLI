"""Recheck the existing 40 public QA cases, keeping only scores and IDs in the result."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import subprocess

HERE = Path(__file__).resolve().parent
CONTEXT = HERE.parent / "context"
lab = runpy.run_path(str(CONTEXT / "bench.py"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action",choices=["run","verify"])
    ap.add_argument("--binary",type=Path)
    ap.add_argument("--data",type=Path)
    ap.add_argument("--results",type=Path,default=HERE/"short.jsonl")
    args = ap.parse_args()
    previous = {r["id"]:r for r in map(json.loads,(CONTEXT/"results.jsonl").read_text().splitlines())
                if r["cohort"]=="external" and r["mode"]=="compact"}
    if args.action=="run":
        if not args.binary or not args.data:
            ap.error("run requires --binary and --data")
        binary=args.binary.resolve()
        state=json.loads(subprocess.check_output([binary,"doctor"]))
        assert state["available"] and state["route"]=="system" and state["target"]=="AFM 3 Core Advanced"
        fingerprint=hashlib.sha256(binary.read_bytes()).hexdigest()
        cases=[c for c in lab["datasets"](args.data) if c["id"] in previous]
        assert {c["id"] for c in cases}==set(previous)
        args.results.parent.mkdir(parents=True,exist_ok=True)
        with args.results.open("x") as output:
            for c in cases:
                result=subprocess.run([binary,"--compact","--timeout","45","run"],input=json.dumps(c["job"],ensure_ascii=False),
                                      text=True,capture_output=True)
                record=json.loads(result.stdout)
                if record["status"]=="error" and not any("timed out" in s for s in record["notes"]):
                    raise RuntimeError(record["notes"])
                row=dict(id=c["id"],binary=fingerprint,status=record["status"],
                         correct=lab["grade"](c,"base",record)["pass"])
                output.write(json.dumps(row,separators=(",",":"))+"\n")
                output.flush()
    rows=[json.loads(s) for s in args.results.read_text().splitlines()]
    assert {r["id"] for r in rows}==set(previous) and len(rows)==len(previous)==40
    assert len({r["binary"] for r in rows})==1
    assert all(set(r)=={"id","binary","status","correct"} and type(r["correct"]) is bool for r in rows)
    changed=sum((r["correct"],r["status"])!=(previous[r["id"]]["correct"],previous[r["id"]]["status"]) for r in rows)
    print(f"{sum(r['correct'] for r in rows)}/40 correct; {changed} changes in grade or status vs v0.3.0.")


if __name__=="__main__":
    main()
