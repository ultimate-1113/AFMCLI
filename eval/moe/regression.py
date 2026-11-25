"""Replay 30 previous long QA and eight numeric cases through the released records route."""
import argparse
import json
from pathlib import Path
import runpy
import subprocess

HERE = Path(__file__).resolve().parent
# Older panels import their own module named "study" during initialization.
RUNNER = runpy.run_path(str(HERE / "study.py"))
OLD = HERE.parent / "grounding"
ROUTES = runpy.run_path(str(OLD / "routes.py"))
STUDY = ROUTES["STUDY"]


def checks():
    rows = RUNNER["read_rows"](OLD / "public.jsonl")
    return {(r["split"], r["id"]): r for r in rows
            if r["option"] == "--compact=records" and r["split"] in ("holdout", "numeric")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "verify"))
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--output", type=Path, default=HERE / "results" / "regression-long.jsonl")
    args = parser.parse_args()
    expected = checks()
    if len(expected) != 38:
        raise ValueError("incorrect reference panel")
    if args.action == "run":
        binary = args.binary.resolve()
        doctor = json.loads(subprocess.check_output([str(binary), "doctor"]))
        if not doctor.get("available") or doctor["target"] != "AFM 3 Core Advanced":
            raise RuntimeError("Core Advanced unavailable")
        args.output.parent.mkdir(exist_ok=True, parents=True)
        prior = {(r["split"], r["id"]): r for r in RUNNER["stored"](args.output)}
        fingerprint = RUNNER["file_hash"](binary)
        if any(r["binary"] != fingerprint for r in prior.values()):
            raise ValueError("binary changed")
        with args.output.open("ab") as stream:
            for split in ("holdout", "numeric"):
                for i, case in STUDY["cases"](split).items():
                    if (split, i) in prior:
                        continue
                    result = STUDY["run_case"](binary, "public", case, "--compact=records")
                    row = {"split": split, "id": i, "binary": fingerprint,
                           "job_sha256": result["job_sha256"], "observed": ROUTES["observation"](result),
                           "elapsed_ms": result["elapsed_ms"]}
                    stream.write(RUNNER["encode"](row) + b"\n")
                    stream.flush()
    rows = RUNNER["read_rows"](args.output)
    if set(expected) != {(r["split"], r["id"]) for r in rows} or len(rows) != 38:
        raise ValueError("incomplete regression panel")
    if len({r["binary"] for r in rows}) != 1:
        raise ValueError("regression panel contains different binaries")
    changed = []
    for row in rows:
        old = expected[row["split"], row["id"]]
        if row["job_sha256"] != old["job_sha256"]:
            raise ValueError("input changed")
        if row["observed"] != old["observed"]:
            changed.append((row["split"], row["id"]))
    print(json.dumps({"cases": len(rows), "changed_outputs_or_costs": changed}))
    if args.action == "verify" and changed:
        raise ValueError("the saved regression panel differs from v0.3.2")


if __name__ == "__main__":
    main()
