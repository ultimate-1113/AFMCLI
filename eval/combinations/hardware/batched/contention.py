"""Balanced standalone vs concurrent official system AFM and verified resident dense retrieval."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import ctypes
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import median
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE),str(HERE.parent/"placement")]
import survey as s
import backend
from client import FixedIndex

GUIDE = "Read the provided synthetic harbour notices and return only the requested file and dock entries."
FIXED_BATCHES = 480
SPEC = {"population":"performance stress test, four ranked synthetic harbour records per AFM receipt",
        "shape":"first qualifying survey shape; if none, 4096 x 1024 as a separately labelled diagnostic",
        "arm_cycles":"rotate the interleaved condition sequence through every position once",
        "fixed_batches":"480, retained from the first calibration before its cross-clock validation failed; report the fresh calibration too",
        "offset":"start sidework 12% of pilot AFM time after prompt handoff, at most 200 ms",
        "clock":"public mach_absolute_time converted with mach_timebase_info in both Python and MPS; verify each native process is bracketed before timing",
        "inputs":"alternating pregenerated resident banks 0,1; full output materialization and comparison"}


@lru_cache(maxsize=1)
def mach_clock():
    class Timebase(ctypes.Structure):
        _fields_=[("numer",ctypes.c_uint32),("denom",ctypes.c_uint32)]
    lib=ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    lib.mach_absolute_time.restype=ctypes.c_uint64
    lib.mach_absolute_time.argtypes=[]
    info=Timebase()
    lib.mach_timebase_info.argtypes=[ctypes.POINTER(Timebase)]
    s.need(lib.mach_timebase_info(ctypes.byref(info))==0 and info.numer>0 and info.denom>0,"Mach timebase")
    return lib.mach_absolute_time, int(info.numer), int(info.denom)


def clock():
    ticks,numer,denom=mach_clock()
    return ticks()*numer//denom


def prompt_for(m, proof, lines=100):
    ranked = [i for i, _ in proof[0]["top"][0]["top5"][:4]]
    indices = list(dict.fromkeys(ranked+[(j*101+3) % m for j in range(lines)]))
    indices.sort()
    source = [s.data.notice(i)[0] for i in indices]
    keys = []
    for i in ranked:
        text = s.data.notice(i)[0]
        file = re.search(r"HR-\d+",text).group()
        dock = re.search(r"(?:dock |岸壁)(\d+)",text).group(1)
        keys.append([file,dock])
    ask = ("Use only these records. For each requested file, copy the identifier and dock number, "
           "one line in the form HR-12345 | 8. Requested files in order: " +
           ", ".join(x[0] for x in keys) + ".\n\n" + "\n".join(source))
    return ask, keys


def transcript(raw, prompt):
    entries = raw["transcript"]["transcript"]["entries"]
    user = [e for e in entries if e["role"]=="user"]
    s.need(raw["modelName"]=="system" and len(user)==1 and
           any(p.get("text")==prompt for p in user[0]["contents"]), "unexpected AFM route or prompt")
    last=entries[-1]
    s.need(last["role"] in ("response","assistant"), "missing AFM response")
    return {"model":raw["modelName"],"user_hash":hashlib.sha256(prompt.encode()).hexdigest(),
            "finish":last.get("finishReason"),"text":"".join(p.get("text","") for p in last["contents"])}


def afm(prompt, side=None, delay=0):
    with tempfile.TemporaryDirectory(prefix="batch-afm-") as td, ThreadPoolExecutor(max_workers=1) as pool:
        path = Path(td)/"transcript.json"
        command=[backend.FM,"respond","--model","system","--greedy","--no-stream","--instructions",GUIDE,
                 "--save-transcript",str(path)]
        start=clock()
        p=subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            p.stdin.write(prompt.encode()); p.stdin.close(); p.stdin=None
            handed=clock()
            def worker():
                rest=(handed+delay-clock())/1e9
                if rest>0:
                    time.sleep(rest)
                return side()
            future=pool.submit(worker) if side is not None else None
            _, error=p.communicate(timeout=90)
            returned=clock()
            s.need(p.returncode==0 and path.exists(), "official AFM capture failed: "+error.decode(errors="replace")[:300])
            record=dict(transcript(json.loads(path.read_bytes()),prompt), start=start,handed=handed,returned=returned)
            record["transcript_sha256"]=s.sha(path)
            return record, (future.result() if future else None)
        finally:
            if p.poll() is None:
                p.kill(); p.wait()


class Work:
    def __init__(self, machine, index, proof):
        self.machine=machine
        self.index=index
        self.proof=proof

    def run(self, method, n):
        start=clock()
        if method=="compiled":
            rows=[]
            for j in range(n):
                r=self.index.call({"id":str(j),"prepared":str(j%2),"compact":True,"fp16_digest_only":True})
                s.need(r["scores_sha256_f16le"]==self.proof[j%2]["f16_sha256"] and
                       r["elements"]==self.proof[j%2]["cells"], "compiled full scores differ")
                rows.append([r["start_ns"],r["predict_start_ns"],r["prediction_returned_ns"],
                             r["complete_ns"],r["native_total_ns"],r["scores_sha256_f16le"]])
        else:
            rows=self.machine.run(method,0,0,n)["trials"]
        return {"method":method,"start":start,"end":clock(),"rows":rows}


def design(methods):
    arms=[("none",True)]
    for i,method in enumerate(methods):
        arms.extend([(method,bool(i%2)),(method,not bool(i%2))])
    # The reverse rotations on alternate positions keep single and concurrent
    # work from running as two contiguous blocks of conditions.
    order=arms[::2]+arms[1::2][::-1]
    return [[order[(i+block)%len(order)] for i in range(len(order))] for block in range(len(order))]


def measure(args, survey):
    selector=survey["selected"]
    shape=selector or list(s.data.SHAPES[-1])
    m,q=shape
    with ExitStack() as stack:
        machine=s.Machine(args.binary,args.inputs,m,q); stack.callback(machine.close)
        proof=machine.ready["proof"]
        methods=["cpu","gpu"]
        placement=None
        prep=[]
        index=None
        if args.placement:
            directory=args.models/f"m{m}-q{q}"
            s.need((directory/"index.mlmodelc").is_dir(),"chosen compiled model unavailable")
            placement=json.loads(subprocess.check_output([str(args.placement),"plan",str(directory),"cpuAndNeuralEngine"],
                                                         timeout=90))
            index=stack.enter_context(FixedIndex(args.placement,directory,"cpuAndNeuralEngine"))
            s.need([index.ready["row_count"],index.ready["queries"]]==shape,"different compiled shape")
            for bank in range(2):
                prep.append(index.call({"id":f"prep{bank}","key":str(bank),"load_query_file":str((args.inputs/"queries.f32").resolve()),
                                        "query_offset":bank*s.data.BANK}))
                initial=index.call({"id":f"verify{bank}","prepared":str(bank),"compact":True})
                s.need(initial["scores_sha256_u16le"]==proof[bank]["u16_sha256"] and
                       initial["elements"]==proof[bank]["cells"], "compiled cross-engine calibration")
                proof[bank]["f16_sha256"]=initial["scores_sha256_f16le"]
                prep[-1]["crosscheck"]=initial
            methods.append("compiled")
        runner=Work(machine,index,proof)
        prompt,keys=prompt_for(m,proof)
        count=backend.count(prompt,GUIDE)
        s.need(0<count<=6200,"AFM input cap")
        samples={}
        clock_checks={"python":sys.version.split()[0],"python_monotonic_ns":time.monotonic_ns(),
                      "mach_absolute_ns":clock(),"native_brackets":{}}
        for method in methods:
            runner.run(method,2)
            samples[method]=runner.run(method,8)
            intervals,_=checked_work(samples[method],method,8,proof)
            clock_checks["native_brackets"][method]=[intervals[0][0]-samples[method]["start"],
                                                     samples[method]["end"]-intervals[-1][1]]
            s.need(min(clock_checks["native_brackets"][method])>=0,"route clock outside Python Mach bracket")
        # Bracket checks run before either the first warm AFM generation or timed work.
        cold,_=afm(prompt); pilot,_=afm(prompt)
        afm_ns=pilot["returned"]-pilot["handed"]
        fastest=min((samples[x]["end"]-samples[x]["start"])/8 for x in ("cpu","gpu"))
        fresh_n=max(2,min(8192,2*math.ceil(.85*afm_ns/(2*fastest))))
        n=FIXED_BATCHES
        delay=min(200_000_000,int(.12*afm_ns))
        rows=[]
        print("selected",m,q,"balanced batches per condition",n,"methods",methods,file=sys.stderr,flush=True)
        for i,block in enumerate(design(methods)):
            for method,generates in block:
                call=(lambda:runner.run(method,n)) if method!="none" else None
                if generates:
                    a,w=afm(prompt,call,delay)
                else:
                    a,w=None,call()
                rows.append({"block":i,"method":method,"generates":generates,"afm":a,"work":w})
            print("completed contention block",i+1,"of",len(design(methods)),file=sys.stderr,flush=True)
        return {"spec":SPEC,"selected":selector,"measured_shape":shape,"methods":methods,"prompt":prompt,"keys":keys,"tokens":count,
                "cold":cold,"pilot":pilot,"samples":samples,"delay_ns":delay,"batches":n,"model_plan":placement,
                "clock_check":clock_checks,"fresh_calibration_batches":fresh_n,
                "prepared":prep,"proof":proof,"rows":rows,"sources":{"contention.py":s.sha(HERE/"contention.py")},
                "benchmark_binary":s.sha(args.binary),"compiled_binary":s.sha(args.placement) if args.placement else None}


def intersect(a,b,c,d):
    return max(0,min(b,d)-max(a,c))


def checked_work(w, method, n, proof):
    s.need(w["method"]==method and len(w["rows"])==n and 0<w["start"]<w["end"],"incomplete work")
    operations=[]; device=[]; last=w["start"]
    for i,r in enumerate(w["rows"]):
        if method=="compiled":
            start,pred,done,end,wall,stamp=r
            s.need(start<=pred<=done<=end and wall>=end-start and stamp==proof[i%2]["f16_sha256"],"compiled proof")
        else:
            s.marks(r,method=="gpu"); start,end=r[0],r[4]
            if method=="gpu":
                device.append(r[6:8])
        s.need(last-1_000_000<=start<=end<=w["end"]+1_000_000,"inconsistent native and parent clocks")
        operations.append((start,end)); last=end
    return operations,device


def answer(a,prompt,keys):
    s.need(a["model"]=="system" and a["user_hash"]==hashlib.sha256(prompt.encode()).hexdigest() and
           0<a["start"]<=a["handed"]<a["returned"] and len(a["transcript_sha256"])==64,"AFM provenance")
    pairs=re.findall(r"(HR-\d+)\s*\|\s*(\d+)\b",a["text"])
    return {"ms":(a["returned"]-a["start"])/1e6,"pairs_correct":list(map(list,pairs))==keys,
            "output_bytes":len(a["text"].encode()),"output_sha256":hashlib.sha256(a["text"].encode()).hexdigest()}


def summarize(raw, survey):
    s.need(raw["spec"]==SPEC and raw["selected"]==survey["selected"],"changed contention protocol")
    shape=raw["selected"] or list(s.data.SHAPES[-1])
    s.need(raw["measured_shape"]==shape, "changed diagnostic shape")
    s.need(raw["sources"]["contention.py"]==s.sha(HERE/"contention.py"),"contention code changed")
    methods=raw["methods"]; n=raw["batches"]; proof=raw["proof"]
    s.need(methods in (["cpu","gpu"],["cpu","gpu","compiled"]) and n%2==0 and n<=8192,"arm or work balance")
    if "compiled" in methods:
        for bank in range(2):
            cross=raw["prepared"][bank]["crosscheck"]
            s.need(cross["scores_sha256_u16le"]==proof[bank]["u16_sha256"] and
                   cross["scores_sha256_f16le"]==proof[bank]["f16_sha256"] and
                   cross["elements"]==proof[bank]["cells"],"compiled numeric cross-check mismatch")
    prompt,keys=prompt_for(shape[0],proof)
    s.need((prompt,keys)==(raw["prompt"],raw["keys"]) and 0<raw["tokens"]<=6200,"request changed")
    for a in (raw["cold"],raw["pilot"]):
        answer(a,prompt,keys)
    for method in methods:
        sample=raw["samples"][method]
        intervals,_=checked_work(sample,method,8,proof)
        bounds=[intervals[0][0]-sample["start"],sample["end"]-intervals[-1][1]]
        s.need(min(bounds)>=0 and bounds==raw["clock_check"]["native_brackets"][method],"early native clock checks")
    expected_n=max(2,min(8192,2*math.ceil(.85*(raw["pilot"]["returned"]-raw["pilot"]["handed"])/(2*
        min((raw["samples"][x]["end"]-raw["samples"][x]["start"])/8 for x in ("cpu","gpu"))))))
    s.need(n==FIXED_BATCHES and raw["fresh_calibration_batches"]==expected_n,"different calibration")
    schedule=design(methods)
    s.need(len(raw["rows"])==len(schedule)**2,"missing balanced block")
    grouped={}; paired={}
    for z,row in enumerate(raw["rows"]):
        b=z//len(schedule); method,gen=schedule[b][z%len(schedule)]
        s.need((row["block"],row["method"],row["generates"])==(b,method,gen),"arm order changed")
        a,w=row["afm"],row["work"]
        s.need(bool(a)==gen and bool(w)==(method!="none"),"missing arm result")
        rec={}
        if a:
            check=answer(a,prompt,keys);rec.update(afm_ms=check["ms"],quality=int(check["pairs_correct"]),
                output_bytes=check["output_bytes"],output_hash=check["output_sha256"])
        if w:
            intervals,gpu=checked_work(w,method,n,proof)
            rec.update(work_ms=(w["end"]-w["start"])/1e6,batches=n,
                       batch_e2e_ms=median((j-i)/1e6 for i,j in intervals))
        if a and w:
            x,y=a["handed"],a["returned"]
            rec.update(overlap_work_fraction=intersect(x,y,w["start"],w["end"])/(w["end"]-w["start"]),
                       afm_window_with_active_work=sum(intersect(x,y,i,j) for i,j in intervals)/(y-x),
                       afm_window_with_explicit_gpu=sum(intersect(x,y,i,j) for i,j in gpu)/(y-x),
                       makespan_ms=(max(y,w["end"])-min(a["start"],w["start"]))/1e6)
        label=method+("_afm" if gen else "_solo")
        grouped.setdefault(label,[]).append(rec);paired[b,label]=rec
    result={}
    for label,rows in grouped.items():
        summary={k:median(r[k] for r in rows) for k in rows[0] if k not in ("quality","output_hash")}
        summary["n"]=len(rows)
        if "quality" in rows[0]:
            summary["exact_receipts"]=sum(r["quality"] for r in rows)
            summary["distinct_outputs"]=len({r["output_hash"] for r in rows})
        if "makespan_ms" in summary:
            method=label.split("_")[0]
            summary["paired_afm_over_alone"]=median(paired[b,label]["afm_ms"]/paired[b,"none_afm"]["afm_ms"] for b in range(len(rows)))
            summary["paired_work_over_alone"]=median(paired[b,label]["work_ms"]/paired[b,method+"_solo"]["work_ms"] for b in range(len(rows)))
            summary["paired_makespan_over_serial"]=median(paired[b,label]["makespan_ms"]/(paired[b,method+"_solo"]["work_ms"]+
                paired[b,"none_afm"]["afm_ms"]) for b in range(len(rows)))
        result[label]=summary
    return {"selected":raw["selected"],"measured_shape":shape,
            "basis":"qualified resident workload" if raw["selected"] else "predeclared nonqualifying diagnostic",
            "afm_input_tokens":raw["tokens"],"balanced_batches":n,"arms":result,
            "warmup_and_calibration_afm":2,"timed_afm":sum(bool(r["afm"]) for r in raw["rows"]),
            "attribution":"AFM host-process and handoff-to-return overlap only. MPS timestamps come from the actual command buffer."}


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("run","replay"));p.add_argument("--out",type=Path,required=True)
    p.add_argument("--binary",type=Path);p.add_argument("--inputs",type=Path)
    p.add_argument("--placement",type=Path);p.add_argument("--models",type=Path)
    args=p.parse_args();survey=s.load(args.out/"survey-summary.json");path=args.out/"contention.json"
    if args.action=="run":
        with s.system_lock():
            raw=s.load(path) if path.exists() else measure(args,survey)
            provisional=args.out/"contention-unvalidated.json"
            s.save(provisional,raw)
            summary=summarize(raw,survey)
            provisional.replace(path)
            s.save(args.out/"contention-summary.json",summary)
    else:
        summary=summarize(s.load(path),survey)
    print(json.dumps(summary,ensure_ascii=False,sort_keys=True))
