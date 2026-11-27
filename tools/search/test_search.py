#!/usr/bin/env python3
"""Check the public CLI. --hardware also exercises native GPU and Core ML."""
import argparse
import array
import base64
import contextlib
import hashlib
import json
import platform
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def query(binary, operation, directory, payload, mode=None, error=False):
    command = [binary, operation, directory] + (["--mode", mode] if mode else [])
    result = subprocess.run(command, input=json.dumps(payload).encode(), stdout=subprocess.PIPE)
    value = json.loads(result.stdout)
    assert (result.returncode != 0) == error and value["status"] == ("error" if error else "ok"), value.get("error")
    return value


class Server:
    def __init__(self, binary, directory, mode):
        self.process = subprocess.Popen([binary, "serve", directory, "--mode", mode],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def call(self, request):
        data = request if isinstance(request, bytes) else json.dumps(request).encode()
        self.process.stdin.write(data + b"\n")
        self.process.stdin.flush()
        return json.loads(self.process.stdout.readline())

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.process.stdin.close()
        self.process.stdout.close()
        self.process.wait(timeout=15)


def feature_bits(text):
    """Independent integer reference for the English/Japanese test corpus."""
    text = unicodedata.normalize("NFKC", text).casefold()
    keys, token, previous, kind = [], [], None, 0
    for char in text + " ":
        n = ord(char)
        group = 2 if 0x3041 <= n <= 0x30FF or 0x3400 <= n <= 0x9FFF or n == 0x3005 else int(char.isalnum())
        if group != kind and token:
            word = "".join(token)
            if kind == 1:
                keys.append("word:" + word)
                if previous:
                    keys.append("words:" + previous + " " + word)
                previous = word
            else:
                previous = None
            sizes = (3,) if kind == 1 else (1, 2, 3)
            for size in sizes:
                for i in range(len(word) - size + 1):
                    keys.append(("lat3:" if kind == 1 else "cjk:") + word[i:i + size])
            token = []
        kind = group
        if group:
            token.append(char)
    value = 0
    for key in keys:
        column = int.from_bytes(hashlib.sha256(key.encode()).digest()[:4], "big") % 1024
        value |= 1 << column
    return value


def popcount(n):
    return bin(n).count("1")


def small(binary, directory):
    docs = [{"id": i, "text": x} for i, x in [
        ("a", "Ａlpha 出荷予定"), ("b", "Alpha 出荷予定"), ("c", "栗製品の伝票"), ("d", "")]]
    query(binary, "index", directory, docs)
    payload = {"queries": ["alpha", "出荷", "", "栗製品"], "all_scores": True, "top_k": 4}
    expected = []
    for i, q in enumerate(payload["queries"]):
        full = [popcount(feature_bits(q) & feature_bits(doc["text"])) for doc in docs]
        hits = [{"id": docs[j]["id"], "score": full[j]}
                for j in sorted(range(len(docs)), key=lambda j: -full[j]) if full[j]]
        expected.append({"query_index": i, "scores": full, "hits": hits})
    response = query(binary, "query", directory, payload)
    assert response["routes"]["cpu"] == len(expected) and response["results"] == expected
    query(binary, "index", directory, [docs[0], docs[0]], error=True)
    assert query(binary, "query", directory, payload)["results"] == expected
    query(binary, "query", directory, {"queries": ["a"], "top_k": -1}, error=True)
    with Server(binary, directory, "throughput") as server:
        assert server.call(b"{")["status"] == "error"
        assert server.call(payload)["results"] == expected
        assert server.call({"queries": []})["results"] == []
        assert "scores" not in server.call({"queries": ["栗"]})["results"][0]
        updated = query(binary, "index", directory, docs[::-1])
        assert updated["index_sha256"] != response["index_sha256"]
        assert server.call(payload)["index_sha256"] == response["index_sha256"]
    assert query(binary, "query", directory, payload)["results"][0]["hits"][0]["id"] == "b"
    print("Small index: CPU scores, ties, Japanese/English, errors, server and index replacement passed.", flush=True)


def source_documents(commit):
    # Take a real tracked source snapshot. No benchmark query or synthetic document banks.
    paths = subprocess.check_output(["git", "-C", ROOT, "ls-tree", "-r", "--name-only", commit, "src", "tests"])
    names = [p for p in paths.decode().splitlines() if p.endswith(".rs")]
    docs = []
    for name in names:
        content = subprocess.check_output(["git", "-C", ROOT, "show", commit + ":" + name]).decode()
        for line, text in enumerate(content.splitlines(), 1):
            if re.search(r"[a-zA-Z]{4}", text):
                docs.append({"id": "{}:{}".format(name, line), "text": text.strip()})
            if len(docs) == 4096:
                return docs
    raise ValueError("hardware check requires 4096 nonempty lines of tracked project source")


def source_questions(docs):
    questions = ["{} {}".format(docs[(17 * i + 13) % len(docs)]["text"][:72], i) for i in range(3073)]
    assert len(set(questions)) == len(questions)
    return questions[:1024], questions[1024:]


def resolve_commit(commit):
    return subprocess.check_output(["git", "-C", ROOT, "rev-parse", "--verify", commit + "^{commit}"]).decode().strip()


def summarize(response, expected):
    assert response["status"] == "ok" and len(response["results"]) == expected
    digest = hashlib.sha256()
    for i, row in enumerate(response["results"]):
        assert i == row["query_index"] and len(row["scores"]) == 4096
        scores = array.array("H", row["scores"])
        if sys.byteorder != "little":
            scores.byteswap()
        digest.update(scores.tobytes())
        digest.update(json.dumps(row["hits"], sort_keys=True, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def no_gpu(binary, directory, commit):
    docs = source_documents(commit)
    query(binary, "index", directory, docs)
    warm = {"queries": [d["text"] for d in docs[:1024]], "top_k": 1}
    new = {"queries": [d["text"] for d in docs[1024:2048]], "top_k": 2, "all_scores": True}
    cold = query(binary, "query", directory, new)
    assert cold["routes"]["cpu"] == 1024 and not cold["warmup"] and not cold["fallbacks"]
    expected = summarize(cold, 1024)
    with Server(binary, directory, "throughput") as server:
        response = server.call(warm)
        assert "metal_unavailable" in response["fallbacks"], response["fallbacks"]
        response = server.call(new)
        assert response["routes"]["cpu"] == 1024 and not response["warmup"]
        assert summarize(response, 1024) == expected
    print("Unavailable Metal: 4194304 scores and all hits matched the cold CPU path.", flush=True)


def hardware(binary, directory, python, commit):
    commit = resolve_commit(commit)
    docs = source_documents(commit)
    index = query(binary, "index", directory, docs)
    disk = json.loads((directory / "afm-search-index.json").read_bytes())
    bits = base64.b64decode(disk["bits"], validate=True)
    # All query strings are distinct, and the two sides of a pair receive different feature vectors.
    first, new = source_questions(docs)

    def request(queries, full=True):
        return {"queries": queries, "top_k": 3, "all_scores": full}

    expected = []
    for offset in range(0, len(new), 1024):
        part = new[offset:offset + 1024]
        response = query(binary, "query", directory, request(part), "cpu")
        assert response["routes"]["cpu"] == len(part) and not response["warmup"]
        for qi in {0, len(part) // 2, len(part) - 1}:
            vec = feature_bits(part[qi])
            raw = [popcount(vec & int.from_bytes(bits[r * 128:(r + 1) * 128], "little")) for r in range(4096)]
            assert raw == response["results"][qi]["scores"]
        expected.append(summarize(response, len(part)))
    del response

    def check_results(answer):
        assert answer["status"] == "ok" and len(answer["results"]) == len(new)
        for i, row in enumerate(answer["results"]):
            assert row["query_index"] == i
        for i, offset in enumerate(range(0, len(new), 1024)):
            rows = answer["results"][offset:offset + 1024]
            for j, row in enumerate(rows):
                row["query_index"] = j
            assert summarize({"status": "ok", "results": rows}, len(rows)) == expected[i]

    traces = {}
    for mode in ("throughput", "interactive"):
        with Server(binary, directory, mode) as server:
            warmed = server.call(request(first, False))
            assert warmed["warmup"] == ["metal"] and warmed["routes"]["cpu"] == 1024, warmed.get("fallbacks")
            answer = server.call(request(new))
            single = answer["calibration"]["single"]["choice"]
            assert answer["routes"][single] == (len(new) if single == "cpu" else len(new) - 1)
            assert answer["routes"]["coreml_ane_preferred"] == 0
            assert ("coreml_index_missing" in answer["fallbacks"]) == (mode == "throughput")
            check_results(answer)
            traces[mode + "_without_model"] = answer["routes"]
            del answer
    corrupt = directory / ("search-" + index["index_sha256"] + ".mlmodelc")
    corrupt.mkdir()
    with Server(binary, directory, "throughput") as server:
        warm = server.call(request(first, False))
        assert warm["warmup"] == ["metal"] and warm["fallbacks"] == ["coreml_bundle_unverified"]
        response = server.call(request(new[:1024]))
        assert response["routes"]["coreml_ane_preferred"] == 0
        assert summarize(response, 1024) == expected[0]
        traces["unreadable_coreml_index"] = {"routes": response["routes"], "fallbacks": response["fallbacks"]}
    build = [python, HERE / "prepare_coreml.py", directory]
    rejected = subprocess.run(build, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert rejected.returncode != 0 and b"failed integrity verification" in rejected.stderr
    corrupt.rename(directory / "unusable-test-model.mlmodelc")
    subprocess.run(build, check=True, stdout=sys.stderr)
    order = [("cpu", "interactive", "throughput"), ("interactive", "throughput", "cpu"),
             ("throughput", "cpu", "interactive"), ("throughput", "interactive", "cpu"),
             ("cpu", "throughput", "interactive"), ("interactive", "cpu", "throughput")]
    timing, calibration = [], {}
    with contextlib.ExitStack() as stack:
        servers = {m: stack.enter_context(Server(binary, directory, m)) for m in order[0]}
        for mode, server in servers.items():
            warm = server.call(request(first, False))
            assert warm["routes"]["cpu"] == 1024
            if mode == "throughput":
                assert warm["warmup"] == ["metal", "coreml_ane_preferred"], warm["fallbacks"]
            answer = server.call(request(new))
            assert not answer["fallbacks"]
            assert answer["routes"]["cpu"] >= 1
            if mode == "throughput":
                choice = answer["calibration"]["pair"]["choice"]
                if choice == "split":
                    assert answer["routes"] == {"cpu": 1, "metal": 1024, "coreml_ane_preferred": 1024}
                    assert answer["parallel_pairs"] == 1
                else:
                    assert answer["routes"][choice] >= 2048 and answer["parallel_pairs"] == 0
            check_results(answer)
            traces[mode + "_prepared"] = answer["routes"]
            calibration[mode] = answer["calibration"]
            del answer
        # Common IPC, feature conversion, full scoring, ranking, JSON encoding and parsing.
        # Full-score equivalence was checked separately above. The timed user asks for 3 hits.
        timed = request(new, False)
        expected_hits = None
        for sequence in order:
            round_ms = {}
            for mode in sequence:
                begin = time.monotonic_ns()
                answer = servers[mode].call(timed)
                round_ms[mode] = (time.monotonic_ns() - begin) / 1e6
                assert answer["routes"] == traces[mode + "_prepared"]
                if expected_hits is None:
                    expected_hits = answer["results"]
                assert answer["results"] == expected_hits
            timing.append({"order": list(sequence), "ms": round_ms})
    model = directory / ("search-" + index["index_sha256"] + ".mlmodelc")
    receipt = json.loads((model / "afm-search-build.json").read_text())
    damaged = next(model / name for name in sorted(receipt["files"]) if (model / name).stat().st_size)
    with damaged.open("r+b") as output:
        first_byte = output.read(1)[0]
        output.seek(0)
        output.write(bytes([first_byte ^ 1]))
    with Server(binary, directory, "throughput") as server:
        warm = server.call(request(first, False))
        assert warm["warmup"] == ["metal"] and warm["fallbacks"] == ["coreml_bundle_unverified"]
    rejected = subprocess.run(build, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert rejected.returncode != 0 and b"failed integrity verification" in rejected.stderr
    model.rename(directory / "saved-damaged-model.mlmodelc")
    subprocess.run(build, check=True, stdout=sys.stderr)
    with Server(binary, directory, "throughput") as server:
        repaired = server.call(request(first, False))
        assert repaired["warmup"] == ["metal", "coreml_ane_preferred"] and not repaired["fallbacks"]
    traces["repaired_coreml_index"] = repaired["warmup"]
    alternate = directory.parent / "alternate"
    alternate.mkdir()
    linked_index = alternate / "documents"
    linked_index.symlink_to(directory.resolve(), target_is_directory=True)
    with Server(binary, linked_index, "throughput") as server:
        via_link = server.call(request(first, False))
        assert via_link["warmup"] == repaired["warmup"] and not via_link["fallbacks"]
    traces["index_via_parent_alias"] = via_link["warmup"]
    return {"source_commit": commit, "source_documents": len(docs), "unique_queries": len(first) + len(new),
            "timed_queries": len(new), "timed_top_k": 3, "chip": subprocess.check_output(
                ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"]).decode().strip(),
            "macos": platform.mac_ver()[0], "index_sha256": index["index_sha256"],
            "checked_scores_per_mode": len(new) * len(docs),
            "ordered_score_and_hit_hashes": expected, "routes": traces, "calibration": calibration,
            "warm_request_rounds": timing}


def cold_queries(binary, directory, python, commit):
    """Measure a newly launched one-shot query process for every observation."""
    commit = resolve_commit(commit)
    docs = source_documents(commit)
    index = query(binary, "index", directory, docs)
    subprocess.run([python, HERE / "prepare_coreml.py", directory], check=True, stdout=sys.stderr)
    _, new = source_questions(docs)
    payload = {"queries": new, "all_scores": False, "top_k": 3}
    order = [("cpu", "interactive", "throughput"), ("interactive", "throughput", "cpu"),
             ("throughput", "cpu", "interactive")]
    expected, rounds = None, []
    for sequence in order:
        values = {}
        for mode in sequence:
            begin = time.monotonic_ns()
            response = query(binary, "query", directory, payload, mode)
            elapsed = (time.monotonic_ns() - begin) / 1e6
            assert response["routes"] == {"cpu": len(new), "metal": 0, "coreml_ane_preferred": 0}
            assert not response["warmup"] and not response["calibration"]
            assert response["index_sha256"] == index["index_sha256"] and not response["fallbacks"]
            hits = hashlib.sha256(json.dumps(response["results"], sort_keys=True,
                                  separators=(",", ":")).encode()).hexdigest()
            if expected is None:
                expected = hits
            assert hits == expected
            values[mode] = {"ms": elapsed, "warmup": response["warmup"], "routes": response["routes"]}
        rounds.append({"order": list(sequence), "observations": values})
    return {"source_commit": commit, "source_documents": len(docs), "timed_queries": len(new),
            "timed_top_k": 3, "index_prepared": True, "new_process_per_request": True,
            "index_sha256": index["index_sha256"], "ordered_hits_sha256": expected, "rounds": rounds,
            "chip": subprocess.check_output(["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"]).decode().strip(),
            "macos": platform.mac_ver()[0], "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "swift_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(HERE.glob("*.swift"))}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--hardware", action="store_true")
    parser.add_argument("--cold", action="store_true", help="measure nine newly launched queries on a prepared index")
    parser.add_argument("--expect-no-gpu", action="store_true", help="verify fallback in a Metal-inaccessible environment")
    parser.add_argument("--coreml-python", default=sys.executable)
    parser.add_argument("--source-commit", default="HEAD")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        small(args.binary.resolve(), directory / "small")
        if args.expect_no_gpu:
            no_gpu(args.binary.resolve(), directory / "no-gpu", args.source_commit)
        if args.hardware or args.cold:
            if args.hardware and args.cold:
                parser.error("choose one measurement per report")
            measurement = cold_queries if args.cold else hardware
            result = measurement(args.binary.resolve(), directory / "real-source", args.coreml_python, args.source_commit)
            output = json.dumps(result, sort_keys=True, indent=2) + "\n"
            if args.report:
                args.report.write_text(output)
            print(output, end="")


if __name__ == "__main__":
    main()
