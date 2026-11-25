"""Calibrated paired experiments on the local Apple system model."""
import argparse
from collections import Counter
import fcntl
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import time

import fixtures

HERE = Path(__file__).resolve().parent
SHARED = runpy.run_path(str(HERE.parent / "moe" / "study.py"))
encode = SHARED["encode"]
read_rows = SHARED["read_rows"]
file_hash = SHARED["file_hash"]
digest = SHARED["digest"]
ARMS = ("records", "indexed", "guard", "checked", "handoff", "review", "staged")
OPTIONS = dict(zip(ARMS, ("records", "indexed", "index-guard", "index-checked", "index-handoff",
                          "index-handoff-review", "index-staged")))
RESULTS = HERE / "results"


def datasets():
    cases = {split: fixtures.cases(split) for split in ("dev", "test")}
    ids = [c["id"] for v in cases.values() for c in v]
    if len(ids) != len(set(ids)) or [len(cases[s]) for s in ("dev", "test")] != [12, 48]:
        raise ValueError("incorrect or duplicate fixture panel")
    for split, panel in cases.items():
        expected = 2 if split == "dev" else 8
        groups = Counter((c["tier"], c["positive"]) for c in panel)
        if len(groups) != 6 or set(groups.values()) != {expected}:
            raise ValueError("unbalanced positive and null cases")
        for pair in {c["pair"] for c in panel}:
            ab = [c for c in panel if c["pair"] == pair]
            if len(ab) != 2 or ab[0]["job"]["question"] != ab[1]["job"]["question"]:
                raise ValueError("unpaired question " + str(pair))
    return cases


def frozen(write=False):
    panels = datasets()
    counts = json.loads((HERE / "source_tokens.json").read_text())
    docs = {}
    for panel in panels.values():
        for c in panel:
            target = int(c["tier"][:-1]) * 1024
            if abs(counts[c["id"]] - target) > .03 * target:
                raise ValueError("document length outside tolerance: " + c["id"])
            docs[c["id"]] = digest(encode(c))
    files = [HERE / "fixtures.py", HERE / "lengths.json", HERE / "source_tokens.json",
             *sorted(p for p in (HERE / "fixtures").rglob("*") if p.is_file() and p.suffix in (".json", ".py"))]
    lock = {"cases": docs, "files": {p.relative_to(HERE).as_posix(): file_hash(p) for p in files}}
    path = HERE / "lock.json"
    if write and not path.exists():
        path.write_text(json.dumps(lock, sort_keys=True, indent=2) + "\n")
    if lock != json.loads(path.read_text()):
        raise ValueError("fixture, calibration or rubric differs from lock")
    return panels, docs


def order(panel):
    # Fourteen Williams sequences for seven treatments, alternating reversals in blocks.
    first = [0, 1, 6, 2, 5, 3, 4]
    for i, c in enumerate(sorted(panel, key=lambda x: digest(("order-v1/" + x["id"]).encode()))):
        sequence = first if (i // len(ARMS)) % 2 == 0 else reversed(first)
        yield c, [ARMS[(n + i) % len(ARMS)] for n in sequence]


def read_manifests(output):
    files = (output / "binaries.json")
    return json.loads(files.read_text()) if files.is_file() else {}


def check_binary(bin_path):
    raw = json.loads(subprocess.check_output([str(bin_path), "doctor"], timeout=45))
    if raw.get("available") is not True or raw.get("route") != "system" or raw.get("target") != "AFM 3 Core Advanced":
        raise RuntimeError("requires the available AFM 3 Core Advanced system model")
    return file_hash(bin_path)


def grade_row(row, case, case_hash, fingerprints):
    if row["case_hash"] != case_hash or row["job_hash"] != digest(encode(case["job"])):
        raise ValueError("modified condition or job: " + case["id"])
    if row["binary"] != fingerprints[row["arm"]] or row["option"] != OPTIONS[row["arm"]]:
        raise ValueError("binary or route mismatch")
    if row["tier"] != case["tier"] or row["pair"] != case["pair"] or row["language"] != case["language"]:
        raise ValueError("incorrect input metadata")
    record = row["record"]
    metrics = record["metrics"]
    if (not 0 <= metrics["peak_sent_tokens"] <= 6200 or metrics["sent_input_tokens"] < metrics["peak_sent_tokens"]
            or not 0 <= metrics["calls"] <= 24 or len(record.get("attempts", [])) > metrics["calls"]):
        raise ValueError("invalid metrics: " + case["id"])
    if not row["elapsed_ms"] >= 0 or encode(row["score"]) != encode(fixtures.grade(case, record)):
        raise ValueError("score no longer reproduces: " + case["id"])


def save(out, row):
    out.write(encode(row) + b"\n")
    out.flush()
    os.fsync(out.fileno())


def measure(args):
    panels, lock = frozen()
    outdir = args.out.resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    output = outdir / (args.split + ".jsonl")
    by_id = {c["id"]: c for c in panels[args.split]}
    selected = set(args.arms or ARMS)
    binaries = {a: (args.records if a == "records" else args.indexed if a == "indexed"
                    else args.candidate).resolve() for a in selected}
    available = {p: check_binary(p) for p in set(binaries.values())}
    guard = Path(tempfile.gettempdir()) / f"afmcli-moe-system-{os.getuid()}.lock"
    with guard.open("a") as serial:
        fcntl.flock(serial, fcntl.LOCK_EX)
        hashes = read_manifests(outdir)
        for a, path in binaries.items():
            if a in hashes and hashes[a] != available[path]:
                raise ValueError("use a distinct output directory for a changed binary")
            hashes[a] = available[path]
        manifest = outdir / "binaries.json"
        temporary = outdir / "binaries.next"
        temporary.write_text(json.dumps(hashes, sort_keys=True, indent=2) + "\n")
        temporary.replace(manifest)
        prev = SHARED["by_key"](SHARED["stored"](output))
        for (i, a), row in prev.items():
            if i not in by_id or a not in ARMS or row["split"] != args.split:
                raise ValueError("unexpected stored row")
            grade_row(row, by_id[i], lock[i], hashes)
        with output.open("ab") as out:
            for c, arms in order(panels[args.split]):
                for a in arms:
                    if a not in selected or (c["id"], a) in prev:
                        continue
                    option = OPTIONS[a]
                    start = time.perf_counter_ns()
                    try:
                        record = SHARED["job_call"](binaries[a], ("--compact=" + option,), c["job"], os.environ.copy())
                    except (RuntimeError, OSError, subprocess.SubprocessError, ValueError) as exc:
                        with (outdir / "interruptions.jsonl").open("ab") as interruptions:
                            save(interruptions, {"split": args.split, "id": c["id"], "arm": a,
                                                 "error_type": type(exc).__name__, "completed": False})
                        raise
                    elapsed = round((time.perf_counter_ns() - start) / 1_000_000, 3)
                    row = {"id": c["id"], "split": args.split, "pair": c["pair"], "tier": c["tier"],
                           "language": c["language"], "arm": a, "option": option, "binary": hashes[a],
                           "case_hash": lock[c["id"]], "job_hash": digest(encode(c["job"])),
                           "record": record, "elapsed_ms": elapsed, "score": fixtures.grade(c, record)}
                    grade_row(row, c, lock[c["id"]], hashes)
                    save(out, row)
                print(c["id"], flush=True)


def verify(output, split):
    panels, lock = frozen()
    panel = {c["id"]: c for c in panels[split]}
    rows = SHARED["by_key"](read_rows(output / (split + ".jsonl")))
    keys = {(i, a) for i in panel for a in ARMS}
    if set(rows) != keys:
        raise ValueError(f"incomplete {split}: {len(rows)} of {len(keys)} case-methods")
    hashes = read_manifests(output)
    if set(hashes) != set(ARMS):
        raise ValueError("incomplete binary manifest")
    for (i, a), row in rows.items():
        if row["split"] != split or row["arm"] != a:
            raise ValueError("unexpected row")
        grade_row(row, panel[i], lock[i], hashes)
    return panels[split], list(rows.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("freeze")
    run = sub.add_parser("run")
    run.add_argument("--arms", nargs="+", choices=ARMS)
    run.add_argument("--records", required=True, type=Path)
    run.add_argument("--indexed", required=True, type=Path)
    run.add_argument("--candidate", required=True, type=Path)
    for command in (run, sub.add_parser("verify"), sub.add_parser("report")):
        command.add_argument("--split", choices=("dev", "test"), required=True)
        command.add_argument("--out", type=Path, default=RESULTS)
    args = parser.parse_args()
    if args.action == "freeze":
        frozen(write=True)
    elif args.action == "run":
        measure(args)
    else:
        cases, rows = verify(args.out, args.split)
        if args.action == "verify":
            print(f"Verified {len(cases)} {args.split} cases, {len(rows)} scored case-methods.")
        else:
            from scores import summarize
            print(json.dumps(summarize(cases, rows), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
