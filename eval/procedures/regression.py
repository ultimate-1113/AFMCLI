"""Replay the original 101 cases and test optional procedures on every applicable job."""
import argparse
from collections import Counter
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from study import HERE, ROOT, SKILL, assess, compact, fixtures, freeze, read_lines, sha

HISTORY = HERE.parent / "row_guard" / "compatibility.jsonl"
MODES = ("base", "skill", "decompose", "combined")


def applicable(case):
    job = case["job"]
    return job["task"] == "answer" or (job["task"] == "solve" and "source" in job)


def historical():
    return {r["id"]: r for r in read_lines(HISTORY) if r["mode"] == "verified"}


def run_signature(fingerprint, keys):
    inputs = [fingerprint, sha((HERE/"lock.json").read_bytes()), sha(HISTORY.read_bytes()),
              sha(Path(__file__).read_bytes()), sorted(keys)]
    return sha(compact(inputs).encode())


def run(args):
    previous = historical()
    cases = read_lines(HERE.parent / "heldout.jsonl") + fixtures(args.data)
    assert {c["id"] for c in cases} == set(previous)
    cases_by_id = {c["id"]: c for c in cases}
    keys = [(c["id"], mode) for c in cases for mode in (MODES if applicable(c) else ("base",))]
    lock = json.loads((HERE / "lock.json").read_text())
    assert freeze() == lock
    binary = args.binary.resolve()
    fingerprint = sha(binary.read_bytes())
    signature = run_signature(fingerprint, keys)
    args.raw.parent.mkdir(exist_ok=True, parents=True)
    with tempfile.TemporaryDirectory() as registry, args.raw.open("a+") as out:
        fcntl.flock(out, fcntl.LOCK_EX | fcntl.LOCK_NB)
        env = dict(os.environ, AFMCLI_SKILLS_DIR=registry)
        for name in SKILL.values():
            subprocess.run([str(binary), "skills", "import", str(ROOT / "skills" / name)],
                           check=True, env=env, stdout=subprocess.DEVNULL)
        rows = read_lines(args.raw)
        done = {(r["id"],r["mode"]) for r in rows}
        assert done <= set(keys) and len(done) == len(rows) and all(r["run_key"] == signature for r in rows)
        for case_id, mode in sorted(keys, key=lambda pair: sha(compact(pair).encode())):
            if (case_id,mode) in done:
                continue
            c = cases_by_id[case_id]
            task = c["job"]["task"]
            flags = []
            if mode in ("skill","combined"):
                flags.extend(["--skill", SKILL[task]])
            if mode in ("decompose","combined"):
                flags.append("--decompose")
            start = time.monotonic()
            proc = subprocess.run([str(binary), *flags, "--trace", "run"], env=env,
                                  input=compact(c["job"]), text=True, capture_output=True, timeout=240)
            rec = json.loads(proc.stdout)
            if proc.returncode not in (0,3) and not (proc.returncode == 2 and rec.get("notes") == ["fm timed out"]):
                raise RuntimeError(f"{case_id} {mode}: CLI exited {proc.returncode}")
            row = dict(id=case_id, task=task, cohort=c.get("dataset","heldout"), mode=mode,
                       binary_sha256=fingerprint, run_key=signature,
                       elapsed_ms=round((time.monotonic()-start)*1000), response=rec,
                       score=assess(c,"verified",rec))
            out.write(compact(row)+"\n")
            out.flush()
            rows.append(row)
    with args.results.open("w") as out:
        for r in rows:
            assert r["score"] == assess(cases_by_id[r["id"]],"verified",r["response"])
            safe = {k:v for k,v in r.items() if k != "response"}
            safe.update(status=r["response"]["status"], metrics=r["response"]["metrics"])
            out.write(compact(safe)+"\n")


def verify(path):
    old = historical()
    held = read_lines(HERE.parent / "heldout.jsonl")
    active = {case["id"] for case in held if applicable(case)}
    active.update(k for k,r in old.items() if r["task"]=="answer")
    keys = {(k,m) for k in old for m in (MODES if k in active else ("base",))}
    rows = read_lines(path)
    assert len(old) == 101 and len(rows) == len(keys) and {(r["id"],r["mode"]) for r in rows} == keys
    fingerprints = {r["binary_sha256"] for r in rows}
    assert len(fingerprints)==1
    assert {r["run_key"] for r in rows} == {run_signature(next(iter(fingerprints)),keys)}
    allowed = {"id","task","cohort","mode","binary_sha256","run_key","elapsed_ms","score","status","metrics"}
    assert all(set(r)==allowed for r in rows)
    assert len({r["binary_sha256"] for r in rows}) == len({r["run_key"] for r in rows}) == 1
    for r in rows:
        assert set(r) == {"id","task","cohort","mode","binary_sha256","run_key","elapsed_ms","score","status","metrics"}
        assert type(r["score"]["pass"]) is bool and r["task"] == old[r["id"]]["task"]
        assert r["elapsed_ms"] >= 0 and r["metrics"]["calls"] >= 0
    lookup = {(r["id"],r["mode"]):r["score"]["pass"] for r in rows}
    for label, ids in (("all",set(old)), ("answer",{k for k in active if old[k]["task"]=="answer"}),
                       ("natural solve",{k for k in active if old[k]["task"]=="solve"})):
        for mode in (("base",) if label=="all" else MODES):
            rs=[r for r in rows if r["id"] in ids and r["mode"]==mode]
            if mode=="base":
                before={k:old[k]["score"]["pass"] for k in ids}
            else:
                before={k:lookup[k,"base"] for k in ids}
            after={r["id"]:r["score"]["pass"] for r in rs}
            gained=sum(after[k] and not before[k] for k in after)
            lost=sum(not after[k] and before[k] for k in after)
            print(label,mode, f"{sum(after.values())}/{len(rs)}", f"gained {gained}, lost {lost}",
                  dict(Counter(r["status"] for r in rs)))
    print("Verified", len(rows), "regression records.")


if __name__ == "__main__":
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action",choices=("run","verify"))
    ap.add_argument("--binary",type=Path,default=ROOT/"target/release/afmcli")
    ap.add_argument("--raw",type=Path,default=ROOT/"eval/work/procedures-regression.jsonl")
    ap.add_argument("--data",type=Path,default=ROOT/"eval/work/data")
    ap.add_argument("--results",type=Path,default=HERE/"regression.jsonl")
    args=ap.parse_args()
    if args.action=="run":
        run(args)
    verify(args.results)
