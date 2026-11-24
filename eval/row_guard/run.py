"""Run or replay the predeclared row-edit panel. Only direct mode calls the Apple model."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
from collections import Counter

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MODES = ("direct", "position", "fixed-id", "row")
SUMMARY_KEYS = ("correct_document", "correct_disposition", "wrong_write", "valid_held", "invalid_output", "unchanged_proposal", "calls")

def compact(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def frozen():
    files = [*sorted((ROOT/"src").glob("*.rs")), ROOT/"Cargo.toml", ROOT/"Cargo.lock",
             *(HERE/n for n in ("protocol.md", "cases.jsonl", "make_cases.py", "run.py"))]
    return {str(p.relative_to(ROOT)):digest(p) for p in files}

def pointer(field):
    return "/" + field.replace("~", "~0").replace("/", "~1")

def payload(case, mode):
    job = case["job"]
    if mode in ("direct", "row"):
        return job
    selector = job["row"]
    coll = selector["collection"]
    rows = job["document"]
    for part in coll.split("/")[1:]:
        rows = rows[part.replace("~1", "/").replace("~0", "~")]
    index = case["original_index"]
    root = f"{coll}/{index}"
    operations = [dict(c, path=root+c["path"]) for c in job["changes"]]
    if mode == "fixed-id":
        operations.append(dict(path=root+pointer(selector["id_field"]), expect=selector["id"], op="set", value=selector["id"]))
    # A positional writer advances its current row's version after a write.
    rev_field = selector["revision_field"]
    seen = selector["revision"] if mode=="fixed-id" else rows[index][rev_field]
    dirty = any(c["op"]=="add" and c["value"]!=0 or c["op"]=="set" and c["value"]!=c["expect"] for c in job["changes"])
    operations.append(dict(path=root+pointer(rev_field), expect=seen, op="add", value=int(dirty)))
    return dict(document=job["document"], changes=operations)

def decode(record, direct):
    value = record.get("result")
    if direct:
        if not isinstance(value, str):
            return None
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value.strip(), re.S | re.I)
        try:
            value = json.loads(fenced[1] if fenced else value)
        except ValueError:
            return None
    if not isinstance(value,dict) or set(value)!={"document","changed"} or type(value["changed"]) is not bool:
        return None
    return value

def grade(case, mode, response):
    direct=mode=="direct"
    result=decode(response,direct)
    gold=case["expected"]
    status=response["status"]
    written=result is not None and compact(result["document"])!=compact(case["job"]["document"])
    same=result is not None and compact(result["document"])==compact(gold["document"])
    correct=same and result["changed"] is gold["changed"]
    return dict(correct_document=correct, correct_disposition=(None if direct else correct and status==gold["status"]),
                wrong_write=written and not same, valid_held=not direct and status=="held" and gold["status"]=="ok",
                invalid_output=result is None, unchanged_proposal=direct and result is not None and not written,
                calls=response["metrics"]["calls"])

def measure(case,mode,binary,fingerprint):
    cmd=[str(binary), *(["--direct"] if mode=="direct" else []), "edit"]
    start=time.monotonic_ns()
    p=subprocess.run(cmd,input=compact(payload(case,mode)),capture_output=True,text=True,timeout=120)
    if p.returncode not in (0,3):
        raise RuntimeError(f"case {case['id']} {mode} exited {p.returncode}: {p.stderr[:100]}")
    response=json.loads(p.stdout)
    return dict(id=case["id"],mode=mode,binary_sha256=fingerprint,elapsed_ms=round((time.monotonic_ns()-start)/1e6),
                response=response,score=grade(case,mode,response))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("action",choices=["freeze","run","verify","replay"])
    ap.add_argument("--binary",type=Path,default=ROOT/"target/release/afmcli")
    ap.add_argument("--output",type=Path,default=HERE/"results.jsonl")
    args=ap.parse_args()
    lock=HERE/"lock.json"
    if args.action=="freeze":
        with lock.open("x") as f: f.write(json.dumps(frozen(),sort_keys=True,indent=2)+"\n")
        return
    if json.loads(lock.read_text()) != frozen():
        raise ValueError("row panel lock mismatch")
    cases=read_lines(HERE/"cases.jsonl")
    outputs=read_lines(args.output) if args.output.exists() else []
    by_key={(r["id"],r["mode"]):r for r in outputs}
    expected={(c["id"],m) for c in cases for m in MODES}
    if len(by_key)!=len(outputs) or not set(by_key)<=expected:
        raise ValueError("duplicate or unexpected output")
    if args.action in ("run","replay"):
        binary=args.binary.resolve()
        fingerprint=digest(binary)
        if args.action=="run":
            if any(r["binary_sha256"]!=fingerprint for r in outputs):
                raise ValueError("resume requires the original binary")
            with args.output.open("a") as f:
                for case in cases:
                    for mode in MODES:
                        key=(case["id"],mode)
                        if key in by_key:continue
                        row=measure(case,mode,binary,fingerprint)
                        f.write(compact(row)+"\n");f.flush();by_key[key]=row
        else:
            for case in cases:
                for mode in MODES[1:]:
                    current=measure(case,mode,binary,fingerprint)
                    prior=by_key[case["id"],mode]
                    assert current["score"]==prior["score"] and current["response"]["result"]==prior["response"]["result"],(case["id"],mode)
    assert set(by_key)==expected and len(cases)==36
    sums={m:Counter() for m in MODES}
    for case in cases:
        for mode in MODES:
            row=by_key[case["id"],mode]
            assert row["score"]==grade(case,mode,row["response"]),(case["id"],mode)
            for k in SUMMARY_KEYS:
                val=row["score"][k]
                if val is not None:sums[mode][k]+=int(val)
    print(json.dumps(sums,sort_keys=True,indent=2))

if __name__=="__main__":
    main()
