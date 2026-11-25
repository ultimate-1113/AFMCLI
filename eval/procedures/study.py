"""Paired, on-device study of imported text procedures and task decomposition."""
import argparse
from collections import Counter
import fcntl
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
from statistics import median
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
from bench import assess, compact, unguided
from external import SOURCES, fixtures

MODES = ("direct", "base", "skill", "decompose", "combined")
SKILL = {"answer": "short-evidence", "solve": "linear-declarations"}
SEED = "afmcli-procedures-20261125"
LIMIT = 250_000


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def datasets(directory):
    # The original panel consumes one article per question. Reserve those entire articles.
    previous = fixtures(directory)
    cases = []
    for name, strata in (("jsquad", [(True, 24)]), ("squad", [(True, 8), (False, 8)])):
        used = {r["group"] for r in previous if r["dataset"] == name}
        data = (directory / (name + ".json")).read_bytes()
        assert sha(data) == SOURCES[name][1]
        pool = []
        for article in json.loads(data)["data"]:
            for para in article["paragraphs"]:
                for q in para["qas"]:
                    gold = list(dict.fromkeys(a["text"] for a in q["answers"]))
                    pool.append(dict(id=name+"-"+q["id"], group=article["title"], cohort=name,
                                     job=dict(task="answer", source=para["context"], question=q["question"]),
                                     gold=gold, dataset=name))
        pool.sort(key=lambda r: sha((SEED+r["id"]).encode()))
        for answerable, count in strata:
            selected = 0
            for row in pool:
                if row["group"] in used or bool(row["gold"]) != answerable:
                    continue
                cases.append(row)
                used.add(row["group"])
                selected += 1
                if selected == count:
                    break
            if selected != count:
                raise ValueError("insufficient unused source groups for "+name)
    for c in read_lines(HERE/"linear.jsonl"):
        c["cohort"] = c["style"]
        cases.append(c)
    return cases


def freeze():
    paths = [*sorted((ROOT/"src").glob("*.rs")), ROOT/"Cargo.toml", ROOT/"Cargo.lock",
             HERE/"study.py", HERE/"linear.jsonl", HERE/"panel.json", HERE/"protocol.md",
             HERE.parent/"bench.py", HERE.parent/"external.py",
             *(ROOT/"skills"/name/"SKILL.md" for name in SKILL.values())]
    return {str(f.relative_to(ROOT)):sha(f.read_bytes()) for f in paths}


def panel(cases):
    return [dict(id=c["id"], cohort=c["cohort"], answerable=bool(c["gold"]) if c["job"]["task"] == "answer" else c["gold"] is not None)
            for c in cases]


OPS = {"gt":int.__gt__, "ge":int.__ge__, "eq":int.__eq__, "le":int.__le__, "lt":int.__lt__}


def feasible(problem):
    variables = problem["variables"]
    names = [v["name"] for v in variables]
    bounds = [range(v["min"],v["max"]+1) for v in variables]
    if len(set(names)) != len(names) or math.prod(len(r) for r in bounds)>LIMIT:
        raise ValueError("invalid model domain")
    constraints = problem["constraints"]
    objectives = problem["objectives"]
    if not objectives or any(len(r["weights"])!=len(names) for r in constraints+objectives):
        raise ValueError("invalid row")
    def dot(row, assignment):
        return sum(a*b for a,b in zip(row["weights"],assignment))
    values = {}
    for x in itertools.product(*bounds):
        if all(OPS[r["relation"]](dot(r,x),r["rhs"]) for r in constraints):
            values[x] = tuple(dot(r,x) for r in objectives)
    return names, values


def characterize(problem):
    names, values = feasible(problem)
    def order(v):
        return tuple(n if r["sense"]=="min" else -n for n,r in zip(v,problem["objectives"]))
    groups = {}
    for x, value in values.items():
        key = order(value)
        canonical = tuple(sorted(zip(names,x)))
        groups.setdefault(key,set()).add(canonical)
    ordered = [groups[k] for k in sorted(groups)]
    solution = dict(feasible=bool(values), assignment=None, objective_values=[], optimal_count=0)
    if values:
        best = min(values, key=lambda x:(order(values[x]),x))
        solution.update(assignment=dict(zip(names,best)), objective_values=list(values[best]), optimal_count=len(ordered[0]))
    return names, set().union(*ordered), ordered, solution


def grade(case, mode, response):
    state = response["status"]
    value = response["result"]
    direct = mode == "direct"
    if case["job"]["task"]=="answer":
        score = assess(case, "direct" if direct else "verified", response)
        answer = None
        if state not in ("error","refused","held"):
            try:
                candidate = unguided(value)["answer"] if direct else value["answer"]
                answer = candidate if direct or candidate is None else candidate["value"]
            except (KeyError,TypeError,ValueError):
                pass
        score["answered"] = isinstance(answer,str) and bool(answer.strip())
        score["unsupported_answer"] = not case["gold"] and score["answered"]
        return score
    model = None
    gold = case["gold"]
    if direct and state=="unverified":
        try: solution = unguided(value)
        except (ValueError,TypeError): solution=None
    elif state=="review" and isinstance(value,dict):
        solution = value.get("solution")
        model = value.get("interpretation")
    else:
        solution = None
    score = dict(passed=False, provided=isinstance(solution,dict), feasible_set=None,
                 preference_order=None, exact_ir=None)
    if gold is None:
        score["passed"] = state == "abstained"
        return score
    names, points, ranks, want = characterize(gold)
    score["passed"] = compact(solution)==compact(want)
    if model is not None:
        n, p, r, _ = characterize(model)
        score["feasible_set"] = set(n)==set(names) and p==points
        score["preference_order"] = score["feasible_set"] and r==ranks
        score["exact_ir"] = compact(model)==compact(gold)
    return score


def passed(record):
    score=record["score"]
    return score["pass"] if "pass" in score else score["passed"]


def measure(case,mode,args,env,bin_sha,run_key):
    task=case["job"]["task"]
    flags=[]
    if mode=="direct": flags.append("--direct")
    if mode in ("skill","combined"): flags += ["--skill",SKILL[task]]
    if mode in ("decompose","combined"): flags.append("--decompose")
    start=time.monotonic()
    run=subprocess.run([str(args.binary.resolve()),*flags,"--trace","run"],input=compact(case["job"]),
                       text=True,capture_output=True,timeout=240,env=env)
    if run.returncode not in (0,3):
        raise RuntimeError(f"{case['id']} {mode} exited {run.returncode}; check model availability")
    response=json.loads(run.stdout)
    return dict(id=case["id"], mode=mode, cohort=case["cohort"], binary_sha256=bin_sha, run_key=run_key,
                elapsed_ms=round(1000*(time.monotonic()-start)), response=response, score=grade(case,mode,response))


def publish(row, external):
    result=dict(row)
    response=result.pop("response")
    result.update(status=response["status"],metrics=response["metrics"])
    if not external: result["response"]=response
    return result


def summarize(rows):
    out={}
    for name in ("jsquad","squad","qa","formula","prose","complete","incomplete"):
        include={"qa":{"jsquad","squad"},"complete":{"formula","prose"}}.get(name,{name})
        members=[r for r in rows if r["cohort"] in include]
        base={r["id"]:passed(r) for r in members if r["mode"]=="base"}
        group={}
        for mode in MODES:
            rs=[r for r in members if r["mode"]==mode]
            group[mode]=dict(n=len(rs), passed=sum(passed(r) for r in rs),
                    gained=sum(passed(r) and not base[r["id"]] for r in rs),
                    lost=sum(not passed(r) and base[r["id"]] for r in rs),
                    states=dict(Counter(r["status"] for r in rs)), median_ms=median(r["elapsed_ms"] for r in rs),
                    calls=sum(r["metrics"]["calls"] for r in rs))
            for key in ("answered","unsupported_answer","provided","feasible_set","preference_order","exact_ir"):
                if any(key in r["score"] for r in rs):
                    group[mode][key]=sum(r["score"].get(key) is True for r in rs)
        out[name]=group
    return out


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("action",choices=("freeze","run","verify"))
    ap.add_argument("--data",type=Path,default=ROOT/"eval/work/data")
    ap.add_argument("--binary",type=Path,default=ROOT/"target/release/afmcli")
    ap.add_argument("--raw",type=Path,default=ROOT/"eval/work/procedures.jsonl")
    ap.add_argument("--results",type=Path,default=HERE)
    args=ap.parse_args()
    if args.action=="freeze":
        entries=panel(datasets(args.data))
        for filename, value in (("panel.json",entries),("lock.json",None)):
            with (HERE/filename).open("x") as f:
                json.dump(freeze() if value is None else value,f,indent=2,sort_keys=True)
                f.write("\n")
        return
    expected=json.loads((HERE/"lock.json").read_text())
    if expected!=freeze():
        raise RuntimeError("study implementation or materials differ from the lock; use the matching source tag")
    manifest=json.loads((HERE/"panel.json").read_text())
    keys={(c["id"],m) for c in manifest for m in MODES}
    if args.action=="run":
        cases=datasets(args.data)
        if panel(cases)!=manifest:raise ValueError("panel mismatch")
        by_id={c["id"]:c for c in cases}
        binary=args.binary.resolve()
        fingerprint=sha(binary.read_bytes())
        key=sha(compact([expected,fingerprint]).encode())
        args.raw.parent.mkdir(exist_ok=True,parents=True)
        with tempfile.TemporaryDirectory() as reg, args.raw.open("a+") as out:
            fcntl.flock(out,fcntl.LOCK_EX|fcntl.LOCK_NB)
            env=dict(os.environ,AFMCLI_SKILLS_DIR=reg)
            for name in SKILL.values():
                subprocess.run([str(binary),"skills","import",str(ROOT/"skills"/name)],check=True,env=env,stdout=subprocess.DEVNULL)
            rows=read_lines(args.raw)
            done={(r["id"],r["mode"]) for r in rows}
            if len(done)!=len(rows) or not done<=keys or any(r["run_key"]!=key for r in rows):raise ValueError("resume mismatch")
            for case_id,mode in sorted(keys,key=lambda k:sha((SEED+compact(k)).encode())):
                if (case_id,mode) not in done:
                    row=measure(by_id[case_id],mode,args,env,fingerprint,key)
                    out.write(compact(row)+"\n");out.flush();rows.append(row)
        args.results.mkdir(parents=True,exist_ok=True)
        with (args.results/"results.jsonl").open("w") as out:
            for r in rows:
                case=by_id[r["id"]]
                assert grade(case,r["mode"],r["response"])==r["score"]
                out.write(compact(publish(r,case["job"]["task"]=="answer"))+"\n")
    rows=read_lines(args.results/"results.jsonl")
    assert len(rows)==len(keys) and {(r["id"],r["mode"]) for r in rows}==keys
    hashes={r["binary_sha256"] for r in rows}
    assert len(hashes)==1 and {r["run_key"] for r in rows}=={sha(compact([expected,next(iter(hashes))]).encode())}
    original={c["id"]:c for c in read_lines(HERE/"linear.jsonl")}
    for r in rows:
        if r["id"] in original:
            assert grade(original[r["id"]],r["mode"],r["response"])==r["score"]
        else:
            assert set(r)=={"id","mode","cohort","binary_sha256","run_key","elapsed_ms","score","status","metrics"}
    summary=summarize(rows)
    path=args.results/"summary.json"
    if args.action=="run":path.write_text(json.dumps(summary,sort_keys=True,indent=2)+"\n")
    else:assert json.loads(path.read_text())==summary
    print("Verified",len(manifest),"cases and",len(rows),"paired trial records.")

if __name__=="__main__":
    main()
