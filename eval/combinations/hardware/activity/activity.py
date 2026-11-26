"""Sample aggregate operating-system counters during synthetic AFM and Metal work."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import plistlib
from statistics import median
import subprocess
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "batched"))
from survey import Machine, need, save, system_lock

MODEL = "AFM 3 Core Advanced"
WINDOW = 6
PERIOD = 0.25
ORDERS = (("afm", "gpu", "both"), ("gpu", "both", "afm"), ("both", "afm", "gpu"))
GPU_SHAPE = (4096, 1024)
GPU_KEY = "Device Utilization %"


@lru_cache(maxsize=1)
def mach_timebase():
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    lib.mach_absolute_time.restype = ctypes.c_uint64
    ratio = (ctypes.c_uint32 * 2)()
    need(lib.mach_timebase_info(ctypes.byref(ratio)) == 0 and ratio[1] > 0, "Mach clock unavailable")
    return lib.mach_absolute_time, ratio[0], ratio[1]


def clock_ns():
    # Older macOS Python versions rebase time.monotonic_ns() to this process.
    # Swift DispatchTime and Metal command timestamps use the system Mach clock.
    ticks, numerator, denominator = mach_timebase()
    return ticks() * numerator // denominator


def prompt():
    rows = [f"Row {i} describes shelf {i % 9}, a collection of coloured tiles, "
            f"and ledger {i % 13} in the fictional harbour archive." for i in range(1, 101)]
    return "\n".join(rows) + ("\nUsing this fictional setting, write a detailed story of about 300 English words "
                               "about two archivists sorting the tiles. Give the story a beginning and an ending.")


def registry_rows(cls):
    proc = subprocess.run(["/usr/sbin/ioreg", "-a", "-r", "-d", "1", "-c", cls],
                          capture_output=True, check=True, timeout=5)
    return plistlib.loads(proc.stdout) if proc.stdout else []


def gauge():
    readings = [node.get("PerformanceStatistics", {}).get(GPU_KEY) for node in registry_rows("IOAccelerator")]
    readings = [v for v in readings if isinstance(v, (int, float)) and 0 <= v <= 100]
    need(len(readings) == 1, "expected a single GPU publishing Device Utilization %")
    return readings[0]


def ane_inventory():
    result = {}
    for cls in ("H11ANEIn", "H1xANELoadBalancer"):
        rows = registry_rows(cls)
        def usage_keys(node):
            tables = (node, node.get("PerformanceStatistics", {}), node.get("DeviceProperties", {}))
            return sorted({k for table in tables for k in table if "utilization" in k.casefold()})
        result[cls] = [usage_keys(row) for row in rows]
    return result


class HostCPU:
    """Aggregate public Mach tick counters. One hundred percent is the whole host."""
    def __init__(self):
        self.lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        self.lib.mach_host_self.restype = ctypes.c_uint
        self.host = self.lib.mach_host_self()
        self.lib.host_statistics.argtypes = (ctypes.c_uint, ctypes.c_int, ctypes.c_void_p,
                                              ctypes.POINTER(ctypes.c_uint))

    def ticks(self):
        count = ctypes.c_uint(4)
        states = (ctypes.c_uint * 4)()
        result = self.lib.host_statistics(self.host, 3, states, ctypes.byref(count))
        need(result == 0 and count.value == 4, "HOST_CPU_LOAD_INFO unavailable")
        return list(states)


def samples(stop, epoch):
    cpu = HostCPU()
    rows = []
    next_tick = time.monotonic()
    while not stop.is_set():
        a = clock_ns()
        gpu, states = gauge(), cpu.ticks()
        b = clock_ns()
        rows.append([a-epoch, b-epoch, gpu, states])
        next_tick += PERIOD
        stop.wait(max(0, next_tick-time.monotonic()))
    return rows


def afm(binary, epoch):
    proc = subprocess.run([str(binary)], input=prompt().encode(), capture_output=True, check=True, timeout=90)
    row = json.loads(proc.stdout)
    need(row["variant"] == MODEL and 0 < row["start"] < row["first"] <= row["end"], "invalid streaming result")
    row["output_sha256"] = hashlib.sha256(row.pop("content").encode()).hexdigest()
    for field in ("start", "first", "end"):
        row[field] -= epoch
    return row


def gpu(machine, epoch):
    """Six seconds of resident lexical search with full output validation each iteration."""
    start = clock_ns()
    end = start + WINDOW * 1_000_000_000
    spans = []
    while clock_ns() < end:
        reply = machine.run("gpu", len(spans) // 4 % 2, 0, 4)
        for trial in reply["trials"]:
            accepted, ready, submitted, completed, copied, verified, gpu_start, gpu_end = trial
            need(accepted <= ready <= submitted <= completed <= copied <= verified and 0 < gpu_start < gpu_end,
                 "invalid Metal evidence")
            spans.append([gpu_start-epoch, gpu_end-epoch])
    return {"start": start-epoch, "end": clock_ns()-epoch, "commands": spans}


def recording(probe, metal, inputs):
    machine = Machine(metal, inputs, *GPU_SHAPE)
    try:
        machine.run("gpu", 0, 0, 2)
        afm(probe, 0)
        inventory = ane_inventory()
        epoch = clock_ns()
        stop = threading.Event()
        spans = []
        with ThreadPoolExecutor(max_workers=3) as workers:
            sample_task = workers.submit(samples, stop, epoch)
            try:
                for rep, order in enumerate(ORDERS):
                    for kind in ("idle", *order):
                        row = {"rep": rep, "kind": kind, "begin": clock_ns()-epoch}
                        if kind == "idle":
                            time.sleep(WINDOW)
                        else:
                            f = workers.submit(afm, probe, epoch) if kind in ("afm", "both") else None
                            g = workers.submit(gpu, machine, epoch) if kind in ("gpu", "both") else None
                            if f:
                                row["afm"] = f.result()
                            if g:
                                row["gpu"] = g.result()
                        row["end"] = clock_ns()-epoch
                        spans.append(row)
            finally:
                stop.set()
            readings = sample_task.result()
    finally:
        machine.close()
    # Retain relative tick changes, without publishing host-wide counters since boot.
    first = readings[0][3]
    for r in readings:
        r[3] = [(v-i) % (2**32) for v, i in zip(r[3], first)]
    return {"format": 1, "model": MODEL, "fixture_sha256": hashlib.sha256(prompt().encode()).hexdigest(),
            "sample_period_ms": int(PERIOD*1000), "metal_shape_m_k_q": [GPU_SHAPE[0],1024,GPU_SHAPE[1]],
            "ane_visible_utilization_keys": inventory, "phases": spans, "samples": readings}


def summary(result):
    phases = result["phases"]
    need(result["model"] == MODEL and result["fixture_sha256"] == hashlib.sha256(prompt().encode()).hexdigest()
         and result["format"] == 1 and result["sample_period_ms"] == int(PERIOD*1000)
         and result["metal_shape_m_k_q"] == [GPU_SHAPE[0],1024,GPU_SHAPE[1]], "different protocol")
    need([(p["rep"],p["kind"]) for p in phases] == [(i,k) for i,o in enumerate(ORDERS) for k in ("idle", *o)],
         "incomplete order")
    rows = result["samples"]
    need(all(0 <= a <= b and 0 <= v <= 100 and len(c) == 4 for a,b,v,c in rows), "invalid sample")
    observations = []
    for old, now in zip(rows,rows[1:]):
        need(old[1] <= now[0], "out-of-order observation")
        di = [(x-y) % (2**32) for x,y in zip(now[3],old[3])]
        observations.append((old[0],now[1],now[2],100*(sum(di)-di[2])/sum(di) if sum(di) else None))
    groups = {}
    for phase in phases:
        k=phase["kind"]
        start,end=phase["begin"],phase["end"]
        intervals={}
        if "afm" in phase:
            f=phase["afm"]
            need(start<=f["start"]<f["first"]<=f["end"]<=end and f["input_tokens"]>0 and f["output_tokens"]>0,
                 "invalid AFM interval")
            if k=="afm":
                intervals["afm-to-first"]=(f["start"],f["first"])
                intervals["afm-after-first"]=(f["first"],f["end"])
                intervals["afm"]=(f["start"],f["end"])
        if "gpu" in phase:
            g=phase["gpu"]
            commands=g["commands"]
            need(start<=g["start"]<g["end"]<=end and commands and
                 all(g["start"]<=a<b<=g["end"] for a,b in commands) and
                 all(p[1]<=q[0] for p,q in zip(commands,commands[1:])), "invalid GPU interval")
            intervals[k]=(g["start"],g["end"]) if k=="gpu" else (max(g["start"],f["start"]),min(g["end"],f["end"]))
        if k=="idle":
            intervals[k]=(start,end)
        for key,(a,b) in intervals.items():
            need(a<b,"empty work overlap")
            eligible=[(g,c) for lo,hi,g,c in observations if a<=lo and hi<=b]
            groups.setdefault(key,[]).append(eligible)
    def stats(values):
        if not values:
            return {"n":0}
        v=sorted(values)
        return {"n":len(v),"median":round(median(v),2),"mean":round(sum(v)/len(v),2),
                "p95":round(v[math.ceil(len(v)*.95)-1],2),"max":round(v[-1],2)}
    aggregate = {}
    for name, rounds in groups.items():
        aggregate[name] = {
            "gpu_percent": stats([g for run in rounds for g, _ in run]),
            "cpu_percent_whole_machine": stats([c for run in rounds for _, c in run if c is not None]),
            "round_gpu_medians": [median([g for g, _ in run]) if run else None for run in rounds],
        }
    afm_times = {}
    for name in ("afm", "both"):
        replies = [phase["afm"] for phase in phases if phase["kind"] == name]
        afm_times[name] = {
            "ttft_ms": stats([(reply["first"]-reply["start"])/1e6 for reply in replies]),
            "completion_ms": stats([(reply["end"]-reply["start"])/1e6 for reply in replies]),
            "input_tokens": [reply["input_tokens"] for reply in replies],
            "output_tokens": [reply["output_tokens"] for reply in replies],
            "output_sha256": [reply["output_sha256"] for reply in replies],
        }
    return {"phases": aggregate, "afm_streaming": afm_times,
            "positive_control_above_idle_median":
                aggregate["gpu"]["gpu_percent"]["median"] > aggregate["idle"]["gpu_percent"]["median"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run","replay"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--probe", type=Path)
    parser.add_argument("--metal", type=Path)
    parser.add_argument("--inputs", type=Path)
    args = parser.parse_args()
    if args.action == "run":
        need(args.probe and args.metal and args.inputs, "supply the AFM probe, Metal binary and input directory")
        with system_lock():
            need(not args.out.exists(), "choose a fresh output path")
            result = recording(args.probe, args.metal, args.inputs)
            save(args.out, result)
        output = summary(result)
    else:
        output = summary(json.loads(args.out.read_bytes()))
    print(json.dumps(output,indent=2,sort_keys=True))


if __name__=="__main__":
    main()
