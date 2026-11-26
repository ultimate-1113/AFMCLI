"""Build a document-specific fixed Core ML index and check its public compute plan."""
import argparse
from contextlib import contextmanager
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter_ns as clock

from client import FixedIndex

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import rank_study as historical

POLICIES = ("cpuOnly", "cpuAndNeuralEngine")
LOCK = Path(tempfile.gettempdir()) / f"afmcli-moe-system-{os.getuid()}.lock"


@contextmanager
def measurement(work):
    work.mkdir(parents=True, exist_ok=True)
    cache = work / "build-tmp"
    cache.mkdir(exist_ok=True)
    # Capture LOCK before changing TMPDIR. All coordinated measurements must
    # use the same OS temp directory as the other AFM experiments.
    with LOCK.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        os.environ["TMPDIR"] = str(cache)
        tempfile.tempdir = str(cache)
        yield


def encode(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def digest(data):
    return sha256(data).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def native(binary, *args):
    process = subprocess.run([str(binary), *map(str, args)], check=True, stdout=subprocess.PIPE)
    return json.loads(process.stdout)


def describe():
    import coremltools as ct
    import numpy as np
    def cmd(*argv):
        p = subprocess.run(argv, capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else None
    return {"macos": platform.mac_ver()[0], "build": cmd("sw_vers", "-buildVersion"),
            "arch": platform.machine(), "chip": cmd("sysctl", "-n", "machdep.cpu.brand_string"),
            "python": platform.python_version(), "numpy": np.__version__, "coremltools": ct.__version__,
            "swift": cmd("swiftc", "--version"), "compute_plan_api": "MLComputePlan.deviceUsage(for:)"}


def size_bytes(path):
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def build_index(directory, weights, queries, binary, metadata):
    import coremltools as ct
    from coremltools.converters.mil import Builder as mb
    from coremltools.converters.mil.mil import types
    import numpy as np
    rows, dim = weights.shape
    if weights.dtype != np.float16 or not np.all((weights == 0) | (weights == 1)):
        raise ValueError("Expected a binary FP16 weight matrix")
    weights_hash = digest(weights.astype("<f2", copy=False).tobytes())
    manifest_path = directory / "manifest.json"
    if manifest_path.is_file():
        existing = json.loads(manifest_path.read_bytes())
        if (existing["weights_sha256_f16le"], existing["queries"], existing["row_count"]) != (weights_hash, queries, rows):
            raise ValueError("Existing index has different weights or shape. Choose a fresh work directory.")
        return existing
    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=directory.name + "-build-", dir=directory.parent))
    package = staging / "index.mlpackage"
    start = clock()

    @mb.program(input_specs=[mb.TensorSpec(shape=(1, dim, 1, queries), dtype=types.fp16)],
                opset_version=ct.target.macOS13)
    def graph(query):
        return mb.conv(x=query, weight=weights.reshape(rows, dim, 1, 1), name="scores")

    ct.convert(graph, source="milinternal", convert_to="mlprogram", minimum_deployment_target=ct.target.macOS13,
               package_dir=str(package), compute_precision=ct.precision.FLOAT16, skip_model_load=True)
    converted = clock()
    compilation = native(binary, "compile", staging)
    ended = clock()
    manifest = {"dimension": dim, "row_count": rows, "queries": queries,
                "weights_sha256_f16le": weights_hash,
                "package_bytes": size_bytes(package), "compiled_bytes": size_bytes(staging / "index.mlmodelc"),
                "convert_and_serialize_ns": converted-start, "compile_process_wall_ns": ended-converted,
                **compilation, **metadata}
    save(staging / "manifest.json", manifest)
    staging.rename(directory)
    return manifest


def build_historical(work):
    import numpy as np
    for case, source_tokens, _, _ in historical.load_cases()[:4]:
        start = clock()
        lines, data = historical.prepare(case["job"], warm=False)
        weights = np.zeros((len(lines), data["dimension"]), dtype=np.float16)
        for i, active in enumerate(data["rows"]):
            weights[i, active] = 1
        metadata = {"id": case["id"], "source_sha256_utf8": digest(case["job"]["source"].encode()),
                    "question_sha256_utf8": digest(case["job"]["question"].encode()),
                    "source_apple_tokens": source_tokens, "features_and_weights_ns": clock()-start}
        build_index(work / "indexes" / case["id"], weights, 1, work / "placement-index", metadata)


def summarize(values):
    v = sorted(values)
    return {"count": len(v), "minimum_ms": v[0]/1e6, "median_ms": statistics.median(v)/1e6,
            "p95_ms": v[(95*len(v)+99)//100-1]/1e6, "maximum_ms": v[-1]/1e6}


def check_documents(work):
    cases = historical.load_cases()[:4]
    ids = [c["id"] for c, _, _, _ in cases]
    queries = {c["id"]: historical.vector(c["job"]["question"], {}) for c, _, _, _ in cases}
    out = {"environment": describe(), "feature_dimension": historical.DIM,
           "fixture_directory": "eval/index_status", "fixture_commit": "2683791860c1ba9249b5da61ce60e8d2a2064b0f",
           "fixture_lock_sha256": digest((historical.OLD / "lock.json").read_bytes()), "documents": []}
    for case, _, order, _ in cases:
        directory = work / "indexes" / case["id"]
        manifest = json.loads((directory / "manifest.json").read_text())
        _, features = historical.prepare(case["job"], warm=False)
        if manifest["source_sha256_utf8"] != digest(case["job"]["source"].encode()):
            raise ValueError("Compiled index source differs")
        # Independent sparse integer set intersection, before Core ML scores.
        ref = {}
        for key, active in queries.items():
            ask = set(active)
            scores = [len(ask.intersection(row)) for row in features["rows"]]
            ref[key] = (scores, sorted(range(len(scores)), key=lambda n: (-scores[n], n)))
        if len({digest(encode(pair[0])) for pair in ref.values()}) != len(queries):
            raise AssertionError("Different questions produced duplicate score vectors")
        probe_order = [case["id"]] + [i for i in ids if i != case["id"]]
        document = {"index": manifest, "policies": {}, "reference": {}}
        for key, (s, r) in ref.items():
            document["reference"][key] = {"scores_sha256_json": digest(encode(s)), "rank_sha256_json": digest(encode(r)),
                                          "active_features": len(queries[key]), "top12_zero_based": r[:12]}
        for policy in order:
            observations = []
            with FixedIndex(work / "placement-index", directory, policy) as index:
                for key in [case["id"]] + probe_order*5:
                    started = clock()
                    trial = index.call({"id": key, "active": queries[key]})
                    wall = clock()-started
                    s, r = trial.pop("scores"), trial.pop("rank")
                    if (s, r) != ref[key]:
                        raise AssertionError(f"Full score or ranking mismatch in {case['id']} ({policy}, {key})")
                    observations.append(dict(trial, wall_ns=wall, scores_and_rank_exact=True,
                                             scores_sha256_json=digest(encode(s)), rank_sha256_json=digest(encode(r))))
                arm = {"ready": index.ready, "startup_wall_ns": index.startup_wall_ns, "trials": observations,
                       "first": observations[0], "warm_predict_and_readback": summarize([t["predict_ns"]+t["readback_ns"] for t in observations[1:]]),
                       "warm_request_wall": summarize([t["wall_ns"] for t in observations[1:]])}
            with FixedIndex(work / "placement-index", directory, policy) as reopened:
                sample = reopened.call({"id": case["id"], "active": queries[case["id"]]})
                if (sample.pop("scores"), sample.pop("rank")) != ref[case["id"]]:
                    raise AssertionError("Scores changed after reloading the same compiled path")
                arm["same_path_reopen"] = {"ready": reopened.ready,
                                           "startup_wall_ns": reopened.startup_wall_ns, "first": sample}
            arm["plan"] = native(work / "placement-index", "plan", directory, policy)
            document["policies"][policy] = arm
        out["documents"].append(document)
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("build", "measure"))
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    with measurement(args.work.resolve()):
        if args.mode == "build":
            build_historical(args.work.resolve())
        else:
            if args.output is None:
                p.error("measure requires --output")
            save(args.output, check_documents(args.work.resolve()))
