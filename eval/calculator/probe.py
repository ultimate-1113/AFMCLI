"""Run six fixed expression probes on the released direct route or the native calculator."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import re
import subprocess
import time

INSTRUCTION = "次の式の値を計算してください。^ はべき乗、sin は度数法、log10 は常用対数です。途中式や説明を付けず、結果を整数、小数、または既約分数で1個だけ返してください。"

CASES = [
    ("integer-control", "(347 * 29) - (186 * 17) + 245", Fraction(7146)),
    ("large-integer", "739 * 418 - 267 * 593 + 18^3 - 764", Fraction(739*418-267*593+18**3-764)),
    ("nested-fraction", "((7/12 + 5/18) / (11/9 - 2/3))^2", Fraction(961,400)),
    ("degree-functions", "12 * sin(30) + sqrt(144) - log10(1000)", Fraction(15)),
    ("exponent-precedence", "-(3^4) + 2^(-3) * 40 + 7 * (13 - 8)", Fraction(-41)),
    ("decimal-cancellation", "(10000000000000001 - 10000000000000000) * (0.1 + 0.2)", Fraction(3,10)),
]


def numeric(value):
    text = str(value).strip()
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+|/\d+)?", text) or len(text) > 100:
        return None
    try:
        return Fraction(text)
    except ZeroDivisionError:
        return None


def run(binary, mode, out):
    if mode == "direct":
        doctor = json.loads(subprocess.check_output([str(binary), "doctor"]))
        if not doctor["available"] or doctor["target"] != "AFM 3 Core Advanced":
            raise ValueError("requires AFM 3 Core Advanced")
    with out.open("x", encoding="utf-8") as stream:
        for name, expression, gold in CASES:
            if mode == "direct":
                command = [str(binary), "--direct", "--trace", "run"]
                request = {"task":"ask","instruction":INSTRUCTION,"source":expression}
                payload = json.dumps(request, ensure_ascii=False)
            else:
                command = [str(binary), "calc", "--angle", "deg"]
                payload = expression
            start = time.perf_counter_ns()
            result = subprocess.run(command, input=payload, text=True, capture_output=True, timeout=120)
            if not result.stdout:
                raise ValueError(f"{name}: no CLI record (exit {result.returncode})")
            record = json.loads(result.stdout)
            value = record["result"] if mode == "direct" else (record["result"] or {}).get("value")
            actual = numeric(value)
            # log/trigonometric results may be approximate even when their mathematical value is integral.
            correct = (actual is not None and (actual == gold or
                       (name == "degree-functions" and abs(actual - gold) <= Fraction(1, 10**12))))
            row = {"id":name,"expression":expression,"route":mode,"expected":str(gold),
                   "output":value,"correct":correct,"record":record,
                   "elapsed_ms":round((time.perf_counter_ns()-start)/1e6,3)}
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",",":")) + "\n")
            stream.flush()
            print(name, str(gold), repr(value), correct, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--mode", choices=("direct","native"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.binary.resolve(), args.mode, args.out)
