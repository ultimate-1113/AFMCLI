"""Run and replay the serial AFM evaluation. Uses only the Python standard library."""
import argparse
import copy
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

HERE = Path(__file__).resolve().parent
TRACKS = ("general", "context", "special")
BASELINE_COMMIT = "37861465faf1364e6b2c677e0ca030bbbf3eac5a"


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    return digest(path.read_bytes())


def track_module(track):
    return importlib.import_module(track)


def read_json(path):
    return json.loads(path.read_bytes())


def data_path(output, track, split):
    return output / f"{track}-{split}.jsonl"


def frozen(track, write=False):
    module = track_module(track)
    cases = {split: list(module.cases(split)) for split in ("dev", "test")}
    lock = {split: {case["id"]: digest(encode(case)) for case in items}
            for split, items in cases.items()}
    unique = set()
    for split, items in cases.items():
        if len(items) != len(lock[split]) or unique.intersection(lock[split]):
            raise ValueError(f"duplicate case IDs in {track} {split}")
        unique.update(lock[split])
    path = HERE / "locks" / (track + ".json")
    if write and not path.exists():
        path.parent.mkdir(exist_ok=True)
        with path.open("x", encoding="utf-8") as out:
            json.dump(lock, out, sort_keys=True, indent=2)
            out.write("\n")
    if read_json(path) != lock:
        raise ValueError(f"fixture lock differs: {track}")
    names = [HERE / f"{track}.py"]
    if track == "general":
        names += sorted((HERE / "hints").glob("*/SKILL.md"))
    if track == "special":
        names += [HERE / "domains" / f"{name}.py" for name in ("common", "table", "translation", "constraints")]
    methods = {p.relative_to(HERE).as_posix(): file_hash(p) for p in names}
    method_file = HERE / "locks" / (track + "-methods.json")
    if write and not method_file.exists():
        with method_file.open("x", encoding="utf-8") as out:
            json.dump(methods, out, sort_keys=True, indent=2)
            out.write("\n")
    if read_json(method_file) != methods:
        raise ValueError(f"procedure, scorer or hint changed: {track}")
    return module, cases, lock


def read_rows(path):
    if not path.exists():
        return []
    with path.open("rb") as stream:
        return [json.loads(line) for line in stream]


def stored(path):
    # A process killed during append can leave just the last row incomplete.
    if path.exists():
        with path.open("rb+") as out:
            last = 0
            for line in iter(out.readline, b""):
                if not line.endswith(b"\n"):
                    out.truncate(last)
                    break
                json.loads(line)
                last = out.tell()
    return read_rows(path)


def by_key(rows):
    keys = {(r["id"], r["arm"]): r for r in rows}
    if len(keys) != len(rows):
        raise ValueError("duplicate case-method result")
    return keys


def all_arms(module, case):
    return module.arms(case) if hasattr(module, "arms") else module.ARMS


def score_winner(output):
    module, cases, locks = frozen("special")
    rows = by_key(read_rows(data_path(output, "special", "dev")))
    required = {(c["id"], a) for c in cases["dev"] for a in all_arms(module, c)}
    if set(rows) != required:
        raise ValueError("complete specialty development results are required for selection")
    for case in cases["dev"]:
        for arm in all_arms(module, case):
            row = rows[case["id"], arm]
            if (row["track"], row["split"], row["group"]) != ("special", "dev", case["group"]):
                raise ValueError("invalid development metadata")
            verify_row(row, case, module, locks["dev"])
    from report import choose
    return choose(list(rows.values()))


def expected(track, split, cases, output):
    if (track, split) != ("special", "test"):
        return cases
    group = score_winner(output)
    return [case for case in cases if case["group"] == group]


def accepted_result(record):
    if not isinstance(record, dict) or not isinstance(record.get("status"), str):
        raise ValueError("CLI did not return an AFMCLI record")
    if record["status"] in ("error", "backend_error") and not any(
            "timed out" in note for note in record.get("notes", [])):
        raise RuntimeError("AFM backend failure: " + str(record.get("notes", ""))[:450])


def job_call(binary, flags, job, env):
    command = [str(binary), *flags, "--trace", "--input-budget", "6200", "--timeout", "45", "run"]
    child = subprocess.run(command, input=encode(job), capture_output=True, env=env, timeout=360)
    if not child.stdout:
        raise RuntimeError(f"CLI exit {child.returncode}: " + child.stderr.decode(errors="replace")[:350])
    record = json.loads(child.stdout)
    accepted_result(record)
    return record


def public_input(fn, case):
    job = case["job"]
    if fn.__name__ != "special":
        return {"job": job}
    # Infer the public input format without consulting gold or the benchmark difficulty group.
    if "table" in job:
        group = "table"
    elif "sentences" in job:
        group = "translation"
    elif set(job) == {"source"}:
        group = "constraints"
    else:
        raise ValueError("unknown specialist input")
    return {"group": group, "job": job}


def measured(binary, fn, arm, case, env):
    steps = []

    def invoke(job, flags=()):
        record = job_call(binary, flags, job, env)
        steps.append({"job_sha256": digest(encode(job)), "flags": list(flags),
                      "record": copy.deepcopy(record)})
        return record

    start = time.perf_counter_ns()
    final = fn.run(public_input(fn, case), arm, invoke)
    elapsed = round((time.perf_counter_ns() - start) / 1_000_000, 3)
    accepted_result(final)
    index = next((i for i, step in enumerate(steps) if step["record"] == final), None)
    outcome = {"final": final} if index is None else {"final_step": index}
    return {"steps": steps, **outcome, **usage(steps), "elapsed_ms": elapsed,
            "score": fn.grade(case, arm, final)}


def usage(steps):
    metrics = [step["record"]["metrics"] for step in steps]
    return {"input_tokens": sum(m["sent_input_tokens"] for m in metrics),
            "peak_tokens": max((m["peak_sent_tokens"] for m in metrics), default=0),
            "calls": sum(m["calls"] for m in metrics),
            "token_checks": sum(m["token_checks"] for m in metrics)}


def final_record(row):
    return row["final"] if "final" in row else row["steps"][row["final_step"]]["record"]


def verify_row(row, case, module, lock):
    if row["case_sha256"] != lock[case["id"]]:
        raise ValueError("changed case " + case["id"])
    expected_usage = usage(row["steps"])
    if any(row[k] != v for k, v in expected_usage.items()) or row["peak_tokens"] > 6200:
        raise ValueError("invalid usage " + case["id"])
    steps = iter(row["steps"])

    def replay(job, flags=()):
        step = next(steps)
        if step["flags"] != list(flags) or step["job_sha256"] != digest(encode(job)):
            raise ValueError("changed model request " + case["id"])
        return copy.deepcopy(step["record"])

    final = module.run(public_input(module, case), row["arm"], replay)
    if next(steps, None) is not None or encode(final) != encode(final_record(row)):
        raise ValueError("stored procedure no longer replays " + case["id"])
    if encode(module.grade(case, row["arm"], final)) != encode(row["score"]):
        raise ValueError("score mismatch " + case["id"])
    return True


def panels(track, split, output):
    module, splits, lock = frozen(track)
    cases = expected(track, split, splits[split], output)
    return module, {c["id"]: c for c in cases}, lock[split]


def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    guard = Path(tempfile.gettempdir()) / f"afmcli-moe-system-{os.getuid()}.lock"
    with guard.open("a") as serial, tempfile.TemporaryDirectory(prefix="afmcli-moe-hints-") as registry:
        fcntl.flock(serial, fcntl.LOCK_EX)
        module, cases, lock = panels(args.track, args.split, output)
        if not cases:
            print("No specialty passed the development safety gate.")
            return
        before, after = args.baseline.resolve(), args.binary.resolve()
        fingerprints = {b: file_hash(b) for b in {before, after}}
        for binary in fingerprints:
            doctor = json.loads(subprocess.check_output([str(binary), "doctor"]))
            if not doctor.get("available") or doctor.get("route") != "system" or "Core Advanced" not in doctor["target"]:
                raise RuntimeError("system model unavailable")
        env = dict(os.environ, AFMCLI_SKILLS_DIR=registry)
        if hasattr(module, "prepare"):
            module.prepare(str(after), Path(registry))
        path = data_path(output, args.track, args.split)
        previous = by_key(stored(path))
        if any(i not in cases or a not in all_arms(module, cases[i]) for i, a in previous):
            raise ValueError("unexpected stored case-method")
        with path.open("ab") as out:
            for case_id, case in cases.items():
                arms = sorted(all_arms(module, case),
                              key=lambda a: digest(encode((case_id, a))))
                for arm in arms:
                    binary = before if arm in ("direct", "current") else after
                    if (case_id, arm) in previous:
                        row = previous[case_id, arm]
                        verify_row(row, case, module, lock)
                        if row["binary_sha256"] != fingerprints[binary]:
                            raise ValueError("incompatible binary for resume")
                        continue
                    result = measured(binary, module, arm, case, env)
                    row = {"id": case_id, "arm": arm, "group": case["group"], "track": args.track,
                           "split": args.split, "case_sha256": lock[case_id],
                           "binary_sha256": fingerprints[binary], **result}
                    out.write(encode(row) + b"\n")
                    out.flush()
                    os.fsync(out.fileno())
                print(case_id, flush=True)


def verify(output, quiet=False):
    total = 0
    for track in TRACKS:
        for split in ("dev", "test"):
            module, cases, lock = panels(track, split, output)
            rows = by_key(read_rows(data_path(output, track, split)))
            required = {(i, a) for i, c in cases.items() for a in all_arms(module, c)}
            if required != set(rows):
                raise ValueError(f"incomplete {track} {split}: {len(rows)}/{len(required)}")
            for (i, _), row in rows.items():
                if row["split"] != split or row["track"] != track or row["group"] != cases[i]["group"]:
                    raise ValueError(f"incorrect metadata: {i}")
                verify_row(row, cases[i], module, lock)
            total += len(rows)
    if not quiet:
        print(f"Verified fixture locks, recorded usage and scores for {total} case-method results.")


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--track", choices=TRACKS, required=True)
    measure = sub.add_parser("run")
    measure.add_argument("--track", choices=TRACKS, required=True)
    measure.add_argument("--split", choices=("dev", "test"), required=True)
    measure.add_argument("--baseline", type=Path, required=True)
    measure.add_argument("--binary", type=Path, required=True)
    for cmd in (measure, sub.add_parser("verify"), sub.add_parser("winner"), sub.add_parser("report")):
        cmd.add_argument("--output", type=Path, default=HERE / "results")
    return parser.parse_args()


if __name__ == "__main__":
    args = arguments()
    if args.command == "run":
        run(args)
    elif args.command == "freeze":
        frozen(args.track, write=True)
    elif args.command == "verify":
        verify(args.output)
    elif args.command == "winner":
        print(score_winner(args.output))
    elif args.command == "report":
        from report import report
        verify(args.output, quiet=True)
        print(json.dumps(report(args.output), ensure_ascii=False, indent=2))
