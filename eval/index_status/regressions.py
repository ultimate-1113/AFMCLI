"""Run the selected fast route and the independent staged route on the previous 30 known jobs."""
import argparse
import json
from pathlib import Path

import bench
from scores import summarize
import previous


def evaluate(args):
    cases, rows = bench.verify(args.out, "test")
    primary = summarize(cases, rows)
    fast = primary["fast_candidate_for_old_cases"]
    fingerprints = bench.read_manifests(args.out)
    if args.action == "run":
        requested = bench.file_hash(args.candidate)
        if any(fingerprints[a] != requested for a in (fast, "staged")):
            raise ValueError("use the identical binary from the frozen primary comparison")
    result = {}
    for label, arm in (("fast", fast), ("staged", "staged")):
        option = bench.OPTIONS[arm]
        path = args.out / ("previous-" + label + ".jsonl")
        if args.action == "run":
            previous.run(args.candidate, path, option)
        archived = previous.verify(path)
        if archived["mode"] != option or archived["checked_binary_sha256"] != fingerprints[arm]:
            raise ValueError("regression mode or binary differs from the chosen primary route")
        old, new = (archived["methods"][a] for a in ("indexed", option))
        prior_ok = (new["correct"] >= old["correct"] and
                    new["unanswerable"]["nonempty"] <= old["unanswerable"]["nonempty"])
        result[label] = {"arm": arm, "passes_primary": primary["gates"][arm]["passes"],
                         "passes_previous": prior_ok,
                         "passes_both": prior_ok and primary["gates"][arm]["passes"],
                         "previous": archived}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run")
    run.add_argument("--candidate", required=True, type=Path)
    for cmd in (run, sub.add_parser("report"), sub.add_parser("verify")):
        cmd.add_argument("--out", type=Path, default=bench.RESULTS)
    args = parser.parse_args()
    data = evaluate(args)
    if args.action == "report":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print("Verified two 30-case regressions against frozen inputs and archived model output.")


if __name__ == "__main__":
    main()
