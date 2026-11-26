"""Show aggregate hardware readings while another application runs on this Mac."""
import argparse
import json
import math
from statistics import mean, median
import time

from activity import HostCPU, PERIOD, ane_inventory, gauge


def observe(seconds):
    start = time.monotonic()
    next_sample = start
    cpu = HostCPU()
    first = cpu.ticks()
    gpu = []
    while next_sample < start + seconds:
        time.sleep(max(0, next_sample - time.monotonic()))
        gpu.append(gauge())
        next_sample += PERIOD
    last = cpu.ticks()
    delta = [(b-a) % (2**32) for a,b in zip(first,last)]
    total = sum(delta)
    return {
        "scope": "whole Mac during the sampling window",
        "seconds": round(time.monotonic()-start,3),
        "gpu_samples": len(gpu),
        "gpu_percent": {"median":median(gpu), "mean":round(mean(gpu),2), "max":max(gpu)},
        "cpu_percent_whole_machine": round(100*(total-delta[2])/total,2) if total else None,
        "ane_compute_percent": None,
        "ane_visible_utilization_keys": ane_inventory(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=10)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds < 0.5:
        parser.error("--seconds must cover at least two samples (0.5 seconds)")
    print(json.dumps(observe(args.seconds), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
