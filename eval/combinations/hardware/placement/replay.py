"""Read-only, standard-library replay of the archived Core ML placement study."""
import argparse
from array import array
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
import struct
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "batched"))
import matrix_data
from matrix_data import rank_study as historical

POLICIES = {"cpuOnly", "cpuAndNeuralEngine"}
SHAPES = ((2048, 128), (4096, 1024))
LOCK_SHA = "5bafed6cd3292d0723ca28f3018e8011b29eafcc396e3706eec28acc521783cb"
COMMIT = "2683791860c1ba9249b5da61ce60e8d2a2064b0f"


def need(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return sha256(data).hexdigest()


def load(path):
    return json.loads(path.read_bytes())


def weights_sha(rows):
    result = sha256()
    for active in rows:
        raw = bytearray(2 * matrix_data.K)
        for feature in active:
            raw[2*feature + 1] = 0x3C  # Little-endian IEEE binary16 one.
        result.update(raw)
    return result.hexdigest()


def manifest(index, rows, q):
    need((index["dimension"], index["row_count"], index["queries"], index["weights_sha256_f16le"]) ==
         (matrix_data.K, len(rows), q, weights_sha(rows)), "candidate weights or index geometry")
    need(0 < index["compile_ns"] + index["move_ns"] <= index["compile_process_wall_ns"]
         and min(index[k] for k in ("compile_ns", "convert_and_serialize_ns", "package_bytes", "compiled_bytes")) > 0,
         "invalid recorded index compilation")


def ready(arm, policy, m, q):
    r = arm["ready"]
    need(r["ready"] is True and (r["policy"], r["row_count"], r["dimension"], r["queries"]) ==
         (policy, m, matrix_data.K, q) and 0 < r["load_ns"] <= arm["startup_wall_ns"], "model load, shape or policy")


def plan(arm, policy, m, q, chip):
    ready(arm, policy, m, q)
    p = arm["plan"]
    need(p["policy"] == policy and p["load_plan_ns"] > 0, "unmatched compute plan")
    for k in ("all_compute_devices", "available_compute_devices"):
        devices = {x["kind"]: x for x in p[k]}
        need(set(devices) == {"cpu", "gpu", "neuralEngine"} and devices["gpu"]["name"] == chip
             and devices["neuralEngine"]["core_count"] == 32, "host device evidence")
    conv, = [op for op in p["functions"]["main"] if op["operator"].endswith(".conv")]
    target = "cpu" if policy == "cpuOnly" or q == 1 else "neuralEngine"
    supported = {d["kind"] for d in conv["supported"]}
    need(conv["outputs"] == ["scores"] and conv["preferred"]["kind"] == target and
         supported == ({"cpu"} if policy == "cpuOnly" else {"cpu", "neuralEngine"}) and
         conv["preferred"] in conv["supported"], "archived convolution preferred/supported devices")


def timing(t):
    start, predict, returned, complete = (t[k] for k in ("start_ns", "predict_start_ns", "prediction_returned_ns", "complete_ns"))
    need(0 < start <= predict < returned <= complete and 0 <= t["pack_ns"] <= predict-start and
         t["predict_ns"] == returned-predict and t["readback_ns"] == complete-returned and
         0 <= t["postprocess_ns"] <= t["native_total_ns"]-(complete-start), "inconsistent prediction timestamps")
    if "wall_ns" in t:
        need(t["wall_ns"] >= t["native_total_ns"], "request wall shorter than native execution")
    return complete-predict


def distribution(values):
    v = sorted(values)
    return {"count": len(v), "minimum_ms": v[0]/1e6, "median_ms": median(v)/1e6,
            "p95_ms": v[(95*len(v)+99)//100-1]/1e6, "maximum_ms": v[-1]/1e6}


def sequential(trials):
    return all(a["complete_ns"] <= b["start_ns"] for a, b in zip(trials, trials[1:]))


def verify_documents(archive):
    need(archive["fixture_commit"] == COMMIT and archive["fixture_lock_sha256"] == LOCK_SHA ==
         sha((historical.OLD / "lock.json").read_bytes()), "published fixture lock")
    cases = historical.load_cases()[:4]  # Also verifies each generated case and each locked source file.
    ids = [c["id"] for c, _, _, _ in cases]
    need([d["index"]["id"] for d in archive["documents"]] == ids and archive["feature_dimension"] == matrix_data.K,
         "historical document coverage")
    queries = {c["id"]: set(historical.vector(c["job"]["question"], {})) for c, _, _, _ in cases}
    checked = 0
    for d, (case, token_count, _, _) in zip(archive["documents"], cases):
        job, index, cid = case["job"], d["index"], case["id"]
        need(index["source_sha256_utf8"] == sha(job["source"].encode()) and
             index["question_sha256_utf8"] == sha(job["question"].encode()) and
             index["source_apple_tokens"] == token_count, "source, question or recorded token count")
        lines, features = historical.prepare(job, warm=False)
        manifest(index, features["rows"], 1)
        expected = {}
        for qid, active in queries.items():
            scores = [len(active.intersection(row)) for row in features["rows"]]
            rank = sorted(range(len(lines)), key=lambda i: (-scores[i], i))
            expected[qid] = {"scores_sha256_json": sha(historical.encode(scores)), "rank_sha256_json": sha(historical.encode(rank)),
                             "active_features": len(active), "top12_zero_based": rank[:12]}
        need(expected == d["reference"] and set(d["policies"]) == POLICIES, "document reference or policy set")
        order = [cid] + ([cid] + [i for i in ids if i != cid]) * 5
        for policy, arm in d["policies"].items():
            plan(arm, policy, len(lines), 1, archive["environment"]["chip"])
            trials = arm["trials"]
            need([t["id"] for t in trials] == order and arm["first"] == trials[0] and sequential(trials), "document first/warm coverage")
            for trial in trials:
                ref = expected[trial["id"]]
                need(trial["scores_and_rank_exact"] is True and all(trial[k] == ref[k] for k in
                     ("scores_sha256_json", "rank_sha256_json")), "archived per-query scores or stable rank")
            durations = [timing(t) for t in trials]
            need(arm["warm_predict_and_readback"] == distribution(durations[1:]) and
                 arm["warm_request_wall"] == distribution([t["wall_ns"] for t in trials[1:]]), "document warm time summary")
            reopened = arm["same_path_reopen"]
            ready(reopened, policy, len(lines), 1)
            need(reopened["first"]["id"] == cid and timing(reopened["first"]) > 0 and
                 sequential([trials[-1], reopened["first"]]), "same-path reload timing")
            checked += len(trials)
    return checked


def little_endian_bytes(values):
    if sys.byteorder != "little":
        values = values[:]
        values.byteswap()
    return values.tobytes()


def integer_hashes(docs, queries):
    # Encode queries as independent 16-bit integer lanes. Each document adds
    # its occupied features. All scores <= 1024, so lanes cannot carry.
    m, q = len(docs), len(queries)
    columns = [0] * matrix_data.K
    for j, active in enumerate(queries):
        lane = 1 << (16*j)
        for feature in active:
            columns[feature] |= lane
    scores = array("H")
    order = array("H", [0]) * (m*q)
    need(scores.itemsize == 2, "uint16 is required")
    for doc in docs:
        scores.frombytes(sum(columns[i] for i in doc).to_bytes(2*q, "little"))
    if sys.byteorder != "little":
        scores.byteswap()
    for j in range(q):
        column = scores[j::q].tolist()
        # A stable sort preserves the source order of tied scores.
        order[j::q] = array("H", sorted(range(m), key=column.__getitem__, reverse=True))
    half, single = sha256(), sha256()
    f16, f32 = struct.Struct(f"<{q}e"), struct.Struct(f"<{q}f")
    for i in range(0, len(scores), q):
        row = scores[i:i+q]
        half.update(f16.pack(*row))
        single.update(f32.pack(*row))
    return {"u16": sha(little_endian_bytes(scores)), "f16": half.hexdigest(), "f32": single.hexdigest(),
            "rank": sha(little_endian_bytes(order))}


def verify_batches(records, survey):
    docs, questions, rows, vectors = matrix_data.features()
    meta = survey["inputs"]
    raw = {name: sha(matrix_data.serialize(data)) for name, data in (("documents.f32", rows), ("queries.f32", vectors))}
    need(meta["raw_sha256"] == raw and meta["docs_sha256"] == sha("\n".join(docs).encode()) and
         meta["queries_sha256"] == sha("\n".join(questions).encode()), "canonical survey inputs")
    checked = 0
    for m, q in SHAPES:
        archive = load(records / f"batch-{m}x{q}.json")
        surveyed, = [r["ready"] for r in survey["measurements"] if (r["ready"]["m"], r["ready"]["q"]) == (m, q)]
        need((archive["M"], archive["K"], archive["Q"]) == (m, matrix_data.K, q) and
             surveyed["k"] == matrix_data.K and archive["index"]["canonical_raw_sha256"] == raw and
             (archive["benchmark_rows"], archive["bank_rows"]) == (matrix_data.M, matrix_data.BANK) == (meta["rows"], meta["bank_rows"]) and
             set(archive["arms"]) == POLICIES and set(archive["banks"]) == {"0", "1"}, "batch geometry or inputs")
        manifest(archive["index"], rows[:m], q)
        expected = {}
        for bank in range(2):
            offset = bank * matrix_data.BANK
            h = integer_hashes(rows[:m], vectors[offset:offset+q])
            ref, other = archive["banks"][str(bank)], surveyed["proof"][bank]
            need((other["bank"], other["cells"], other["u16_sha256"], other["sha256"]) == (bank, m*q, h["u16"], h["f32"]),
                 f"Accelerate/Metal survey evidence for {m}x{q} bank {bank}")
            need((ref["offset_rows"], ref["elements"], ref["scores_sha256_u16le"], ref["scores_sha256_f16le"],
                  ref["stable_rank_sha256_u16le"]) == (offset, m*q, h["u16"], h["f16"], h["rank"]), "batch reference archive")
            expected[bank] = h
            checked += m*q
        for policy, arm in archive["arms"].items():
            plan(arm, policy, m, q, archive["environment"]["chip"])
            first, warm = arm["first_per_bank"], arm["warm"]
            need(len(first) == len(arm["prepack"]) == 2 and len(warm) == 12 and sequential(first + warm), "batch trial coverage")
            for bank, init in enumerate(arm["prepack"]):
                need(init["prepared"] == str(bank) and init["pack_ns"] > 0, "input bank prepack")
            for i, t in enumerate(first + warm):
                bank, h = i % 2, expected[i % 2]
                need((t["id"], t["bank"], t["elements"], t["scores_sha256_f16le"]) == (str(i), bank, m*q, h["f16"]) and
                     t["full_materialized_ns"] == timing(t), "alternating batch prediction or timing")
                if i < 2:
                    need(t["scores_sha256_u16le"] == h["u16"] and t["native_rank_sha256_u16le"] == h["rank"], "first bank full scores/rank")
            for key, sample in (("warm_materialized", "full_materialized_ns"), ("warm_predict", "predict_ns"), ("warm_wire", "wall_ns")):
                need(arm[key] == distribution([t[sample] for t in warm]), "batch warm time summary")
    return checked


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, default=HERE)
    parser.add_argument("--survey", type=Path, default=HERE.parent / "batched" / "results" / "survey.json")
    args = parser.parse_args()
    n = verify_documents(load(args.records / "documents.json"))
    cells = verify_batches(args.records, load(args.survey))
    print(json.dumps({"verified_document_trials": n, "verified_batch_cells_and_ranks": cells, "batch_shapes": len(SHAPES),
                      "banks_per_shape": 2, "policies": sorted(POLICIES)}, sort_keys=True))
