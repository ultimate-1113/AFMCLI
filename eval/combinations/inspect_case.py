"""Export the ordinary input for a published case, or inspect one verified observation."""
import argparse
import json
from pathlib import Path

import study


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_id")
    parser.add_argument("--observed", metavar="METHOD", help="show a saved trial, including all model and tool steps")
    parser.add_argument("--results", type=Path, default=study.HERE / "results")
    args = parser.parse_args()

    _, groups = study.frozen()
    cases = {case["id"]: (track, case) for track, items in groups.items() for case in items}
    if args.case_id not in cases:
        parser.error("unknown case; available IDs: " + ", ".join(sorted(cases)))
    track, case = cases[args.case_id]
    if args.observed:
        if args.observed not in study.ARMS[track]:
            parser.error("choose a method for this task: " + ", ".join(study.ARMS[track]))
        _, by_track = study.load_results(args.results, [track])
        output = next(row for row in by_track[track]
                      if row["id"] == args.case_id and row["arm"] == args.observed)
    elif track == "qa":
        output = {key: case["job"][key] for key in ("task", "source", "question")}
    else:
        output = {"question": case["question"]}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
