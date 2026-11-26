"""Capture or replay the public AFM interfaces. Only synthetic requests enter a model."""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "calculator" / "native"))
from run import read_events, verify_native
from verify import number, verify_transcript
from words import INSTRUCTION

FM = "/usr/bin/fm"
SYSTEM = "AFM 3 Core Advanced"
GUIDANCE = "Use the supplied source and return the requested structured response."
CAP = 6200


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def need(condition, message):
    if not condition:
        raise ValueError(message)


def count(text, instruction=None):
    args = [FM, "count-tokens", "--quiet"]
    if instruction is not None:
        args.extend(["--instructions", instruction])
    value = int(subprocess.check_output(args, input=text.encode(), stderr=subprocess.PIPE, timeout=45))
    need(value > 0, "unexpected tokenizer result")
    return value


def preflight(paths):
    meta = verify_native(paths["native"])
    need(meta.get("variant") == SYSTEM, "native route differs")
    for key in ("indexed", "checked"):
        doc = json.loads(subprocess.check_output([str(paths[key]), "doctor"], timeout=45))
        need(doc["available"] and doc["target"] == SYSTEM and doc["route"] == "system", "index route differs")
    return meta


def run_structured(req):
    prompt, schema, limit = req["prompt"], req["schema"], req["limit"]
    serialized = encode(schema).decode()
    try:
        inputs = count(prompt + "\n" + serialized, GUIDANCE)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"status": "count_error", "input_tokens": None, "sent": False}
    if inputs > limit:
        return {"status": "over_budget", "input_tokens": inputs, "sent": False}
    trace = {"input_tokens": inputs, "sent": True}
    with tempfile.TemporaryDirectory(prefix="afm-combo-") as tmp:
        schema_file = Path(tmp) / "schema.json"
        transcript = Path(tmp) / "response.json"
        schema_file.write_text(serialized, encoding="utf-8")
        args = [FM, "respond", "--model", "system", "--greedy", "--no-stream", "--instructions", GUIDANCE,
                "--schema", str(schema_file), "--save-transcript", str(transcript)]
        try:
            proc = subprocess.run(args, input=prompt.encode(), capture_output=True, timeout=45)
        except subprocess.TimeoutExpired:
            return dict(trace, status="timeout")
        if proc.returncode or not transcript.is_file():
            return dict(trace, status="backend_error", exit_code=proc.returncode)
        raw = json.loads(transcript.read_bytes())
    entries = raw["transcript"]["transcript"]["entries"]
    users = [e for e in entries if e["role"] == "user"]
    need(raw.get("modelName") == "system" and len(users) == 1
         and any(v.get("text") == prompt for v in users[0]["contents"]), "unexpected fm route or prompt")
    last = entries[-1]
    parts = last.get("contents", [])
    status = "invalid_output"
    output = None
    if last.get("role") in ("response", "assistant") and last.get("finishReason", "contentCompleted") == "contentCompleted":
        try:
            output = "".join(encode(v["structure"]["content"]).decode() if v["type"] == "structure"
                             else v["text"] for v in parts)
            if isinstance(json.loads(output), dict):
                status = "ok"
        except (KeyError, TypeError, ValueError):
            pass
    return dict(trace, status=status, model=raw["modelName"], confirmed_user_sha256=sha(prompt.encode()),
                finish=last.get("finishReason"), raw_output=output)


def run_native(paths, req):
    args = [str(paths["native"]), req["mode"], str(paths["calculator"]), "deg"]
    try:
        p = subprocess.run(args, input=req["prompt"].encode(), capture_output=True, timeout=90)
    except subprocess.TimeoutExpired as exc:
        events, errors = read_events(exc.stdout)
        return {"status": "timeout", "events": events, "parse_errors": len(errors)}
    events, errors = read_events(p.stdout)
    return {"status": "ok" if not p.returncode and not errors else "backend_error",
            "exit_code": p.returncode, "events": events, "parse_errors": len(errors)}


def native_result(raw, req):
    events = raw["events"]
    model = next((e for e in events if e["event"] == "model"), {})
    requests = [e for e in events if e["event"] == "request"]
    finals = [e for e in events if e["event"] == "final"]
    callbacks = [e for e in events if e["event"] == "callback"]
    if model:
        need(model.get("route") == "system" and model.get("variant") == SYSTEM
             and model.get("available") is True and model.get("core_advanced_3") is True, "wrong Swift model")
    if requests:
        need(requests == [{"event": "request", "prompt": req["prompt"], "sampling": "greedy",
                           "max_response_tokens": 256, "tool_calling_mode": "allowed",
                           "tools": ["scientificCalculator"] if req["mode"] == "registered" else []}],
             "Swift request differs")
    empty = {"kind": "held", "value": None, "callbacks": []}
    if raw["status"] != "ok" or len(finals) != 1 or finals[0].get("error"):
        return empty
    need(bool(model) and len(requests) == 1, "incomplete Swift provenance")
    verify_transcript(finals[0]["transcript"], callbacks)
    values = []
    for call in callbacks:
        need(req["mode"] == "registered" and call["source"] == "framework"
             and call["tool"] == "scientificCalculator" and call["angle"] == "deg", "unexpected callback")
        if call.get("value") is not None:
            need(call.get("started_process") is True and call.get("backend_status") == "ok"
                 and call.get("backend_exit") == 0 and call["returned"] == call["value"]
                 and number(call["value"]) is not None, "invalid calculator result")
            values.append(call["value"])
    text = (finals[0].get("answer") or "").strip()
    return {"kind": "number" if number(text) is not None else "unknown" if text == "不明" else "held",
            "value": text if number(text) is not None else None, "callbacks": values}


class IO:
    """Replay consumes exactly the observed calls, including checks whose budgets prevented a send."""
    def __init__(self, paths=None, archive=None):
        self.paths = paths
        self.archive = archive
        self.events = []
        self.last_read_status = None

    def call(self, kind, req, perform):
        stamp = sha(encode(req))
        if self.archive is None:
            start = time.monotonic_ns()
            data = perform()
            row = {"kind": kind, "request_sha256": stamp, "raw": data,
                   "elapsed_ms": round((time.monotonic_ns() - start) / 1e6, 3)}
        else:
            need(len(self.events) < len(self.archive), "missing recorded step")
            row = self.archive[len(self.events)]
            need(row["kind"] == kind and row["request_sha256"] == stamp, "replayed input differs")
            need(isinstance(row["elapsed_ms"], (int, float)) and math.isfinite(row["elapsed_ms"])
                 and row["elapsed_ms"] >= 0, "invalid step timing")
        self.events.append(row)
        return row["raw"]

    def checked_index(self, job, previous):
        key, option = ("checked", "index-checked") if previous else ("indexed", "indexed")
        def perform():
            args = [str(self.paths[key]), "--compact=" + option, "--trace", "--input-budget", str(CAP),
                    "--timeout", "45", "run"]
            p = subprocess.run(args, input=encode(job), capture_output=True, timeout=360)
            need(bool(p.stdout), "AFMCLI did not finish a measurement")
            return json.loads(p.stdout)
        raw = self.call(key, {"job": job, "option": option}, perform)
        metrics = raw["metrics"]
        need(0 <= metrics["peak_sent_tokens"] <= CAP and metrics["peak_sent_tokens"] <= metrics["sent_input_tokens"]
             and metrics["calls"] >= len(raw.get("attempts", [])), "invalid index accounting")
        return raw

    def indexed(self, job):
        return self.checked_index(job, False)

    def read(self, prompt, schema, *, max_input):
        need(0 < max_input <= CAP, "structured reader budget")
        req = {"prompt": prompt, "schema": schema, "limit": max_input, "instructions": GUIDANCE}
        raw = self.call("read", req, lambda: dict(run_structured(req), limit=max_input))
        self.last_read_status = raw["status"]
        need(raw["limit"] == max_input, "changed stage budget")
        need(raw["status"] in ("count_error", "over_budget", "backend_error", "timeout", "invalid_output", "ok"),
             "unknown fm termination")
        if raw["sent"]:
            need(isinstance(raw["input_tokens"], int) and 0 < raw["input_tokens"] <= max_input, "oversized send")
        else:
            need((raw["status"] == "count_error" and raw["input_tokens"] is None)
                 or (raw["status"] == "over_budget" and raw["input_tokens"] > max_input), "invalid skipped read")
        if raw["status"] != "ok":
            return None
        need(raw["sent"] and raw["model"] == "system" and raw["confirmed_user_sha256"] == sha(prompt.encode()),
             "structured transcript did not match")
        value = json.loads(raw["raw_output"])
        need(isinstance(value, dict), "structured answer is not an object")
        return value

    def native(self, question, mode="registered"):
        req = {"prompt": INSTRUCTION + "\n\n" + question, "mode": mode}
        raw = self.call("native", req, lambda: run_native(self.paths, req))
        return native_result(raw, req)

    def calc(self, expression):
        def perform():
            args = [str(self.paths["calculator"]), "calc", "--angle", "deg"]
            p = subprocess.run(args, input=expression.encode(), capture_output=True, timeout=10)
            return {"exit_code": p.returncode, "reply": json.loads(p.stdout), "expression": expression}
        raw = self.call("calc", {"expression": expression, "angle": "deg"}, perform)
        need(raw["expression"] == expression, "calculator expression changed")
        result = raw["reply"]
        if raw["exit_code"] or result.get("status") != "ok":
            return None
        payload = result["result"]
        need(payload["expression"] == expression.strip() and number(payload["value"]) is not None,
             "calculator yielded an invalid number")
        return payload["value"]
