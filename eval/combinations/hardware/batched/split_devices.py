"""Divide independent search batches between explicit MPS and an ANE-preferred Core ML model.

AFM gets one ordinary, unchanged request. Replaying the public UTF-8 traces does
not launch any model or open a device.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
from statistics import median
import subprocess
import sys
import threading

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import contention as previous
import survey
from client import FixedIndex


N = 480
SHAPE = [4096, 1024]
SPEC = {"shape": SHAPE, "logical_batches": N, "bank": "job_id mod 2",
        "partition": "MPS [0,240), compiled [240,480); both branches start behind the same barrier",
        "afm": "one system fm process; exact same prompt, guide and sidework launch delay as the earlier accepted study",
        "replicates": 7, "order": "rotate the seven fixed conditions so that every condition occupies every position",
        "scope": "applications schedule independent auxiliary search jobs; AFM internals are opaque"}
BASE = (("none", True), ("gpu", False), ("compiled", True), ("split", False),
        ("gpu", True), ("compiled", False), ("split", True))


def order():
    return [(block, *BASE[(block + position) % len(BASE)])
            for block in range(len(BASE)) for position in range(len(BASE))]


def jobs(method, count):
    if method == "split":
        return [("gpu", 0, count // 2), ("compiled", count // 2, count // 2)]
    return [(method, 0, count)]


class Dispatch:
    def __init__(self, work, pool):
        self.work = work
        self.pool = pool

    def run(self, method, count):
        parts = jobs(method, count)
        released = []
        start = previous.clock()
        gate = threading.Barrier(len(parts) + 1, action=lambda: released.append(previous.clock()))

        def branch(route, begin, length):
            gate.wait(timeout=10)
            return {"begin": begin, "length": length, "work": self.work.run(route, length)}

        futures = [self.pool.submit(branch, *part) for part in parts]
        gate.wait(timeout=10)
        branches = [f.result() for f in futures]
        return {"method": method, "count": count, "start": start, "released": released[0],
                "end": previous.clock(), "branches": branches}


def overlap(first, second):
    """Length-ordered, disjoint intervals within each one of the two streams."""
    i = j = 0
    result = []
    while i < len(first) and j < len(second):
        a,b = first[i]; x,y = second[j]
        if max(a,x) < min(b,y):
            result.append((max(a,x), min(b,y)))
        if b <= y:
            i += 1
        else:
            j += 1
    return result


def length(intervals):
    return sum(b-a for a,b in intervals)


def check_dispatch(dispatch, method, count, proof):
    start, released, end = (dispatch[k] for k in ("start","released","end"))
    survey.need(dispatch["method"] == method and dispatch["count"] == count and start <= released <= end,
                "changed sidework request")
    parts = jobs(method,count)
    survey.need(len(dispatch["branches"]) == len(parts), "branch count")
    logical = {}
    windows = {}
    kernels = []
    predictions = []
    for expected, branch in zip(parts,dispatch["branches"]):
        route, begin, n = expected
        w = branch["work"]
        survey.need((branch["begin"],branch["length"],w["method"]) == expected[1:]+(route,), "wrong partition")
        intervals, gpu = previous.checked_work(w,route,n,proof)
        survey.need(released <= w["start"] <= intervals[0][0] <= intervals[-1][1] <= w["end"] <= end,
                    "parent and native Mach clock brackets differ")
        windows[route] = [(w["start"],w["end"])]
        if route == "gpu":
            kernels = gpu
        else:
            predictions = [(row[1],row[2]) for row in w["rows"]]
        for i in range(n):
            job = begin+i
            survey.need(job not in logical and job%2==i%2,"duplicate batch or changed bank")
            logical[job] = proof[i%2]["u16_sha256"]
    survey.need(sorted(logical) == list(range(count)), "missing or repeated logical output")
    digest = hashlib.sha256("".join(f"{i} {logical[i]}\n" for i in range(count)).encode()).hexdigest()
    pair_window = overlap(windows.get("gpu",[]),windows.get("compiled",[]))
    physical_and_host = overlap(kernels,predictions)
    return {"jobs_sha256":digest, "batch_count":count, "side_ms":(end-start)/1e6,
            "both_branch_wall_ms":length(pair_window)/1e6,
            "gpu_command_ms":length(kernels)/1e6, "compiled_prediction_call_ms":length(predictions)/1e6,
            "gpu_command_and_compiled_prediction_ms":length(physical_and_host)/1e6}, kernels, predictions


def check_plan(plan):
    convs=[op for group in plan["functions"].values() for op in group if op["operator"].endswith(".conv")]
    survey.need(plan["policy"]=="cpuAndNeuralEngine" and len(convs)==1 and
                convs[0]["preferred"]["kind"]=="neuralEngine", "compiled convolution is not ANE-preferred")
    return convs[0]["preferred"]


def sources():
    return {str(p.relative_to(HERE.parent)):survey.sha(p) for p in
            (HERE/"split_devices.py",HERE/"contention.py",HERE/"survey.py",HERE/"Batch.mm",
             HERE.parent/"placement"/"client.py",HERE.parent/"placement"/"Placement.swift")}


def capture(args, earlier):
    survey.need(earlier["measured_shape"]==SHAPE and earlier["batches"]==N,"changed reference workload")
    binary=args.binary; place=args.placement
    model=args.models/"m4096-q1024"
    prompt=earlier["prompt"]
    with ExitStack() as stack:
        machine=survey.Machine(binary,args.inputs,*SHAPE); stack.callback(machine.close)
        plan=json.loads(subprocess.check_output([str(place),"plan",str(model),"cpuAndNeuralEngine"],timeout=90))
        check_plan(plan)
        index=stack.enter_context(FixedIndex(place,model,"cpuAndNeuralEngine"))
        survey.need([index.ready["row_count"],index.ready["queries"]]==SHAPE,"compiled model shape")
        proof=machine.ready["proof"]; prepared=[]
        for b in range(2):
            record=index.call({"id":f"p{b}","key":str(b),"load_query_file":str((args.inputs/"queries.f32").resolve()),
                               "query_offset":1024*b})
            verify=index.call({"id":f"v{b}","prepared":str(b),"compact":True})
            survey.need(verify["scores_sha256_u16le"]==proof[b]["u16_sha256"] and
                        verify["elements"]==SHAPE[0]*SHAPE[1],"all-score comparison")
            proof[b]["f16_sha256"]=verify["scores_sha256_f16le"]
            survey.need(proof[b]==earlier["proof"][b],"source or complete outputs differ from reference study")
            prepared.append(dict(record,verification=verify))
        pool=stack.enter_context(ThreadPoolExecutor(max_workers=2))
        dispatch=Dispatch(previous.Work(machine,index,proof),pool)
        warm=[]
        for method in ("gpu","compiled","split"):
            one=dispatch.run(method,4)
            check_dispatch(one,method,4,proof)
            warm.append(one)
        warm_afm,_=previous.afm(prompt)
        tokens=previous.backend.count(prompt,previous.GUIDE)
        survey.need(tokens==earlier["tokens"],"AFM input changed")
        raw={"spec":SPEC,"sources":sources(),"reference_sha256":survey.sha(args.out/"contention.json"),
             "binary_sha256":survey.sha(binary),"compiled_binary_sha256":survey.sha(place),
             "proof":proof,"prepared":prepared,"plan":plan,"prompt":prompt,"guide":previous.GUIDE,
             "delay_ns":earlier["delay_ns"],"tokens":tokens,"warm_search":warm,"warm_afm":warm_afm,"rows":[]}
        for block,method,gen in order():
            call=(lambda:dispatch.run(method,N)) if method!="none" else None
            if gen:
                a,w=previous.afm(prompt,call,raw["delay_ns"])
            else:
                a,w=None,call()
            raw["rows"].append({"block":block,"method":method,"generates":gen,"afm":a,"dispatch":w})
            if len(raw["rows"]) % len(BASE)==0:
                print(f"completed split block {block+1}/{len(BASE)}",file=sys.stderr,flush=True)
    return raw


def summarize(raw, earlier, reference_hash):
    survey.need(raw["spec"]==SPEC and raw["sources"]==sources() and raw["reference_sha256"]==reference_hash,
                "source or reference capture changed")
    survey.need(raw["proof"]==earlier["proof"] and raw["prompt"]==earlier["prompt"] and
                raw["tokens"]==earlier["tokens"] and raw["guide"]==previous.GUIDE and
                raw["delay_ns"]==earlier["delay_ns"],"changed inputs")
    preferred=check_plan(raw["plan"])
    proof=raw["proof"]
    for b,record in enumerate(raw["prepared"]):
        verify=record["verification"]
        survey.need(verify["scores_sha256_u16le"]==proof[b]["u16_sha256"] and
                    verify["scores_sha256_f16le"]==proof[b]["f16_sha256"] and verify["elements"]==SHAPE[0]*SHAPE[1],
                    "prepared bank proof mismatch")
    survey.need(len(raw["prepared"])==2 and len(raw["warm_search"])==3,"missing warm stages")
    for method,w in zip(("gpu","compiled","split"),raw["warm_search"]):
        check_dispatch(w,method,4,proof)
    previous.answer(raw["warm_afm"],raw["prompt"],earlier["keys"])
    survey.need(len(raw["rows"])==len(order()),"incomplete condition schedule")
    records={}; groups={}; logical_hashes=set(); answers=set()
    baseline_texts={r["afm"]["text"] for r in earlier["rows"] if r["afm"]}
    for item,(block,method,gen) in zip(raw["rows"],order()):
        a,w=item["afm"],item["dispatch"]
        survey.need((item["block"],item["method"],item["generates"])==(block,method,gen)
                    and bool(a)==gen and bool(w)==(method!="none"),"changed rotation")
        entry={}
        if a:
            check=previous.answer(a,raw["prompt"],earlier["keys"])
            answers.add(a["text"])
            entry["afm_ms"]=check["ms"]
            entry["exact_receipt"]=int(check["pairs_correct"])
            entry["afm_output_bytes"]=check["output_bytes"]
        if w:
            metrics,gpu,pred=check_dispatch(w,method,N,proof)
            logical_hashes.add(metrics.pop("jobs_sha256"))
            entry.update(metrics)
        if a and w:
            active=[(a["handed"],a["returned"])]
            gpu_window=overlap(gpu,active); compiled_window=overlap(pred,active)
            entry["gpu_command_during_afm_ms"]=length(gpu_window)/1e6
            entry["compiled_prediction_during_afm_ms"]=length(compiled_window)/1e6
            entry["gpu_command_and_compiled_prediction_during_afm_ms"]=length(overlap(gpu_window,compiled_window))/1e6
            entry["side_outer_during_afm_ms"]=length(overlap([(w["start"],w["end"])],active))/1e6
            entry["whole_ms"]=(max(a["returned"],w["end"])-min(a["start"],w["start"]))/1e6
        else:
            entry["whole_ms"]=entry.get("afm_ms",entry.get("side_ms"))
        label=f"{method}_{'afm' if gen else 'solo'}"
        records[block,label]=entry
        groups.setdefault(label,[]).append(entry)
    survey.need(len(logical_hashes)==1,"conditions produced different ordered results")
    arms={}
    for label,rows in groups.items():
        arm={k:median(v[k] for v in rows) for k in rows[0] if k!="exact_receipt"}
        arm["n"]=len(rows)
        if "exact_receipt" in rows[0]:
            arm["exact_receipts"]=sum(v["exact_receipt"] for v in rows)
            ratios=[records[i,label]["afm_ms"]/records[i,"none_afm"]["afm_ms"] for i in range(len(rows))]
            arm["paired_afm_over_alone"]=[min(ratios),median(ratios),max(ratios)]
        if label.startswith("split_"):
            suffix=label.split("_")[1]
            for other in ("gpu","compiled"):
                ratios=[records[i,label]["side_ms"]/records[i,f"{other}_{suffix}"]["side_ms"] for i in range(len(rows))]
                arm["paired_side_over_"+other]=[min(ratios),median(ratios),max(ratios)]
        arms[label]=arm
    return {"shape":SHAPE,"batches":N,"tokens":raw["tokens"],"arms":arms,"compiled_preferred":preferred,
            "timed_afm":sum(bool(r["afm"]) for r in raw["rows"]),"timed_distinct_answers":len(answers),
            "same_output_as_earlier":answers==baseline_texts,"ordered_outputs_sha256":next(iter(logical_hashes)),
            "attribution":"MPS command-buffer on-device intervals; Core ML prediction-call host intervals; AFM process handoff to return"}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("run","replay"));parser.add_argument("--out",type=Path,required=True)
    for name in ("binary","inputs","placement","models"):
        parser.add_argument("--"+name,type=Path)
    args=parser.parse_args()
    prior_path=args.out/"contention.json"; earlier=survey.load(prior_path)
    raw_path=args.out/"split.json"; summary_path=args.out/"split-summary.json"
    if args.action=="run":
        with survey.system_lock():
            raw=survey.load(raw_path) if raw_path.exists() else capture(args,earlier)
            provisional=args.out/"split-unvalidated.json";survey.save(provisional,raw)
            summary=summarize(raw,earlier,survey.sha(prior_path))
            provisional.replace(raw_path);survey.save(summary_path,summary)
    else:
        summary=summarize(survey.load(raw_path),earlier,survey.sha(prior_path))
        survey.need(survey.load(summary_path)==summary,"saved split summary differs")
    print(json.dumps(summary,ensure_ascii=False,sort_keys=True))
