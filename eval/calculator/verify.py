"""Verify the archived calculator study without generating responses. Run python3 -B eval/calculator/verify.py."""
import argparse
import ast
from collections import Counter
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
import check_math
import probe
import word_problems

HERE = Path(__file__).resolve().parent
TOOL = "scientificCalculator"
EXPLICIT = "登録された関数電卓ツールを呼び出して計算してください。"
EXPLICIT_IDS = ("integer-control", "nested-fraction")
NUMBER = re.compile(r"[+-]?\d+(?:\.\d+|/\d+)?")
SHA = re.compile(r"[a-f0-9]{64}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def same(description, actual, expected):
    # JSON distinguishes booleans from numbers; Python equality alone does not.
    if json.dumps(actual, sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise ValueError(f"{description}: expected {expected!r}, got {actual!r}")


def milliseconds(value):
    return type(value) in (float, int) and math.isfinite(value) and value >= 0


def number(text):
    if not isinstance(text, str):
        return None
    text = text.strip()
    if len(text) > 100 or not NUMBER.fullmatch(text):
        return None
    try:
        return Fraction(text)
    except ZeroDivisionError:
        return None


def arithmetic(expression):
    """Independent exact rational arithmetic for this panel; transcendental calls use math in degrees."""
    require(isinstance(expression, str) and len(expression) <= 4096, "invalid calculator expression")
    src = expression.strip().replace("^", "**")
    tree = ast.parse(src, mode="eval")
    require(sum(1 for _ in ast.walk(tree)) <= 256, "expression exceeds the panel's verifier limit")

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            literal = ast.get_source_segment(src, node)
            require(re.fullmatch(r"\d+(?:\.\d+)?", literal) and len(literal) <= 100, "invalid decimal literal")
            return Fraction(literal)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            result = visit(node.operand)
            return -result if isinstance(node.op, ast.USub) else result
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                result = left + right
            elif isinstance(node.op, ast.Sub):
                result = left - right
            elif isinstance(node.op, ast.Mult):
                result = left * right
            elif isinstance(node.op, ast.Div):
                result = left / right
            elif isinstance(node.op, ast.Pow) and isinstance(right, Fraction) and right.denominator == 1 and abs(right) <= 64:
                result = left ** int(right)
            else:
                raise ValueError("unsupported arithmetic operator")
            if isinstance(result, Fraction):
                require(max(result.numerator.bit_length(), result.denominator.bit_length()) <= 2048, "arithmetic limit")
            return result
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and len(node.args) == 1 and not node.keywords:
            value = visit(node.args[0])
            if node.func.id == "sin":
                return math.sin(math.radians(value))
            if node.func.id == "sqrt":
                return math.sqrt(value)
            if node.func.id == "log10":
                return math.log10(value)
        raise ValueError("expression is outside the six-problem calculator grammar")

    return visit(tree.body)


def correct(text, gold):
    value = number(text)
    if value is None:
        return False
    if isinstance(gold, Fraction):
        return value == gold
    return abs(float(value) - gold) <= 1e-12


def formula_cases():
    cases = {}
    for key, expr, documented in probe.CASES:
        gold = arithmetic(expr)
        require(correct(str(documented), gold), f"{key}: arithmetic disagrees with fixture")
        require(key not in cases, "duplicate formula fixture")
        cases[key] = (expr, str(documented), gold)
    require(len(cases) == 6, "expected six distinct calculator problems")
    return cases


def rows(path):
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def verify_cli(row, case, route):
    expression, canonical, gold = case
    same("route", row["route"], route)
    same("source expression", row["expression"], expression)
    same("expected", row["expected"], canonical)
    require(milliseconds(row["elapsed_ms"]), "invalid CLI time")
    raw = row["record"]
    if route == "direct":
        same("task", raw["task"], "ask")
        same("status", raw["status"], "unverified")
        same("generation count", raw["metrics"]["calls"], 1)
        require(len(raw["attempts"]) == 1, "expected one raw AFM response")
        output = raw["attempts"][0]["response"]
        same("AFM result versus raw response", raw["result"], output)
    else:
        same("task", raw["task"], "calculate")
        same("status", raw["status"], "ok")
        same("generation count", raw["metrics"]["calls"], 0)
        require(not raw.get("attempts"), "unexpected AFM attempt in local calculator")
        result = raw["result"]
        same("calculator expression", result["expression"], expression)
        same("calculator angle", result["angle_unit"], "deg")
        same("approximation flag", result["approximate"], isinstance(gold, float))
        output = result["value"]
        require(correct(output, gold), "local formula value disagrees with arithmetic")
    same("saved output", row["output"], output)
    score = correct(output, gold)
    same("saved grade", row["correct"], score)
    return score


def verify_transcript(entries, callbacks):
    calls, outputs = {}, {}
    for event in entries:
        key = event["id"]
        require(isinstance(key, str) and key, "missing framework tool ID")
        same("transcript tool", event["tool"], TOOL)
        if event["kind"] == "tool_call":
            require(key not in calls, "reused transcript call ID")
            require(set(event["arguments"]) == {"expression"}, "unexpected transcript argument schema")
            calls[key] = event["arguments"]["expression"]
        else:
            same("transcript entry kind", event["kind"], "tool_output")
            require(key in calls and key not in outputs, "unpaired or repeated transcript output ID")
            require(isinstance(event["segments"], list) and all(isinstance(s, str) for s in event["segments"]),
                    "malformed transcript output")
            outputs[key] = "".join(event["segments"])
    require(calls.keys() == outputs.keys(), "framework tool call without a tool output")
    # Detect substitution of results between calls, including batched/repeated invocations.
    evidence = Counter((c["expression"], c["returned"]) for c in callbacks)
    require(evidence == Counter((expression, outputs[key]) for key, expression in calls.items()),
            "callback arguments/results do not match the framework call IDs")
    return len(calls), len(outputs)


def verify_native(row, case, condition):
    expr, canonical, gold = case
    registered = row["route"] == "native-registered"
    same("angle", row["angle"], "deg")
    same("condition", row["condition"], condition)
    same("source formula", row["expression"], expr)
    same("expected", row["expected"], canonical)
    prompt = probe.INSTRUCTION + (EXPLICIT if condition == "explicit" else "") + "\n\n" + expr
    same("saved prompt", row["request"], prompt)
    same("timeout", row["timed_out"], False)
    same("exit", row["exit_code"], 0)
    same("errors", row["errors"], [])
    events = row["record"]
    require(len(events) >= 3, "incomplete native stream")
    model, request, *callbacks, final = events
    same("session model", row["native_model"], model)
    for key, value in {"event":"model", "route":"system", "available":True, "availability":"available",
                       "core_advanced_3":True, "variant":"AFM 3 Core Advanced"}.items():
        same("model." + key, model[key], value)
    require(isinstance(model["os"], str) and model["os"], "missing OS build")
    same("native request", request, {"event":"request", "max_response_tokens":256, "sampling":"greedy",
                                     "tool_calling_mode":"allowed", "tools":[TOOL] if registered else [], "prompt":prompt})
    same("final event", final["event"], "final")
    same("final error", row["model_error"], final.get("error"))
    require(row["model_error"] is None and isinstance(final["answer"], str), "incomplete model response")
    require(milliseconds(row["elapsed_ms"]) and milliseconds(final["elapsed_ms"])
            and final["elapsed_ms"] <= row["elapsed_ms"] + 1, "invalid native session timestamps")
    require(registered or not callbacks, "unregistered session dispatched a tool")
    successful = []
    scored = []
    for i, c in enumerate(callbacks, 1):
        for key, value in {"event":"callback", "source":"framework", "sequence":i, "tool":TOOL, "angle":"deg"}.items():
            same("callback." + key, c[key], value)
        require(all(milliseconds(c[t]) for t in ["started_ms", "finished_ms", "elapsed_ms"])
                and c["started_ms"] <= c["finished_ms"] <= final["elapsed_ms"], "invalid callback time")
        if c["value"] is not None:
            same("calculator process started", c["started_process"], True)
            same("calculator exit", c["backend_exit"], 0)
            same("calculator status", c["backend_status"], "ok")
            same("value sent to AFM", c["returned"], c["value"])
            oracle = arithmetic(c["expression"])
            require(correct(c["value"], oracle), "callback result disagrees with its own formula")
            require(type(c["approximate"]) is bool, "invalid callback numeric representation")
            successful.append(c)
        else:
            require(isinstance(c.get("error"), str) and c["returned"].startswith("Calculator error:"),
                    "unsuccessful callback lacks a tool error")
        scored.append({**c, "value_correct": correct(c["value"], gold) if c["value"] is not None else None})
    call_count, output_count = verify_transcript(final["transcript"], callbacks)
    same("scored callback copies", row["callbacks"], scored)
    last = successful[-1] if successful else {}
    model_score = correct(final["answer"], gold)
    tool_score = correct(last["value"], gold) if last else None
    for key, value in {
        "output":final["answer"], "correct":model_score, "tool_direct_correct":tool_score,
        "tool_direct_output":last.get("value"), "tool_direct_expression":last.get("expression"),
        "tool_direct_finished_ms":last.get("finished_ms"), "callback_count":len(callbacks),
        "successful_callback_count":len(successful), "transcript_call_count":call_count,
        "transcript_output_count":output_count, "matched_dispatch_count":len(callbacks),
        "matched_output_count":len(successful),
    }.items():
        same(key, row[key], value)
    return model_score, tool_score, call_count


def verify_math(data, binary):
    summary = json.loads((data / "math-run.json").read_text())
    expected = Counter(kind for kind, _, _, _ in check_math.cases())
    same("math panel composition", summary["by_kind"], dict(expected))
    size = sum(expected.values())
    same("math panel size", summary["total"], size)
    same("math panel errors", summary["errors"], [])
    same("math model calls", summary["model_calls"], 0)
    require(SHA.fullmatch(summary["binary_sha256"]), "invalid math binary SHA-256")
    duration, rate = summary["elapsed_ms"], summary["single_batch_jobs_per_second"]
    require(milliseconds(duration) and duration > 0 and milliseconds(rate) and rate > 0
            and math.isclose(size * 1000, duration * rate, abs_tol=0.00051 * rate + 0.051 * duration),
            "math throughput disagrees with its rounded duration")
    if binary:
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        same("selected math binary SHA-256", digest, summary["binary_sha256"])
        proc = subprocess.run([sys.executable, "-B", str(HERE / "check_math.py"), "--binary", str(binary)],
                              capture_output=True, text=True, check=True, timeout=60)
        repeated = json.loads(proc.stdout)
        for key in ["binary_sha256", "total", "by_kind", "errors", "model_calls"]:
            same("replayed math." + key, repeated[key], summary[key])
    return size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=HERE, help="directory of archived JSON and JSONL files")
    parser.add_argument("--binary", type=Path, help="also rerun the deterministic math panel with the exact archived binary")
    args = parser.parse_args()
    cases = formula_cases()
    answer = {}
    for name, mode in [("direct", "direct"), ("native-formulas", "native")]:
        records = rows(args.data / (name + ".jsonl"))
        require(Counter(r["id"] for r in records) == Counter(cases.keys()), f"{name}: wrong or duplicate problem IDs")
        scores = [verify_cli(r, cases[r["id"]], mode) for r in records]
        answer[name] = {"correct":sum(scores), "total":len(scores)}
    for filename, condition, ids, routes in [
        ("native-paired", "natural", list(cases), ("native-registered", "native-unregistered")),
        ("native-explicit", "explicit", EXPLICIT_IDS, ("native-registered",)),
    ]:
        records = rows(args.data / (filename + ".jsonl"))
        require(Counter((r["id"], r["route"]) for r in records) == Counter((i, route) for i in ids for route in routes),
                f"{filename}: wrong, missing or repeated session")
        require(len({json.dumps(r["native_model"], sort_keys=True) for r in records}) == 1,
                f"{filename}: different native model/OS metadata within the comparison")
        summary = {r: Counter(total=0, final_correct=0, tool_values=0, tool_correct=0, tool_calls=0) for r in routes}
        for row in records:
            try:
                final_score, tool_score, calls = verify_native(row, cases[row["id"]], condition)
            except (KeyError, TypeError, ValueError) as err:
                raise ValueError(f"{filename}/{row['id']}/{row['route']}: {err}") from err
            summary[row["route"]].update(total=1, final_correct=int(final_score), tool_values=int(tool_score is not None),
                                          tool_correct=int(tool_score is True), tool_calls=calls)
        answer[filename] = summary
    words = word_problems.validate((args.data / "words.jsonl").read_bytes(), complete=True)
    answer["words"] = {"correct":sum(r["correct"] for r in words), "total":len(words)}
    answer["math_cases"] = verify_math(args.data, args.binary.resolve() if args.binary else None)
    print(json.dumps(answer, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (KeyError, TypeError, ValueError, OSError, ArithmeticError, SyntaxError, subprocess.SubprocessError) as exc:
        print(f"calculator verification failed: {exc}", file=sys.stderr)
        sys.exit(1)
