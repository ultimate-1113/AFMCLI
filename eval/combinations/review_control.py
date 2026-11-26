"""Dispatch controls for the short arbitration request, excluded from quality scores.

The two primary findings are fixed, intentionally disagreeing development inputs.
Only the third reader calls the real backend. Both live and replay exercise the
same qa.execute branch, passage extraction, prompt, schema and byte checks.

    python3 eval/combinations/review_control.py run --out review-control.jsonl
    python3 eval/combinations/review_control.py replay --out review-control.jsonl
"""

import argparse
import fcntl
import json
import os
from pathlib import Path

if __package__:
    from . import backend, qa
else:
    import backend
    import qa


CASES = (
    {"id": "dispatch-en-two-values", "type": "different_values",
     "question": "Which berth was confirmed for Seaborne?",
     "source": "Harbour notice\nSeaborne's berth was confirmed as Dune Pier.\n"
               "A harbour amendment superseded the first Seaborne berth notice.\n"
               "Seaborne's revised berth was confirmed as Lake Pier.",
     "old_quote": "Seaborne's berth was confirmed as Dune Pier.", "old_value": "Dune Pier",
     "quote": "Seaborne's revised berth was confirmed as Lake Pier.", "value": "Lake Pier",
     "state": "set", "subject": "", "field": ""},
    {"id": "dispatch-ja-withdrawal", "type": "positive_vs_supported_null",
     "question": "夕凪号の停泊岸壁はどこですか。",
     "source": "連絡帳⚓\r\n夕凪号の停泊岸壁は青貝岸壁に決定した。\r\n"
               "夕凪号の停泊岸壁の指定は撤回された。\r\n代替の停泊場所は検討中である。",
     "old_quote": "夕凪号の停泊岸壁は青貝岸壁に決定した。", "old_value": "青貝岸壁",
     "quote": "夕凪号の停泊岸壁の指定は撤回された。", "value": "",
     "state": "withdrawn", "subject": "夕凪号", "field": "停泊岸壁"},
)


def unique_range(raw, literal):
    needle = literal.encode()
    backend.need(bool(needle) and raw.count(needle) == 1, "ambiguous seeded source witness")
    begin = raw.index(needle)
    return [begin, begin + len(needle)]


class SeededPair:
    def __init__(self, case, transport):
        self.case = case
        self.transport = transport
        self.n_index = self.n_packet = self.n_third = 0
        self.last_read_status = None

    def indexed(self, job):
        backend.need(self.n_index == 0 and job["source"] == self.case["source"], "unexpected indexed call")
        self.n_index += 1
        value = self.case["old_value"]
        return qa.answer(value, *unique_range(job["source"].encode(), value))

    def read(self, prompt, schema, *, max_input):
        backend.need(self.n_index == 1, "packet preceded fixed indexed finding")
        if self.n_packet == 0:
            backend.need(schema == qa.PRIMARY_SCHEMA and max_input == qa.PRIMARY_INPUT, "wrong primary stage")
            material = json.loads(prompt.split("\n", 1)[1])
            backend.need(material["question"] == self.case["question"], "wrong primary question")
            quote = self.case["quote"]
            labels = [i for i, shown in material["passages"] if quote in shown]
            backend.need(len(labels) == 1, "fixed verifier witness missing from actual packet")
            self.n_packet += 1
            self.last_read_status = "ok"
            return dict(state=self.case["state"], passage=labels[0], quote=quote, value=self.case["value"],
                        subject=self.case["subject"], field=self.case["field"])
        backend.need(self.n_third == 0 and schema == qa.THIRD_SCHEMA and max_input == 850, "wrong third stage")
        self.n_third += 1
        result = self.transport.read(prompt, schema, max_input=max_input)
        self.last_read_status = self.transport.last_read_status
        return result


def measurement(case, transport):
    job = {key: case[key] for key in ("question", "source")}
    job["task"] = "answer"
    raw = job["source"].encode()
    first_span = unique_range(raw, case["old_value"])
    first_quote = unique_range(raw, case["old_quote"])
    second_quote = unique_range(raw, case["quote"])
    backend.need(first_quote[0] <= first_span[0] < first_span[1] <= first_quote[1], "first witness mismatch")
    second_span = unique_range(raw, case["value"]) if case["value"] else None
    if second_span:
        backend.need(second_quote[0] <= second_span[0] < second_span[1] <= second_quote[1], "second witness mismatch")
    pair = SeededPair(case, transport)
    result, notes = qa.execute("arbitrate", job, pair)
    backend.need((pair.n_index, pair.n_packet, pair.n_third) == (1, 1, 1)
                 and len(transport.events) == 1 and "third" in notes, "seeded disagreement did not route to arbitration")
    event = transport.events[0]
    observed = event["raw"]
    backend.need(event["kind"] == "read" and observed["limit"] == 850, "unexpected third event")
    shown = notes["third"]["ranges"]
    for a, b in (first_span, second_span or second_quote):
        backend.need(any(start <= a < b <= end for start, end in shown), "third packet lost a seeded witness")
    return {"id": case["id"], "conflict": case["type"], "job": job,
            "seeded": [{"state": "set", "value": case["old_value"], "value_range": first_span,
                        "quote_range": first_quote},
                       {"state": case["state"], "value": case["value"], "value_range": second_span,
                        "quote_range": second_quote}],
            "third": {"attempts": 1, "sends": int(observed["sent"]), "input_tokens": observed["input_tokens"],
                      "limit": observed["limit"], "status": observed["status"], "decision": notes["decision"]},
            "result": result, "notes": notes, "events": transport.events}


def header():
    here = Path(__file__).resolve().parent
    sources = {name: backend.sha((here / name).read_bytes()) for name in ("qa.py", "backend.py", "review_control.py")}
    return {"format": 1, "use": "development_dispatch_control", "quality_denominator": 0,
            "definitions_sha256": backend.sha(backend.encode(CASES)), "source_sha256": sources}


def append(stream, row):
    stream.write(backend.encode(row).decode() + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def archive(path, live):
    if live:
        path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+" if live else "r", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX if live else fcntl.LOCK_SH)
        stream.seek(0)
        lines = stream.readlines()
        if lines:
            backend.need(all(line.endswith("\n") for line in lines), "incomplete control archive line")
            backend.need(json.loads(lines[0]) == header(), "control sources or definitions changed")
        else:
            backend.need(live, "empty control archive")
            append(stream, header())
        rows = [json.loads(line) for line in lines[1:]]
        backend.need(len(rows) <= len(CASES), "extra control results")
        for case, stored in zip(CASES, rows):
            reproduced = measurement(case, backend.IO(archive=stored["events"]))
            backend.need(reproduced == stored, "control replay differs")
        backend.need(live or len(rows) == len(CASES), "control run is incomplete")
        for case in CASES[len(rows):]:
            row = measurement(case, backend.IO())
            backend.need(measurement(case, backend.IO(archive=row["events"])) == row, "immediate control replay differs")
            append(stream, row)
            rows.append(row)
    return [{"id": row["id"], **row["third"]} for row in rows]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("run", "replay"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(archive(args.out, args.mode == "run"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
