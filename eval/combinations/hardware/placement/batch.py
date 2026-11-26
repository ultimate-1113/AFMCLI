"""Score the same binary word-feature matrices used by the Metal and CPU arms."""
import argparse
import json
from pathlib import Path
from time import perf_counter_ns as clock

from client import FixedIndex
from study import POLICIES, build_index, describe, digest, measurement, native, save, summarize


def bitsets(array):
    import numpy as np
    packed = np.packbits(array.astype(bool), axis=1)
    return [int.from_bytes(row.tobytes(), "little") for row in packed]


def inputs(directory, rows, queries):
    import numpy as np
    meta = json.loads((directory / "inputs.json").read_bytes())
    if [rows, queries] not in meta["shapes"]:
        raise ValueError("Shape outside the published shape sweep")
    for name, expected in meta["raw_sha256"].items():
        if digest((directory / name).read_bytes()) != expected:
            raise ValueError(f"Canonical file hash differs for {name}")
    d = meta["dimension"]
    a = np.memmap(directory / "documents.f32", mode="r", dtype="<f4").reshape(-1, d)[:rows]
    t = np.memmap(directory / "queries.f32", mode="r", dtype="<f4").reshape(-1, d)
    return meta, a, t


def run(work, common, rows, q, repetitions):
    import numpy as np
    meta, a, t = inputs(common, rows, q)
    directory = work / "batch-indexes" / f"m{rows}-q{q}"
    start = clock()
    weight = np.array(a, dtype=np.float16)
    build = build_index(directory, weight, q, work / "placement-index",
                        {"canonical_raw_sha256": meta["raw_sha256"], "weight_cast_ns": clock()-start})
    result = {"environment": describe(), "index": build, "M": rows, "K": meta["dimension"], "Q": q,
              "benchmark_rows": meta["rows"], "bank_rows": meta["bank_rows"], "banks": {}, "arms": {}}
    a_bits = bitsets(a)
    refs = {}
    for bank in (0, 1):
        offset = bank * meta["bank_rows"]
        t_bits = bitsets(t[offset:offset+q])
        started = clock()
        scores = np.array([[(doc & query).bit_count() for query in t_bits] for doc in a_bits], dtype="<u2")
        ranks = np.empty((rows, q), dtype="<u2")
        for col in range(q):
            column = scores[:, col].tolist()
            ranks[:, col] = sorted(range(rows), key=lambda row: (-column[row], row))
        refs[bank] = scores, ranks
        result["banks"][bank] = {"offset_rows": offset, "integer_ref_and_rank_ns": clock()-started,
                                "scores_sha256_u16le": digest(scores.tobytes()),
                                "scores_sha256_f16le": digest(scores.astype("<f2").tobytes()),
                                "stable_rank_sha256_u16le": digest(ranks.tobytes()), "elements": scores.size}
    if result["banks"][0]["scores_sha256_u16le"] == result["banks"][1]["scores_sha256_u16le"]:
        raise AssertionError("The two query banks must change the output")
    for policy in POLICIES:
        with FixedIndex(work / "placement-index", directory, policy) as index:
            arm = {"ready": index.ready, "startup_wall_ns": index.startup_wall_ns, "prepack": [], "warm": [], "first_per_bank": []}
            for bank in (0, 1):
                arm["prepack"].append(index.call({"id": f"pack{bank}", "key": str(bank), "query_offset": bank*meta["bank_rows"],
                                                  "load_query_file": str(common / "queries.f32")}))
            for step in range(2 + repetitions):
                bank = step % 2
                request = {"id": str(step), "prepared": str(bank), "compact": True,
                           "fp16_digest_only": step >= 2}
                raw = directory / f"{policy}-bank{bank}.u16"
                if step < 2:
                    request["save_scores"] = str(raw)
                start = clock()
                observation = index.call(request)
                observation["wall_ns"] = clock()-start
                observation["bank"] = bank
                observation["full_materialized_ns"] = observation["complete_ns"] - observation["predict_start_ns"]
                if observation["scores_sha256_f16le"] != result["banks"][bank]["scores_sha256_f16le"]:
                    raise AssertionError("Full output hash differs from the independent integer reference")
                if step < 2:
                    if observation["scores_sha256_u16le"] != result["banks"][bank]["scores_sha256_u16le"]:
                        raise AssertionError("Integer output hash differs")
                    native_scores = np.fromfile(raw, dtype="<u2").reshape(rows, q)
                    native_ranks = np.argsort(-native_scores.astype(np.int32), axis=0, kind="stable").astype("<u2")
                    expected_scores, expected_ranks = refs[bank]
                    if not np.array_equal(native_scores, expected_scores) or not np.array_equal(native_ranks, expected_ranks):
                        raise AssertionError("Element or tie-stable rank mismatch")
                    observation["native_rank_sha256_u16le"] = digest(native_ranks.tobytes())
                    arm["first_per_bank"].append(observation)
                else:
                    arm["warm"].append(observation)
            arm["warm_materialized"] = summarize([v["full_materialized_ns"] for v in arm["warm"]])
            arm["warm_wire"] = summarize([v["wall_ns"] for v in arm["warm"]])
            arm["warm_predict"] = summarize([v["predict_ns"] for v in arm["warm"]])
        arm["plan"] = native(work / "placement-index", "plan", directory, policy)
        result["arms"][policy] = arm
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--work", required=True, type=Path)
    p.add_argument("--common", required=True, type=Path)
    p.add_argument("--rows", required=True, type=int)
    p.add_argument("--queries", required=True, type=int)
    p.add_argument("--repetitions", type=int, default=12)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    if args.repetitions < 2 or args.repetitions % 2:
        p.error("Use an even number of repetitions of at least two")
    with measurement(args.work.resolve()):
        save(args.output, run(args.work.resolve(), args.common.resolve(), args.rows, args.queries, args.repetitions))
