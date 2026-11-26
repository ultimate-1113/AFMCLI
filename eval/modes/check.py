"""Rerun the shipping CLI on published inputs, or score the saved records offline."""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
from statistics import median
import subprocess
import sys
import tempfile
from time import perf_counter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "combinations"))
import study as previous
sys.path.insert(0, str(HERE.parent / "moe"))
from domains import translation, translation_fixtures

MODEL = "AFM 3 Core Advanced"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def required(ok, message):
    if not ok:
        raise ValueError(message)


def plan(track):
    if track == "translation":
        for i, case in enumerate(translation_fixtures.make("test")):
            for mode in (("draft", "checked") if i % 2 == 0 else ("checked", "draft")):
                yield case, mode, {"task":"translate", "input":case["job"], "mode":mode}
        return
    _, groups = previous.frozen()
    cases = groups["qa"]
    # Choose one matched pair per language and length by its ID before running the new CLI.
    pairs = {}
    for c in cases:
        key = c["language"], c["tier"]
        pairs.setdefault(key, set()).add(c["pair"])
    selected = {min(group, key=lambda k: digest(("afmcli modes " + k).encode())) for group in pairs.values()}
    for i, case in enumerate(cases):
        modes = ["preview", "lookup", "cautious"] if case["pair"] in selected else ["preview"]
        k = i % len(modes)
        for mode in modes[k:] + modes[:k]:
            yield case, mode, dict(case["job"], mode=mode)


def score(track, case, mode, row):
    r = row["record"]
    required(r["task"] == ("answer" if track == "qa" else "translate"), "wrong task")
    required(row["model"] == MODEL and len(row["binary_sha256"]) == 64, "unexpected runtime")
    required(isinstance(row["elapsed_ms"], (int, float)) and math.isfinite(row["elapsed_ms"])
             and row["elapsed_ms"] >= 0, "invalid elapsed time")
    metrics = r["metrics"]
    required(0 <= metrics["peak_sent_tokens"] <= 6200 and metrics["sent_input_tokens"] >= metrics["peak_sent_tokens"],
             "invalid token accounting")
    if track == "qa":
        required(metrics["answer_mode"] == mode, "wrong answer policy")
        if mode == "preview":
            required(metrics["calls"] <= 1 and r["status"] in ("review", "held", "error", "refused"),
                     "preview returned a final answer or generated twice")
            # Grade the candidate's bytes and authoritative location, while retaining its real status.
            if r["status"] == "review":
                r = dict(r, status="ok")
        return previous.qa_grade(case, r)
    required(metrics["translation_mode"] == mode, "wrong translation policy")
    if mode == "draft":
        required(metrics["calls"] <= 1 and r["status"] in ("unverified", "held", "error", "refused"),
                 "draft made a second model call or claimed verification")
    else:
        required(metrics["calls"] <= 2, "checked translation exceeded one local repair")
        required(r["status"] == "ok" or r["result"] is None, "unchecked text on a translation failure")
    return translation.grade(case["job"], case["gold"], "candidate", r)


def load(path):
    if not path.exists():
        return {}
    seen = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        key = row["id"], row["mode"]
        required(key not in seen, f"duplicate record {key}")
        seen[key] = row
    return seen


def save(path, rows):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
        pending = Path(f.name)
        try:
            for row in rows.values():
                f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"))+"\n")
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            pending.unlink(missing_ok=True)
            raise
    try:
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def run(track, cases, path, binary):
    binary = binary.resolve()
    doctor = json.loads(subprocess.check_output([str(binary), "doctor"]))
    required(doctor["available"] and doctor["target"] == MODEL and doctor["route"] == "system", "wrong model")
    fingerprint = digest(binary.read_bytes())
    for case, mode, job in cases:
        key = case["id"], mode
        with previous.system_lock():
            rows = load(path)
            if key in rows:
                row = rows[key]
                required(row["binary_sha256"] == fingerprint, "use a new output directory for another build")
                required(row["job_sha256"] == digest(encode(job)) and score(track, case, mode, row) == row["score"],
                         "saved observation does not match this input or scorer")
                continue
            start = perf_counter()
            result = subprocess.run([str(binary), "--timeout", "45", "run"], input=encode(job),
                                    capture_output=True, timeout=360)
            duration = round((perf_counter()-start)*1000, 3)
            required(result.returncode in (0, 2, 3) and result.stdout, f"missing CLI record for {key}")
            row = dict(id=key[0], mode=mode, job_sha256=digest(encode(job)), binary_sha256=fingerprint,
                       model=MODEL, elapsed_ms=duration, record=json.loads(result.stdout))
            row["score"] = score(track, case, mode, row)
            rows[key] = row
            save(path, rows)
        print(case["id"], mode, row["record"]["status"], flush=True)


def replay(track, cases, path):
    rows = load(path)
    required(set(rows) == {(c["id"], m) for c, m, _ in cases}, "incomplete or extra cases")
    groups = {}
    for case, mode, job in cases:
        row = rows[case["id"], mode]
        required(row["job_sha256"] == digest(encode(job)), "input changed")
        grade = score(track, case, mode, row)
        required(grade == row["score"], "score changed")
        groups.setdefault(mode, []).append((case, row, grade))
    output = {}
    for mode, items in groups.items():
        metrics = [r["record"]["metrics"] for _, r, _ in items]
        result = dict(n=len(items), statuses=dict(Counter(r["record"]["status"] for _, r, _ in items)),
                      correct=sum(s["correct"] for _, _, s in items),
                      input_tokens=sum(m["sent_input_tokens"] for m in metrics),
                      max_calls=max(m["calls"] for m in metrics),
                      median_ms=round(median(r["elapsed_ms"] for _, r, _ in items), 3))
        if track == "qa":
            result.update(settled=sum(c["positive"] for c, _, _ in items),
                          settled_correct=sum(c["positive"] and s["correct"] for c, _, s in items),
                          correct_null=sum(s["outcome"] == "correct_null" for _, _, s in items),
                          false_nonnull=sum(s["false_nonnull"] for _, _, s in items))
        else:
            result.update(delivered=sum(s["answered"] for _, _, s in items),
                          serious=sum(s["serious"] for _, _, s in items))
        output[mode] = result
    return output


def decisions(track, measured):
    if track == "qa":
        baseline_path = HERE.parent / "combinations/results/summary.json"
        baseline = json.loads(baseline_path.read_bytes())["qa"]["all"]["indexed"]
        preview, lookup, cautious = (measured[m] for m in ("preview", "lookup", "cautious"))
        gates = {
            "preview": {
                "at_most_one_generation": preview["max_calls"] <= 1,
                "two_thirds_of_confirmed_with_source": 3*preview["settled_correct"] >= 2*preview["settled"],
                "half_the_prior_input_or_less": 2*preview["input_tokens"] <= baseline["counted_input_tokens"],
                "at_least_30_percent_faster_by_median": 10*preview["median_ms"] <= 7*baseline["median_ms"],
            },
            "lookup": {"three_quarters_of_confirmed_with_source":
                       4*lookup["settled_correct"] >= 3*lookup["settled"]},
            "cautious": {
                "at_most_half_the_unsupported_values_of_lookup": 2*cautious["false_nonnull"] <= lookup["false_nonnull"],
                "at_least_a_third_of_confirmed_with_source": 3*cautious["settled_correct"] >= cautious["settled"],
            },
        }
    else:
        draft, checked = measured["draft"], measured["checked"]
        gates = {
            "draft": {"four_fifths_delivered": 5*draft["delivered"] >= 4*draft["n"],
                      "at_most_one_generation": draft["max_calls"] <= 1},
            "checked": {"at_most_half_the_serious_outputs_of_draft": 2*checked["serious"] <= draft["serious"],
                        "at_least_a_third_delivered": 3*checked["delivered"] >= checked["n"]},
        }
    return {mode: {"adopt": all(criteria.values()), "criteria": criteria} for mode, criteria in gates.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "replay", "decide"))
    parser.add_argument("--track", choices=("qa", "translation"), required=True)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--out", type=Path, default=HERE)
    args = parser.parse_args()
    cases = list(plan(args.track))
    path = args.out / (args.track+".jsonl")
    if args.action == "run":
        if args.binary is None:
            parser.error("run requires --binary")
        args.out.mkdir(parents=True, exist_ok=True)
        run(args.track, cases, path, args.binary)
    measured = replay(args.track, cases, path)
    output = decisions(args.track, measured) if args.action == "decide" else measured
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    if args.action == "decide" and not all(mode["adopt"] for mode in output.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
