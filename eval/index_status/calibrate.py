"""Fit the pair background lengths using Apple's system tokenizer. Makes no generation calls."""
import json
from pathlib import Path
import subprocess

import fixtures

HERE = Path(__file__).resolve().parent


def count(source):
    return int(subprocess.check_output(["/usr/bin/fm", "count-tokens", "--quiet"],
                                       input=source, text=True, timeout=45))


def main():
    lengths, counts = {}, {}
    for split in ("dev", "test"):
        for base in fixtures.cases(split):
            if base["pair"] in lengths:
                continue
            tier, index = base["tier"], base["pair_index"]
            target = int(tier[:-1]) * 1024
            n = base["filler_count"]
            for _ in range(7):
                pair = [fixtures.make_case(split, tier, index, positive, filler_count=n)
                        for positive in (False, True)]
                tokens = [count(c["job"]["source"]) for c in pair]
                if all(abs(x - target) <= target * .03 for x in tokens):
                    break
                n = max(12, round(n * target / (sum(tokens) / 2)))
            else:
                raise ValueError((base["pair"], target, tokens))
            lengths[base["pair"]] = n
            counts.update({c["id"]: t for c, t in zip(pair, tokens)})
            print(base["pair"], *tokens, flush=True)
    for name, values in (("lengths.json", lengths), ("source_tokens.json", counts)):
        path = HERE / name
        data = json.dumps(values, indent=2, sort_keys=True) + "\n"
        if path.exists() and path.read_text(encoding="utf-8") != data:
            raise ValueError(f"calibration differs from {name}; use a fresh checkout for recalibration")
        path.write_text(data, encoding="utf-8")


if __name__ == "__main__":
    main()
