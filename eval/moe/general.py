"""Route one short procedure from public job features; score the fixed bilingual panel."""

from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import unicodedata


HERE = Path(__file__).resolve().parent
ARMS = ("direct", "current", "common", "selected")
HINTS = HERE / "hints"
COMMON = "general-check"
# Keep this expression identical to src/engine.rs, including digit width and signs.
NUMBER = re.compile(r"[+＋\-−－]?[0-9０-９]+(?:[,，.．][0-9０-９]+)*")
REVISION = re.compile(
    r"\b(?:withdr\w*|revok\w*|resciss\w*|rescind\w*|reinstat\w*|supersed\w*|"
    r"replac\w*|cancel\w*|correct(?:ed|ion)s?)\b|撤回|取り?消[しすさせそ]|取り下げ|"
    r"訂正|変更|差し替え|再指定|復活", re.I)
UNIT = re.compile(
    r"\b(?:unit|mass|weight|pressure|temperature|length|width|volume|diameter|flow|"
    r"price|amount|distance|speed|duration|thickness|height|cost)s?\b|"
    r"単位|金額|価格|料金|温度|水温|質量|重量|流量|長さ|幅|速さ|風速|圧力|時間", re.I)
MULTI = re.compile(r"\n|\b(?:whereas|while|respectively|other|versus)\b|一方|それぞれ|対して|別の", re.I)
QUALIFIED = re.compile(
    r"\b(?:not|never|no|nor|neither|but|however|instead|also|both|although|earlier|"
    r"yesterday|already|rather|previously)\b|(?:n['’]t)\b|ません|しない|いない|"
    r"それと(?:は)?別|それに加え|両方|どちらも|以前|一方|ただし|のではなく|済み", re.I)


def cases(split):
    if split not in ("dev", "test"):
        raise ValueError(f"unknown general split {split!r}")
    return json.loads((HERE / f"general_{split}.json").read_text(encoding="utf-8"))


def selected_hint(job):
    task = job["task"]
    source = job.get("source", "")
    if task == "rewrite":
        return "number-binding" if job.get("keep_numbers") and NUMBER.search(source) else None
    if task == "classify":
        return "label-scope" if QUALIFIED.search(source) else None
    if task not in ("answer", "extract"):
        return None
    if REVISION.search(source):
        return "revision-scope"
    fields = job.get("fields", [])
    if any(field.get("many") for field in fields):
        return "separate-items"
    request = " ".join([job.get("question", ""), *(f.get("description", "") for f in fields)])
    if UNIT.search(request) and NUMBER.search(source):
        return "value-units"
    if MULTI.search(source):
        return "subject-scope"
    return None


def hint_for(arm, job):
    if arm not in ARMS:
        raise ValueError(f"unknown general arm {arm!r}")
    if arm == "selected":
        return selected_hint(job)
    return COMMON if arm == "common" else None


def skill_prefix(name):
    """Exact added request text built by src/fm.rs, including both final newlines."""
    if name not in {p.parent.name for p in HINTS.glob("*/SKILL.md")}:
        raise ValueError(f"unknown hint {name!r}")
    markdown = (HINTS / name / "SKILL.md").read_text(encoding="utf-8")
    body = markdown.split("---\n", 2)[2].strip()
    data = json.dumps({"guidance": body, "procedure": name}, ensure_ascii=False, separators=(",", ":"))
    return "Use the selected procedure when applicable. Follow the task and its output contract.\n" + data + "\n\n"


def prepare(binary, registry):
    """Register in the caller's explicit trial directory; identical packages are reusable."""
    if not registry or not str(registry).strip():
        raise ValueError("supply an explicit study skill registry")
    env = {**os.environ, "AFMCLI_SKILLS_DIR": str(Path(registry).resolve())}
    names = []
    for path in sorted(HINTS.glob("*/SKILL.md")):
        result = subprocess.run([str(Path(binary).resolve()), "skills", "import", str(path)],
                                capture_output=True, text=True, env=env, check=True, timeout=30)
        name = json.loads(result.stdout)["skill"]["name"]
        if name != path.parent.name:
            raise ValueError("unexpected imported skill name")
        names.append(name)
    return names


def run(case, arm, invoke):
    job = case["job"]
    hint = hint_for(arm, job)
    if arm == "direct":
        return invoke(job, flags=("--direct",))
    if hint:
        return invoke(job, flags=("--skill", hint))
    return invoke(job)


def pairs_object(entries):
    result = dict(entries)
    if len(result) != len(entries):
        raise ValueError("duplicate object member")
    return result


def decode_direct(value):
    if not isinstance(value, str):
        raise ValueError("direct result must contain the model's text")
    value = value.strip()
    block = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, re.S | re.I)
    return json.loads(block[1] if block else value, object_pairs_hook=pairs_object)


def quoted(item, source, anchors=()):
    """Return verbatim value and check every UTF-8 range, plus any required supporting row."""
    if not isinstance(item, dict) or not isinstance(item.get("value"), str):
        raise ValueError("invalid evidence value")
    value = item["value"]
    raw = source.encode("utf-8")
    needle = value.encode("utf-8")
    spans = item.get("spans")
    if not needle or not isinstance(spans, list) or not spans:
        return value, False
    allowed = []
    for anchor in anchors:
        target = anchor.encode("utf-8")
        at = raw.index(target)
        allowed.append((at, at + len(target)))
    seen = []
    for span in spans:
        if not isinstance(span, dict):
            return value, False
        a, b = span.get("start_byte"), span.get("end_byte")
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(raw) or raw[a:b] != needle:
            return value, False
        seen.append((a, b))
    anchored = not allowed or any(lo <= a and b <= hi for a, b in seen for lo, hi in allowed)
    return value, anchored and len(set(seen)) == len(seen)


def decision(correct, serious=False, outcome="correct", **detail):
    return {"correct": bool(correct), "serious": bool(serious), "outcome": outcome, **detail}


def normalized(text):
    return unicodedata.normalize("NFKC", text).casefold()


MISSING_TEXT = re.compile(
    r"(?:^|\b)(?:unknown|unspecified|not (?:given|stated|provided|known|specified|confirmed|"
    r"listed|credited|named|decided|yet)|no (?:confirmed|artist|name|date|answer)|yet to be)\b|"
    r"不明|未定|未確定|分から|わから|確定していな|決まっていな|記載(?:が|は)?(?:ない|なし)|なし", re.I)


def minor_variant(value, gold):
    """Malformed presentation of known values has a lower severity than assigning a new one."""
    v = normalized(value).strip()
    if MISSING_TEXT.search(v):
        return True
    remaining = v
    for expected in sorted((normalized(x) for x in gold), key=len, reverse=True):
        remaining = remaining.replace(expected, " ")
        if v == expected:
            return True
    return bool(gold) and not re.sub(r"\b(?:and|or|the)\b|[\s、,・&/「」『』:：。.!?]+|および|及び|と", "", remaining)


def grade_answer(case, direct, result, status):
    if not isinstance(result, dict) or set(result) != {"answer"}:
        raise ValueError("invalid answer object")
    entry = result["answer"]
    if direct:
        if not isinstance(entry, str):
            raise ValueError("invalid direct answer")
        value, spans = entry, None
    elif entry is None:
        value, spans = "", None
    else:
        value, spans = quoted(entry, case["job"]["source"], case.get("evidence", {}).get("answer", ()))
    if not direct and (status == "abstained") != (entry is None):
        raise ValueError("inconsistent abstention status")
    gold = case["gold"]
    if spans is False:
        return decision(False, True, "wrong_evidence", spans_valid=False)
    if not value:
        return decision(not gold, False, "missed_answer" if gold else "correct_abstention", spans_valid=spans)
    if value in gold:
        return decision(True, False, "correct_answer", spans_valid=spans)
    # An overlong source quote can still contain the requested fact. It fails the exact
    # answer contract but, by itself, does not establish a false factual assertion.
    overlong = value in case["job"]["source"] and any(g in value for g in gold)
    return decision(False, not overlong and not minor_variant(value, gold),
                    "wrong_answer" if gold else "unsupported_answer",
                    spans_valid=spans)


def grade_extract(case, direct, result, status):
    gold = case["gold"]
    if not isinstance(result, dict) or set(result) != set(gold):
        raise ValueError("invalid extraction fields")
    found, extra, missing = {}, {}, {}
    evidence = []
    serious = False
    for field, expected in gold.items():
        items = result[field]
        if not isinstance(items, list):
            raise ValueError("field values must be an array")
        actual = []
        for item in items:
            if direct:
                if not isinstance(item, str) or not item:
                    raise ValueError("direct field value must be nonempty text")
                value = item
            else:
                value, ok = quoted(item, case["job"]["source"], case.get("evidence", {}).get(field, ()))
                evidence.append(ok)
            actual.append(value)
        found[field] = actual
        surplus = list((Counter(actual) - Counter(expected)).elements())
        absent = list((Counter(expected) - Counter(actual)).elements())
        if surplus:
            extra[field] = surplus
            serious |= any(value not in expected and not minor_variant(value, expected) for value in surplus)
        if absent:
            missing[field] = absent
    populated = sum(bool(values) for values in found.values())
    expected_status = "abstained" if not populated else "ok" if populated == len(found) else "partial"
    if not direct and status != expected_status:
        raise ValueError("inconsistent extraction status")
    spans = all(evidence) if evidence else None
    if spans is False:
        outcome = "wrong_evidence"
    elif extra:
        outcome = "extra_extraction"
    elif missing:
        outcome = "missing_extraction"
    else:
        outcome = "correct_extraction"
    return decision(not extra and not missing and spans is not False, serious or spans is False,
                    outcome, spans_valid=spans, missing=missing, extra=extra)


def grade_classify(case, direct, result, status):
    if not isinstance(result, dict) or set(result) != {"label"}:
        raise ValueError("invalid classification result")
    label = result["label"]
    if direct and not isinstance(label, str):
        raise ValueError("invalid direct label")
    if not direct and ((label is not None and (not isinstance(label, str) or not label))
                       or (status == "abstained") != (label is None)):
        raise ValueError("invalid label or abstention status")
    value = label or ""
    if value == case["gold"]:
        return decision(True, False, "correct_label" if value else "correct_abstention")
    return decision(False, bool(value), "wrong_label" if value else "missed_label")


# Check a value and its subject in either word order, within a clause and with no other
# number in between. This accepts e.g. "€9 for balcony seats" and "balcony seats cost €9".
CLAUSE_BREAK = re.compile(r"[;；!?！？。\n、]|(?<!\d)\.(?=\s|$)|,(?!\d)|\b(?:and|but|whereas|while)\b")


def linked(text, subject, quantity, rivals=()):
    for entity in re.finditer(subject, text):
        for value in re.finditer(quantity, text):
            if entity.end() <= value.start():
                gap = text[entity.end():value.start()]
                # A pause after a topic or table label can occur inside one binding.
                gap = re.sub(r"(?<=[はがもに])、|^[\s,:：、]+$|(?<=seats),|(?<=price),", " ", gap)
            elif value.end() <= entity.start():
                gap = text[value.end():entity.start()]
            else:
                return True
            if (len(gap) <= 80 and not CLAUSE_BREAK.search(gap) and not NUMBER.search(gap)
                    and not any(re.search(other, gap) for other in rivals)):
                return True
    return False


def coordinated(text, gold):
    """Shared units in an explicit parallel list such as 'A and B are 14 and 9 euros respectively'."""
    bindings = gold["bindings"]
    if len(bindings) < 2 or not re.search(r"\brespectively\b|それぞれ|順に|順で", text):
        return False
    example = normalized(gold["example"])
    expected = [re.search(q, example).group() for _, q in bindings]
    unit = [NUMBER.sub("", v).strip() for v in expected]
    if len(set(unit)) != 1 or not any(re.search(q, text) for _, q in bindings):
        return False
    occurrences = [re.search(subject, text) for subject, _ in bindings]
    if not all(occurrences):
        return False
    indices = sorted(range(len(bindings)), key=lambda i: occurrences[i].start())
    return NUMBER.findall(text) == [NUMBER.search(expected[i]).group() for i in indices]


def style_key(text):
    return "".join(c for c in normalized(text) if c.isalnum())


def grade_rewrite(case, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid rewrite text")
    value = value.strip()
    job, gold = case["job"], case["gold"]
    text = normalized(value)
    quantities = Counter(NUMBER.findall(value)) == Counter(NUMBER.findall(job["source"]))
    bindings = gold["bindings"]
    unbound = []
    swapped = False
    for i, (subject, quantity) in enumerate(bindings):
        rivals = [r[0] for j, r in enumerate(bindings) if i != j]
        if not linked(text, subject, quantity, rivals):
            unbound.append(i)
            swapped |= any(i != j and q != quantity and linked(text, subject, q, rivals)
                           for j, (_, q) in enumerate(bindings))
    if unbound and coordinated(text, gold):
        unbound, swapped = [], False
    terms_missing = [i for i, p in enumerate(gold["terms"]) if not re.search(p, text)]
    conditions_missing = [i for i, p in enumerate(gold["conditions"]) if not re.search(p, text)]
    contradictory = any(re.search(p, text) for p in gold["contradictions"])
    missing_unit = any(not re.search(bindings[i][1], text) for i in unbound)
    changed = style_key(value) != style_key(job["source"])
    length = len(value) <= job["max_chars"]
    semantic = quantities and not (unbound or terms_missing or conditions_missing or contradictory)
    serious = not quantities or swapped or missing_unit or contradictory or bool(conditions_missing)
    outcome = ("wrong_quantities" if not quantities else "contradiction" if contradictory or swapped else
               "unverified_facts" if not semantic else "style_or_length" if not (length and changed)
               else "correct_rewrite")
    return decision(semantic and length and changed, serious, outcome, numbers_exact=quantities,
                    unbound=unbound, terms_missing=terms_missing, conditions_missing=conditions_missing,
                    changed=changed, within_limit=length)


def grade(case, arm, record):
    status = record.get("status")
    direct = arm == "direct"
    expected_status = {"unverified"} if direct else {"ok", "abstained"}
    if not direct and case["job"]["task"] == "extract":
        expected_status.add("partial")
    if status not in expected_status:
        return decision(False, False, status or "missing_status")
    job, result = case["job"], record.get("result")
    try:
        if job["task"] == "rewrite":
            if status == "abstained":
                return decision(False, False, "abstained")
            return grade_rewrite(case, result)
        if direct:
            result = decode_direct(result)
        grader = {"answer": grade_answer, "extract": grade_extract, "classify": grade_classify}[job["task"]]
        return grader(case, direct, result, status)
    except (KeyError, TypeError, ValueError, UnicodeError):
        return decision(False, False, "invalid_format")
