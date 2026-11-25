"""Paired measurements of periodic sources and the existing synthetic context panel."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import runpy
from statistics import median
import subprocess
import time

HERE = Path(__file__).resolve().parent
CONTEXT = runpy.run_path(str(HERE.parent / "context" / "bench.py"))


def cases():
    for row in json.loads((HERE / "panel.json").read_text()):
        cycle = row["cycle"]
        source = "\n".join(row["events"].get(str(i),cycle[i % len(cycle)]) for i in range(row["rows"]))
        yield dict(id=row["id"], cohort="periodic", budget=row["budget"], gold=row["gold"],
                   job=dict(task="answer",question=row["question"],source=source))
    yield from CONTEXT["synthetic"]()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["run", "verify"])
    ap.add_argument("--before", type=Path)
    ap.add_argument("--after", type=Path)
    ap.add_argument("--results", type=Path, default=HERE/"results.jsonl")
    args = ap.parse_args()
    panel = {c["id"]:c for c in cases()}
    if args.action == "run":
        if not args.before or not args.after:
            ap.error("run needs --before and --after pointing to the two binaries")
        paths = {"before":args.before.resolve(), "after":args.after.resolve()}
        hashes = {m:hashlib.sha256(p.read_bytes()).hexdigest() for m,p in paths.items()}
        for path in paths.values():
            doctor = json.loads(subprocess.check_output([path,"doctor"]))
            assert doctor["available"] and doctor["target"]=="AFM 3 Core Advanced" and doctor["route"]=="system"
        args.results.parent.mkdir(exist_ok=True,parents=True)
        with args.results.open("x") as output:
            for case in panel.values():
                for mode in sorted(paths,key=lambda m:hashlib.sha256((case["id"]+m).encode()).digest()):
                    start = time.monotonic_ns()
                    process = subprocess.run([paths[mode],"--compact","--timeout","45","--input-budget",str(case["budget"]),"run"],
                                             input=json.dumps(case["job"],ensure_ascii=False),text=True,capture_output=True)
                    elapsed = round((time.monotonic_ns()-start)/1e6)
                    record = json.loads(process.stdout)
                    if record["status"] == "error" and not any("timed out" in s for s in record["notes"]):
                        raise RuntimeError(record["notes"])
                    answer = (record["result"] or {}).get("answer")
                    m = record["metrics"]
                    row = dict(id=case["id"], mode=mode, binary=hashes[mode], status=record["status"], notes=record["notes"],
                               answer=answer, correct=CONTEXT["synth_score"](case,record["status"],answer),
                               elapsed_ms=elapsed, calls=m["calls"], token_checks=m["token_checks"],
                               sent=m["sent_input_tokens"], peak=m["peak_sent_tokens"], context=m.get("context"),
                               input_sha256=hashlib.sha256(case["job"]["source"].encode()).hexdigest())
                    output.write(json.dumps(row,ensure_ascii=False,separators=(",",":"))+"\n")
                    output.flush()
                    print(case["id"],mode,row["status"],row["correct"],row["token_checks"],row["sent"],flush=True)
    records = [json.loads(s) for s in args.results.read_text().splitlines()]
    assert len(records)==2*len(panel)
    assert {(r["id"],r["mode"]) for r in records} == {(i,m) for i in panel for m in ("before","after")}
    for mode in ("before","after"):
        assert len({r["binary"] for r in records if r["mode"]==mode})==1
    groups = defaultdict(list)
    for r in records:
        case = panel[r["id"]]
        assert r["correct"] == CONTEXT["synth_score"](case,r["status"],r["answer"])
        assert r["input_sha256"] == hashlib.sha256(case["job"]["source"].encode()).hexdigest()
        assert 0 <= r["peak"] <= case["budget"] and r["peak"] <= r["sent"]
        method = (r["context"] or {}).get("method")
        group = case["cohort"] if case["cohort"]=="periodic" else "previous"
        groups[group,r["mode"]].append(r)
        if method in ("full_scan","full_scan_fallback") and group=="previous":
            groups["ordinary windows",r["mode"]].append(r)
    for key, rows in sorted(groups.items()):
        print(*key,len(rows),sum(r["correct"] for r in rows),sum(r["sent"] for r in rows),
              sum(r["token_checks"] for r in rows),sum(r["calls"] for r in rows),median(r["elapsed_ms"] for r in rows))


if __name__ == "__main__":
    main()
