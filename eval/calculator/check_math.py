"""Compare the native batch calculator with Python's fractions and math libraries."""
import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import time


def cases():
    rng = random.Random(20261126)
    for _ in range(200):
        a, b, c, d, e, f, g = (rng.randint(1, 38) for _ in range(7))
        yield "rational", f"({a}/{b}+{c}/{d})*({e}-{f})/{g}", (Fraction(a,b)+Fraction(c,d))*(e-f)/g, "rad"
    for _ in range(100):
        a,b,n = rng.randint(1,800),rng.randint(1,95),rng.randint(0,4)
        yield "decimal", f"({a}e-3+{b}/100)^{n}", (Fraction(a,1000)+Fraction(b,100))**n, "rad"
    for i in range(32):
        k = i // 3
        yield "choose", f"nCr({i},{k})", Fraction(math.comb(i,k)), "rad"
        yield "factorial", f"{i}!", Fraction(math.factorial(i)), "rad"
    for degrees in range(-72, 73, 6):
        for fn in ("sin", "cos", "tan"):
            yield "angle", f"{fn}({degrees})", getattr(math,fn)(math.radians(degrees)), "deg"
    for i in range(1, 33):
        yield "root", f"sqrt({i}/7)", math.sqrt(i/7), "rad"
        yield "log", f"ln({i}/5)", math.log(i/5), "rad"
        yield "inverse", f"asin({i}/33)", math.asin(i/33), "rad"
    for expr in ("1/0","sqrt(-1)","ln(0)","tan(90)","acos(2)","(-1)!","0^0","2+",
                 "2(3)","sqrt()","1e9000","2^10000"):
        yield "invalid", expr, None, "deg"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    binary = args.binary.resolve()
    panel = list(cases())
    data = [json.dumps({"id":str(i),"task":"calculate","expression":x,"angle":angle})
            for i,(_,x,_,angle) in enumerate(panel)]
    start = time.perf_counter_ns()
    p = subprocess.run([binary,"batch"], input="\n".join(data)+"\n", capture_output=True, text=True, timeout=30)
    duration = (time.perf_counter_ns()-start)/1e9
    rows = [json.loads(l) for l in p.stdout.splitlines()]
    if len(rows) != len(panel):
        raise ValueError("incomplete batch response")
    wrong, counts, calls = [], Counter(), 0
    for i,((kind,expr,expected,_),r) in enumerate(zip(panel,rows)):
        counts[kind] += 1
        if r["id"] != str(i):
            raise ValueError("batch changed order")
        calls += r["metrics"]["calls"]
        if expected is None:
            good = r["status"] == "error" and r["result"] is None
        elif r["status"] != "ok":
            good = False
        elif isinstance(expected,Fraction):
            good = not r["result"]["approximate"] and Fraction(r["result"]["value"]) == expected
        else:
            good = math.isclose(float(Fraction(r["result"]["value"])),expected,rel_tol=1e-12,abs_tol=1e-12)
        if not good:
            wrong.append({"expression":expr,"expected":str(expected),"actual":r["result"]})
    answer = {"binary_sha256":hashlib.sha256(binary.read_bytes()).hexdigest(),"total":len(rows),
              "by_kind":dict(counts),"errors":wrong,"model_calls":calls,"elapsed_ms":round(duration*1000,3),
              "single_batch_jobs_per_second":round(len(rows)/duration,1)}
    print(json.dumps(answer,ensure_ascii=False))
    if args.out:
        with args.out.open("x") as f:
            json.dump(answer,f,ensure_ascii=False,indent=2)
            f.write("\n")
    if wrong or calls:
        raise ValueError("numeric oracle mismatch or unexpected model calls")


if __name__ == "__main__":
    main()
