"""Public synthetic documents -> fixed features -> MLTensor top lines -> optional system AFM."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter_ns as clock
import unicodedata

HERE = Path(__file__).resolve().parent
OLD = HERE.parents[1] / "index_status"
sys.path[:0] = [str(HERE.parent), str(OLD)]
import backend
import fixtures

CPU, ANE = "cpuOnly", "cpuAndNeuralEngine"
# Tier, pair, published member, first policy. First four have the AFM connection.
PANEL = [("8k", 1, True, CPU), ("8k", 2, False, ANE), ("16k", 2, False, CPU), ("16k", 1, True, ANE),
         ("8k", 4, True, CPU), ("8k", 3, False, ANE), ("16k", 3, False, CPU), ("16k", 4, True, ANE)]
DIM, K = 1024, 12
TOKEN = re.compile(r"[a-z0-9]+|[ぁ-ヿ㐀-鿿々]+")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["state", "value", "line"],
          "properties": {"state": {"type": "string", "enum": ["value", "unknown"]},
                         "value": {"type": "string"}, "line": {"type": "integer"}}}
GUIDE = """Read the original lines selected for the question below. Answer only the currently settled fact about the named subject. Copy the shortest exact answer value from its authoritative line, and return that line's numeric ID. A suggestion, suspended entry, or different subject cannot establish a current value. When the extracts do not establish it, use state unknown, an empty value, and line 0.
"""
encode, sha, need = backend.encode, backend.sha, backend.need


def load_cases():
    lock = json.loads((OLD / "lock.json").read_bytes())
    tokens = json.loads((OLD / "source_tokens.json").read_bytes())
    for rel, digest in lock["files"].items():
        need(sha((OLD / rel).read_bytes()) == digest, "old fixture file changed: " + rel)
    out = []
    for i, (tier, index, positive, first) in enumerate(PANEL):
        c = fixtures.make_case("test", tier, index, positive)
        need(sha(encode(c)) == lock["cases"][c["id"]], "old case changed")
        out.append((c, tokens[c["id"]], (first, ANE if first == CPU else CPU), i < 4))
    return out


def split_source(source):
    lines = []
    start = 0
    for n, s in enumerate(source.encode().split(b"\n"), 1):
        if s.strip():
            lines.append({"line": n, "start_byte": start, "end_byte": start + len(s), "text": s.decode()})
        start += len(s) + 1
    return lines


def vector(text, memo):
    """Binary occupancy in fixed SHA-256 buckets. Text preprocessing runs on the host."""
    terms = TOKEN.findall(unicodedata.normalize("NFKC", text).casefold())
    keys = set()
    previous = None
    for term in terms:
        if term.isascii():
            keys.add("word:" + term)
            if previous is not None:
                keys.add("words:" + previous + " " + term)
            previous = term
            keys.update("lat3:" + term[i:i+3] for i in range(len(term)-2))
        else:
            previous = None
            for n in (1, 2, 3):
                keys.update("cjk:" + term[i:i+n] for i in range(len(term)-n+1))
    active = set()
    for key in keys:
        if key not in memo:
            memo[key] = int.from_bytes(hashlib.sha256(key.encode()).digest()[:4], "big") % DIM
        active.add(memo[key])
    return sorted(active)


def prepare(job, warm):
    lines = split_source(job["source"])
    memo = {}
    rows = [vector(line["text"], memo) for line in lines]
    active = vector(job["question"], memo)
    base = [0] * DIM
    for col in active:
        base[col] = 1
    queries = [base]
    if warm:
        # Distinct observed feature columns guarantee distinct outputs with the same graph shape.
        signatures = set()
        for col in active:
            signature = tuple(i for i, row in enumerate(rows) if col in row)
            if not signature or signature in signatures:
                continue
            signatures.add(signature)
            q = base.copy()
            q[col] = 2
            queries.append(q)
            if len(queries) == 4:
                break
        need(len(queries) == 4, "could not construct distinct warm queries")
    return lines, {"rows": rows, "queries": queries, "dimension": DIM}


def reference(request):
    values = []
    for q in request["queries"]:
        scores = [sum(q[j] for j in row) for row in request["rows"]]
        order = sorted(range(len(scores)), key=lambda j: (-scores[j], j))
        values.append((scores, order))
    return values


def top(lines, order):
    return sorted((lines[i] for i in order[:K]), key=lambda x: x["line"])


def process(binary, policy, features):
    payload = encode(dict(features, policy=policy))
    t = clock()
    run = subprocess.run([str(binary)], input=payload, capture_output=True, timeout=150)
    wall = clock()-t
    if run.returncode:
        raise RuntimeError("MLTensor exited %s: %s" % (run.returncode, run.stderr.decode(errors="replace")[:1800]))
    data = json.loads(run.stdout)
    need(data["policy"] == policy, "unexpected compute policy")
    return {"swift": data, "swift_process_ns": wall, "swift_stderr_sha256": sha(run.stderr)}


def check_tensor(policy, lines, features, obs):
    before = clock()
    expected = reference(features)
    elapsed = clock()-before
    swift = obs["swift"]
    need(swift["policy"] == policy and len(swift["trials"]) == len(expected), "tensor observations missing")
    checks = []
    for query, (scores, ranks), trial in zip(features["queries"], expected, swift["trials"]):
        need(trial["scores"] == scores and trial["order"] == ranks, "score or tie order differs")
        snippet = top(lines, trial["order"])
        need(snippet == top(lines, ranks), "selected source lines differ")
        checks.append({"query_sha256": sha(encode(query)), "scores_sha256": sha(encode(scores)),
                       "order_sha256": sha(encode(ranks)), "selected_sha256": sha(encode(snippet))})
    need(len(set(c["scores_sha256"] for c in checks)) == len(checks), "warm readbacks did not change")
    return checks, elapsed


def as_record(answer, selected):
    if not isinstance(answer, dict):
        return {"status": "held", "result": None, "reason": "reader_failed"}
    kind, value, line_id = answer.get("state"), answer.get("value"), answer.get("line")
    if kind == "unknown" and value == "" and type(line_id) is int and line_id == 0:
        return {"status": "abstained", "result": {"answer": None}}
    if kind != "value" or not isinstance(value, str) or not value or type(line_id) is not int:
        return {"status": "held", "result": None, "reason": "inconsistent_reader_fields"}
    spans = []
    for line in selected:
        if line["line"] != line_id:
            continue
        raw, needle = line["text"].encode(), value.encode()
        start = raw.find(needle)
        if start >= 0 and raw.find(needle, start+1) < 0:
            a = line["start_byte"] + start
            spans.append({"start_byte": a, "end_byte": a+len(needle)})
    return {"status": "ok", "result": {"answer": {"value": value, "spans": spans}}}


def read_extract(io, question, selected):
    packet = {"question": question, "lines": [{"line": r["line"], "text": r["text"]} for r in selected]}
    prompt = GUIDE + encode(packet).decode()
    value = io.read(prompt, SCHEMA, max_input=3600)
    return as_record(value, selected), sha(prompt.encode())


def capture_run(binary, case, policy, job_type):
    before = clock()
    lines, features = prepare(case["job"], warm=job_type == "tensor")
    features_done = clock()
    obs = process(binary, policy, features)
    ranked = clock()
    selected = top(lines, obs["swift"]["trials"][0]["order"])
    selected_at = clock()
    if job_type == "afm":
        io = backend.IO(paths={})
        answer, prompt_sha = read_extract(io, case["job"]["question"], selected)
        ended = clock()
    row = {"id": case["id"], "policy": policy, "task": job_type, "candidate_count": len(lines),
           "source_sha256": sha(case["job"]["source"].encode()), "features_sha256": sha(encode(features)),
           "selected": selected, "selected_sha256": sha(encode(selected)), "feature_ns": features_done-before,
           "pipe_and_rank_ns": ranked-features_done, "selection_ns": selected_at-ranked, **obs}
    if job_type == "afm":
        row.update(reader_events=io.events, prompt_sha256=prompt_sha, answer=answer,
                   grade=fixtures.grade(case, answer), e2e_actual_ns=ended-before)
    checks, elapsed = check_tensor(policy, lines, features, obs)
    row.update(reference_checks=checks, integer_reference_ns=elapsed)
    return row


def to_ms(ns):
    return round(ns/1e6, 3)


def distribution(ns):
    return {"n": len(ns), "median_ms": to_ms(statistics.median(ns)),
            "min_ms": to_ms(min(ns)), "max_ms": to_ms(max(ns))}


def summary(rows):
    output = {"source_documents": len({r["id"] for r in rows}), "policies": {},
              "scored_candidates_checked": sum(len(t["scores"]) for r in rows if r["task"] == "tensor"
                                               for t in r["swift"]["trials"]),
              "tensor_trials_checked": sum(len(r["swift"]["trials"]) for r in rows if r["task"] == "tensor"),
              "pairs_with_equal_reader_input": 0}
    for policy in (CPU, ANE):
        tensor = [r for r in rows if r["task"] == "tensor" and r["policy"] == policy]
        afm = [r for r in rows if r["task"] == "afm" and r["policy"] == policy]
        first, warm = [], []
        for r in tensor:
            for i, t in enumerate(r["swift"]["trials"]):
                (warm if i else first).append(t["queryTensorNS"] + t["submitNS"] + t["materializeNS"])
        output["policies"][policy] = {
            "first_query_through_readback": distribution(first),
            "warm_query_through_readback": distribution(warm),
            "four_queries_swift_process": distribution([r["swift_process_ns"] for r in tensor]),
            "host_features_and_warm_queries": distribution([r["feature_ns"] for r in tensor]),
            "one_query_host_features": distribution([r["feature_ns"] for r in afm]),
            "one_query_swift_process": distribution([r["swift_process_ns"] for r in afm]),
            "actual_e2e_including_afm": distribution([r["e2e_actual_ns"] for r in afm]),
            "reader_observed": distribution([r["reader_events"][0]["elapsed_ms"]*1e6 for r in afm]),
            "afm_grades": dict(Counter(r["grade"]["outcome"] for r in afm)),
            "reader_submissions": sum(r["reader_events"][0]["raw"]["sent"] for r in afm)}
    for cid in sorted({r["id"] for r in rows}):
        for task in ("tensor", "afm"):
            pair = [r for r in rows if r["id"] == cid and r["task"] == task]
            if not pair:
                continue
            need(len(pair) == 2 and pair[0]["reference_checks"] == pair[1]["reference_checks"], "unpaired scores")
            if task == "afm":
                need(pair[0]["prompt_sha256"] == pair[1]["prompt_sha256"], "different reader prompts")
                output["pairs_with_equal_reader_input"] += 1
    return output


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def manifest(args, cases):
    probe = json.loads(subprocess.check_output([str(args.doctor), "doctor"], timeout=60))
    need(probe["available"] and probe["target"] == backend.SYSTEM and probe["route"] == "system", "wrong AFM")
    cli = lambda cmd: subprocess.check_output(cmd, text=True).strip()
    return {"utc": datetime.now(timezone.utc).isoformat(), "machine": platform.machine(),
            "chip": cli(["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"]),
            "os": cli(["sw_vers", "-productVersion"]), "os_build": cli(["sw_vers", "-buildVersion"]),
            "swift": cli(["xcrun", "swiftc", "--version"]), "doctor": {k: probe[k] for k in ("available", "target", "route")},
            "binary_sha256": sha(args.binary.read_bytes()), "doctor_sha256": sha(args.doctor.read_bytes()),
            "source_files": {p.name: sha(p.read_bytes()) for p in (Path(__file__), HERE/"Rank.swift", OLD/"lock.json", Path(backend.__file__))},
            "active_ane_counters": None, "placement_observed": False,
            "cases": [{"id": c["id"], "source_tokens_from_public_lock": n, "language": c["language"],
                       "tier": c["tier"], "policies": ps, "afm": afm} for c, n, ps, afm in cases]}


def capture(args):
    cases = load_cases()
    args.out.mkdir(parents=True, exist_ok=True)
    need(not any(args.out.iterdir()), "use an empty results directory")
    lock = Path(tempfile.gettempdir()) / ("afmcli-moe-system-%s.lock" % os.getuid())
    with lock.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        metadata = manifest(args, cases)
        write_json(args.out/"run.json", metadata)
        rows = []
        with gzip.open(args.out/"observations.jsonl.gz", "wt", encoding="utf-8") as out:
            for task in ("tensor", "afm"):
                for case, _, policies, include_afm in cases:
                    if task == "afm" and not include_afm:
                        continue
                    for policy in policies:
                        row = capture_run(args.binary, case, policy, task)
                        rows.append(row)
                        print(encode(row).decode(), file=out, flush=True)
                        print(task, case["id"], policy, row.get("grade", {}).get("outcome", "verified"), flush=True)
    write_json(args.out/"summary.json", summary(rows))
    verify(args)


def verify(args):
    m = json.loads((args.out/"run.json").read_bytes())
    for p in (Path(__file__), HERE/"Rank.swift", OLD/"lock.json", Path(backend.__file__)):
        need(m["source_files"][p.name] == sha(p.read_bytes()), "frozen replay code changed: " + p.name)
    need(m["doctor"] == {"available": True, "target": backend.SYSTEM, "route": "system"}, "wrong recorded model")
    cases = load_cases()
    expected = [(t, c, policy) for t in ("tensor", "afm") for c, _, policies, eligible in cases
                if t == "tensor" or eligible for policy in policies]
    with gzip.open(args.out/"observations.jsonl.gz", "rt", encoding="utf-8") as f:
        rows = [json.loads(x) for x in f]
    need(len(rows) == len(expected) == 24, "incomplete observation set")
    for row, (task, c, policy) in zip(rows, expected):
        need((row["task"], row["id"], row["policy"]) == (task, c["id"], policy), "order differs")
        lines, features = prepare(c["job"], warm=task == "tensor")
        need(row["features_sha256"] == sha(encode(features)) and row["source_sha256"] == sha(c["job"]["source"].encode()), "changed source/features")
        checks, _ = check_tensor(policy, lines, features, row)
        need(checks == row["reference_checks"], "reference mismatch")
        selected = top(lines, row["swift"]["trials"][0]["order"])
        need(selected == row["selected"] and row["selected_sha256"] == sha(encode(selected)), "line recovery differs")
        for line in selected:
            need(c["job"]["source"].encode()[line["start_byte"]:line["end_byte"]] == line["text"].encode(), "source slice differs")
        if task == "afm":
            io = backend.IO(archive=row["reader_events"])
            answer, prompt_sha = read_extract(io, c["job"]["question"], selected)
            need(len(io.events) == len(row["reader_events"]) == 1 and answer == row["answer"] and prompt_sha == row["prompt_sha256"], "reader replay differs")
            need(fixtures.grade(c, answer) == row["grade"], "legacy grade differs")
    need(json.loads((args.out/"summary.json").read_bytes()) == summary(rows), "summary differs")
    print("Verified 64 tensor trials, eight independent reader paths, and every original source slice.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    live = subs.add_parser("capture")
    live.add_argument("--binary", type=Path, required=True)
    live.add_argument("--doctor", type=Path, required=True)
    for sub in (live, subs.add_parser("verify")):
        sub.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    (capture if args.command == "capture" else verify)(args)
