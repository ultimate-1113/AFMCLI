"""Compare a registered Swift calculator with an independent, tool-free sufficiency decision.

Each JSONL row is one completed arm. A temporary .pending file preserves a READY
session during a restart. Both phases use fresh native processes. Elapsed time
includes the wait between phases if a READY checkpoint is resumed.
"""
import argparse
import fcntl
import hashlib
import inspect
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
from run import numeric, read_events, verify_native
from words import INSTRUCTION, close_to, middle
from verify import NUMBER, TOOL, milliseconds, number, require, same, verify_transcript


GATE = ("本文を根拠に、問いに必要な量と関係がそろい、答えが一意に定まるかを判断してください。"
        "問いから独立した項目が未定でも、答えが定まれば READY。必要な量や関係が不足すれば MISSING。"
        "計算や説明はせず、READY または MISSING の1語だけを返してください。")
ARMS = ("registered", "sufficiency")
NATIVE_MODE = {"gate": "unregistered", "solve": "registered"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protocol_hash():
    # Unrelated edits to the legacy study's reports must not change this comparison.
    functions = (numeric, read_events, verify_native, close_to, middle, milliseconds, number, require, same, verify_transcript)
    dependencies = [INSTRUCTION, NUMBER.pattern, TOOL, *(inspect.getsource(fn) for fn in functions)]
    return hashlib.sha256(Path(__file__).read_bytes() + json.dumps(dependencies).encode()).hexdigest()


def panel(path):
    raw = path.read_bytes()
    data = json.loads(raw)
    require(isinstance(data, list) and data, "expected a nonempty JSON array of cases")
    seen = set()
    for case in data:
        for field in ("id", "language", "question", "reason"):
            require(isinstance(case.get(field), str) and case[field].strip(), "missing case field " + field)
        require(type(case.get("pair")) is int or isinstance(case.get("pair"), str) and case["pair"].strip(), "missing pair")
        require(case["id"] not in seen, "duplicate case id")
        seen.add(case["id"])
        require(case["expected"] is None or number(case["expected"]) is not None, "expected a numeric string or null")
    return data, hashlib.sha256(raw).hexdigest()


def schedule(cases):
    return [(case, arm) for index, case in enumerate(cases) for arm in (ARMS if index % 2 == 0 else ARMS[::-1])]


def prompt(role, question):
    return (GATE if role == "gate" else INSTRUCTION) + "\n\n" + question


def collect(args, role, question):
    """Only question and frozen instructions cross the native process boundary."""
    request = prompt(role, question)
    command = [str(args.native), NATIVE_MODE[role], str(args.calculator), "deg"]
    started = time.monotonic_ns()
    stdout = stderr = b""
    timed_out, exit_code, launch_error = False, None, None
    try:
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        launch_error = {"exception": type(exc).__name__, "errno": exc.errno}
    else:
        try:
            stdout, stderr = child.communicate(request.encode(), timeout=args.timeout)
        except BaseException as exc:
            # A native process may be waiting for a child Rust calculator.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = child.communicate()
            if not isinstance(exc, subprocess.TimeoutExpired):
                raise
            timed_out = True
        exit_code = child.returncode
    events, errors = read_events(stdout)
    stage = {"stage": role, "registration": NATIVE_MODE[role], "request": request, "events": events,
             "parse_errors": errors, "stderr": stderr.decode(errors="replace"), "exit_code": exit_code,
             "timed_out": timed_out, "launch_error": launch_error,
             "elapsed_ms": round((time.monotonic_ns() - started) / 1e6, 3)}
    if errors:
        stage["raw_stdout_hex"] = stdout.hex()
    return stage


def observe(stage, question, model):
    role = stage["stage"]
    same("registration", stage["registration"], NATIVE_MODE[role])
    request = prompt(role, question)
    same("input", stage["request"], request)
    require(milliseconds(stage["elapsed_ms"]), "invalid stage duration")
    require(type(stage["timed_out"]) is bool and isinstance(stage["stderr"], str)
            and (type(stage["exit_code"]) is int or stage["launch_error"] and stage["exit_code"] is None),
            "invalid stage process observation")
    events = stage["events"]
    if stage["parse_errors"] or "raw_stdout_hex" in stage:
        parsed, errors = read_events(bytes.fromhex(stage["raw_stdout_hex"]))
        same("partial events", events, parsed)
        same("event parse errors", stage["parse_errors"], errors)
    require(isinstance(events, list) and all(isinstance(e, dict) and "event" in e for e in events), "invalid events")
    expected_request = {"event": "request", "prompt": request, "sampling": "greedy", "max_response_tokens": 256,
                        "tool_calling_mode": "allowed", "tools": [TOOL] if role == "solve" else []}
    for event in events:
        if event["event"] == "model":
            same("session model", event, model)
        elif event["event"] == "request":
            same("native request", event, expected_request)
    calls = [event for event in events if event["event"] == "callback" and event.get("source") == "framework"]
    finals = [event for event in events if event["event"] == "final"]
    value = finals[-1].get("answer") if finals else None
    numbers = [c["value"] for c in calls if c.get("tool") == TOOL and c.get("started_process") is True
               and c.get("backend_exit") == 0 and c.get("backend_status") == "ok"
               and number(c.get("value")) is not None and c.get("returned") == c["value"]]
    error = None
    if stage["timed_out"]:
        error = "timeout"
    elif stage["launch_error"] or stage["exit_code"] != 0 or stage["stderr"] or stage["parse_errors"]:
        error = "execution_error"
    else:
        try:
            same("event order", [e["event"] for e in events], ["model", "request", *(["callback"] * len(calls)), "final"])
            require(isinstance(value, str) and not finals[0].get("error"), "incomplete native final")
            require(role == "solve" or not calls, "tool-free stage dispatched a callback")
            for index, call in enumerate(calls, 1):
                require(call["tool"] == TOOL and call["angle"] == "deg" and call["sequence"] == index, "unexpected callback")
            verify_transcript(finals[0]["transcript"], calls)
            if len(numbers) != len(calls):
                error = "calculator_error"
        except (ValueError, TypeError, KeyError, IndexError):
            error = "invalid_native_stream"
    return {"error": error, "raw_answer": value, "session_attempts": 1,
            "model_requests": sum(e["event"] == "request" for e in events), "callback_count": len(calls),
            "callback_values": numbers, "elapsed_ms": stage["elapsed_ms"]}


def gate_decision(facts):
    reply = facts["raw_answer"]
    token = reply.strip() if isinstance(reply, str) else None
    if facts["error"]:
        return "held", "gate_" + facts["error"]
    if token in ("READY", "MISSING"):
        return token, None
    return "held", "invalid_gate_answer"


def outcome(arm, stages, question, model):
    facts = {s["stage"]: observe(s, question, model) for s in stages}
    gate, reason = gate_decision(facts["gate"]) if arm == "sufficiency" else (None, None)
    roles = (["gate"] if arm == "sufficiency" else []) + (["solve"] if gate in (None, "READY") else [])
    same("required independent sessions", [s["stage"] for s in stages], roles)
    value = None
    if gate == "MISSING":
        kind, value = "unknown", "不明"
    elif reason:
        kind = "held"
    else:
        solve = facts["solve"]
        reply = solve["raw_answer"]
        if solve["error"]:
            kind, reason = "held", "solve_" + solve["error"]
        elif isinstance(reply, str) and reply.strip() == "不明":
            kind, value = "unknown", "不明"
        elif number(reply) is not None:
            kind, value = "number", reply.strip()
        else:
            kind, reason = "held", "invalid_numeric_answer"
    return {"gate": gate, "kind": kind, "output": value, "held_reason": reason, "stages": facts}


def score(result, expected):
    gold = number(expected)
    solve = result["stages"].get("solve", {})
    values = solve.get("callback_values", [])
    raw = solve.get("raw_answer")
    return {"correct": result["kind"] == "unknown" if gold is None else close_to(result["output"], gold),
            "numeric_final_on_missing": gold is None and result["kind"] == "number",
            "raw_numeric_final_on_missing": gold is None and number(raw) is not None,
            "raw_reply_with_digits_on_missing": gold is None and isinstance(raw, str) and any(c.isdecimal() for c in raw),
            "callback_values_on_missing": len(values) if gold is None else 0,
            "last_tool_value_correct": close_to(values[-1], gold) if values and gold is not None else None}


def model_ok(model):
    require(model.get("event") == "model" and model.get("route") == "system"
            and model.get("available") is True and model.get("core_advanced_3") is True
            and model.get("variant") == "AFM 3 Core Advanced", "requires available AFM 3 Core Advanced on system")


def row_header(item, meta):
    case, arm = item
    return {**meta, "case": case, "arm": arm}


def check(row, item, meta):
    case, arm = item
    for key, value in row_header(item, meta).items():
        same("row " + key, row[key], value)
    model_ok(row["model"])
    view = outcome(arm, row["sessions"], case["question"], row["model"])
    same("output from native events", row["result"], view)
    same("grade", row["grade"], score(view, case["expected"]))
    require(type(row["resumed_between_stages"]) is bool and isinstance(row["started_unix_ns"], int)
            and isinstance(row["finished_unix_ns"], int), "invalid E2E timestamps")
    if row["resumed_between_stages"]:
        measured_ns = row["finished_unix_ns"] - row["started_unix_ns"]
    else:
        measured_ns = row["finished_monotonic_ns"] - row["started_monotonic_ns"]
    same("E2E duration", row["elapsed_ms"], round(measured_ns / 1e6, 3))
    require(milliseconds(row["elapsed_ms"]) and row["elapsed_ms"] + 2 >= sum(s["elapsed_ms"] for s in row["sessions"]),
            "E2E shorter than the stages")
    require(not row["resumed_between_stages"] or view["gate"] == "READY", "invalid resume point")


def read_rows(stream, items, meta):
    stream.seek(0)
    rows = []
    for line in stream:
        require(line.endswith(b"\n"), "partial JSONL tail; preserve it before resuming")
        require(len(rows) < len(items), "extra trial at end of archive")
        row = json.loads(line)
        check(row, items[len(rows)], meta)
        rows.append(row)
    return rows


def save_pending(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        name = Path(tmp.name)
        try:
            tmp.write(json.dumps(value, ensure_ascii=False).encode())
            tmp.flush()
            os.fsync(tmp.fileno())
        except BaseException:
            name.unlink(missing_ok=True)
            raise
    os.replace(name, path)


def sessions(args, question, arm, model, pending=None, on_ready=None):
    mono_start = None if pending else time.monotonic_ns()
    start = pending["started_unix_ns"] if pending else time.time_ns()
    stages = []
    if arm == "sufficiency":
        gate = pending["session"] if pending else collect(args, "gate", question)
        stages.append(gate)
        if gate_decision(observe(gate, question, model))[0] == "READY":
            if on_ready and not pending:
                on_ready({"started_unix_ns": start, "session": gate})
            stages.append(collect(args, "solve", question))
    else:
        stages.append(collect(args, "solve", question))
    result = outcome(arm, stages, question, model)
    end = time.time_ns()
    mono_end = time.monotonic_ns()
    measured_ns = end - start if pending else mono_end - mono_start
    return {"sessions": stages, "result": result, "started_unix_ns": start, "finished_unix_ns": end,
            "started_monotonic_ns": mono_start, "finished_monotonic_ns": mono_end,
            "elapsed_ms": round(measured_ns / 1e6, 3), "resumed_between_stages": pending is not None}


def read_pending(path, rows, items, meta):
    if not path.exists():
        return None
    saved = json.loads(path.read_bytes())
    index = saved["index"]
    require(type(index) is int and 0 <= index < len(items) and index in (len(rows) - 1, len(rows)), "wrong READY checkpoint")
    case, arm = items[index]
    same("checkpoint identity", saved["header"], row_header(items[index], meta))
    require(arm == "sufficiency" and type(saved["started_unix_ns"]) is int and saved["session"]["stage"] == "gate",
            "checkpoint is not a sufficiency decision")
    view = observe(saved["session"], case["question"], meta["model"])
    require(gate_decision(view)[0] == "READY", "checkpoint did not establish READY")
    if index == len(rows) - 1:
        same("already consumed checkpoint", saved["session"], rows[index]["sessions"][0])
        same("already consumed start", saved["started_unix_ns"], rows[index]["started_unix_ns"])
        return None
    return saved


def build_meta(args, cases_hash):
    binaries = {name: digest(getattr(args, name)) for name in ("native", "calculator")}
    model = verify_native(args.native)
    model_ok(model)
    return {"cases_sha256": cases_hash, "protocol_sha256": protocol_hash(), "binary_sha256": binaries,
            "timeout_seconds": args.timeout, "model": model}


def benchmark(args):
    cases, cases_hash = panel(args.cases)
    items = schedule(cases)
    pending_path = args.out.with_name(args.out.name + ".pending")
    with args.out.open("rb" if args.action == "verify" else "a+b") as stream:
        verifying = args.action == "verify"
        fcntl.flock(stream, fcntl.LOCK_SH if verifying else fcntl.LOCK_EX)
        if verifying:
            first = json.loads(stream.readline())
            meta = {key: first[key] for key in ("binary_sha256", "timeout_seconds", "model")}
            hashes = meta["binary_sha256"]
            require(set(hashes) == {"native", "calculator"} and all(isinstance(v, str) and len(v) == 64
                    and not set(v) - set("0123456789abcdef") for v in hashes.values()), "invalid binary fingerprints")
            require(type(meta["timeout_seconds"]) is int and meta["timeout_seconds"] > 0, "invalid stage timeout")
            meta.update(cases_sha256=cases_hash, protocol_sha256=protocol_hash())
            for key in ("native", "calculator"):
                path = getattr(args, key)
                if path is not None:
                    same(key + " binary", digest(path), hashes[key])
        else:
            meta = build_meta(args, cases_hash)
        rows = read_rows(stream, items, meta)
        pending = read_pending(pending_path, rows, items, meta)
        if verifying:
            require(len(rows) == len(items) and pending is None, f"incomplete comparison: {len(rows)}/{len(items)}")
            return rows
        if pending is None and pending_path.exists():
            pending_path.unlink()
        for index in range(len(rows), len(items)):
            item = items[index]
            case, arm = item
            for key in ("native", "calculator"):
                same(key + " binary before trial", digest(getattr(args, key)), meta["binary_sha256"][key])
            header = row_header(item, meta)
            def checkpoint(state):
                save_pending(pending_path, {**state, "index": index, "header": header})
            trial = sessions(args, case["question"], arm, meta["model"], pending, checkpoint)
            row = {**header, **trial, "grade": score(trial["result"], case["expected"])}
            check(row, item, meta)
            stream.write((json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
            if pending_path.exists():
                pending_path.unlink()
            pending = None
            rows.append(row)
            print(case["id"], arm, row["result"]["kind"], row["grade"]["correct"], flush=True)
        return rows


def summary(rows):
    result = {}
    for arm in ARMS:
        group = [r for r in rows if r["arm"] == arm]
        known = [r for r in group if r["case"]["expected"] is not None]
        unknown = [r for r in group if r["case"]["expected"] is None]
        by_stage = {}
        for role in NATIVE_MODE:
            facts = [r["result"]["stages"][role] for r in group if role in r["result"]["stages"]]
            by_stage[role] = {"sessions": len(facts), "requests": sum(f["model_requests"] for f in facts),
                              "callbacks": sum(f["callback_count"] for f in facts),
                              "numeric_callbacks": sum(len(f["callback_values"]) for f in facts),
                              "median_ms": middle([f["elapsed_ms"] for f in facts])}
        result[arm] = {"trials": len(group), "correct": sum(r["grade"]["correct"] for r in group),
                       "numeric_total": len(known), "numeric_correct": sum(r["grade"]["correct"] for r in known),
                       "missing_total": len(unknown), "missing_correct": sum(r["grade"]["correct"] for r in unknown),
                       "numeric_final_on_missing": sum(r["grade"]["numeric_final_on_missing"] for r in unknown),
                       "raw_numeric_final_on_missing": sum(r["grade"]["raw_numeric_final_on_missing"] for r in unknown),
                       "raw_reply_with_digits_on_missing": sum(r["grade"]["raw_reply_with_digits_on_missing"] for r in unknown),
                       "numeric_callbacks_on_missing": sum(r["grade"]["callback_values_on_missing"] for r in unknown),
                       "held": sum(r["result"]["kind"] == "held" for r in group),
                       "e2e_median_ms": middle([r["elapsed_ms"] for r in group]),
                       "resumed_between_stages": sum(r["resumed_between_stages"] for r in group), "stages": by_stage}
        if arm == "sufficiency":
            result[arm]["gate_decisions"] = {
                label: {decision: sum(r["result"]["gate"] == decision for r in subset)
                        for decision in ("READY", "MISSING", "held")}
                for label, subset in (("numeric", known), ("missing", unknown))}
    base, candidate = (result[arm] for arm in ARMS)
    result["criteria_met"] = bool(candidate["numeric_total"] and candidate["missing_total"]
                                  and candidate["numeric_correct"] >= base["numeric_correct"]
                                  and candidate["numeric_final_on_missing"] < base["numeric_final_on_missing"]
                                  and candidate["correct"] > base["correct"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="measure serial matched pairs, resuming at the last saved row")
    verify = sub.add_parser("verify", help="replay scores and raw events without model access")
    answer = sub.add_parser("answer", help="read a problem on stdin; return a number, 不明, or 保留 (exit 3)")
    for opts in (run, verify):
        opts.add_argument("--cases", type=Path, required=True)
        opts.add_argument("--out", type=Path, required=True)
    for opts in (run, answer):
        opts.add_argument("--native", type=Path, required=True)
        opts.add_argument("--calculator", type=Path, required=True)
        opts.add_argument("--timeout", type=int, default=120, help="seconds per fresh native session")
    for name in ("native", "calculator"):
        verify.add_argument("--" + name, type=Path, help="also verify this executable's digest without running it")
    args = parser.parse_args()
    try:
        if args.action in ("run", "answer"):
            args.native = args.native.resolve(strict=True)
            args.calculator = args.calculator.resolve(strict=True)
            require(args.timeout > 0, "expected a positive stage timeout")
        if args.action == "answer":
            question = sys.stdin.read()
            require(bool(question.strip()), "empty question")
            model = verify_native(args.native)
            model_ok(model)
            view = sessions(args, question, "sufficiency", model)["result"]
            if view["kind"] == "held":
                raise ValueError(view["held_reason"])
            print(view["output"])
        else:
            print(json.dumps(summary(benchmark(args)), ensure_ascii=False, sort_keys=True))
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        if args.action == "answer":
            print("保留")
            return 3
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
