"""Run matched, serial native Foundation Models sessions and score both the AFM and callback results."""
import argparse
from collections import Counter
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from probe import CASES, INSTRUCTION, numeric

EXPLICIT = "登録された関数電卓ツールを呼び出して計算してください。"


def correct(name, value, expected):
    actual = numeric(value)
    return actual is not None and (actual == expected or
                                   (name == "degree-functions" and abs(actual - expected) <= Fraction(1, 10**12)))


def read_events(payload):
    events, errors = [], []
    for line in (payload or b"").splitlines():
        try:
            value = json.loads(line)
            if not isinstance(value, dict) or "event" not in value:
                raise ValueError("expected a native probe event")
            events.append(value)
        except (ValueError, UnicodeError) as exc:
            errors.append(str(exc))
    return events, errors


def grade(name, expected, events):
    final = next((e for e in reversed(events) if e["event"] == "final"), {})
    callbacks = [e for e in events if e["event"] == "callback" and e.get("source") == "framework"]
    successes = [c for c in callbacks if c.get("value") is not None and c.get("backend_status") == "ok"]
    transcript = final.get("transcript", [])
    calls = [e for e in transcript if e["kind"] == "tool_call"]
    outputs = [e for e in transcript if e["kind"] == "tool_output"]
    # Compare multisets because frameworks can issue multiple calls, even with identical arguments.
    dispatched = Counter((c["tool"], c.get("expression")) for c in callbacks)
    requested = Counter((e["tool"], e["arguments"].get("expression")) for e in calls
                        if isinstance(e.get("arguments"), dict))
    succeeded = Counter((c["tool"], c["value"]) for c in successes)
    delivered = Counter((e["tool"], "".join(e.get("segments", []))) for e in outputs)
    tail = successes[-1] if successes else {}
    value = tail.get("value")
    return {
        "output": final.get("answer"), "correct": correct(name, final.get("answer"), expected),
        "model_error": final.get("error"), "callback_count": len(callbacks),
        "successful_callback_count": len(successes), "transcript_call_count": len(calls),
        "transcript_output_count": len(outputs), "matched_dispatch_count": sum((requested & dispatched).values()),
        "matched_output_count": sum((delivered & succeeded).values()), "tool_direct_output": value,
        "tool_direct_expression": tail.get("expression"), "tool_direct_finished_ms": tail.get("finished_ms"),
        "tool_direct_correct": correct(name, value, expected) if value is not None else None,
        "callbacks": [{**c, "value_correct": correct(name, c["value"], expected) if c.get("value") is not None else None}
                      for c in callbacks],
    }


def verify_native(binary):
    info = subprocess.run([str(binary), "doctor"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    events, errors = read_events(info.stdout)
    meta = next((e for e in events if e["event"] == "model"), {})
    if info.returncode or errors or meta.get("route") != "system" or meta.get("core_advanced_3") is not True or meta.get("available") is not True:
        raise ValueError(f"requires available native AFM 3 Core Advanced, doctor reported {meta!r}")
    return meta


def invoke(args, case, arm, prompt, meta):
    name, expression, expected = case
    start = time.monotonic_ns()
    timed_out = False
    try:
        result = subprocess.run([str(args.native), arm, str(args.calculator), "deg"], input=prompt.encode(),
                                capture_output=True, timeout=args.timeout)
        events, errors = read_events(result.stdout)
        exit_code = result.returncode
        if result.stderr:
            errors.append(result.stderr.decode(errors="replace")[:2000])
    except subprocess.TimeoutExpired as exc:
        events, errors = read_events(exc.stdout)
        exit_code = None
        timed_out = True
    native_errors = [e["error"] for e in events if e["event"] == "error"]
    return {
        "id": name, "route": "native-" + arm, "condition": args.condition, "request": prompt, "angle": "deg",
        "expression": expression, "expected": str(expected), "native_model": meta,
        **grade(name, expected, events),
        "elapsed_ms": round((time.monotonic_ns() - start) / 1e6, 3), "timed_out": timed_out,
        "exit_code": exit_code, "errors": errors + native_errors, "record": events,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, required=True, help="compiled CalculatorProbe")
    parser.add_argument("--calculator", type=Path, required=True, help="afmcli with the local calc subcommand")
    parser.add_argument("--mode", choices=("paired", "registered", "unregistered"), default="paired")
    parser.add_argument("--condition", choices=("natural", "explicit"), default="natural")
    parser.add_argument("--ids", nargs="+", choices=[case[0] for case in CASES])
    parser.add_argument("--timeout", type=int, default=120, help="wall-clock seconds per fresh native process")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.native = args.native.resolve(strict=True)
    args.calculator = args.calculator.resolve(strict=True)
    meta = verify_native(args.native)
    selection = set(args.ids) if args.ids else {case[0] for case in CASES}
    with args.out.open("x", encoding="utf-8") as stream:
        for index, case in enumerate(CASES):
            name, expression, _ = case
            if name not in selection:
                continue
            prompt = INSTRUCTION + (EXPLICIT if args.condition == "explicit" else "") + "\n\n" + expression
            paired = ("unregistered", "registered") if index % 2 == 0 else ("registered", "unregistered")
            for arm in paired if args.mode == "paired" else (args.mode,):
                row = invoke(args, case, arm, prompt, meta)
                stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                stream.flush()
                print(name, arm, row["callback_count"], repr(row["tool_direct_output"]), repr(row["output"]), row["correct"], flush=True)


if __name__ == "__main__":
    main()
