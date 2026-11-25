"""Compare real, optional AFM calculator calls on the ten fixed word problems."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
from statistics import median
import sys

sys.dont_write_bytecode = True
from run import invoke, verify_native
from run import grade as native_grade
from probe import numeric
from verify import milliseconds, require, same, verify_transcript
from word_problems import CASES

INSTRUCTION = "次の文章題の答えを数値ひとつで返してください。算出に必要な情報がなければ「不明」とだけ返してください。"
CONDITION = "natural-word-problem"
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "native-words.jsonl"


def schedule():
    for i, problem in enumerate(CASES):
        arms = ("unregistered", "registered") if i % 2 == 0 else ("registered", "unregistered")
        for arm in arms:
            yield problem, arm


def close_to(value, gold):
    n = numeric(value)
    return n is not None and gold is not None and abs(n - gold) <= numeric("0.000000000001")


def grade(case, events):
    ident, _, expected = case
    gold = numeric(expected)
    result = native_grade(ident, gold, events)
    output = result["output"]
    is_unknown = isinstance(output, str) and output.strip() == "不明"
    result["final_kind"] = ("unknown" if is_unknown else "number" if numeric(output) is not None
                            else "missing" if output is None else "text")
    result["correct"] = is_unknown if expected is None else close_to(output, gold)
    for c in result["callbacks"]:
        c["value_correct"] = close_to(c["value"], gold) if c.get("value") is not None else None
    value = result["tool_direct_output"]
    # No tool output is unscored. Any numeric intermediate for an unknown answer is incorrect.
    result["tool_direct_correct"] = close_to(value, gold) if value is not None else None
    return result


def check_row(row, item):
    (ident, source, expected), arm = item
    prompt = INSTRUCTION + "\n\n" + source
    for key, value in {"id":ident, "source":source, "expected":expected, "condition":CONDITION,
                       "route":"native-" + arm, "request":prompt, "angle":"deg"}.items():
        same(ident + " " + key, row[key], value)
    meta = row["native_model"]
    require(meta["route"] == "system" and meta["variant"] == "AFM 3 Core Advanced"
            and meta["core_advanced_3"] is True and meta["available"] is True, "unexpected native model")
    events = row["record"]
    final = None
    model_seen = request_seen = False
    for event in events:
        if event["event"] == "model":
            require(not model_seen and not request_seen, "duplicate or out-of-order model event")
            same("model", event, meta)
            model_seen = True
        elif event["event"] == "request":
            require(model_seen and not request_seen, "duplicate or out-of-order request")
            same("model request", event, {"event":"request", "prompt":prompt, "sampling":"greedy",
                                         "max_response_tokens":256, "tool_calling_mode":"allowed",
                                         "tools":["scientificCalculator"] if arm == "registered" else []})
            request_seen = True
        elif event["event"] == "callback":
            require(request_seen and final is None and arm == "registered" and event["source"] == "framework"
                    and event["tool"] == "scientificCalculator" and event["angle"] == "deg", "unexpected callback")
            if event.get("value") is not None:
                require(event["started_process"] is True and event["backend_status"] == "ok" and event["backend_exit"] == 0
                        and numeric(event["value"]) is not None and event["returned"] == event["value"],
                        "callback lacks a successful numerical result")
        elif event["event"] == "final":
            require(request_seen and final is None, "duplicate or out-of-order final")
            final = event
        else:
            same("event kind", event["event"], "error")
    scored = grade(item[0], events)
    for key, value in scored.items():
        same(ident + " " + key, row[key], value)
    callbacks = scored["callbacks"]
    same("callback sequence", [c["sequence"] for c in callbacks], list(range(1, len(callbacks) + 1)))
    if final is not None:
        if row["exit_code"] == 0:
            require(not row["timed_out"] and not row["errors"] and not final.get("error")
                    and isinstance(final.get("answer"), str), "successful run has an incomplete/error response")
            verify_transcript(final["transcript"], callbacks)
    else:
        require(row["timed_out"] or row["exit_code"] not in (None, 0), "missing final event on a successful run")
    require(milliseconds(row["elapsed_ms"]), "invalid wall time")


def read_prefix(stream, items, repair=False):
    """Only an interrupted, invalid final fragment can be discarded when resuming."""
    stream.seek(0)
    rows = []
    while True:
        start = stream.tell()
        line = stream.readline()
        if not line:
            return rows
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            if line.endswith(b"\n") or not repair:
                raise ValueError(f"invalid JSON at byte {start}")
            stream.seek(start)
            stream.truncate()
            return rows
        require(len(rows) < len(items), "extra session in words file")
        check_row(row, items[len(rows)])
        rows.append(row)
        if not line.endswith(b"\n") and repair:
            stream.write(b"\n")
            stream.flush()


def middle(values):
    return round(median(values), 3) if values else None


def summary(rows):
    out = {}
    for arm in ("native-unregistered", "native-registered"):
        group = [r for r in rows if r["route"] == arm]
        known = [r for r in group if r["expected"] is not None]
        unknown = [r for r in group if r["expected"] is None]
        values = [r for r in group if r["tool_direct_output"] is not None]
        out[arm] = {
            "sessions":len(group), "errors_or_timeouts":sum(r["exit_code"] != 0 or bool(r["errors"]) or r["timed_out"] for r in group),
            "final_correct":sum(r["correct"] for r in group), "sessions_with_calls":sum(r["callback_count"] > 0 for r in group),
            "callback_count":sum(r["callback_count"] for r in group),
            "elapsed_median_ms":middle([r["elapsed_ms"] for r in group]),
            "last_tool_result_median_ms":middle([r["tool_direct_finished_ms"] for r in values]),
            "numeric_problems":{"total":len(known), "final_correct":sum(r["correct"] for r in known),
                                "with_tool_value":sum(r["tool_direct_output"] is not None for r in known),
                                "tool_correct":sum(r["tool_direct_correct"] is True for r in known)},
            "insufficient_information":{"total":len(unknown), "final_unknown":sum(r["final_kind"] == "unknown" for r in unknown),
                                        "numeric_final":sum(r["final_kind"] == "number" for r in unknown),
                                        "non_unknown_replies_with_digits":sum(any(ch.isdecimal() for ch in (r["output"] or ""))
                                                                              for r in unknown if r["final_kind"] != "unknown"),
                                        "sessions_with_calls":sum(r["callback_count"] > 0 for r in unknown),
                                        "sessions_with_tool_value":sum(r["tool_direct_output"] is not None for r in unknown),
                                        "numeric_callback_count":sum(r["successful_callback_count"] for r in unknown)},
        }
    return out


def run(args, items):
    args.native = args.native.resolve(strict=True)
    args.calculator = args.calculator.resolve(strict=True)
    args.condition = CONDITION
    binaries = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                for name, path in (("native", args.native), ("calculator", args.calculator))}
    with args.out.open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        rows = read_prefix(stream, items, repair=True)
        for existing in rows:
            same("executables at resume", existing.get("binary_sha256"), binaries)
        if len(rows) == len(items):
            return rows
        meta = verify_native(args.native)
        for case, arm in items[len(rows):]:
            ident, source, expected = case
            prompt = INSTRUCTION + "\n\n" + source
            # invoke sends only the prompt on stdin. Expected values remain in the host-side scorer.
            row = invoke(args, (ident, source, numeric(expected)), arm, prompt, meta)
            row["source"] = row.pop("expression")
            row["expected"] = expected
            row["binary_sha256"] = binaries
            row.update(grade(case, row["record"]))
            stream.write((json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
            stream.flush()
            # Keep the completed attempt even if an unexpected framework event fails validation.
            check_row(row, (case, arm))
            rows.append(row)
            print(ident, arm, row["callback_count"], repr(row["output"]), row["correct"], flush=True)
        return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    run_opts = sub.add_parser("run", help="generate once per session, resuming a valid JSONL prefix")
    run_opts.add_argument("--native", type=Path, required=True)
    run_opts.add_argument("--calculator", type=Path, required=True)
    run_opts.add_argument("--timeout", type=int, default=120)
    verify_opts = sub.add_parser("verify", help="verify all saved sessions and recompute scores without using AFM")
    for opts in (run_opts, verify_opts):
        opts.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    items = list(schedule())
    if args.action == "run":
        rows = run(args, items)
    else:
        with args.out.open("rb") as stream:
            rows = read_prefix(stream, items)
        require(len(rows) == len(items), f"incomplete word comparison, found {len(rows)}/{len(items)} sessions")
    initial = rows[0]["binary_sha256"]
    require(set(initial) == {"native", "calculator"}
            and all(len(v) == 64 and not set(v) - set("0123456789abcdef") for v in initial.values()),
            "invalid binary digest")
    for row in rows:
        same("executable hashes across sessions", row["binary_sha256"], initial)
    print(json.dumps(summary(rows), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError, OSError, TypeError) as exc:
        print(f"word comparison failed: {exc}", file=sys.stderr)
        sys.exit(1)
