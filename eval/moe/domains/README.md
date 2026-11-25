# Specialist pilots

The three opt-in study procedures accept a task's public input through `special.run({"group": group, "job": job}, "candidate", invoke)`. The study calls `grade` separately, after generation. Development uses eight examples per specialty. Thirty additional cases per specialty were defined before selection. The panels use newly authored, synthetic inputs. Invoice layouts, controlled bilingual notice templates and linear problem families recur across the splits. Held-out scores therefore describe new inputs within those families.

## Invoice questions

The model proposes an aggregate, column names, typed filters and amendments. The host supports row counts, column sums, sums of products and quantity times catalog price through a single exact key join. It applies amendments to an in-memory copy before filtering. An amendment identifies the note and the ledger row. Replacement numbers must occur in the note. Categorical replacements require a supporting term. The host checks the join cardinality, data types, arithmetic precision and row identifiers. A missing catalog match, duplicate key, unknown amendment or invalid plan holds the result. A model that identifies an ambiguous request can explicitly ask for clarification.

The input below requests the catalog-priced total for posted items. Its two three-unit lines have different statuses, and its amended quantity comes from the note.

```json
{
  "request": "訂正メモを反映して、確定明細の数量に商品台帳の単価を掛け、総額を出してください。",
  "table": {
    "columns": {"id":"id","状態":"text","商品":"text","数量":"integer","単価":"decimal"},
    "rows": [
      {"id":"L21","状態":"確定","商品":"茶","数量":"3","単価":"12.50"},
      {"id":"L33","状態":"保留","商品":"布","数量":"3","単価":"4.00"}
    ],
    "catalog": {
      "columns": {"品名":"text","価格":"decimal"},
      "rows": [{"品名":"茶","価格":"11.90"},{"品名":"布","価格":"3.50"}]
    },
    "notes": [{"id":"N3","text":"L21 の数量は再カウントの結果、4 に訂正します。"}]
  }
}
```

The expected amount is `47.60`, supported by ledger row `L21` and note `N3`. Input cells are strings. Decimal cells allow up to 16 integer digits and eight fractional places. Integer and date comparisons use their declared types. The interpreter accepts up to 500 ledger rows and 500 catalog rows. An empty catalog has empty columns and rows.

Direct AFM receives the final answer contract in an unguided `structure --direct` request. The v0.3.2 control uses ordinary guided `structure` with the same contract. A fourth, host-only control parses common English and Japanese expressions and a limited set of amendment patterns. Both host procedures execute the same typed aggregation logic. The scorer requires the amount and the complete sets of contributing row IDs and operative note IDs. Publishing an incorrect amount or evidence set counts as a serious error.

## Translation

Each source item contains one sentence. The glossary maps source terms to requested target expressions. Proper names and unit symbols are protected across translation. Both directions support Japanese and English.

```json
{
  "source_language": "en",
  "target_language": "ja",
  "sentences": [
    "Pavo limits nozzle pressure to at most 6.5 kPa.",
    "Keep the spare vial for 4 h after the inspection."
  ],
  "glossary": {"nozzle pressure":"ノズル圧","spare vial":"予備瓶"},
  "names": ["Pavo"],
  "units": ["kPa","h"]
}
```

Direct and current use the same `rewrite` request with number preservation and a required change. The candidate takes a single draft with built-in retries disabled. If a sentence fails the language, number, number–unit, name or glossary checks, the next request sees only that original sentence, its draft, its obligations and its violations. The host repairs at most one sentence and rechecks every sentence. It holds drafts with unresolved failures or ambiguous sentence alignment.

The separate scoring rubric checks the direction and strictness of the numerical statement, the requested action, its timing and its reference event. It also counts obligations fixed or damaged between the draft and the final attempt. The rubric uses a finite list of English and Japanese constructions, so an acceptable paraphrase outside that list can lose credit. A wrong number, bound unit, proper name or recognized semantic reversal in a published translation counts as serious. The runtime checks alone can accept a grammatical reversal that preserves the protected literals.

## Constrained optimization

```json
{"source":"整数 A と B は各 0 から 4。合計は 5 未満。まず B を最大化し、同点なら A を最小化する。"}
```

Direct AFM receives a single unguided `ask --direct` request for both the model and solution. This exposes the complete feasible-set contract to the scorer. The current control uses v0.3.2 `solve --from-text`. The candidate obtains a declaration-wise proposal through `--decompose solve --from-text` and checks its reported solution by exhaustive enumeration of the proposed model. It selects examples close to either side of each proposed linear constraint and outside each declared domain edge. It also selects pairs to check ties and objective priorities where the feasible set permits. A fresh structured model call sees the original passage, concrete assignments and assignment pairs. The host compares all returned judgments with its predicted labels and holds a disagreement. Agreement returns the existing `review` status. The bounded check supports up to 4,096 integer assignments and 14 feasibility examples. Agreement on the sampled points leaves other assignments and shared interpretation errors open.

The independent scorer exhaustively compares the approved interpretation with the gold feasible set and objective order. It checks the final assignment, objective values and optimal tie count. An explicit clarification is correct on an underspecified input. An asserted solution on such an input, an incorrect reported optimum, or a model that admits illegal assignments counts as serious.

## Running a public input

Save the relevant input object as `/tmp/special-input.json`. Run the following example from the repository root. Change `table` to `translation` or `constraints` for the other inputs. The callback uses the normal AFMCLI entry point with tracing, a 6,200-token request budget and a 45-second timeout for each model process. The output includes the final status, answer, notes and summed calls and input tokens. Execution uses the supplied local binary.

```sh
python3 - ./target/release/afmcli table /tmp/special-input.json <<'PY'
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, "eval/moe")
import special
from study import job_call, usage

binary, group, input_file = sys.argv[1:]
task = json.loads(Path(input_file).read_text(encoding="utf-8"))
steps = []
def invoke(job, flags=()):
    reply = job_call(Path(binary).resolve(), flags, job, os.environ.copy())
    steps.append({"record": reply})
    return reply

reply = special.run({"group": group, "job": task}, "candidate", invoke)
output = {key: reply[key] for key in ("status", "result", "notes")}
print(json.dumps(dict(output, **usage(steps)), ensure_ascii=False, indent=2))
PY
```

Run `python3 -B eval/moe/special_check.py` to exercise host arithmetic, amendment validation, repair locality, semantic flips, strict inequalities, tie counts and independent point judgments using deterministic model responses.
