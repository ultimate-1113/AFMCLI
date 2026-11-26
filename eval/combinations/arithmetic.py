"""Research arms for arithmetic. All calculations use io.calc or a registered tool call.

Evidence offsets count original Python Unicode code points; [start, end) is half-open.
Only the selected evidence is visible to the fresh formula reader. The model never
chooses the numeric binding of a variable and does not see another arm's answer.
"""
import json
import re
import unicodedata
from fractions import Fraction


ARMS = ("native", "native_value", "formula", "evidence_formula", "parallel", "selective")
MAX_INPUT = 3600
STR = {"type": "string"}


def obj(**fields):
    return {"type": "object", "properties": fields, "required": list(fields),
            "additionalProperties": False}


def array(item):
    return {"type": "array", "items": item}


FORM = obj(decision={"type": "string", "enum": ["calculate", "unknown"]}, expression=STR)
EVIDENCE = obj(target=STR, quantities=array(obj(quote=STR, quantity=STR)), relations=array(STR))
EXTRACT_PROMPT = """Read the English or Japanese question. Extract verbatim source text, without solving it.
target: copy the clause asking for the answer. quantities: for each potentially needed number, copy a short unique quote identifying its role and its exact number text inside that quote. Keep a sign or a whole fraction together (including number words). Put units in the quote.
relations: copy one or more short exact clauses connecting the needed facts, defining a rate, or establishing an unknown. For a single fact copy its assignment. Preserve which unit the question asks for. Omit unrelated details and ignore missing information about unrelated items. Do not paraphrase or translate.
Question:
"""
PLAIN_PROMPT = """Translate this English or Japanese question to a small arithmetic expression. Determine only the requested quantity. Ignore unrelated information, even if it is missing. Use the source quantities, unit conversions, explicit + - * / ^ and parentheses. Do not calculate the answer yourself. If a needed value or relationship cannot be determined, set decision to unknown and expression to the empty string. Otherwise set decision to calculate.
Question:
"""
BOUND_PROMPT = """Use these verbatim extracts to solve only the target. Each variable is the printed quantity in its quoted unit (a percent is in percentage points). Return decision calculate and an arithmetic expression using the listed variable names, explicit + - * / ^ and parentheses. Use names instead of their numeric values. Only the stated conversion constants may appear as literals. Respect direction, units, and stated exceptions. Ignore missing quantities unrelated to the target. If the answer cannot be determined from the extracts, return decision unknown and an empty expression.
Extracts:
"""


def result(kind, value=None):
    return {"kind": kind, "value": value}


SMALL = dict(zip(("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                  "fourteen fifteen sixteen seventeen eighteen nineteen").split(), range(20)))
TENS = dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10)))
DENOMS = {"half": 2, "halves": 2, "third": 3, "quarter": 4, "fourth": 4, "fifth": 5,
          "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
JP_DIGITS = dict(zip("〇一二三四五六七八九", "0123456789"), 零="0")
JP_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000, "億": 100000000}
JP_NUMS = "".join(JP_DIGITS) + "".join(JP_UNITS)
DECIMAL = r"(?:(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?|\.[0-9]+)"
PUNCT = str.maketrans({"−": "-", "×": "*", "÷": "/", "⁄": "/", "∕": "/", "‐": "-", "‑": "-"})


def english_integer(words):
    if not words:
        return None
    if len(words) == 1:
        return SMALL.get(words[0], TENS.get(words[0]))
    if len(words) == 2 and words[0] in TENS and 0 < SMALL.get(words[1], 0) < 10:
        return TENS[words[0]] + SMALL[words[1]]
    for word, scale in (("million", 1000000), ("thousand", 1000), ("hundred", 100)):
        if words.count(word) != 1:
            continue
        i = words.index(word)
        high = 1 if words[:i] == ["a"] else english_integer(words[:i])
        tail = words[i + 1:]
        if tail[:1] == ["and"] and len(tail) > 1:
            tail = tail[1:]
        low = english_integer(tail) if tail else 0
        if high is not None and 0 < high < min(scale, 1000) and low is not None and low < scale:
            return high * scale + low
    return None


def japanese_integer(s):
    if not re.fullmatch("[0-9" + JP_NUMS + "]+", s):
        return None
    digits = "".join(JP_DIGITS.get(c, c) for c in s)
    if digits.isdigit():
        return int(digits)
    for unit, scale in reversed(tuple(JP_UNITS.items())):
        if unit not in digits:
            continue
        if digits.count(unit) != 1:
            return None
        left, right = digits.split(unit)
        high = japanese_integer(left) if left else 1
        low = japanese_integer(right) if right else 0
        if high is None or low is None or high >= scale or low >= scale:
            return None
        return high * scale + low
    return None


def scalar(s):
    if re.fullmatch(DECIMAL, s):
        return Fraction(s.replace(",", ""))
    n = japanese_integer(s)
    if n is None:
        n = english_integer(s.replace("-", " ").split())
    return Fraction(n) if n is not None else None


def source_number(surface):
    """Decode a single quoted number, without evaluating a proposed expression."""
    if not isinstance(surface, str) or not 0 < len(surface) <= 72:
        return None
    # NFKC turns adjacent '1½' into '11⁄2'; preserve the mixed-number boundary.
    s = re.sub(r"(?<=\d)([¼½¾⅐-⅞])", r" \1", surface)
    s = unicodedata.normalize("NFKC", s).translate(PUNCT).lower().strip()
    s = re.sub(r"^[¥$£€]\s*|\s*(?:%|percent|per cent|パーセント)$", "", s).strip()
    sign = -1 if s.startswith("-") else 1
    s = s[1:].strip() if s[:1] in ("-", "+") else s
    if "分の" in s or "/" in s:
        jp = "分の" in s
        pieces = re.split("分の" if jp else "/", s)
        if len(pieces) != 2:
            return None
        num, den = (pieces[::-1] if jp else pieces)
        mixed = re.fullmatch(r"([0-9]+)\s+([0-9]+)", num.strip())
        whole = int(mixed[1]) if mixed and not jp else 0
        numerator = scalar(mixed[2] if mixed and not jp else num.strip())
        denominator = scalar(den.strip())
        if numerator is None or not denominator:
            return None
        return sign * (whole + numerator / denominator)
    if s in ("半", "半分"):
        return sign * Fraction(1, 2)
    words = s.replace("-", " ").split()
    if words:
        denom = DENOMS.get(words[-1], DENOMS.get(words[-1].rstrip("s")))
        if denom:
            leading = words[:-1]
            whole = 0
            if "and" in leading:
                cut = len(leading) - 1 - leading[::-1].index("and")
                whole = english_integer(leading[:cut])
                leading = leading[cut + 1:]
            numerator = 1 if not leading or leading == ["a"] else english_integer(leading)
            if whole is not None and numerator is not None:
                return sign * (whole + Fraction(numerator, denom))
    value = scalar(s)
    return sign * value if value is not None else None


def numeric_value(s):
    """Bound comparison of already-produced numeric results, including rational output."""
    if not isinstance(s, str) or not 0 < len(s) <= 350:
        return None
    value = s.strip()
    if not re.fullmatch(r"[+-]?(?:[0-9]+/[0-9]+|(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,3})?)", value):
        return None
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError):
        return None


def native_results(question, io):
    row = io.native(question)
    if not isinstance(row, dict):
        row = {}
    val = row.get("value")
    kind = row.get("kind")
    if kind == "number" and numeric_value(val) is not None:
        final = result("number", val)
    else:
        final = result("unknown" if kind == "unknown" else "held")
    callbacks = row.get("callbacks", [])
    last = callbacks[-1] if isinstance(callbacks, list) and callbacks else None
    callback = result("number", last) if numeric_value(last) is not None else None
    notes = {"final": final, "successful_callbacks": len(callbacks) if isinstance(callbacks, list) else 0,
             "last_callback": last}
    return final, callback, notes


class InvalidEvidence(ValueError):
    pass


def exact_span(source, text, field):
    if not isinstance(text, str) or not text.strip() or len(text) > 512:
        raise InvalidEvidence(field + ":empty_or_oversize")
    start = source.find(text)
    if start < 0:
        raise InvalidEvidence(field + ":not_in_source")
    if source.find(text, start + 1) >= 0:
        raise InvalidEvidence(field + ":ambiguous_occurrence")
    return {"text": text, "start": start, "end": start + len(text)}


def whole_surface(source, start, end):
    # Match the original coordinates. Normalization is only for boundary classification.
    left = unicodedata.normalize("NFKC", source[:start]).translate(PUNCT)
    right = unicodedata.normalize("NFKC", source[end:]).translate(PUNCT)
    term = unicodedata.normalize("NFKC", source[start:end]).translate(PUNCT)
    number_chars = "0-9" + JP_NUMS
    if re.match("[" + number_chars + "]", term):
        if re.search("[" + number_chars + "]$", left) or re.search(r"(?:[0-9],|\.)$", left):
            return False
        if re.search("[" + number_chars + "]\\s*/\\s*$", left) or left.endswith("分の"):
            return False
        if left.endswith(("-", "+")) and not re.search(r"[0-9)]\s*[+-]$", left):
            return False
    if re.search("[" + number_chars + "]$", term):
        if re.match("[" + number_chars + "]", right) or re.match(r"[.,][0-9]", right):
            return False
        if re.match("\\s*/\\s*[" + number_chars + "]", right) or right.startswith("分の"):
            return False
    if re.search(r"[a-zA-Z]$", left) and re.match(r"[a-zA-Z]", term):
        return False
    if re.search(r"[a-zA-Z]$", term) and re.match(r"[a-zA-Z]", right):
        return False
    if re.match(r"[a-zA-Z]", term) or re.search(r"[a-zA-Z]$", term):
        # Exclude picking just 'five' from 'twenty-five' or 'thirds' from 'two thirds'.
        left_window, right_window = left[-72:], right[:72]
        for match in re.finditer(r"(?<![A-Za-z0-9])[A-Za-z0-9]+", left_window):
            if source_number(left_window[match.start():] + term) is not None:
                return False
        for match in re.finditer(r"[A-Za-z0-9]+(?![A-Za-z0-9])", right_window):
            if source_number(term + right_window[:match.end()]) is not None:
                return False
    return True


def ground(question, raw):
    if not isinstance(raw, dict):
        raise InvalidEvidence("evidence:missing")
    target = exact_span(question, raw.get("target"), "target")
    quantities, relations = raw.get("quantities"), raw.get("relations")
    if not isinstance(quantities, list) or len(quantities) > 12:
        raise InvalidEvidence("quantities:invalid_list")
    if not isinstance(relations, list) or not 1 <= len(relations) <= 10:
        raise InvalidEvidence("relations:invalid_list")
    links = [exact_span(question, x, "relation" + str(i)) for i, x in enumerate(relations)]
    bindings, seen = {}, set()
    for i, item in enumerate(quantities):
        field = "quantity" + str(i)
        if not isinstance(item, dict):
            raise InvalidEvidence(field + ":invalid_record")
        quote = exact_span(question, item.get("quote"), field + ".quote")
        term = item.get("quantity")
        value = source_number(term)
        if value is None:
            raise InvalidEvidence(field + ":unsupported_number")
        positions = [(quote["start"] + m.start(), quote["start"] + m.end())
                     for m in re.finditer(re.escape(term), quote["text"])]
        positions = [(a, b) for a, b in positions if whole_surface(question, a, b)]
        if len(positions) != 1 or positions[0] in seen:
            raise InvalidEvidence(field + ":requires_one_distinct_whole_source_number")
        a, b = positions[0]
        seen.add((a, b))
        bindings[chr(ord("A") + i)] = {"value": str(value), "quote": quote,
                                       "quantity": {"text": term, "start": a, "end": b}}
    return {"target": target, "quantities": bindings, "relations": links}


def allowed_constants(evidence):
    parts = [evidence["target"]["text"]] + [r["text"] for r in evidence["relations"]]
    parts.extend(row["quote"]["text"] for row in evidence["quantities"].values())
    source = unicodedata.normalize("NFKC", " ".join(parts)).lower()
    out = {"1": "one whole"}
    if re.search(r"%|per\s*cent|パーセント|百分率", source):
        out["100"] = "percentage points per whole"
    units = {
        "sec": r"seconds?\b|秒", "min": r"minutes?\b|分間|分(?!の)",
        "hour": r"hours?\b|時間", "day": r"days?\b|日", "week": r"weeks?\b|週",
        "kg": r"kilograms?\b|(?<![a-z])kg\b|キログラム", "g": r"(?<!kilo)grams?\b|(?<![a-z])g\b|(?<!キロ)グラム",
        "km": r"kilomet(?:er|re)s?\b|(?<![a-z])km\b|キロメートル", "m": r"(?<!kilo)(?<!centi)met(?:er|re)s?\b|(?<![a-z])m\b|(?<!キロ)(?<!センチ)メートル",
        "cm": r"centimet(?:er|re)s?\b|(?<![a-z])cm\b|センチメートル",
        "dollar": r"dollars?\b|\$|ドル", "cent": r"\bcents?\b|セント",
    }
    for a, b, n in (("sec", "min", 60), ("min", "hour", 60), ("hour", "day", 24),
                    ("day", "week", 7), ("kg", "g", 1000), ("km", "m", 1000),
                    ("m", "cm", 100), ("dollar", "cent", 100)):
        if re.search(units[a], source) and re.search(units[b], source):
            out[str(n)] = a + "/" + b + " conversion"
    return out


TOKEN = re.compile(r"\s+|(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)|[A-Za-z]+|[()+*/^\-]")


def compile_formula(expression, bindings, constants):
    if not isinstance(expression, str) or not 0 < len(expression) <= 200:
        raise ValueError("empty_or_oversize_expression")
    s = unicodedata.normalize("NFKC", expression).translate(PUNCT)
    tokens = TOKEN.findall(s)
    if "".join(tokens) != s:
        raise ValueError("unsupported_token")
    tokens = [t for t in tokens if not t.isspace()]
    if len(tokens) > 64:
        raise ValueError("token_limit")
    output, used = [], set()
    expect_operand, depth = True, 0
    for tok in tokens:
        if tok == "(" and expect_operand:
            depth += 1
            if depth > 10:
                raise ValueError("nesting_limit")
        elif tok == ")" and not expect_operand and depth > 0:
            depth -= 1
        elif tok in ("+", "-") and expect_operand:
            pass
        elif tok in ("+", "-", "*", "/", "^") and not expect_operand:
            expect_operand = True
        elif expect_operand and (tok[0].isascii() and (tok[0].isdigit() or tok[0] == ".")):
            if len(tok) > 40 or (bindings is not None and tok not in constants):
                raise ValueError("unpermitted_literal")
            expect_operand = False
        elif expect_operand and bindings is not None and tok.upper() in bindings:
            name = tok.upper()
            used.add(name)
            tok = "(" + bindings[name]["value"] + ")"
            expect_operand = False
        else:
            raise ValueError("invalid_syntax_or_unbound_variable")
        output.append(tok)
    if expect_operand or depth:
        raise ValueError("incomplete_expression")
    if bindings is not None and not used:
        raise ValueError("no_grounded_operand")
    return " ".join(output), sorted(used)


def decide(prompt, io, bindings=None, constants=()):
    response = io.read(prompt, FORM, max_input=MAX_INPUT)
    if not isinstance(response, dict):
        return result("held"), {"reason": "formula_reader_failed"}
    expr = response.get("expression")
    kind = response.get("decision")
    if kind == "unknown" and isinstance(expr, str) and not expr.strip():
        return result("unknown"), {"reason": "model_declared_insufficient"}
    if kind != "calculate":
        return result("held"), {"reason": "inconsistent_formula_decision"}
    try:
        compiled, used = compile_formula(expr, bindings, constants)
    except ValueError as exc:
        return result("held"), {"reason": str(exc), "proposed_expression": expr}
    value = io.calc(compiled)
    notes = {"proposed_expression": expr, "compiled_expression": compiled, "variables_used": used}
    if numeric_value(value) is None:
        return result("held"), dict(notes, reason="calculator_rejected")
    return result("number", value), dict(notes, reason="calculator_value")


def evidence_formula(question, io):
    raw = io.read(EXTRACT_PROMPT + question, EVIDENCE, max_input=MAX_INPUT)
    try:
        evidence = ground(question, raw)
    except InvalidEvidence as exc:
        return result("held"), {"reason": str(exc)}
    constants = allowed_constants(evidence)
    bindings = evidence["quantities"]
    # Offsets are recorded for audit, and omitted from the reader input.
    packet = {"target": evidence["target"]["text"], "relations": [r["text"] for r in evidence["relations"]],
              "variables": {n: {"value": row["value"], "quote": row["quote"]["text"]}
                            for n, row in bindings.items()}, "allowed_constants": constants}
    prompt = BOUND_PROMPT + json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
    answer, notes = decide(prompt, io, bindings, constants)
    return answer, {"evidence": evidence, "formula": notes, "allowed_constants": constants}


def agree(a, b):
    if "held" in (a["kind"], b["kind"]):
        return result("held"), "branch_held"
    if a["kind"] == b["kind"] == "unknown":
        return result("unknown"), "both_unknown"
    if a["kind"] == b["kind"] == "number":
        n = numeric_value(a["value"])
        if n is not None and n == numeric_value(b["value"]):
            return result("number", str(n)), "same_number"
        return result("held"), "numbers_disagree"
    return result("held"), "number_unknown_disagreement"


def execute(arm, job, io):
    question = job["question"]
    if arm not in ARMS:
        raise ValueError("unknown arithmetic arm: " + str(arm))
    if arm == "formula":
        return decide(PLAIN_PROMPT + question, io)
    if arm == "evidence_formula":
        return evidence_formula(question, io)
    final, callback, notes = native_results(question, io)
    candidate = callback or final
    if arm == "native":
        return final, dict(notes, reason="native_final")
    if arm == "native_value":
        return candidate, dict(notes, reason="last_callback" if callback else "native_final")
    if arm == "selective" and callback and agree(final, callback)[1] == "same_number":
        return candidate, {"native": notes, "policy": "final_confirms_callback"}
    evidence_answer, evidence_notes = evidence_formula(question, io)
    if arm == "selective" and candidate["kind"] == "held":
        answer, reason = evidence_answer, "evidence_after_native_held"
    else:
        answer, reason = agree(candidate, evidence_answer)
    return answer, {"native": notes, "native_candidate": candidate, "evidence_formula": evidence_notes,
                    "evidence_answer": evidence_answer, "policy": reason}
