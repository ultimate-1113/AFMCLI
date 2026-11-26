"""Canonical binary features for explicit Metal, Accelerate and compiled Core ML."""
import argparse
from array import array
import hashlib
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import rank_study

M, K, BANK = 4096, 1024, 1024
SHAPES = ((256, 1), (1024, 32), (2048, 128), (4096, 256), (4096, 512), (4096, 1024))
SEED = "afmcli.binary.harbour.20261126.v1"
NAMES = ("Aster", "Birch", "Cedar", "Delta", "Elm", "Fern", "Gale", "Harbour",
         "Iris", "Juniper", "Kite", "Larch", "Maple", "North", "Olive", "Pine")
PLACES = ("外港", "西岸", "北浦", "川口", "入江", "南浜", "新港", "内海")
ITEMS = ("berth", "inspection", "transfer", "manifest", "repair", "delivery", "gate", "launch")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def notice(i):
    b = hashlib.sha256((SEED + "/" + str(i)).encode()).digest()
    name = NAMES[b[0] % 16] + "-" + NAMES[b[1] % 16] + str(b[2])
    place, event = PLACES[b[3] % 8], ITEMS[b[4] % 8]
    ref = "HR-" + str(10000 + (i * 2143 + 151) % 4096)
    day, dock = 1 + b[5] % 28, 1 + b[6] % 77
    if i % 2:
        return (f"{ref}　{place}。{name}の{event}を11月{day}日に岸壁{dock}で記録。区画は{b[7]}番。",
                f"{place}の{name}について、{ref}の{event}は11月のどの岸壁で記録されましたか。")
    return (f"File {ref} at {place} recorded {name} for a {event} on November {day}, dock {dock}, zone {b[7]}.",
            f"Which dock recorded the {event} for {name} at {place} under file {ref}?")


def features():
    material = [notice(i) for i in range(M)]
    docs = [row[0] for row in material]
    queries = [material[(i * 2017 + 37) % M][1] for i in range(2 * BANK)]
    _, indexed = rank_study.prepare({"source": "\n".join(docs), "question": queries[0]}, warm=False)
    memo = {}
    vectors = [rank_study.vector(q, memo) for q in queries]
    if [i for i, x in enumerate(indexed["queries"][0]) if x] != vectors[0] or indexed["dimension"] != K:
        raise ValueError("inconsistent public feature transform")
    return docs, queries, indexed["rows"], vectors


def serialize(rows):
    result = bytearray()
    for cols in rows:
        values = array("f", [0]) * K
        for col in cols:
            values[col] = 1
        if values.itemsize != 4:
            raise ValueError("expected IEEE binary32")
        if sys.byteorder != "little":
            values.byteswap()
        result.extend(values.tobytes())
    return result


def write_inputs(directory):
    start = time.monotonic_ns()
    docs, qs, rows, vectors = features()
    extracted = time.monotonic_ns()
    a, t = serialize(rows), serialize(vectors)
    packed = time.monotonic_ns()
    directory.mkdir(parents=True, exist_ok=True)
    files = {"documents.f32": a, "queries.f32": t}
    for name, data in files.items():
        p = directory / name
        if p.exists() and digest(p.read_bytes()) != digest(data):
            raise ValueError("existing fixture bytes differ: " + name)
        if not p.exists():
            tmp = directory / (name + ".partial")
            tmp.write_bytes(data)
            tmp.replace(p)
    meta = {"seed": SEED, "rows": M, "dimension": K, "query_banks": 2, "bank_rows": BANK,
            "format": "LE binary32 row-major; A[M,K], T[2*1024,K], C=A*T^T",
            "shapes": SHAPES, "raw_sha256": {key: digest(value) for key, value in files.items()},
            "docs_sha256": digest("\n".join(docs).encode()), "queries_sha256": digest("\n".join(qs).encode()),
            "features_ns": extracted - start, "pack_ns": packed - extracted,
            "feature_source_sha256": digest((HERE.parent / "rank_study.py").read_bytes())}
    (directory / "inputs.json").write_text(json.dumps(meta, sort_keys=True, indent=2) + "\n")
    return meta


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(write_inputs(args.directory), ensure_ascii=False))
