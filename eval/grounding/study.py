"""Run and replay synthetic QA trials for independent evidence checking."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import runpy
from statistics import median
import subprocess
from time import monotonic_ns

HERE = Path(__file__).resolve().parent
REPEATS = runpy.run_path(str(HERE.parent / "repeats" / "bench.py"))
CONTEXT = REPEATS["CONTEXT"]
BASE = runpy.run_path(str(HERE.parent / "bench.py"))
MODES = {"dev": ("before", "after"), "holdout": ("direct", "before", "after"),
         "numeric": ("direct", "before", "after")}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def job_bytes(job):
    return json.dumps(job, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def cases(split):
    if split == "dev":
        return {case["id"]: case for case in REPEATS["cases"]()}
    result = {}
    if split == "numeric":
        for case_id, lang, query in json.loads((HERE / "numeric.json").read_text()):
            rows = []
            gold = []
            for index in range(1, 83):
                # A permutation prevents interpolation from the first and last rows.
                reading = (53 * index + 19) % 83
                if lang == "en":
                    value = f"{reading:03d}"
                    row = f"During inspection {index:03d} the staff assigned tray {value} and recorded the seal."
                    question = f"Which tray number was assigned during inspection {query:03d}?"
                else:
                    value = f"{reading // 10}.{reading % 10}グラム"
                    row = f"計量室の記録では検体番号{index:03d}の質量は{value}で、担当者が当日の記録欄に記入しました。"
                    question = f"検体番号{query:03d}の質量はいくらですか？"
                rows.append(row)
                if query == index:
                    gold.append(value)
            result[case_id] = dict(id=case_id, budget=1300, gold=gold,
                job=dict(task="answer", source="\n".join(rows), question=question))
        return result
    for pair in json.loads((HERE / "holdout.json").read_text()):
        rows = pair["rows"]
        for variant, change in enumerate(pair["variants"]):
            events = {3: pair["opening"], rows // 2: pair["middle"], rows - 4: change["ending"]}
            source = "\n".join(events.get(i, CONTEXT["filler"](pair["layout"], i)) for i in range(rows))
            case_id = f"{pair['id']}-{variant + 1}"
            result[case_id] = {
                "id": case_id, "pair": pair["id"], "gold": change["gold"], "budget": pair["budget"],
                "job": {"task": "answer", "question": pair["question"], "source": source},
            }
    assert len(result) == 30
    assert sum(bool(c["gold"]) for c in result.values()) == 15
    return result


def outcome(case, row):
    status = row["status"]
    if status in ("error", "refused", "held"):
        return status
    if row["mode"] == "direct":
        if status != "unverified":
            return "invalid"
        try:
            value = BASE["unguided"](row["result"])["answer"]
        except (TypeError, KeyError, ValueError):
            return "invalid"
        if not isinstance(value, str):
            return "invalid"
    else:
        if status not in ("ok", "abstained"):
            return "invalid"
        answer = row["result"]["answer"]
        if status == "abstained":
            assert answer is None
            value = ""
        else:
            assert CONTEXT["check_spans"](case["job"], answer), (case["id"], answer)
            value = answer["value"]
    if value == "":
        return "missed_answer" if case["gold"] else "correct_abstention"
    if value in case["gold"]:
        return "correct_answer"
    return "wrong_answer" if case["gold"] else "unsupported_answer"


def run_case(binary, mode, case):
    budget = 6200 if mode == "direct" else case["budget"]
    flags = ["--direct"] if mode == "direct" else ["--compact=evidence"]
    payload = job_bytes(case["job"])
    start = monotonic_ns()
    process = subprocess.run(
        [binary, *flags, "--trace", "--input-budget", str(budget), "--timeout", "45", "run"],
        input=payload, capture_output=True, timeout=360,
    )
    elapsed = round((monotonic_ns() - start) / 1_000_000)
    record = json.loads(process.stdout)
    if record["status"] == "error" and not any("timed out" in note for note in record["notes"]):
        raise RuntimeError((case["id"], record["notes"]))
    return dict(
        id=case["id"], mode=mode, budget=budget, job_sha256=digest(payload), elapsed_ms=elapsed,
        status=record["status"], result=record["result"], notes=record["notes"],
        metrics=record["metrics"], attempts=record.get("attempts", []),
    )


def measure(args, panel):
    paths = {"before": args.before.resolve(), "direct": args.before.resolve(), "after": args.after.resolve()}
    hashes = {}
    for path in set(paths.values()):
        state = json.loads(subprocess.check_output([path, "doctor"]))
        assert state["available"] and state["route"] == "system" and state["target"] == "AFM 3 Core Advanced"
        hashes[path] = digest(path.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        for case in panel.values():
            modes = sorted(MODES[args.split], key=lambda m: digest((case["id"] + m).encode()))
            for mode in modes:
                row = run_case(paths[mode], mode, case)
                row["binary"] = hashes[paths[mode]]
                row["outcome"] = outcome(case, row)
                stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                stream.flush()
                print(row["id"], mode, row["outcome"], flush=True)


def verify(path, split):
    panel = cases(split)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    expected = {(case_id, mode) for case_id in panel for mode in MODES[split]}
    assert len(rows) == len(expected) and {(r["id"], r["mode"]) for r in rows} == expected
    for row in rows:
        case = panel[row["id"]]
        assert row["job_sha256"] == digest(job_bytes(case["job"]))
        assert row["outcome"] == outcome(case, row)
        assert row["budget"] == (6200 if row["mode"] == "direct" else case["budget"])
        metrics = row["metrics"]
        assert 0 <= metrics["peak_sent_tokens"] <= row["budget"]
        assert metrics["sent_input_tokens"] >= metrics["peak_sent_tokens"]
    for mode in MODES[split]:
        group = [r for r in rows if r["mode"] == mode]
        assert len({r["binary"] for r in group}) == 1
        counts = Counter(r["outcome"] for r in group)
        score = counts["correct_answer"] + counts["correct_abstention"]
        pair_hits = Counter(panel[r["id"]].get("pair") for r in group if r["outcome"].startswith("correct_"))
        pairs = sum(n == 2 for n in pair_hits.values()) if split == "holdout" else None
        print(split, mode, f"{score}/{len(group)}", "both", pairs, dict(counts),
              "calls", sum(r["metrics"]["calls"] for r in group),
              "input", sum(r["metrics"]["sent_input_tokens"] for r in group),
              "median_ms", median(r["elapsed_ms"] for r in group))
    return {r["mode"]: r["binary"] for r in rows}


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--split", choices=MODES, required=True)
    run.add_argument("--before", type=Path, required=True)
    run.add_argument("--after", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    show = commands.add_parser("show")
    show.add_argument("case")
    commands.add_parser("verify")
    args = parser.parse_args()
    if args.command == "show":
        panel = {**cases("dev"), **cases("holdout"), **cases("numeric")}
        if args.case not in panel:
            parser.error("unknown case ID")
        print(job_bytes(panel[args.case]["job"]).decode())
    elif args.command == "run":
        measure(args, cases(args.split))
        verify(args.output, args.split)
    else:
        dev = verify(HERE / "dev.jsonl", "dev")
        held = verify(HERE / "holdout.jsonl", "holdout")
        numeric = verify(HERE / "numeric.jsonl", "numeric")
        assert dev["after"] == held["after"] and dev["before"] == held["before"] == held["direct"]
        assert numeric == held


if __name__ == "__main__":
    main()
