"""Matched resident and streamed dense retrieval. CPU is multithreaded Accelerate SGEMM."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
from statistics import median
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from study import system_lock
if __package__:
    from . import matrix_data as data
else:
    import matrix_data as data

PROTOCOL = {"k": 1024, "shapes": list(map(list, data.SHAPES)), "warmup_pairs": 2, "timed_pairs": 8,
            "criterion": "smallest ordered shape where resident median GPU E2E / median CPU E2E < 0.90",
            "pair_order": "CPU-GPU, GPU-CPU alternating. Timed banks 0,1,1,0 twice.",
            "e2e": "accept caller query through full synchronous host copy, before full comparison",
            "stream": "CPU reads caller-contiguous query directly; GPU copies it into one reusable shared buffer"}


def need(ok, msg):
    if not ok:
        raise ValueError(msg)


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, allow_nan=False, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(handle.name, path)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


class Machine:
    def __init__(self, binary, directory, m, q):
        self.p = subprocess.Popen([str(binary), str(directory), str(m), str(q)], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        try:
            self.ready = self.readline()
        except BaseException:
            self.close()
            raise

    def readline(self):
        line = self.p.stdout.readline()
        if not line:
            raise RuntimeError(self.p.stderr.read()[-1500:] or "missing native message")
        return json.loads(line)

    def run(self, method, bank, stream=0, count=1):
        self.p.stdin.write(f"{method} {bank} {int(stream)} {count}\n")
        self.p.stdin.flush()
        obj = self.readline()
        need((obj["method"], obj["first_bank"], obj["streaming"], len(obj["trials"])) ==
             (method, bank, int(stream), count), "native request mismatch")
        return obj

    def close(self):
        if self.p.poll() is None:
            try:
                self.p.stdin.write("quit\n")
                self.p.stdin.flush()
            except BrokenPipeError:
                pass
        self.p.wait(timeout=20)
        for f in (self.p.stdin,self.p.stdout,self.p.stderr):
            f.close()


def schedule(index):
    for stream in ((0,1) if index % 2 == 0 else (1,0)):
        for stage, reps in (("warmup",2),("timed",8)):
            for rep in range(reps):
                bank = (rep ^ (rep // 2)) % 2
                order = ("cpu","gpu") if (index + rep) % 2 == 0 else ("gpu","cpu")
                for method in order:
                    yield stage, rep, stream, bank, method


def marks(t, gpu):
    need(len(t) == 8 and all(isinstance(x, int) for x in t), "timing format")
    a, p, submitted, complete, out, checked, gs, ge = t
    need(0 < a <= p <= submitted <= complete <= out <= checked and out > a, "invalid host timing")
    need((0 < gs < ge and gs >= a - 1_000_000 and ge <= complete + 1_000_000)
         if gpu else gs == ge == 0, "invalid GPU interval")
    return {"e2e_ms":(out-a)/1e6, "compute_sync_ms":(complete-p)/1e6, "readback_ms":(out-complete)/1e6,
            "query_upload_ms":(p-a)/1e6, "verified_cycle_ms":(checked-a)/1e6, "gpu_command_ms":(ge-gs)/1e6}


def inputs_key(directory):
    meta = load(directory/"inputs.json")
    for name, expected in meta["raw_sha256"].items():
        need(sha(directory/name) == expected, "input bytes differ")
    need(meta["seed"] == data.SEED and meta["shapes"] == PROTOCOL["shapes"], "fixture protocol mismatch")
    return meta


def counts(x):
    return x.bit_count() if hasattr(int,"bit_count") else bin(x).count("1")


def check_proof(proof, m, q, nums):
    docs, qs = nums
    for bank, item in enumerate(proof):
        need(item["bank"] == bank and item["cells"] == m*q, "bank coverage")
        for i,j,score in item["samples"]:
            need(counts(docs[i] & qs[bank*data.BANK+j]) == score, "independent integer sample")
        for top in item["top"]:
            vector = qs[bank*data.BANK+top["query"]]
            ranking = sorted(((counts(v & vector),i) for i,v in enumerate(docs[:m])), key=lambda x:(-x[0],x[1]))
            need([[i,s] for s,i in ranking[:5]] == top["top5"], "independent integer top five")
        need(len(item["sha256"]) == len(item["u16_sha256"]) == 64, "full score hashes")
    need(len(proof) == 2 and proof[0]["sha256"] != proof[1]["sha256"], "stale bank outputs")


def verify(raw, directory):
    need(raw["protocol"] == PROTOCOL, "protocol differs")
    meta = inputs_key(directory)
    need(meta["raw_sha256"] == raw["inputs"]["raw_sha256"], "different common matrices")
    need(raw["sources"] == {n:sha(HERE/n) for n in ("matrix_data.py","Batch.mm","survey.py")}, "survey code differs")
    _, _, docs, queries = data.features()
    bitrows = lambda rows: [sum(1 << col for col in row) for row in rows]
    nums = bitrows(docs), bitrows(queries)
    shapes = []
    need(len(raw["measurements"]) == len(data.SHAPES), "missing shapes")
    for index, ((m,q), row) in enumerate(zip(data.SHAPES,raw["measurements"])):
        ready = row["ready"]
        need((ready["m"],ready["q"],ready["k"]) == (m,q,data.K) and ready["automatic_blas_threads"], "native shape/thread mode")
        check_proof(ready["proof"],m,q,nums)
        records = {}
        order = list(schedule(index))
        need(len(row["trials"]) == len(order), "unbalanced timing")
        for (stage, rep, stream, bank, method), item in zip(order,row["trials"]):
            actual = item["native"]
            need([stage,rep,stream,bank,method] == [item["stage"],item["rep"],actual["streaming"],
                 actual["first_bank"],actual["method"]] and len(actual["trials"])==1,"pair order")
            timing = marks(actual["trials"][0],method=="gpu")
            if stage == "timed":
                records.setdefault((stream,method),[]).append(timing)
        s = {"m":m,"q":q,"cells":m*q}
        for stream,label in ((0,"resident"),(1,"stream")):
            med = {r:{key:median(v[key] for v in records[stream,r]) for key in records[stream,r][0]}
                   for r in ("cpu","gpu")}
            ratio = med["gpu"]["e2e_ms"]/med["cpu"]["e2e_ms"]
            s[label] = {**med,"gpu_over_cpu":ratio,
                        "gpu_faster_pairs":sum(g["e2e_ms"] < c["e2e_ms"]
                            for c,g in zip(records[stream,"cpu"],records[stream,"gpu"]))}
        shapes.append(s)
    passing = [[s["m"],s["q"]] for s in shapes if s["resident"]["gpu_over_cpu"] < .9]
    return {"device":raw["measurements"][0]["ready"]["device"], "eligible":passing, "selected":next(iter(passing),None),
            "shapes":shapes,"inputs":meta["raw_sha256"],"paired_timed_per_shape_per_input_mode":8,
            "evidence": "gpu_command_ms is explicit MPS command-buffer device time; CPU uses threaded Accelerate and may use matrix hardware"}


def run(binary, directory, out):
    dest = out/"survey.json"
    if dest.exists():
        return verify(load(dest),directory)
    meta = inputs_key(directory)
    raw = {"protocol":PROTOCOL,"inputs":meta, "sources":{n:sha(HERE/n) for n in ("matrix_data.py","Batch.mm","survey.py")},
           "binary_sha256":sha(binary),"macos":platform.mac_ver()[0],"measurements":[]}
    with system_lock():
        for index,(m,q) in enumerate(data.SHAPES):
            start = time.monotonic_ns()
            machine = Machine(binary,directory,m,q)
            row = {"cold_to_verified_ready_ns":time.monotonic_ns()-start,"ready":machine.ready,"trials":[]}
            try:
                for stage,rep,stream,bank,method in schedule(index):
                    row["trials"].append({"stage":stage,"rep":rep,"native":machine.run(method,bank,stream)})
            finally:
                machine.close()
            raw["measurements"].append(row)
            print(f"finished matched shape {m} x {q}",file=sys.stderr,flush=True)
        summary = verify(raw,directory)
        save(dest,raw)
        save(out/"survey-summary.json",summary)
    return summary


if __name__ == "__main__":
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument("action",choices=("run","replay")); a.add_argument("--binary",type=Path)
    a.add_argument("--inputs",type=Path,required=True); a.add_argument("--out",type=Path,required=True)
    args=a.parse_args()
    summary = run(args.binary,args.inputs,args.out) if args.action=="run" else verify(load(args.out/"survey.json"),args.inputs)
    print(json.dumps({"selected":summary["selected"],"resident_gpu_over_cpu":
          [[s["m"],s["q"],round(s["resident"]["gpu_over_cpu"],4)] for s in summary["shapes"]]}))
