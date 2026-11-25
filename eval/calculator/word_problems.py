"""Measure the frozen natural-language calculation cases in their original order.

Use record-previous-timeout --out FILE to append the logged failure to the exact
original seven rows without running AFM. Its response and elapsed time are unknown.
Then use run --binary PATH --out FILE to measure only the remaining cases and
verify --binary PATH --out FILE to replay validation. Run also recognizes that prefix.
"""
import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import signal
from pathlib import Path
import subprocess
import time

from probe import numeric

CASES = (
    ("labels", "ラベルを18袋買います。1袋275円、送料は注文全体で320円です。合計は何円ですか。", "5270"),
    ("cards", "There are 12 boxes of 24 cards. Remove 7 damaged cards and add 3 loose replacements. How many cards remain?", "284"),
    ("liquid", "最初に15 L入っていました。その6分の1が蒸発し、最後に2.5 Lを足しました。現在は何Lですか。", "15"),
    ("inspection", "A workshop inspects 48 pieces per day for 5 days. Exactly three eighths require polishing. How many require it?", "90"),
    ("trapezoid", "上底7 cm、下底13 cm、高さ6 cmの台形です。面積は何平方cmですか。", "60"),
    ("sequential-rate", "定価2800円の品物を25%引きし、割引後の価格に10%の税を加えます。支払額はいくらですか。", "2310"),
    ("height", "A 10 m straight ramp rises at 30 degrees to the horizontal. What is the vertical height in metres?", "5"),
    ("combinations", "Eight distinct lantern designs are available. How many different sets of three can be chosen, without repetition and ignoring order?", "56"),
    ("missing-side", "長方形の板の幅は5 cmです。長さは記録されていません。この板の面積は何平方cmですか。", None),
    ("missing-rate", "A kit costs 90 before a discount. The discount rate will be decided next month. What is the final price?", None),
)


FLAGS = ("--trace", "calc", "--from-text", "--angle", "deg")
HOST_TIMEOUT = 90  # Same end-to-end deadline and CLI command as the initial seven records.
PREVIOUS_PREFIX = "6d6d255b2032af7d932196797ef47b0ea112a1d9996a671c64b0efa176e71694"
PREVIOUS_BINARY = "8f36cc0b5eb7c9e8db27c59150daca8d2fbe4af2ffe1650f96ec5361ba24a3c9"


def check(row):
    if row.get("execution", {}).get("kind", "completed") != "completed":
        return False
    record = row["record"]
    if row["expected"] is None:
        return record["status"] == "held" and record["result"] is None
    result = record["result"] or {}
    answer = numeric(result.get("value"))
    return (record["status"] == "review" and bool(result.get("expression")) and answer is not None
            and abs(answer - numeric(row["expected"])) <= numeric("0.000000000001"))


def previous_timeout():
    i, source, expected = CASES[7]
    return {"id":i, "source":source, "expected":expected, "binary":PREVIOUS_BINARY, "record":None, "correct":False,
            "execution":{"kind":"host_timeout", "provenance":"previous_run_log",
                         "exception":"subprocess.TimeoutExpired", "args":list(FLAGS), "timeout_seconds":HOST_TIMEOUT,
                         "elapsed_ms":None, "returncode":None, "stdout":None, "stderr":None}}


def cli_record(value):
    return (isinstance(value, dict) and value.get("task") == "calculate"
            and value.get("status") in ("ok", "review", "held", "refused", "error")
            and "result" in value and (value["result"] is None or isinstance(value["result"], dict))
            and isinstance(value.get("metrics"), dict))


def exit_matches(record, code):
    return code == {"held":3, "refused":3, "error":2}.get(record["status"], 0)


def measurement_matches(e, record):
    kind = e.get("kind")
    elapsed = e.get("elapsed_ms")
    if (e.get("provenance") != "measured" or type(elapsed) not in (int, float)
            or not math.isfinite(elapsed) or elapsed < 0 or "returncode" not in e):
        return False
    if kind == "launch_error":
        return record is None and e["returncode"] is None and isinstance(e.get("exception"), str)
    if type(e["returncode"]) is not int:
        return False
    if kind == "completed":
        return cli_record(record) and exit_matches(record, e["returncode"])
    if not isinstance(e.get("stdout"), str) or not isinstance(e.get("stderr"), str):
        return False
    if kind == "host_timeout":
        return record is None and e.get("exception") == "subprocess.TimeoutExpired"
    raw_stdout = bytes.fromhex(e["stdout_hex"]) if "stdout_hex" in e else e["stdout"].encode()
    if raw_stdout.decode("utf-8", "replace") != e["stdout"]:
        return False
    try:
        candidate = json.loads(raw_stdout)
    except (ValueError, UnicodeError):
        candidate = None
    if kind == "invalid_output":
        return record is None and not cli_record(candidate)
    return kind == "exit_mismatch" and cli_record(record) and candidate == record and not exit_matches(record, e["returncode"])


def validate(raw, binary=None, complete=False):
    lines = raw.splitlines(keepends=True)
    if raw and not raw.endswith(b"\n"):
        raise ValueError("incomplete last JSONL row; preserve it for inspection before resuming")
    rows = [json.loads(line) for line in lines]
    if len(rows) > len(CASES) or (complete and len(rows) != len(CASES)):
        raise ValueError(f"unexpected number of rows: {len(rows)}/{len(CASES)}")
    previous_prefix = len(rows) >= 7 and hashlib.sha256(b"".join(lines[:7])).hexdigest() == PREVIOUS_PREFIX
    if previous_prefix and len(rows) > 7 and rows[7] != previous_timeout():
        raise ValueError("the observed eighth-case timeout must follow its original seven records")
    hashes = set()
    for n, row in enumerate(rows):
        i, source, expected = CASES[n]
        if (row["id"], row["source"], row["expected"]) != (i, source, expected):
            raise ValueError("changed input, expected answer or sequence at "+i)
        sha = row["binary"]
        if not isinstance(sha, str) or not re.fullmatch("[0-9a-f]{64}", sha) or (binary and sha != binary):
            raise ValueError("changed or invalid binary for "+i)
        hashes.add(sha)
        e = row.get("execution")
        if e is not None:
            if e.get("args") != list(FLAGS) or e.get("timeout_seconds") != HOST_TIMEOUT:
                raise ValueError("changed measurement protocol for "+i)
            if e.get("provenance") == "previous_run_log":
                if n != 7 or row != previous_timeout() or not previous_prefix:
                    raise ValueError("historical timeout does not match its original seven rows")
            elif not measurement_matches(e, row.get("record")):
                raise ValueError("execution evidence does not match its outcome for "+i)
        if (e is None or e["kind"] == "completed") and not cli_record(row.get("record")):
            raise ValueError("invalid AFMCLI record for "+i)
        if type(row["correct"]) is not bool or row["correct"] != check(row):
            raise ValueError("stored grade does not reproduce for "+i)
    if len(hashes) > 1:
        raise ValueError("mixed binary fingerprints")
    return rows


def append(stream, row):
    stream.seek(0, os.SEEK_END)
    stream.write((json.dumps(row, ensure_ascii=False, separators=(",",":"))+"\n").encode())
    stream.flush()
    os.fsync(stream.fileno())


def recover_previous(stream, raw, rows):
    if len(rows) >= 8 and rows[7] == previous_timeout():
        return
    if len(rows) != 7 or hashlib.sha256(raw).hexdigest() != PREVIOUS_PREFIX or rows[0]["binary"] != PREVIOUS_BINARY:
        raise ValueError("record-previous-timeout requires the exact original seven-row prefix")
    row = previous_timeout()
    append(stream, row)
    rows.append(row)
    print("combinations: restored the observed 90-second host timeout; exact elapsed time unavailable", flush=True)


def stop_group(child):
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return child.communicate()


def measure(binary, case, fingerprint):
    i, source, expected = case
    start = time.monotonic_ns()
    record = None
    e = {"kind":"completed", "provenance":"measured", "args":list(FLAGS), "timeout_seconds":HOST_TIMEOUT,
         "returncode":None}
    try:
        child = subprocess.Popen([str(binary), *FLAGS], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        e.update(kind="launch_error", exception=type(exc).__name__, errno=exc.errno)
    else:
        try:
            stdout, stderr = child.communicate(source.encode(), timeout=HOST_TIMEOUT)
        except subprocess.TimeoutExpired:
            # Stop descendant fm processes as well, before advancing to the next local trial.
            stdout, stderr = stop_group(child)
            e.update(kind="host_timeout", exception="subprocess.TimeoutExpired")
        except BaseException:
            stop_group(child)
            raise
        e["returncode"] = child.returncode
        if e["kind"] == "completed":
            try:
                candidate = json.loads(stdout)
            except (ValueError, UnicodeError):
                candidate = None
            if not cli_record(candidate):
                e["kind"] = "invalid_output"
            else:
                record = candidate
                if not exit_matches(record, child.returncode):
                    e["kind"] = "exit_mismatch"
        if e["kind"] != "completed":
            e.update(stdout=stdout.decode("utf-8", "replace"), stderr=stderr.decode("utf-8", "replace"))
            if e["stdout"].encode() != stdout:
                e["stdout_hex"] = stdout.hex()
            if e["stderr"].encode() != stderr:
                e["stderr_hex"] = stderr.hex()
    e["elapsed_ms"] = round((time.monotonic_ns()-start)/1e6, 3)
    row = {"id":i,"source":source,"expected":expected,"binary":fingerprint,"record":record,"execution":e}
    row["correct"] = check(row)
    return row


def report(rows):
    for missing in (False, True):
        group = [r for r in rows if (r["expected"] is None) == missing]
        print("unknown" if missing else "numeric", sum(r["correct"] for r in group), "/", len(group))
    for r in rows:
        kind = r.get("execution", {}).get("kind", "completed")
        if kind != "completed":
            print(r["id"], kind, r["execution"]["provenance"])
        elif r["record"]["status"] == "error":
            print(r["id"], "cli_error")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=("run", "verify", "record-previous-timeout"))
    ap.add_argument("--binary", type=Path)
    ap.add_argument("--out", type=Path, default=Path(__file__).with_name("words.jsonl"))
    args = ap.parse_args()
    if args.action == "run" and args.binary is None:
        ap.error("run requires --binary")
    binary = args.binary.resolve() if args.binary else None
    fingerprint = hashlib.sha256(binary.read_bytes()).hexdigest() if binary else None
    mode = {"run":"a+b", "verify":"rb", "record-previous-timeout":"r+b"}[args.action]
    with args.out.open(mode) as stream:
        fcntl.flock(stream, fcntl.LOCK_SH if args.action == "verify" else fcntl.LOCK_EX)
        stream.seek(0)
        raw = stream.read()
        rows = validate(raw, fingerprint, complete=args.action=="verify")
        if args.action == "record-previous-timeout":
            recover_previous(stream, raw, rows)
            return
        if args.action == "run" and len(rows) < len(CASES):
            # Bind the logged eighth attempt before starting any process, even on an ordinary resume.
            if len(rows)==7 and rows[0]["binary"]==PREVIOUS_BINARY and all("execution" not in r for r in rows):
                recover_previous(stream, raw, rows)
            state = json.loads(subprocess.check_output([str(binary),"doctor"], timeout=HOST_TIMEOUT))
            if not state.get("available") or state.get("target") != "AFM 3 Core Advanced" or state.get("route") != "system":
                raise ValueError("requires AFM 3 Core Advanced on the system route")
            for case in CASES[len(rows):]:
                if hashlib.sha256(binary.read_bytes()).hexdigest() != fingerprint:
                    raise ValueError("binary changed during measurement")
                row = measure(binary, case, fingerprint)
                append(stream, row)
                print(case[0], row.get("execution", {}).get("kind"), row["correct"], flush=True)
            stream.seek(0)
            rows = validate(stream.read(), fingerprint, complete=True)
        report(rows)


if __name__ == "__main__":
    main()
