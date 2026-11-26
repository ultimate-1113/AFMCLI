"""Use UTF-8 JSON/JSONL transport with the unchanged, fingerprinted hardware runners."""
import argparse
from pathlib import Path

import pipeline_study
import rank_study


class PublicRecords:
    @staticmethod
    def open(path, mode, encoding="utf-8"):
        path = Path(path)
        plain = path.with_suffix("")
        if path.suffix != ".gz" or plain.suffix not in (".json", ".jsonl") or mode not in ("rt", "wt"):
            raise ValueError("unexpected record transport")
        return plain.open(mode, encoding=encoding)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", choices=("single", "batch"))
    commands = parser.add_subparsers(dest="action", required=True)
    capture = commands.add_parser("capture")
    capture.add_argument("--binary", type=Path, required=True)
    capture.add_argument("--doctor", type=Path, required=True)
    capture.add_argument("--engine", choices=("tensor", "fixed"), default="tensor")
    capture.add_argument("--index-root", type=Path)
    for command in (capture, commands.add_parser("verify")):
        command.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    runner = rank_study if args.study == "single" else pipeline_study
    # Each frozen runner refers to its own module-local opener. All scoring, model
    # calls, source hash checks and timing remain the captured implementation.
    runner.gzip = PublicRecords
    if args.action == "capture":
        runner.capture(args)
    else:
        runner.verify(args)


if __name__ == "__main__":
    main()
