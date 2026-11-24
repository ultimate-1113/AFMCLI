"""Generate independent snapshots and expected outcomes for the row-edit comparison."""
import copy
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMAS = [
    ("stock", [], "sku", ["N-1", "N-2", "N-3", "N-4"], "version", "quantity", "bin"),
    ("appointments", ["groups", "active"], "番号", [106, 209, 515, 825], "版", "席数", "地域"),
    ("scores", ["event/day"], "entry~id", ["A/0", "B~1", "C/2", "D~3"], "rev/key", "得点/枠", "stage"),
]
SCENARIOS = [
    "unchanged", "reordered", "unrelated-write", "inserted", "quantity-aba", "same-row-other-field",
    "removed", "duplicate", "confirmed-noop", "old-value-mismatch", "replayed", "second-field-conflict",
]

def pointer(key):
    return "/" + key.replace("~", "~0").replace("/", "~1")

def envelope(rows, keys):
    result = rows
    for key in reversed(keys):
        result = {key: result, "context": "synthetic"}
    return result

def cases():
    for domain, keys, key, ids, version, quantity, note in SCHEMAS:
        before = [{key: identity, version: 7, quantity: 4, note: chr(65 + i)} for i, identity in enumerate(ids)]
        for scenario in SCENARIOS:
            rows = copy.deepcopy(before[:3])
            accepted = scenario in {"unchanged", "reordered", "unrelated-write", "inserted", "confirmed-noop"}
            target_index = 1
            if scenario == "reordered":
                rows[0], rows[1] = rows[1], rows[0]
                target_index = 0
            elif scenario == "inserted":
                rows.insert(0, copy.deepcopy(before[3]))
                target_index = 2
            elif scenario == "unrelated-write":
                rows[0].update({quantity: 19, version: 8})
            elif scenario == "quantity-aba":
                rows[1][version] = 9
            elif scenario == "same-row-other-field":
                rows[1].update({version: 8, note: "changed"})
            elif scenario == "removed":
                rows.pop(1)
            elif scenario == "duplicate":
                rows.append(copy.deepcopy(rows[1]))
            elif scenario == "old-value-mismatch":
                rows[1][quantity] = 6
            elif scenario == "replayed":
                rows[1].update({quantity: 6, version: 8})
            elif scenario == "second-field-conflict":
                rows[1][note] = "changed"
            noop = scenario == "confirmed-noop"
            ops = [dict(path=pointer(quantity), expect=4, op="set" if noop else "add", value=4 if noop else 2)]
            if scenario == "second-field-conflict":
                ops.append(dict(path=pointer(note), expect="B", op="set", value="completed"))
            expect = copy.deepcopy(rows)
            if accepted and not noop:
                expect[target_index].update({quantity: 6, version: 8})
            yield dict(id=f"{domain}-{scenario}", scenario=scenario, original_index=1,
                       job=dict(document=envelope(rows, keys), row=dict(collection="".join(map(pointer, keys)),
                                id_field=key, id=ids[1], revision_field=version, revision=7), changes=ops),
                       expected=dict(status="ok" if accepted else "held", changed=expect != rows, document=envelope(expect, keys)))

if __name__ == "__main__":
    dest = HERE / "cases.jsonl"
    dest.write_text("".join(json.dumps(c, ensure_ascii=False, separators=(",", ":")) + "\n" for c in cases()))
