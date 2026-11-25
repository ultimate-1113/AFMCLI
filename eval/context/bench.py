"""Replay paired synthetic and published QA inputs on the same Mac, one generation at a time."""
import argparse
import fcntl
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from statistics import median
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "procedures"))
from study import datasets, grade


def digest(data):
    return hashlib.sha256(data).hexdigest()


def filler(layout, i):
    if layout == "repeat-ja":
        return "巡回係：各階の案内板、非常口、照明と保管棚の封印を点検し、共用の点検票に記入しました。"
    if layout == "alternating-en":
        return ("Ops: The duty officer checked the corridor lights, storeroom seals and the dispatch board before handing over the log.",
                "Clerk: The archive team checked the paper inventory and initialled the daily inspection form at the end of the round.")[i % 2]
    if layout == "unique-ja":
        return f"資料係の点検記録第{i+1}号では、棚番号{3*i+7}の封印と台帳番号を照合し、次回の点検欄を空欄のまま保存しました。"
    if layout == "unique-en":
        return f"Inspection entry {i+1} records that the archive staff checked the seal on shelf {3*i+7} against its paper inventory, then initialled the maintenance record."
    if layout == "varied-ja":
        places = ["北棟","植栽室","和室","小ホール","旧館","厨房","工作室","応接室"]
        staff = ["管理人","園芸係","記録係","館長","案内係","夜間当番","清掃係","備品係"]
        actions = ["の備品一覧を整え、控えを資料室に届けました。","の窓を点検し、修繕に必要な部品を帳簿に記載しました。","に残っていた紙箱を片付け、通路の幅を測りました。"]
        return f"{staff[i%8]}：{places[(i//8)%8]}{actions[(i+i//8)%3]}"
    if layout == "varied-en":
        places = ["reading room","loft","blue gallery","old kitchen","annex","greenhouse","craft room","east wing"]
        speakers = ["Porter","Curator","Clerk","Cleaner","Caretaker","Archivist","Steward","Florist"]
        tasks = ["checked the lamps and returned the wooden steps to storage","moved the empty crates and recorded the repairs on paper","compared the printed inventory with the tins on the upper shelf"]
        return f"{speakers[i%8]}: In the {places[(i//8)%8]} we {tasks[(i+i//8)%3]}."
    raise ValueError(layout)


def synthetic():
    cases = []
    for file in ("panel.json", "holdout.json"):
        for row in json.loads((HERE / file).read_text()):
            source = "\n".join(row["events"][str(i)] if str(i) in row["events"] else filler(row["layout"], i)
                                for i in range(row["rows"]))
            cohort = row["layout"].split("-")[0]
            if file == "holdout.json":
                cohort = "holdout-varied" if cohort == "varied" else "holdout-repeat"
            cases.append(dict(id=row["id"], cohort=cohort, budget=row["budget"], gold=row["gold"],
                              job=dict(task="answer", source=source, question=row["question"])))
    return cases


def modes(case):
    return ("baseline","compact","evidence") if case["cohort"] in ("unique","holdout-varied") else ("baseline","compact")


def check_spans(job, answer):
    source = job["source"].encode()
    if not answer or not answer["spans"]:
        return False
    value = answer["value"].encode()
    return all(0 <= s["start_byte"] < s["end_byte"] <= len(source)
               and source[s["start_byte"]:s["end_byte"]] == value for s in answer["spans"])


def synth_score(case, status, answer):
    if status == "abstained":
        return not case["gold"] and answer is None
    return bool(status == "ok" and answer and answer["value"] in case["gold"] and check_spans(case["job"], answer))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("run", "verify", "show"))
    ap.add_argument("--case", help="synthetic case ID to print as a JSON job")
    ap.add_argument("--baseline", type=Path)
    ap.add_argument("--binary", type=Path, default=ROOT / "target/release/afmcli")
    ap.add_argument("--data", type=Path)
    ap.add_argument("--results", type=Path, default=HERE / "results.jsonl")
    args = ap.parse_args()
    if args.action == "show":
        for case in synthetic():
            if case["id"] == args.case:
                print(json.dumps(case["job"],ensure_ascii=False))
                return
        ap.error("supply a synthetic ID from panel.json or holdout.json with --case")
    if args.action == "run":
        if not args.baseline:
            ap.error("run requires --baseline pointing to the v0.2.0 binary")
        args.results.parent.mkdir(parents=True, exist_ok=True)
        with args.results.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            evaluate(args)
    else:
        evaluate(args)


def evaluate(args):
    cases = synthetic()
    if args.data:
        cases.extend(dict(c, budget=6200, cohort="external") for c in datasets(args.data) if c["job"]["task"] == "answer")
    rows = [json.loads(line) for line in args.results.read_text().splitlines()] if args.results.exists() else []
    panel_hash = digest((HERE / "panel.json").read_bytes() + (HERE / "holdout.json").read_bytes())
    lookup = {(r["id"], r["mode"]): r for r in rows}
    assert len(rows) == len(lookup)
    if args.action == "run":
        paths = {"baseline": args.baseline.resolve(), "compact": args.binary.resolve(), "evidence": args.binary.resolve()}
        hashes = {mode: digest(path.read_bytes()) for mode, path in paths.items()}
        for path in set(paths.values()):
            state = json.loads(subprocess.check_output([path, "doctor"]))
            assert state["available"] and state["target"] == "AFM 3 Core Advanced" and state["route"] == "system"
        with args.results.open("a") as output:
            for case in cases:
                source_hash = digest(case["job"]["source"].encode())
                for mode in sorted(modes(case), key=lambda m: digest((case["id"]+m).encode())):
                    key = case["id"], mode
                    if key in lookup:
                        old = lookup[key]
                        assert (old["binary"],old["source_sha256"],old["panel_sha256"]) == (hashes[mode],source_hash,panel_hash)
                        continue
                    cmd = [paths[mode], "--trace", "--timeout", "45", "--input-budget", str(case["budget"])]
                    if mode == "compact":
                        cmd.append("--compact")
                    elif mode == "evidence":
                        cmd.append("--compact=evidence")
                    start = time.monotonic_ns()
                    run = subprocess.run([*cmd,"run"],input=json.dumps(case["job"],ensure_ascii=False),
                                         capture_output=True,text=True)
                    elapsed = round((time.monotonic_ns()-start)/1e6)
                    response = json.loads(run.stdout)
                    status = response["status"]
                    metrics = response["metrics"]
                    if status == "error" and not any("timed out" in v for v in response["notes"]):
                        raise RuntimeError(f"model environment error for {key}: {response['notes']}")
                    traces = response.get("attempts", [])
                    counted = sum(t["input_tokens"] for t in traces) if len(traces)==metrics["calls"] else None
                    if counted is not None and "sent_input_tokens" in metrics:
                        assert counted == metrics["sent_input_tokens"]
                    answer = (response["result"] or {}).get("answer")
                    score = (grade(case,"base",response)["pass"] if case["cohort"] == "external"
                             else synth_score(case,status,answer))
                    row = dict(id=case["id"],mode=mode,cohort=case["cohort"],binary=hashes[mode],
                               source_sha256=source_hash,panel_sha256=panel_hash,budget=case["budget"],status=status,
                               correct=score,elapsed_ms=elapsed,calls=metrics["calls"],peak=metrics["peak_sent_tokens"],
                               sent=metrics.get("sent_input_tokens",counted),context=metrics.get("context"))
                    if case["cohort"] != "external":
                        row.update(answer=answer,notes=response["notes"])
                    output.write(json.dumps(row,ensure_ascii=False,separators=(",",":"))+"\n")
                    output.flush()
                    os.fsync(output.fileno())
                    rows.append(row)
                    lookup[key] = row
                    print(case["id"],mode,status,score,row["sent"],flush=True)
    expected = set()
    public_fields = {"id","mode","cohort","binary","source_sha256","panel_sha256","budget","status",
                     "correct","elapsed_ms","calls","peak","sent","context"}
    for r in rows:
        fields = public_fields if r["cohort"] == "external" else public_fields | {"answer","notes"}
        assert set(r) == fields and isinstance(r["correct"], bool)
        assert len(r["binary"]) == 64 and r["panel_sha256"] == panel_hash
        assert 0 <= r["peak"] <= r["budget"] and r["calls"] >= 0
        assert r["sent"] is None or r["sent"] >= r["peak"]
        if r["context"]:
            c = r["context"]
            assert 0 <= c["retained_segments"] <= c["source_segments"] and c["selection_windows"] >= 0
    for group in ({"baseline"},{"compact","evidence"}):
        assert len({r["binary"] for r in rows if r["mode"] in group}) == 1
    for case in cases:
        for mode in modes(case):
            key = case["id"],mode
            r = lookup[key]
            expected.add(key)
            assert r["source_sha256"] == digest(case["job"]["source"].encode()) and r["panel_sha256"] == panel_hash
            assert r["budget"] == case["budget"] and r["peak"] <= r["budget"]
            if case["cohort"] != "external":
                assert r["correct"] == synth_score(case,r["status"],r["answer"])
    if args.data:
        assert set(lookup) == expected
    else:
        historical = [json.loads(l) for l in (HERE.parent/"procedures/results.jsonl").read_text().splitlines()]
        ids = {r["id"] for r in historical if r["cohort"] in ("jsquad","squad")}
        external_keys = {(i,m) for i in ids for m in ("baseline","compact")}
        if args.results.resolve() == (HERE / "results.jsonl").resolve():
            assert set(lookup)-expected == external_keys
        else:
            assert set(lookup)-expected in (set(),external_keys)
        for key in set(lookup)-expected:
            assert lookup[key]["cohort"]=="external" and "answer" not in lookup[key]
    for cohort in sorted({r["cohort"] for r in rows}):
        for mode in sorted({r["mode"] for r in rows if r["cohort"] == cohort}):
            group = [r for r in rows if (r["cohort"],r["mode"])==(cohort,mode)]
            token_counts = [r["sent"] for r in group]
            print(cohort,mode,len(group),sum(r["correct"] for r in group),
                  sum(token_counts) if None not in token_counts else None,
                  median(r["elapsed_ms"] for r in group),dict(Counter(r["status"] for r in group)))


if __name__ == "__main__":
    main()
