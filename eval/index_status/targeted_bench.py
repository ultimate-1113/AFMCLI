"""Secondary comparison on nonrepeating long documents, using the primary runner and rubric."""
import argparse
from collections import Counter
import json
from pathlib import Path

import bench
from calibrate import count
from scores import compare, tallies
import targeted

HERE = Path(__file__).resolve().parent
ARMS = ("records", "indexed", "guard")
SPLIT = "targeted"
LENGTHS = HERE / "targeted_lengths.json"
LOCK = HERE / "targeted_lock.json"


def freeze():
    if LOCK.exists():
        frozen()
        return
    lengths, counts = {}, {}
    for base in targeted.cases(SPLIT):
        pair_id = base["pair"]
        if pair_id in lengths:
            continue
        tier, index = base["tier"], base["pair_index"]
        target = int(tier[:-1]) * 1024
        n = base["filler_count"]
        for _ in range(8):
            pair = [targeted.make_case(SPLIT, tier, index, flag, filler_count=n) for flag in (False, True)]
            tokens = [count(c["job"]["source"]) for c in pair]
            if all(abs(t - target) <= target * .03 for t in tokens):
                break
            n = max(12, round(n * target / (sum(tokens) / 2)))
        else:
            raise ValueError("calibration failed for " + pair_id)
        lengths[pair_id] = n
        counts.update({c["id"]: t for c, t in zip(pair, tokens)})
        print(pair_id, *tokens, flush=True)
    if LENGTHS.exists() and json.loads(LENGTHS.read_text()) != lengths:
        raise ValueError("existing calibrated lengths differ")
    LENGTHS.write_text(json.dumps(lengths, sort_keys=True, indent=2) + "\n")
    data = {"case_hashes": {c["id"]: bench.digest(bench.encode(c)) for c in panel()},
            "source_tokens": counts, "generator_sha256": bench.file_hash(HERE / "targeted.py")}
    with LOCK.open("x") as f:
        json.dump(data, f, sort_keys=True, indent=2)
        f.write("\n")
    frozen()


def panel():
    lengths = json.loads(LENGTHS.read_text())
    return [targeted.make_case(SPLIT, c["tier"], c["pair_index"], c["positive"],
                               filler_count=lengths[c["pair"]]) for c in targeted.cases(SPLIT)]


def frozen():
    lock = json.loads(LOCK.read_text())
    cases = panel()
    ids = {c["id"]: bench.digest(bench.encode(c)) for c in cases}
    if (len(cases) != 12 or sum(c["positive"] for c in cases) != 6 or lock["case_hashes"] != ids
            or set(ids) != set(lock["source_tokens"]) or lock["generator_sha256"] != bench.file_hash(HERE / "targeted.py")):
        raise ValueError("changed supplementary panel")
    for c in cases:
        goal = int(c["tier"][:-1]) * 1024
        if abs(lock["source_tokens"][c["id"]] - goal) > goal * .03:
            raise ValueError("calibration outside tolerance")
    return {SPLIT: cases}, ids


def order(panel):
    for n, case in enumerate(sorted(panel, key=lambda c: bench.digest(("secondary/" + c["id"]).encode()))):
        positions = (0, 1, 2) if n // 3 % 2 == 0 else (2, 1, 0)
        yield case, [ARMS[(n + p) % 3] for p in positions]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("freeze")
    run = sub.add_parser("run")
    for key in ("records", "indexed", "candidate"):
        run.add_argument("--" + key, required=True, type=Path)
    for p in (run, sub.add_parser("verify"), sub.add_parser("report")):
        p.add_argument("--out", type=Path, default=bench.RESULTS / "secondary")
    args = parser.parse_args()
    if args.command == "freeze":
        freeze()
    elif args.command == "run":
        args.arms, args.split = ARMS, SPLIT
        bench.measure(args, frozen, order)
    else:
        cases, rows = bench.verify(args.out, SPLIT, frozen, ARMS)
        if args.command == "verify":
            print(f"Verified {len(cases)} secondary cases and {len(rows)} responses.")
            return
        by_id = {c["id"]: c for c in cases}
        result = {}
        for a in ARMS:
            group = [r for r in rows if r["arm"] == a]
            result[a] = tallies(group, by_id)
            uses = [(r["record"]["metrics"].get("context") or {}).get("retrieval") or {} for r in group]
            result[a]["with_retrieval_rounds"] = sum(bool(u.get("rounds")) for u in uses)
            result[a]["fallback_reasons"] = dict(Counter(u["fallback"] for u in uses if u.get("fallback")))
        result["guard_vs_indexed"] = compare(rows, "indexed", "guard")
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
