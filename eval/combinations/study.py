"""Measure the frozen fresh panel serially, or reconstruct every decision without a model."""
import argparse
from collections import Counter
from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time

import arithmetic
import panels
import qa
from backend import CAP, HERE, INSTRUCTION, IO, SYSTEM, count, encode, need, number, preflight, sha


def load_grader():
    spec = importlib.util.spec_from_file_location("index_status_fixture_grader", HERE.parent / "index_status/fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.grade


qa_grade = load_grader()
ARMS = {"qa": ("indexed", "checked", "packet", "anchored", "selective", "independent", "arbitrate"),
        "calc": ("plain", "native", "native_value", "formula", "evidence_formula", "parallel", "selective")}
LOCK = HERE / "lock.json"


def save_json(path, value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(f.name, path)


@contextmanager
def system_lock():
    path = Path(tempfile.gettempdir()) / f"afmcli-moe-system-{os.getuid()}.lock"
    with path.open("a") as gate:
        fcntl.flock(gate, fcntl.LOCK_EX)
        yield


def code_hashes():
    files = [HERE / n for n in ("qa.py", "arithmetic.py", "panels.py", "study.py", "backend.py", "protocol.md")]
    files += sorted((HERE / "fixtures").glob("*.json"))
    files += [HERE.parent / n for n in ("index_status/fixtures.py", "calculator/native/run.py",
                                         "calculator/native/words.py", "calculator/verify.py", "calculator/probe.py")]
    return {p.relative_to(HERE.parent).as_posix(): sha(p.read_bytes()) for p in files}


def get_cases(lengths):
    qs, ms = panels.qa_cases(lengths), panels.calc_cases()
    need(len(qs) == 24 and len(ms) == 16, "unexpected panel size")
    need(Counter((c["tier"], c["language"], c["positive"]) for c in qs) ==
         Counter({(t, lang, flag): 3 for t in ("8k", "16k") for lang in ("ja", "en") for flag in (True, False)}),
         "unbalanced document lengths, languages or outcomes")
    for group in (qs, ms):
        need(len(group) == len({c["id"] for c in group}), "reused case ID")
        need(set(Counter(c["pair"] for c in group).values()) == {2}, "unmatched pair")
        for pair in {c["pair"] for c in group}:
            a,b = [c for c in group if c["pair"] == pair]
            if group is qs:
                need(a["job"]["question"] == b["job"]["question"] and a["positive"] != b["positive"], "unpaired QA")
            else:
                need((a["expected"] is None) != (b["expected"] is None) and a["question"] != b["question"],
                     "unpaired arithmetic")
    need(Counter((c["language"], c["expected"] is None) for c in ms) ==
         Counter({(lang, empty):4 for lang in ("ja", "en") for empty in (False, True)}), "unbalanced arithmetic")
    return {"qa": qs, "calc": ms}


def freeze():
    need(not LOCK.exists(), "a frozen panel already exists; verify it or use a separate study")
    panels.validate()
    lengths, tokens = {}, {}
    for base in panels.qa_cases():
        key = base["pair"]
        if key in lengths:
            continue
        goal = int(base["tier"][:-1]) * 1024
        n = base["filler_count"]
        for _ in range(10):
            pair = [c for c in panels.qa_cases({key:n}) if c["pair"] == key]
            measured = [count(c["job"]["source"]) for c in pair]
            if all(abs(v-goal) <= .03*goal for v in measured):
                break
            n = max(8, round(n*goal/(sum(measured)/len(measured))))
        else:
            raise ValueError("length calibration failed for " + key)
        lengths[key] = n
        tokens.update({c["id"]:v for c,v in zip(pair,measured)})
        print(key, measured, flush=True)
    groups = get_cases(lengths)
    visible = {c["id"]:count(INSTRUCTION + "\n\n" + c["question"]) for c in groups["calc"]}
    data = {"lengths":lengths,"source_tokens":tokens,"native_visible_prompt_tokens":visible,"code":code_hashes(),
            "cases":{k:{c["id"]:sha(encode(c)) for c in g} for k,g in groups.items()}}
    save_json(LOCK, data)


def frozen():
    data = json.loads(LOCK.read_bytes())
    need(data["code"] == code_hashes(), "frozen source changed")
    groups = get_cases(data["lengths"])
    for track, cases in groups.items():
        need({c["id"]:sha(encode(c)) for c in cases} == data["cases"][track], "frozen inputs or oracle changed")
    for c in groups["qa"]:
        target = int(c["tier"][:-1])*1024
        need(abs(data["source_tokens"][c["id"]]-target) <= target*.03, "uncalibrated case")
    need(set(data["native_visible_prompt_tokens"]) == set(data["cases"]["calc"]), "incomplete native input counts")
    return data, groups


def schedule(track, cases):
    methods = ARMS[track]
    order = [0, 1, 6, 2, 5, 3, 4]
    for i, case in enumerate(sorted(cases, key=lambda c:sha(("combo-order-1/"+c["id"]).encode()))):
        indices = order if (i//7)%2 == 0 else reversed(order)
        for n in indices:
            yield case, methods[(i+n)%len(methods)]


def execute(track, arm, case, io):
    if track == "qa":
        job = {k:case["job"][k] for k in ("task","question","source")}
        if arm == "checked":
            result, notes = io.checked_index(job, True), {"route":"published-checked"}
        else:
            result, notes = qa.execute(arm, job, io)
        outcome = {k:result.get(k) for k in ("status","result")}
        return outcome, notes
    if arm == "plain":
        raw = io.native(case["question"], "unregistered")
        outcome = {k:raw[k] for k in ("kind","value")}
        notes = {"route":"unregistered-final"}
    else:
        outcome, notes = arithmetic.execute(arm, {"question":case["question"]}, io)
    return outcome, notes


def grade(track, case, outcome):
    if track == "qa":
        return qa_grade(case, outcome)
    need(outcome["kind"] in ("unknown","number","held"), "unrecognized math output")
    gold, actual = number(case["expected"]), number(outcome["value"])
    need((actual is not None) == (outcome["kind"] == "number"), "invalid math number or output type")
    correct = outcome["kind"] == "unknown" if gold is None else actual is not None and actual == gold
    return {"outcome":outcome["kind"],"correct":correct,"false_nonnull":gold is None and actual is not None}


def accounting(events, visible):
    calls = tokens = max_tokens = native = native_text = callbacks = host_calc = 0
    counts = Counter()
    for event in events:
        kind, raw = event["kind"], event["raw"]
        counts[kind] += 1
        if kind in ("indexed","checked"):
            m = raw["metrics"]
            tokens += m["sent_input_tokens"]
            calls += m["calls"]
            max_tokens = max(max_tokens,m["peak_sent_tokens"])
        elif kind == "read":
            if raw["sent"]:
                calls += 1
                tokens += raw["input_tokens"]
                max_tokens = max(max_tokens,raw["input_tokens"])
            counts["read_"+raw["status"]] += 1
        elif kind == "native":
            emitted = [e["event"] for e in raw["events"]]
            requests = emitted.count("request")
            native += requests
            native_text += visible*requests
            callbacks += emitted.count("callback")
            calls += requests
        else:
            need(kind == "calc", "unknown step")
            host_calc += 1
    return {"requests":calls,"counted_input_tokens":tokens,"peak_counted_input_tokens":max_tokens,
            "native_requests":native,"native_visible_prompt_tokens":native_text,"native_callbacks":callbacks,
            "host_calc":host_calc,"steps":dict(counts)}


def header(track, case, arm, manifest):
    return {"track":track,"arm":arm,"id":case["id"],"pair":case["pair"],"language":case["language"],
            "case_sha256":sha(encode(case)),"manifest_sha256":sha(encode(manifest))}


def check_row(row, item, track, lock, manifest):
    case,arm = item
    need(all(row[k]==v for k,v in header(track,case,arm,manifest).items()), "row identity or binary changed")
    io = IO(archive=row["events"])
    final,notes = execute(track,arm,case,io)
    score = grade(track,case,final)
    need(len(io.events) == len(row["events"]), "extra recorded steps")
    observed = accounting(io.events,lock["native_visible_prompt_tokens"].get(case["id"],0))
    for data, expected in ((final,row["final"]),(notes,row["notes"]),(score,row["score"]),(observed,row["usage"])):
        need(encode(data)==encode(expected), "replay differs for "+row["id"]+"/"+row["arm"])
    need(isinstance(row["elapsed_ms"],(int,float)) and row["elapsed_ms"] >= 0
         and row["elapsed_ms"]+1 >= sum(e["elapsed_ms"] for e in io.events), "invalid full duration")


def read_prefix(path, items, track, lock, manifest):
    rows = []
    if path.exists():
        with path.open("rb") as file:
            for line in file:
                need(line.endswith(b"\n") and len(rows)<len(items), "truncated or excessive archive")
                row = json.loads(line)
                check_row(row,items[len(rows)],track,lock,manifest)
                rows.append(row)
    return rows


def confirm_manifest(m, lock):
    need(m["lock_sha256"]==sha(encode(lock)) and m["model"].get("available") is True
         and m["model"].get("variant")==SYSTEM and m["model"].get("route")=="system", "wrong study/model")
    hashes = m["binaries"]
    need(set(hashes) == {"indexed","checked","native","calculator"} and
         all(isinstance(h,str) and len(h)==64 and set(h)<=set("0123456789abcdef") for h in hashes.values()),
         "missing binary hashes")


def run(args, lock, cases):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    paths = {k:getattr(args,k).resolve(strict=True) for k in ("indexed","checked","native","calculator")}
    hashes = {k:sha(p.read_bytes()) for k,p in paths.items()}
    manifest = {"lock_sha256":sha(encode(lock)),"model":preflight(paths),"binaries":hashes}
    confirm_manifest(manifest,lock)
    dest = out / "manifest.json"
    if dest.exists():
        need(json.loads(dest.read_bytes())==manifest, "changed binary or environment; use a new output directory")
    else:
        save_json(dest,manifest)
    for track in args.tracks:
        items = list(schedule(track,cases[track]))
        path = out/(track+".jsonl")
        done = read_prefix(path,items,track,lock,manifest)
        with path.open("ab") as stream:
            for pos,(case,arm) in enumerate(items[len(done):],len(done)+1):
                io = IO(paths)
                t = time.monotonic_ns()
                final,notes = execute(track,arm,case,io)
                ms = round((time.monotonic_ns()-t)/1e6,3)
                score = grade(track,case,final)
                row = dict(header(track,case,arm,manifest),final=final,notes=notes,score=score,
                           events=io.events,usage=accounting(io.events,lock["native_visible_prompt_tokens"].get(case["id"],0)),
                           elapsed_ms=ms)
                check_row(row,(case,arm),track,lock,manifest)
                stream.write(encode(row)+b"\n")
                stream.flush()
                os.fsync(stream.fileno())
                print(track,pos,len(items),case["id"],arm,score["outcome"],flush=True)
    need({k:sha(p.read_bytes()) for k,p in paths.items()}==hashes,"binary changed during run")


def load_results(out, tracks):
    lock,cases = frozen()
    manifest = json.loads((out/"manifest.json").read_bytes())
    confirm_manifest(manifest,lock)
    all_rows = {}
    for track in tracks:
        items = list(schedule(track,cases[track]))
        all_rows[track] = read_prefix(out/(track+".jsonl"),items,track,lock,manifest)
        need(len(all_rows[track])==len(items),"incomplete "+track)
    return cases,all_rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest="action",required=True)
    commands.add_parser("freeze")
    measurement=commands.add_parser("run")
    for arg in ("indexed","checked","native","calculator"):
        measurement.add_argument("--"+arg,type=Path,required=True)
    for sub in (measurement,commands.add_parser("verify"),commands.add_parser("report")):
        sub.add_argument("--out",type=Path,default=HERE/"results")
        sub.add_argument("--tracks",choices=("calc","qa"),nargs="+",default=["calc","qa"])
    args=parser.parse_args()
    if args.action=="freeze":
        with system_lock():
            freeze()
    elif args.action=="run":
        lock,cases=frozen()
        with system_lock():
            run(args,lock,cases)
    else:
        cases,rows=load_results(args.out,args.tracks)
        if args.action=="verify":
            print("Verified", {k:len(v) for k,v in rows.items()})
        else:
            from reporting import report
            print(json.dumps(report(cases,rows),ensure_ascii=False,sort_keys=True,indent=2))


if __name__=="__main__":
    main()
