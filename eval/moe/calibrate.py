"""Calibrate synthetic document lengths with Apple's local tokenizer, without generation."""
import json
import subprocess
from pathlib import Path

import context


def token_count(text):
    return int(subprocess.check_output(["/usr/bin/fm", "count-tokens", "--quiet"],
                                       input=text, text=True, timeout=45).strip())


def main():
    lengths, counts = {}, {}
    for split in ("dev", "test"):
        for initial in context.cases(split):
            case_id = initial["id"]
            _, _, tier, slot = case_id.split("-")
            target = int(tier[:-1]) * 1024
            n = initial["filler_count"]
            for _ in range(6):
                case = context.make_case(split, tier, int(slot), n)
                measured = token_count(case["job"]["source"])
                if abs(measured - target) <= target * 0.025:
                    break
                n = max(10, round(n * target / measured))
            else:
                raise ValueError((case_id, n, measured, target))
            lengths[case_id], counts[case_id] = n, measured
            print(case_id, measured, flush=True)
    for name, values in (("context_lengths.json", lengths), ("context_tokens.json", counts)):
        (Path(__file__).resolve().parent / name).write_text(
            json.dumps(values, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

