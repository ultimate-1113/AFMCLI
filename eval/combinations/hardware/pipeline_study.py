"""Four public jobs per batch, serial AFM, and at most one retrieval in flight."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, ExitStack
import fcntl
import gzip
import json
import os
from pathlib import Path
import subprocess
import tempfile

import rank_study as base

CPU, ANE = base.CPU, base.ANE
clock, need, sha, encode = base.clock, base.need, base.sha, base.encode
# Each policy sees both scheduling orders across rounds. Each condition has positions summing to 9.
ORDER = [(CPU, "sequential"), (ANE, "prefetch"), (ANE, "sequential"), (CPU, "prefetch")]


class Tensor:
    def __init__(self, binary):
        self.binary = binary

    def search(self, case, policy):
        start = clock()
        lines, features = base.prepare(case["job"], warm=False)
        prepared = clock()
        obs = base.process(self.binary, policy, features)
        scored = clock()
        trial = obs["swift"]["trials"][0]
        selected = base.top(lines, trial["order"])
        return {"start": start, "prepared": prepared, "scored": scored, "end": clock(),
                "scores": trial["scores"], "rank": trial["order"], "selected": selected,
                "process_ns": obs["swift_process_ns"], "inner_ns": trial["queryTensorNS"] + trial["submitNS"] + trial["materializeNS"],
                "swift_details": {k: v for k, v in obs["swift"].items() if k != "trials"}}


class FixedIndexes:
    """Keep each document model loaded for its policy; record load and first prediction separately."""
    def __init__(self, binary, root, cases, stack):
        self.lines = {}
        self.servers = {}
        self.initial = []
        for case in cases:
            cid = case["id"]
            lines, features = base.prepare(case["job"], warm=False)
            self.lines[cid] = lines
            policies = (CPU, ANE) if len(self.lines)%2 else (ANE, CPU)
            for policy in policies:
                start = clock()
                proc = subprocess.Popen([str(binary), "serve", str(root/cid), policy], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
                stack.callback(self.close, proc)
                ready = json.loads(proc.stdout.readline())
                loaded = clock()
                need(ready["ready"] and ready["policy"] == policy and ready["dimension"] == base.DIM
                     and ready["row_count"] == len(lines), "fixed index mismatch")
                self.servers[policy, cid] = proc
                response = self.predict(policy, cid, [0])
                end = clock()
                query = [0] * base.DIM
                query[0] = 1
                ref, order = base.reference(dict(features, queries=[query]))[0]
                need(response["scores"] == ref and response["rank"] == order, "cold index output differs")
                self.initial.append({"id": cid, "policy": policy, "ready": ready, "launch_to_ready_ns": loaded-start,
                                     "first_prediction_ns": response["predict_ns"], "first_request_roundtrip_ns": end-loaded,
                                     "first_scores_sha256": sha(encode(ref))})

    @staticmethod
    def close(proc):
        proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdout.close()

    def predict(self, policy, cid, active):
        proc = self.servers[policy, cid]
        proc.stdin.write(encode({"id": cid, "active": active}).decode()+"\n")
        proc.stdin.flush()
        row = json.loads(proc.stdout.readline())
        need(row["id"] == cid, "wrong index response")
        return row

    def search(self, case, policy):
        cid = case["id"]
        start = clock()
        active = base.vector(case["job"]["question"], {})
        prepared = clock()
        response = self.predict(policy, cid, active)
        scored = clock()
        selected = base.top(self.lines[cid], response["rank"])
        return {"start": start, "prepared": prepared, "scored": scored, "end": clock(),
                "scores": response["scores"], "rank": response["rank"], "selected": selected,
                "process_ns": scored-prepared, "inner_ns": response["predict_ns"]}


class ObserveFM:
    """Observe the ordinary public backend's fm respond child after its input is delivered."""
    def __init__(self, on_submitted):
        self.on_submitted = on_submitted
        self.submitted = self.completed = self.spawned = None

    def __getattr__(self, name):
        return getattr(subprocess, name)

    def run(self, args, **options):
        if args[:2] != [base.backend.FM, "respond"]:
            return subprocess.run(args, **options)
        need(self.submitted is None and options.get("capture_output") is True, "unexpected additional reader")
        with subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
            self.spawned = clock()
            child.stdin.write(options["input"])
            child.stdin.close()
            child.stdin = None
            self.submitted = clock()
            self.on_submitted()
            try:
                stdout, stderr = child.communicate(timeout=options.get("timeout"))
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate()
                raise
            finally:
                self.completed = clock()
            return subprocess.CompletedProcess(args, child.returncode, stdout, stderr)


@contextmanager
def observed(observer):
    original = base.backend.subprocess
    base.backend.subprocess = observer
    try:
        yield
    finally:
        base.backend.subprocess = original


def run_batch(engine, cases, policy, schedule, round_no, order_position):
    jobs = []
    with ThreadPoolExecutor(max_workers=1) as worker:
        begin = clock()
        pending = None
        for i, case in enumerate(cases):
            search = engine.search(case, policy) if pending is None else pending.result()
            pending = None
            def submit_next():
                nonlocal pending
                if schedule == "prefetch" and i + 1 < len(cases):
                    pending = worker.submit(engine.search, cases[i+1], policy)
            io = base.backend.IO(paths={})
            tracker = ObserveFM(submit_next)
            reader_start = clock()
            with observed(tracker):
                answer, prompt_hash = base.read_extract(io, case["job"]["question"], search["selected"])
            reader_end = clock()
            need(tracker.submitted is not None, "one actual fm respond per job is required")
            jobs.append({"id": case["id"], "search": search, "reader_start": reader_start,
                         "fm_spawned": tracker.spawned, "fm_input_delivered": tracker.submitted,
                         "fm_finished": tracker.completed, "reader_end": reader_end,
                         "reader_events": io.events, "prompt_sha256": prompt_hash, "answer": answer})
        finish = clock()
    for case, job in zip(cases, jobs):
        job["grade"] = base.fixtures.grade(case, job["answer"])
    return {"round": round_no, "position": order_position, "policy": policy, "schedule": schedule,
            "begin": begin, "end": finish, "jobs": jobs}


def timeline(batch):
    jobs = batch["jobs"]
    overlaps = []
    for i, row in enumerate(jobs):
        s = row["search"]
        need(batch["begin"] <= s["start"] <= s["prepared"] <= s["scored"] <= s["end"] <= row["reader_start"]
             <= row["fm_spawned"] <= row["fm_input_delivered"] <= row["fm_finished"] <= row["reader_end"] <= batch["end"],
             "invalid query timeline")
        if i == 0:
            continue
        prev = jobs[i-1]
        need(prev["reader_end"] <= row["reader_start"], "two responses were in flight")
        if batch["schedule"] == "sequential":
            need(prev["reader_end"] <= s["start"], "unexpected early search")
        else:
            need(prev["fm_input_delivered"] <= s["start"], "search started before previous AFM received the prompt")
        overlap = max(0, min(s["end"], prev["fm_finished"])-max(s["start"], prev["fm_input_delivered"]))
        overlaps.append(base.to_ms(overlap))
    return {"round": batch["round"], "position": batch["position"], "policy": batch["policy"],
            "schedule": batch["schedule"], "batch_ms": base.to_ms(batch["end"]-batch["begin"]),
            "overlap_ms": round(sum(overlaps),3), "pairwise_overlap_ms": overlaps,
            "retrieval_ms": [base.to_ms(j["search"]["end"]-j["search"]["start"]) for j in jobs],
            "fm_process_after_input_ms": [base.to_ms(j["fm_finished"]-j["fm_input_delivered"]) for j in jobs]}


def summarize(batches):
    batch_times = [timeline(b) for b in batches]
    by_question = {}
    comparisons = []
    policies = {}
    for policy in (CPU, ANE):
        for round_no in (1, 2):
            vals = {x["schedule"]: x for x in batch_times if x["round"] == round_no and x["policy"] == policy}
            serial, ahead = vals["sequential"]["batch_ms"], vals["prefetch"]["batch_ms"]
            comparisons.append({"policy": policy, "round": round_no, "sequential_ms": serial, "prefetch_ms": ahead,
                                "saved_ms": round(serial-ahead,3), "saved_percent": round(100*(1-ahead/serial),2),
                                "actual_overlap_ms": vals["prefetch"]["overlap_ms"]})
        rows = [j for b in batches if b["policy"] == policy for j in b["jobs"]]
        policies[policy] = {"query_preparation": base.distribution([j["search"]["prepared"]-j["search"]["start"] for j in rows]),
                            "swift_process_or_index_round_trip": base.distribution([j["search"]["process_ns"] for j in rows]),
                            "inner_tensor_or_prediction": base.distribution([j["search"]["inner_ns"] for j in rows]),
                            "retrieval_to_selected": base.distribution([j["search"]["end"]-j["search"]["start"] for j in rows]),
                            "grade_counts": dict(Counter(j["grade"]["outcome"] for j in rows))}
    for cid in sorted({j["id"] for b in batches for j in b["jobs"]}):
        jobs = [j for b in batches for j in b["jobs"] if j["id"] == cid]
        def distinct(values):
            return len({sha(encode(v)) for v in values})
        need(distinct(j["search"]["scores"] for j in jobs) == distinct(j["search"]["rank"] for j in jobs) ==
             distinct(j["search"]["selected"] for j in jobs) == distinct(j["prompt_sha256"] for j in jobs) == 1, "different inputs")
        by_question[cid] = {"runs": len(jobs), "select_and_prompt_identical": True,
                            "distinct_validated_answers": distinct(j["answer"] for j in jobs),
                            "distinct_raw_structured_answers": distinct(j["reader_events"][0]["raw"].get("raw_output") for j in jobs),
                            "grades": dict(Counter(j["grade"]["outcome"] for j in jobs))}
    return {"batches": batch_times, "paired": comparisons, "by_policy": policies, "by_question": by_question,
            "fm_submissions": sum(len(b["jobs"]) for b in batches), "simultaneous_fm_max": 1}


def orders(cases):
    for r in (1, 2):
        for slot, (policy, schedule) in enumerate(ORDER if r == 1 else reversed(ORDER), 1):
            yield r, slot, policy, schedule, cases if r == 1 else list(reversed(cases))


def verify(args):
    info = json.loads((args.out/"run.json").read_bytes())
    for path in (Path(__file__), Path(base.__file__), Path(base.backend.__file__), base.HERE/"Rank.swift", base.OLD/"lock.json"):
        need(sha(path.read_bytes()) == info["source_files"][path.name], "frozen replay source differs: " + path.name)
    cases = [c for c, _, _, eligible in base.load_cases() if eligible]
    ref = {}
    for c in cases:
        lines, f = base.prepare(c["job"], warm=False)
        scores, order = base.reference(f)[0]
        ref[c["id"]] = scores, order, base.top(lines, order)
    if info["engine"] == "fixed":
        need(len(info["index_processes"]) == 8, "missing index lifecycle")
    with gzip.open(args.out/"batches.jsonl.gz", "rt") as f:
        batches = [json.loads(line) for line in f]
    need(len(batches) == 8, "missing batches")
    for batch, (r, pos, policy, schedule, ordered) in zip(batches, orders(cases)):
        need((batch["round"], batch["position"], batch["policy"], batch["schedule"]) == (r, pos, policy, schedule), "wrong crossover")
        need([row["id"] for row in batch["jobs"]] == [c["id"] for c in ordered], "wrong job order")
        for row, c in zip(batch["jobs"], ordered):
            s = row["search"]
            need((s["scores"], s["rank"], s["selected"]) == ref[c["id"]], "scores, rank or original bytes differ")
            io = base.backend.IO(archive=row["reader_events"])
            answer, digest = base.read_extract(io, c["job"]["question"], s["selected"])
            need(len(io.events) == len(row["reader_events"]) == 1 and io.events[0]["raw"]["sent"], "unobserved reader")
            need(digest == row["prompt_sha256"] and answer == row["answer"] and base.fixtures.grade(c, answer) == row["grade"], "reader replay differs")
    need(json.loads((args.out/"summary.json").read_bytes()) == summarize(batches), "timing or grades changed")
    print("Verified 32 model submissions, ranks, answer spans and synchronized batch timelines.")


def capture(args):
    selected = [r for r in base.load_cases() if r[3]]
    cases = [r[0] for r in selected]
    args.out.mkdir(parents=True, exist_ok=True)
    need(not any(args.out.iterdir()), "use an empty output directory")
    lock = Path(tempfile.gettempdir()) / ("afmcli-moe-system-%s.lock" % os.getuid())
    with lock.open("a+") as lockfile, ExitStack() as stack:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        meta = base.manifest(args, selected)
        meta["source_files"][Path(__file__).name] = sha(Path(__file__).read_bytes())
        meta["engine"] = args.engine
        if args.engine == "fixed":
            need(args.index_root and args.index_root.is_dir(), "fixed index directories are required")
            engine = FixedIndexes(args.binary, args.index_root, cases, stack)
            meta["index_processes"] = engine.initial
        else:
            engine = Tensor(args.binary)
        base.write_json(args.out/"run.json", meta)
        batches = []
        with gzip.open(args.out/"batches.jsonl.gz", "wt", encoding="utf-8") as out:
            for r, pos, policy, schedule, jobs in orders(cases):
                batch = run_batch(engine, jobs, policy, schedule, r, pos)
                batches.append(batch)
                print(encode(batch).decode(), file=out, flush=True)
                t = timeline(batch)
                print(r, pos, policy, schedule, t["batch_ms"], t["overlap_ms"], flush=True)
    base.write_json(args.out/"summary.json", summarize(batches))
    verify(args)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    live = subs.add_parser("capture")
    live.add_argument("--engine", choices=("tensor","fixed"), required=True)
    live.add_argument("--binary", type=Path, required=True)
    live.add_argument("--index-root", type=Path)
    live.add_argument("--doctor", type=Path, required=True)
    for sub in (live, subs.add_parser("verify")):
        sub.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    (capture if args.command == "capture" else verify)(args)
