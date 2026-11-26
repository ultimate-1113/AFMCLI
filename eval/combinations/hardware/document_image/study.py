"""Exploratory scanned-form morphology on Apple vImage and Metal Performance Shaders."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import fcntl
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
from statistics import median
import struct
import subprocess
import tempfile
import time
import zlib

HERE = Path(__file__).resolve().parent
PROBE_SOURCE = HERE.parent / "activity" / "StreamProbe.swift"
SIZES = ((512,768), (2048,3072), (4096,6144))
MODES = ("afm", "vimage", "mps", "vimage+afm", "mps+afm", "vimage-then-afm", "mps-then-afm")
SCHEDULES = ((0,1,4,5,2,3,6),(6,3,2,5,4,1,0),(3,6,0,2,5,1,4))
VISION_PROMPT = "Read the large two-digit number printed at the top of this scanned form. Return just the two digits."
MODEL = "AFM 3 Core Advanced"
FASTEST_RATIO = 0.90


def need(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value, pretty=False):
    path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2 if pretty else None,
                               separators=None if pretty else (",",":"))+"\n",encoding="utf-8")


@contextmanager
def gate():
    path = Path(tempfile.gettempdir()) / f"afmcli-moe-system-{os.getuid()}.lock"
    with path.open("a+") as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        yield


@lru_cache(maxsize=1)
def clock_api():
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    lib.mach_absolute_time.restype = ctypes.c_uint64
    tb = (ctypes.c_uint32*2)()
    need(lib.mach_timebase_info(ctypes.byref(tb)) == 0 and tb[1], "Mach timebase missing")
    return lib.mach_absolute_time, tb[0], tb[1]


def now():
    raw, n, d = clock_api()
    return raw()*n//d


def prompt():
    rows = [f"Row {i} describes shelf {i % 9}, a collection of coloured tiles, "
            f"and ledger {i % 13} in the fictional harbour archive." for i in range(1,101)]
    return "\n".join(rows)+("\nUsing this fictional setting, write a detailed story of about 300 English words "
                            "about two archivists sorting the tiles. Give the story a beginning and an ending.")


class Machine:
    def __init__(self, binary, size):
        self.proc = subprocess.Popen([str(binary),*map(str,size)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                     text=True,encoding="utf-8",bufsize=1)
        try:
            self.ready = self.receive()
            need(self.ready["event"]=="ready" and (self.ready["width"],self.ready["height"])==size,"wrong image worker")
        except BaseException:
            self.close()
            raise

    def receive(self):
        line=self.proc.stdout.readline()
        need(line,"image worker exited before returning its result")
        return json.loads(line)

    def run(self,backend,pooled,repeats,**options):
        self.proc.stdin.write(json.dumps(dict(backend=backend,pooled=pooled,repeats=repeats,**options))+"\n")
        self.proc.stdin.flush()
        out=self.receive()
        need(out["backend"]==backend and out["pooled"]==pooled and len(out["frames"])==repeats,"image reply differs")
        return out

    def close(self):
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.proc.stdout.close()


def survey_order(i):
    pools=(False,True) if i%2==0 else (True,False)
    for pooled in pools:
        for repetition in range(6):
            engines=("vimage","mps") if (i+int(pooled)+repetition)%2==0 else ("mps","vimage")
            for engine in engines:
                yield pooled,repetition,engine


def check_frames(item,ready):
    fs=item["frames"]
    need(item["all_pixels_equal"] and item["output_sha256"]==ready["reference_sha256"] and
         item["pixels_compared"]==len(fs)*ready["width"]*ready["height"],"pixel check missing")
    for i,f in enumerate(fs):
        need(0<f["start"]<=f["prepared"]<f["delivered"]<=f["checked"],"invalid host timeline")
        if i:
            need(fs[i-1]["checked"]<=f["start"],"overlapping frames")
        if item["backend"]=="mps":
            need(f["prepared"]<=f["submitted"]<=f["gpu_start"]<f["gpu_end"]<=f["delivered"],"invalid GPU interval")
        else:
            need(f["gpu_start"]==f["gpu_end"]==f["submitted"]==0,"unexpected GPU work")


def ms(ns):
    return round(ns/1e6,3)


def breakdown(survey):
    table=[]
    for case in survey:
        ready, trials=case["ready"],case["trials"]
        row={"size":[ready["width"],ready["height"]],"pixels":ready["width"]*ready["height"],
             "noise_pixels":ready["noise_pixels"],"changed_pixels":ready["changed_pixels"],
             "absolute_error_before":ready["input_abs_error"],"absolute_error_after":ready["output_abs_error"],
             "source_generation_ms":ms(ready["generation_ns"]),"cpu_pool_setup_ms":ms(ready["cpu_pool_ns"]),
             "gpu_context_ms":ms(ready["context_ns"]),"gpu_pool_setup_ms":ms(ready["gpu_pool_ns"])}
        for pooled,mode in ((False,"fresh"),(True,"pooled")):
            sides={}
            for engine in ("vimage","mps"):
                selected=[x["run"]["frames"][0] for x in trials if x["run"]["backend"]==engine and x["run"]["pooled"]==pooled]
                times=[f["delivered"]-f["start"] for f in selected]
                sides[engine]=times[1:]
                row[mode+"_"+engine]={"first_ms":ms(times[0]),"warm_median_ms":ms(median(times[1:])),
                                      "warm_min_ms":ms(min(times[1:])),"warm_max_ms":ms(max(times[1:])),
                                      "gpu_command_median_ms":ms(median(f["gpu_end"]-f["gpu_start"] for f in selected[1:]))}
            ratio=median(sides["mps"])/median(sides["vimage"])
            wins=sum(g<c for c,g in zip(sides["vimage"],sides["mps"]))
            row[mode+"_gpu_to_cpu"]=round(ratio,5)
            row[mode+"_gpu_wins_of_five"]=wins
            if pooled:
                row["eligible"]=ratio<=FASTEST_RATIO and wins>=4
        table.append(row)
    return table


def selection(table):
    eligible=[row for row in table if row["eligible"]]
    if not eligible:
        return None,0
    row=eligible[-1]
    # Equal page count for both engines. The short fixed duration targets a real overlap
    # with the ongoing stream and is capped to bound the amount of work.
    repeats=min(64,max(4,math.ceil(350/row["pooled_mps"]["warm_median_ms"])))
    return tuple(row["size"]),repeats


def film(stream):
    p=subprocess.run([str(stream)],input=prompt().encode(),capture_output=True,timeout=100,check=True)
    r=json.loads(p.stdout)
    need(r["variant"]==MODEL and r["start"]<r["first"]<=r["end"],"system stream markers missing")
    r["content_sha256"]=sha(r["content"].encode())
    return r


def condition(worker,stream,mode,repeats,pool):
    engine=mode.split("+")[0].split("-")[0]
    t0=now()
    model=helper=None
    if mode=="afm":
        model=film(stream)
    elif mode in ("vimage","mps"):
        helper=worker.run(engine,True,repeats)
    elif "-then-" in mode:
        helper=worker.run(engine,True,repeats)
        model=film(stream)
    else:
        pending=pool.submit(film,stream)
        time.sleep(0.3)
        helper=worker.run(engine,True,repeats)
        model=pending.result()
    return {"mode":mode,"begin":t0,"end":now(),"model":model,"helper":helper}


def interval(a,b,c,d):
    return max(0,min(b,d)-max(a,c))


def timing(trial):
    m,h=trial["model"],trial["helper"]
    result={"total_ms":ms(trial["end"]-trial["begin"])}
    if m:
        result.update(afm_ms=ms(m["end"]-m["start"]),to_first_ms=ms(m["first"]-m["start"]),
                      after_first_ms=ms(m["end"]-m["first"]),output_tokens=m["output_tokens"])
    if h:
        fs=h["frames"]
        result.update(auxiliary_span_ms=ms(fs[-1]["checked"]-fs[0]["start"]),
                      active_host_ms=ms(sum(f["delivered"]-f["start"] for f in fs)),
                      checked_pixel_ms=ms(sum(f["checked"]-f["delivered"] for f in fs)),
                      gpu_command_ms=ms(sum(f["gpu_end"]-f["gpu_start"] for f in fs)))
    if h and m:
        def overlap(a,b,k,l):
            return ms(sum(interval(f[a],f[b],m[k],m[l]) for f in fs))
        result.update(host_overlap_ms=overlap("start","delivered","start","end"),
                      host_overlap_before_first_ms=overlap("start","delivered","start","first"),
                      host_overlap_after_first_ms=overlap("start","delivered","first","end"),
                      gpu_command_overlap_ms=overlap("gpu_start","gpu_end","start","end"))
    return result


def png(raw,w,h):
    def chunk(name,data):
        return struct.pack(">I",len(data))+name+data+struct.pack(">I",zlib.crc32(name+data)&0xffffffff)
    scan=b"".join(b"\0"+raw[y*w:(y+1)*w] for y in range(h))
    return (b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",w,h,8,0,0,0,0))+
            chunk(b"IDAT",zlib.compress(scan))+chunk(b"IEND",b""))


def read_image(path,kind):
    transcript=path.with_suffix(".transcript.json")
    args=["/usr/bin/fm","respond","--model","system","--greedy","--no-stream","--image",str(path),
          "--text",VISION_PROMPT,"--save-transcript",str(transcript)]
    t0=now()
    proc=subprocess.run(args,capture_output=True,timeout=75)
    out={"image":kind,"png_sha256":sha(path.read_bytes()),"reader_ms":ms(now()-t0),"exit_code":proc.returncode}
    if proc.returncode or not transcript.exists():
        return dict(out,status="model_error",stderr_sha256=sha(proc.stderr))
    data=json.loads(transcript.read_bytes())
    entries=data["transcript"]["transcript"]["entries"]
    users=[x for x in entries if x["role"]=="user"]
    last=entries[-1]
    need(data["modelName"]=="system" and len(users)==1 and any(v.get("text")==VISION_PROMPT for v in users[0]["contents"]),"image route changed")
    types=[c["type"] for c in users[0]["contents"]]
    need(any(t!="text" for t in types),"image was missing from captured AFM prompt")
    answer="".join(c.get("text","") for c in last.get("contents",[]) if c.get("type")=="text").strip()
    return dict(out,status="ok" if answer else "empty",model=data["modelName"],user_content_types=types,answer=answer,
                correct_exact=answer=="42")


def image_readers(worker,scratch):
    with tempfile.TemporaryDirectory(dir=scratch,prefix="form-") as work:
        d=Path(work)
        image={}
        for engine in ("vimage","mps"):
            raw=d/(engine+".raw")
            options={"dump":str(raw)}
            if engine=="vimage":
                options["dump_source"]=str(d/"original.raw")
            rec=worker.run(engine,True,1,**options)
            check_frames(rec,worker.ready)
            need(sha(raw.read_bytes())==worker.ready["reference_sha256"],"dumped output differs")
        for kind in ("original","vimage","mps"):
            raw=(d/(kind+".raw")).read_bytes()
            digest=sha(raw)
            need(digest==worker.ready["source_sha256" if kind=="original" else "reference_sha256"],"vision bytes differ")
            path=d/(kind+".png")
            path.write_bytes(png(raw,*SIZES[0]))
            image[kind]=dict(read_image(path,kind),pixel_sha256=digest)
        return image


def original_pixel(x,y,w,h):
    glyph=(("10010","10010","10010","11111","00010","00010","00010"),
           ("01110","10001","00001","00010","00100","01000","11111"))
    scale=max(7,w//30); margin=w//16; table=h*37//100
    dx=x-(w-11*scale)//2; dy=y-h//18
    v=239+x*7//w+y*6//h
    if 0<=dx<11*scale and 0<=dy<7*scale and (dx//scale)%6<5:
        if glyph[dx//(6*scale)][dy//scale][(dx//scale)%6]=="1":
            v=24
    if margin<=x<=w-margin and table<=y<h-margin:
        col=(w-2*margin)//3; row=max(28,h//22)
        rx=x-margin; ry=y-table; cx=rx%col; cy=ry%row; idx=ry//row
        if cy<6 or cx<6:
            v=96
        elif row//3<cy<row//3+max(7,row//6) and col//10<cx<col*(3+(idx+rx//col)%3)//8 and (cx//max(7,col//28))%4!=3:
            v=40+idx%30
    r=(y*w+x+0x84512ab3)&0xffffffff
    r=((r^(r>>16))*0x7feb352d)&0xffffffff
    r=((r^(r>>15))*0x846ca68b)&0xffffffff
    r^=r>>16
    return (255 if r&128 else 0) if not (r&127) else v


def sample_oracle(ready):
    w,h=ready["width"],ready["height"]
    @lru_cache(maxsize=None)
    def point(stage,x,y):
        if stage==0:
            return original_pixel(x,y,w,h)
        op=max if stage in (2,3) else min
        return op(point(stage-1,max(0,min(w-1,x+dx)),max(0,min(h-1,y+dy)))
                  for dx in range(-2,3) for dy in range(-2,3))
    samples=ready["reference_samples"]
    need(len(samples)==14 and all(point(4,x,y)==v for x,y,v in samples),"independent sampled 2D oracle differs")
    point.cache_clear()


def report(data):
    blocks={}
    for row in data["contention"]:
        blocks.setdefault(row["mode"],[]).append(timing(row))
    aggregate={name:{key:round(median(r[key] for r in values),3) for key in values[0]} for name,values in blocks.items()}
    return {"survey":breakdown(data["survey"]),"selected_size":data["size"],"pages_per_aux_job":data["repeats"],
            "contention_medians":aggregate,
            "vision":{k:{"status":v["status"],"answer":v.get("answer"),"correct_exact":v.get("correct_exact")} for k,v in data["vision"].items()}}


def verify(args):
    data=json.loads((args.out/"observations.json").read_bytes())
    files=[Path(__file__),HERE/"Filter.mm",PROBE_SOURCE]
    need(data["sources"]=={p.name:sha(p.read_bytes()) for p in files} and data["prompt_sha256"]==sha(prompt().encode()),"source or prompt changed")
    need(len(data["survey"])==3,"incomplete shape panel")
    for i,(case,size) in enumerate(zip(data["survey"],SIZES)):
        r=case["ready"]
        need((r["width"],r["height"])==size and r["kernel"]==5 and r["changed_pixels"] and r["noise_pixels"],"wrong source")
        sample_oracle(r)
        expected=list(survey_order(i))
        need(len(case["trials"])==len(expected),"incomplete survey")
        for row,(pooled,rep,engine) in zip(case["trials"],expected):
            out=row["run"]
            need((out["pooled"],row["repetition"],out["backend"])==(pooled,rep,engine) and len(out["frames"])==1,"survey order differs")
            check_frames(out,r)
    selected,repeats=selection(breakdown(data["survey"]))
    need((list(selected) if selected else None)==data["size"] and repeats==data["repeats"],"selection differs")
    if selected:
        ready=data["survey"][SIZES.index(selected)]["ready"]
        schedules=[(i+1,MODES[j]) for i,seq in enumerate(SCHEDULES) for j in seq]
        need(len(schedules)==len(data["contention"]),"incomplete concurrency panel")
        for r,(round_no,mode) in zip(data["contention"],schedules):
            need((r["round"],r["mode"])==(round_no,mode) and r["begin"]<r["end"],"trial scheduling differs")
            h,m=r["helper"],r["model"]
            need(bool(h)==(mode!="afm") and bool(m)==(mode not in ("vimage","mps")),"missing route")
            if h:
                need(len(h["frames"])==repeats and h["backend"]==mode.split("+")[0].split("-")[0] and h["pooled"],"unequal image work")
                check_frames(h,ready)
                need(r["begin"]<=h["frames"][0]["start"]<=h["frames"][-1]["checked"]<=r["end"],"worker clock differs")
            if m:
                need(r["begin"]<=m["start"]<m["first"]<=m["end"]<=r["end"] and m["variant"]==MODEL and sha(m["content"].encode())==m["content_sha256"],"wrong stream/clock")
            if "-then-" in mode:
                need(h["frames"][-1]["checked"]<=m["start"],"sequential work overlapped")
    else:
        need(not data["contention"],"unexpected post-selection experiment")
    vision=data["vision"]
    need(set(vision)=={"original","mps","vimage"} and vision["vimage"]["png_sha256"]==vision["mps"]["png_sha256"],"different filtered images")
    for name,r in vision.items():
        need(r["pixel_sha256"]==data["survey"][0]["ready"]["source_sha256" if name=="original" else "reference_sha256"],"reader pixel hash differs")
        if r["status"]=="ok":
            need(r["model"]=="system" and any(x!="text" for x in r["user_content_types"]) and r["correct_exact"]==(r["answer"]=="42"),"image grading differs")
    need(json.loads((args.out/"summary.json").read_bytes())==report(data),"summary differs")
    print("Verified 72 host-image runs, 42 independent pixel oracles, vision records and all selected overlap intervals.")


def capture(args):
    args.out.mkdir(parents=True,exist_ok=True)
    need(not any(args.out.iterdir()),"use an empty output directory")
    args.scratch.mkdir(parents=True,exist_ok=True)
    with gate():
        code=[Path(__file__),HERE/"Filter.mm",PROBE_SOURCE]
        data={"utc":datetime.now(timezone.utc).isoformat(),"machine":platform.machine(),"os":platform.mac_ver()[0],
              "os_build":subprocess.check_output(["sw_vers","-buildVersion"],text=True).strip(),
              "sources":{p.name:sha(p.read_bytes()) for p in code},"image_binary_sha256":sha(args.binary.read_bytes()),
              "stream_binary_sha256":sha(args.stream.read_bytes()),"prompt_sha256":sha(prompt().encode()),
              "survey":[],"contention":[],"vision":{}}
        for i,size in enumerate(SIZES):
            worker=Machine(args.binary,size)
            try:
                trials=[]
                for pooled,rep,engine in survey_order(i):
                    record=worker.run(engine,pooled,1)
                    check_frames(record,worker.ready)
                    trials.append({"repetition":rep,"run":record})
                data["survey"].append({"ready":worker.ready,"trials":trials})
                if i==0:
                    data["vision"]=image_readers(worker,args.scratch)
            finally:
                worker.close()
            print(size,breakdown(data["survey"])[-1],flush=True)
        size,repeats=selection(breakdown(data["survey"]))
        data.update(size=list(size) if size else None,repeats=repeats)
        if size:
            worker=Machine(args.binary,size)
            try:
                for engine in ("vimage","mps"):
                    check_frames(worker.run(engine,True,1),worker.ready)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    for i,sequence in enumerate(SCHEDULES,1):
                        for j in sequence:
                            row=dict(condition(worker,args.stream,MODES[j],repeats,pool),round=i)
                            data["contention"].append(row)
                            print(i,MODES[j],timing(row),flush=True)
            finally:
                worker.close()
        save(args.out/"observations.json",data)
        save(args.out/"summary.json",report(data),True)
    verify(args)


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    subs=p.add_subparsers(dest="action",required=True)
    live=subs.add_parser("capture")
    for name in ("binary","stream","scratch"):
        live.add_argument("--"+name,type=Path,required=True)
    for cmd in (live,subs.add_parser("verify")):
        cmd.add_argument("--out",type=Path,required=True)
    args=p.parse_args()
    (capture if args.action=="capture" else verify)(args)
