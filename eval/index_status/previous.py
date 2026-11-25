"""Paired regression on the 30 known AFMCLI long-document cases. Verification is offline."""
import argparse
from collections import Counter
import fcntl
import json
import math
import os
from pathlib import Path
from statistics import median
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
PRIOR = HERE.parent / "moe"
sys.path.insert(0, str(PRIOR))
import context
import study

OLD_RESULTS = PRIOR / "results/context-test.jsonl"
OLD_DIGEST = "5ba1160f56ef7abed0c1f3ccc855023072e7f20b358916cfaa45854e978a9815"
OLD_RUNNER_DIGEST = "6f7b6125ba4ba51840cabeec56293144e5024de9dd0b1ae69279480c629867ba"
DEFAULT_MODE = "index-checked"
MODES = (DEFAULT_MODE, "index-handoff", "index-handoff-review", "index-staged", "index-guard")


def cli_args(mode):
    return ["--compact="+mode, "--trace", "--input-budget", "6200", "--timeout", "45", "run"]


SYSTEM_MODEL = {"available": True, "route": "system", "target": "AFM 3 Core Advanced"}
OLD_ARMS = {"records": "current", "indexed": "indexed"}
PANEL = "known-moe-context-test"


class Checked:
    @staticmethod
    def run(case, arm, invoke):
        if arm not in MODES:
            raise ValueError("unexpected regression mode")
        return invoke(case["job"], flags=(cli_args(arm)[0],))

    @staticmethod
    def grade(case, arm, record):
        return context.grade(case, "indexed", record)


def reference():
    if study.file_hash(OLD_RESULTS) != OLD_DIGEST or study.file_hash(PRIOR / "study.py") != OLD_RUNNER_DIGEST:
        raise ValueError("the prior measurements or run protocol differ from the frozen version")
    module, splits, locks = study.frozen("context")
    cases = {c["id"]: c for c in splits["test"]}
    if len(cases) != 30:
        raise ValueError("expected the original 30-case panel")
    original = study.by_key(study.read_rows(OLD_RESULTS))
    if original.keys() != {(i, arm) for i in cases for arm in module.ARMS}:
        raise ValueError("incomplete prior case-method matrix")
    selected = {}
    for name, old_arm in OLD_ARMS.items():
        selected[name] = {}
        for i, c in cases.items():
            row = original[(i, old_arm)]
            if row["track"] != "context" or row["split"] != "test" or row["group"] != c["group"]:
                raise ValueError("incorrect prior row metadata: " + i)
            study.verify_row(row, c, module, locks["test"])
            selected[name][i] = row
    return cases, locks["test"], selected


def answer_view(row, case):
    """Distinguish a published null from a held/failed record, including on unanswerable questions."""
    rec = study.final_record(row)
    result = rec["result"]
    answer = result.get("answer") if isinstance(result, dict) else None
    value = answer.get("value") if isinstance(answer, dict) else None
    explicit_null = rec["status"] == "abstained" and isinstance(result, dict) and "answer" in result and answer is None
    nonempty = rec["status"] == "ok" and isinstance(value, str) and bool(value)
    source_match = None
    if nonempty:
        source = case["job"]["source"].encode()
        spans = answer.get("spans")
        def matches(span):
            if not isinstance(span, dict):
                return False
            lo, hi = span.get("start_byte"), span.get("end_byte")
            return (type(lo) is int and type(hi) is int and 0 <= lo < hi <= len(source)
                    and source[lo:hi] == value.encode())
        source_match = isinstance(spans, list) and bool(spans) and all(map(matches, spans))
    return {"status": rec["status"], "answer": value if nonempty else None, "explicit_null": explicit_null,
            "nonempty": nonempty, "cited_text_matches_source": source_match, "outcome": row["score"]["outcome"]}


def input_stamp(case):
    job = case["job"]
    data = job["source"].encode()
    return {"job": job, "job_sha256": study.digest(study.encode(job)),
            "source_sha256": study.digest(data), "source_utf8_bytes": len(data)}


def validate(rows, cases, lock, prior, complete, fingerprint=None, mode=None):
    ids = set()
    binaries = set()
    modes = set()
    for row in rows:
        i = row["id"]
        if i not in cases or i in ids:
            raise ValueError("unknown or duplicate regression case: " + str(i))
        ids.add(i)
        case = cases[i]
        sha = row["binary_sha256"]
        if (not isinstance(sha, str) or len(sha) != 64 or any(x not in "0123456789abcdef" for x in sha)
                or (fingerprint is not None and fingerprint != sha)):
            raise ValueError("binary mismatch: " + i)
        binaries.add(sha)
        modes.add(row["arm"])
        if (row["panel"] != PANEL or row["arm"] not in MODES or row["cli"] != cli_args(row["arm"])
                or (mode is not None and row["arm"] != mode) or row["doctor"] != SYSTEM_MODEL
                or row["prior_results_sha256"] != OLD_DIGEST or row["group"] != case["group"]):
            raise ValueError("incorrect protocol metadata: " + i)
        if any(row[key] != v for key, v in input_stamp(case).items()):
            raise ValueError("stored request or original source differs: " + i)
        if not isinstance(row["elapsed_ms"], (int, float)) or not math.isfinite(row["elapsed_ms"]) or row["elapsed_ms"] < 0:
            raise ValueError("invalid elapsed time: " + i)
        if (len(row["steps"]) != 1 or row.get("final_step") != 0 or "final" in row
                or any(prior[name][i]["steps"][0]["job_sha256"] != row["job_sha256"] for name in OLD_ARMS)):
            raise ValueError("request does not pair with the frozen jobs: " + i)
        study.verify_row(row, case, Checked, lock)
        if answer_view(row, case) != row["checked_answer"]:
            raise ValueError("stored answer or original bytes differ: " + i)
    if len(binaries) > 1 or len(modes) > 1 or (complete and ids != cases.keys()):
        raise ValueError(f"incomplete panel or mixed binaries ({len(ids)}/30)")
    return ids


def atomic_checkpoint(path, rows):
    # At most 30 rows. Replacing a complete snapshot makes even the first result safe to resume.
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=path.name+".", delete=False) as f:
        tmp = Path(f.name)
        try:
            for row in rows:
                f.write(study.encode(row) + b"\n")
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def run(binary, output, mode=DEFAULT_MODE):
    if mode not in MODES:
        raise ValueError("unrecognized regression mode")
    binary = binary.resolve()
    output = output.resolve()
    if output.is_relative_to(PRIOR):
        raise ValueError("choose an output outside the frozen eval/moe directory")
    cases, lock, prior = reference()
    fingerprint = study.file_hash(binary)
    output.parent.mkdir(parents=True, exist_ok=True)
    guard = Path(tempfile.gettempdir()) / f"afmcli-moe-system-{os.getuid()}.lock"
    with guard.open("a") as serial, tempfile.TemporaryDirectory(prefix="afmcli-previous-skills-") as registry:
        fcntl.flock(serial, fcntl.LOCK_EX)
        rows = study.read_rows(output)
        done = validate(rows, cases, lock, prior, complete=False, fingerprint=fingerprint, mode=mode)
        if done == cases.keys():
            return
        env = dict(os.environ, AFMCLI_SKILLS_DIR=registry)
        doctor = json.loads(subprocess.check_output([str(binary), "doctor"], env=env, timeout=90))
        if any(doctor.get(k) != v for k, v in SYSTEM_MODEL.items()):
            raise RuntimeError("AFM 3 Core Advanced system route unavailable")
        for i, case in cases.items():
            if i in done:
                continue
            data = study.measured(binary, Checked, mode, case, env)
            row = {"id": i, "arm": mode, "panel": PANEL, "group": case["group"], "case_sha256": lock[i],
                   "binary_sha256": fingerprint, "prior_results_sha256": OLD_DIGEST,
                   "doctor": {k: doctor[k] for k in SYSTEM_MODEL}, "cli": cli_args(mode), **input_stamp(case), **data}
            row["checked_answer"] = answer_view(row, case)
            if study.file_hash(binary) != fingerprint:
                raise ValueError("binary changed while measuring " + i)
            validate([row], cases, lock, prior, complete=False, fingerprint=fingerprint, mode=mode)
            rows.append(row)
            atomic_checkpoint(output, rows)
            print(f"{i}  {row['score']['outcome']}  {row['input_tokens']} tokens  {row['elapsed_ms']} ms", flush=True)


def totals(by_id, cases):
    subset = list(by_id.values())
    def bucket(answerable):
        selected = [r for r in subset if (cases[r["id"]]["gold"] is not None) == answerable]
        views = [answer_view(r, cases[r["id"]]) for r in selected]
        return {"n": len(selected), "correct": sum(r["score"]["correct"] for r in selected),
                "explicit_null": sum(v["explicit_null"] for v in views), "nonempty": sum(v["nonempty"] for v in views),
                "held": sum(v["status"] == "held" for v in views), "errors": sum(v["status"] == "error" for v in views)}
    times = sorted(r["elapsed_ms"] for r in subset)
    return {"n": len(subset), "correct": sum(r["score"]["correct"] for r in subset),
            "answerable": bucket(True), "unanswerable": bucket(False),
            "statuses": dict(Counter(study.final_record(r)["status"] for r in subset)),
            "sent_input_tokens": sum(r["input_tokens"] for r in subset), "peak_tokens": max(r["peak_tokens"] for r in subset),
            "model_calls": sum(r["calls"] for r in subset), "total_ms": round(sum(times), 3),
            "median_ms": median(times), "p95_ms": times[math.ceil(len(times)*0.95)-1]}


def verify(output):
    cases, lock, old = reference()
    rows = study.read_rows(output)
    validate(rows, cases, lock, old, complete=True)
    checked = {r["id"]: r for r in rows}
    mode = rows[0]["arm"]
    methods = dict(old, **{mode: checked})
    changes = {}
    for name, scores in old.items():
        changes[name] = {
            "gained": [i for i in cases if checked[i]["score"]["correct"] and not scores[i]["score"]["correct"]],
            "lost": [i for i in cases if scores[i]["score"]["correct"] and not checked[i]["score"]["correct"]],
        }
    errors = {i: {n: answer_view(s[i], cases[i]) for n, s in methods.items()} for i in cases
              if old["indexed"][i]["score"]["outcome"] == "unsupported_answer"}
    return {"panel": PANEL, "mode": mode, "prior_results_sha256": OLD_DIGEST, "checked_binary_sha256": rows[0]["binary_sha256"],
            "methods": {n: totals(s, cases) for n, s in methods.items()}, "paired_changes": changes,
            "previous_indexed_nonempty_on_unanswerable": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    measure = sub.add_parser("run")
    measure.add_argument("--binary", type=Path, required=True)
    measure.add_argument("--output", type=Path, required=True)
    measure.add_argument("--mode", choices=MODES, default=DEFAULT_MODE)
    check = sub.add_parser("verify")
    check.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "run":
        run(args.binary, args.output, args.mode)
    else:
        print(json.dumps(verify(args.output), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
