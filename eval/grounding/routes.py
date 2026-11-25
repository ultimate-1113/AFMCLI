"""Compare every shipped compact option with its frozen on-device reference."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import runpy
from statistics import median
import subprocess

HERE = Path(__file__).resolve().parent
STUDY = runpy.run_path(str(HERE / "study.py"))
OPTIONS = {"before": "--compact=evidence", "packing": "--compact=records", "after": "--compact=review"}


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def observation(row):
    m = row["metrics"]
    return dict(status=row["status"], answer=(row["result"] or {}).get("answer"),
                notes=row["notes"], calls=m["calls"], sent=m["sent_input_tokens"],
                peak=m["peak_sent_tokens"], context=m.get("context"))


def references():
    entries = {}
    for split in ("dev", "holdout", "numeric", "controls"):
        panel = STUDY["cases"](split)
        for row in read_rows(HERE / (split + ".jsonl")):
            if row["mode"] in OPTIONS:
                key = (split, row["id"], OPTIONS[row["mode"]])
                entries[key] = (panel[row["id"]], observation(row))
    # v0.3.1 --compact, including its original UTF-8 offsets and generation costs.
    panel = STUDY["cases"]("dev")
    for row in read_rows(HERE.parent / "repeats" / "results.jsonl"):
        if row["mode"] == "after":
            key = ("dev", row["id"], "--compact")
            entries[key] = (panel[row["id"]], {k: row[k] for k in
                            ("status", "answer", "notes", "calls", "sent", "peak", "context")})
    return entries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("run", "verify"))
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--output", type=Path, default=HERE / "public.jsonl")
    args = parser.parse_args()
    entries = references()
    if args.action == "run":
        if not args.binary:
            parser.error("run needs a public AFMCLI binary")
        binary = args.binary.resolve()
        state = json.loads(subprocess.check_output([binary, "doctor"]))
        assert state["available"] and state["route"] == "system" and state["target"] == "AFM 3 Core Advanced"
        fingerprint = hashlib.sha256(binary.read_bytes()).hexdigest()
        args.output.parent.mkdir(exist_ok=True, parents=True)
        with args.output.open("x") as output:
            for i, ((split, key, option), (case, expected)) in enumerate(entries.items(), 1):
                row = STUDY["run_case"](binary, "public", case, option)
                obs = observation(row)
                item = dict(split=split, id=key, option=option, binary=fingerprint,
                            job_sha256=row["job_sha256"], elapsed_ms=row["elapsed_ms"], observed=obs)
                output.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
                output.flush()
                if obs != expected or i % 25 == 0:
                    print(i, split, key, option, "matches" if obs == expected else "differs", flush=True)
    rows = read_rows(args.output)
    assert len(rows) == len(entries) == 224
    assert {(r["split"], r["id"], r["option"]) for r in rows} == set(entries)
    assert len({r["binary"] for r in rows}) == 1
    changes = Counter()
    for r in rows:
        case, expected = entries[r["split"], r["id"], r["option"]]
        assert r["job_sha256"] == STUDY["digest"](STUDY["job_bytes"](case["job"]))
        if r["observed"] != expected:
            changes[r["option"]] += 1
    for option in ("--compact", *OPTIONS.values()):
        group = [r for r in rows if r["option"] == option]
        print(option, len(group), "changed", changes[option], flush=True)
        holdout = [r for r in group if r["split"] == "holdout"]
        if holdout:
            print("  held-out median ms", median(r["elapsed_ms"] for r in holdout))
    # A different greedy output should be investigated before publication.
    assert not changes, changes


if __name__ == "__main__":
    main()
