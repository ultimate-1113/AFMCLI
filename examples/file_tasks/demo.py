"""Run local CLI file examples. --files-only supports v0.3.2; the arithmetic example needs calc."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

FIELDS = ("id", "revision", "item", "quantity", "unit_price")
INTEGER_FIELDS = ("revision", "quantity", "unit_price")
EXPECTED_AMOUNT = 5270


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def invoke(binary, args, data=None):
    if isinstance(data, str):
        stdin = data.encode()
    else:
        stdin = None if data is None else json_bytes(data)
    proc = subprocess.run([str(binary), "--timeout", "45", *args], input=stdin, capture_output=True, timeout=180)
    if not proc.stdout:
        raise ValueError(f"afmcli exited with {proc.returncode} before returning a record")
    record = json.loads(proc.stdout)
    expected = ("ok", "review") if args == ["calc", "--from-text"] else ("ok",)
    if proc.returncode or record.get("status") not in expected:
        raise ValueError(f"afmcli {record.get('task', 'task')} returned {record.get('status', 'unknown')}")
    return record


def csv_write(path, rows):
    with path.open("x", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def csv_read(path):
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, strict=True)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError("unexpected CSV header")
        items = []
        for row in reader:
            if set(row) != set(FIELDS) or any(v is None for v in row.values()) or not row["id"]:
                raise ValueError("CSV row shape or ID changed")
            for key in INTEGER_FIELDS:
                if not re.fullmatch(r"(?:0|[1-9][0-9]{0,14})", row[key]):
                    raise ValueError("expected a nonnegative integer in "+key)
                row[key] = int(row[key])
            items.append(row)
    if len({item["id"] for item in items}) != len(items):
        raise ValueError("duplicate row ID")
    return items


def grounded(source, text, span):
    lo, hi = span["start_byte"], span["end_byte"]
    return type(lo) is int and type(hi) is int and 0 <= lo < hi <= len(source) and source[lo:hi] == text.encode()


def demonstrate(binary, folder, files_only=False, call=invoke):
    notice = folder / "notice.txt"
    notice.write_text("資料を受け取る会場は白波館です。\n窓口は午前9時に開きます。\n受付では受領票を提示してください。\n", encoding="utf-8")
    original = notice.read_bytes()
    qa = call(binary, ["--input", str(notice), "answer", "資料を受け取る会場はどこですか？"])
    answer = qa["result"]["answer"]
    if answer["value"] != "白波館" or not answer["spans"] or not all(grounded(original, answer["value"], s) for s in answer["spans"]):
        raise ValueError("the TXT answer or its byte offsets do not match the notice")
    summary = call(binary, ["--input", str(notice), "summarize", "--sentences", "2"])
    segments = summary["result"]["segments"]
    if not 1 <= len(segments) <= 2 or not all(grounded(original, s["text"], s) for s in segments):
        raise ValueError("summary segments do not match the original notice")
    if any(a["end_byte"] > b["start_byte"] for a, b in zip(segments, segments[1:])):
        raise ValueError("summary is out of source order")

    source_json = folder / "before.json"
    source_json.write_bytes(json_bytes({"event": {"room": "白波館", "capacity": 25}}))
    json_before = source_json.read_bytes()
    change = {"document": json.loads(json_before), "changes": [{"path": "/event/capacity", "expect": 25, "op": "set", "value": 30}]}
    update = call(binary, ["edit"], change)
    if update["result"] != {"changed": True, "document": {"event": {"room": "白波館", "capacity": 30}}}:
        raise ValueError("conditional JSON edit did not produce the expected preview")
    (folder / "after.json").write_bytes(json_bytes(update["result"]["document"]))

    rows = [dict(id="0007", revision=3, item='ラベル, "春"', quantity=4, unit_price=275),
            dict(id="0008", revision=9, item="展示札\n青", quantity=4, unit_price=180)]
    source_csv = folder / "before.csv"
    csv_write(source_csv, rows)
    csv_before = source_csv.read_bytes()
    table = {"items": csv_read(source_csv)}
    (folder / "csv-input.json").write_bytes(json_bytes(table))
    csv_job = {"document": table,
               "row": {"collection": "/items", "id_field": "id", "id": "0007", "revision_field": "revision", "revision": 3},
               "changes": [{"path": "/quantity", "expect": 4, "op": "add", "value": 2}]}
    csv_update = call(binary, ["edit"], csv_job)
    wanted = [dict(rows[0], revision=4, quantity=6), rows[1]]
    if not csv_update["result"]["changed"] or csv_update["result"]["document"] != {"items": wanted}:
        raise ValueError("the guarded row update changed an unexpected field or row")
    csv_write(folder / "after.csv", csv_update["result"]["document"]["items"])
    if csv_read(folder / "after.csv") != wanted:
        raise ValueError("quoted CSV fields, IDs, or typed values changed during export")

    if notice.read_bytes() != original or source_json.read_bytes() != json_before or source_csv.read_bytes() != csv_before:
        raise ValueError("an input file changed")
    result = {"text_answer":answer, "extractive_summary":segments, "json_capacity":30, "csv_updated_id":"0007",
              "csv_revision":4, "csv_quantity":6, "model_calls":{name:r["metrics"]["calls"] for name,r in [
                  ("answer",qa), ("summarize",summary), ("json_edit",update), ("csv_edit",csv_update)]}}
    if not files_only:
        order = "ノートを18冊買います。単価は275円で、注文全体の送料は320円です。支払額を求めてください。"
        (folder / "order.txt").write_text(order+"\n", encoding="utf-8")
        calc = call(binary, ["calc", "--from-text"], order)
        expression, amount = calc["result"]["expression"], calc["result"]["value"]
        if not isinstance(expression, str) or not re.fullmatch(r"[0-9.+*/()×÷− \t-]{1,192}", expression):
            raise ValueError("the calculator did not return a numeric expression")
        if "+" not in expression or not any(op in expression for op in ("*", "×")):
            raise ValueError("expected addition and multiplication in the shopping expression")
        if sorted(re.findall(r"[0-9]+", expression)) != ["18", "275", "320"]:
            raise ValueError("calculator expression does not use the three purchase amounts")
        if (type(amount) not in (int,float) or amount != EXPECTED_AMOUNT) and not (
                isinstance(amount,str) and re.fullmatch(r"5270(?:\.0+)?",amount)):
            raise ValueError("calculator result does not match the known amount")
        result["calculator"] = {"expression": expression, "value": amount, "expected": EXPECTED_AMOUNT, "status":calc["status"]}
        result["model_calls"]["calculator"] = calc["metrics"]["calls"]
    (folder / "result.json").write_bytes(json_bytes(result))
    return result


def run(binary, output, files_only=False):
    out = output.absolute()
    if out.is_symlink() or (out.exists() and (not out.is_dir() or any(out.iterdir()))):
        raise ValueError("choose a new or empty output directory")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent, prefix=".afmcli-file-demo-") as tmp:
        folder = Path(tmp) / "results"
        folder.mkdir()
        result = demonstrate(binary.resolve(), folder, files_only)
        os.rename(folder, out)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--binary", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--files-only", action="store_true", help="Run the TXT, JSON and CSV workflows supported by v0.3.2.")
    args = ap.parse_args()
    try:
        print(json.dumps(run(args.binary, args.out, args.files_only), indent=2, ensure_ascii=False))
    except subprocess.TimeoutExpired:
        ap.exit(1, "demo stopped because an afmcli command timed out\n")
    except OSError as error:
        ap.exit(1, f"demo stopped: {error.strerror or type(error).__name__}\n")
    except (ValueError, KeyError, TypeError) as error:
        ap.exit(1, f"demo stopped: {error}\n")
