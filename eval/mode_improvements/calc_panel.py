"""Frozen bilingual arithmetic panel. Only the job member is sent to AFMCLI.

Each pair differs in one piece of evidence. IDs and iteration order do not encode
the answer class. Run this file directly for model-free contract tests.
"""
import ast
import hashlib
import operator
import re
import unicodedata
from fractions import Fraction
from typing import NamedTuple


class Pair(NamedTuple):
    language: str
    frame: str
    settled: str
    unresolved: str
    quantities: dict
    formula: str
    answer: str
    missing: str
    concepts: tuple


DEV = (
    Pair("en", "Two donation jars were counted separately at a bake sale. The blue jar held 38 dollars. @ A roll of paper cups cost 6 dollars. How much money was in the two jars altogether?",
         "The yellow jar held 27 dollars.", "The yellow jar weighed 27 ounces.",
         {"a": 38, "b": 27}, "a+b", "65", "amount of money in the yellow jar",
         (r"yellow|other jar|second jar|黄色|もう一方", r"amount|money|dollars?|cash|donations?|collected|contents|how much|held|raised|金額|募金|寄付|いくら|中身")),
    Pair("ja", "園芸部が同じ値段の素焼き鉢を注文した。鉢は1個145円。@ 倉庫には受け皿が8枚ある。今回注文した鉢の代金はいくらか。",
         "注文は12個だった。", "注文書には鉢の色だけが書いてあった。",
         {"a": 145, "b": 12}, "a*b", "1740", "number of pots ordered",
         (r"pots?|planters?|鉢|注文数|注文個数", r"how many|quantity|number|amount ordered|count|何個|いくつ|個数|数量|注文数|鉢の数|鉢数")),
    Pair("en", "The rehearsal room has 18 working microphones. @ What percentage of the microphones installed in the rehearsal room are working?",
         "There are 24 microphones installed in this room.", "There are 24 microphones installed in the adjoining room.",
         {"a": 18, "b": 24}, "100*(a/b)", "75", "total installed microphone count for the rehearsal room",
         (r"microphones?|mics?\b|マイク", r"total|denominator|all (?:the )?microphones|how many.*installed|number.*installed|installed.*(?:number|count)|全数|総数|合計|設置.*数|何本")),
    Pair("ja", "貸し出し前の棚には楽譜が56冊あった。今日は貸し出しと返却があり、それ以外の出入りはなかった。@ 返却された5冊は棚に戻した。今その棚には何冊あるか。",
         "この棚から貸し出したのは19冊だ。", "隣の棚から貸し出したのは19冊だ。",
         {"a": 56, "b": 19, "c": 5}, "a-b+c", "42", "number of scores borrowed from this shelf",
         (r"borrow|check(?:ed)? out|loan|lent|貸[し出]|借り", r"how many|number|count|quantity|amount|何冊|冊数|数量|いくつ|[たす]数|出数")),
)

TEST = (
    Pair("en", "The aquarium recorded 46 walk-in entries before lunch. @ The café served 19 lunches. How many walk-in entries were there over the morning and afternoon combined?",
         "In the afternoon it recorded 31 more walk-in entries.", "In the afternoon it recorded 31 online reservations.",
         {"a": 46, "b": 31}, "a+b", "77", "number of afternoon walk-in entries",
         (r"afternoon|after lunch|second (?:period|session)|午後", r"walk[ -]?ins?|walk[ -]?up|in[ -]?person|door (?:entries|admissions)|当日|予約なし|飛び込み|窓口|来場|入場")),
    Pair("ja", "作業前のタンクには冷却水が84 Lあり、作業中はそのタンクから水を使うだけだった。@ 点検時の水温は22度だった。作業でこのタンクから使った冷却水は何Lか。",
         "作業後に同じタンクを測ると59 L残っていた。", "作業後に隣のタンクを測ると59 L残っていた。",
         {"a": 84, "b": 59}, "a-b", "25", "amount of coolant remaining in the original tank after work",
         (r"remain|\bleft\b|after|final|end(?:ing)?|closing|residual|残|作業後|終了時|使用後", r"level|water|coolant|tank|volume|amount|lit[er]+s?|冷却水|タンク|残量|水量|液量")),
    Pair("en", "The museum orders 7 identical mounting brackets. The invoice lists a single delivery fee of 16 dollars. @ What is the total charge for the brackets and delivery?",
         "Each bracket costs 13 dollars.", "A display hook costs 13 dollars.",
         {"a": 7, "b": 13, "c": 16}, "a*b+c", "107", "price per mounting bracket",
         (r"brackets?|mounts?\b|金具|ブラケット", r"price|unit cost|how much|cost(?:s)? (?:per|of|each)|cost\b|単価|値段|価格|代金|いくら")),
    Pair("ja", "劇場が幕の補修に使うリボンは1巻360円で、送料はかからない。@ 伝票には公演日数が5日とある。今回のリボン代は何円か。",
         "今回買ったのは8巻だった。", "倉庫に保管してある予備は8巻だった。",
         {"a": 360, "b": 8}, "a*b", "2880", "number of ribbon rolls purchased",
         (r"ribbons?|rolls?|purchase|bought|注文|購入|買った|リボン|巻数",
          r"how many|quantity|number|count|何巻|巻数|個数|購入数|注文数|数量|買った数|リボンの数|いくつ")),
    Pair("en", "The wetland plan lists the entire site as 260 hectares. It marks reed beds on 15% of @. The map legend also lists 7 boardwalk entrances. What area in hectares does that 15% represent?",
         "the entire site", "the area covered by the canoe survey",
         {"a": 260, "r": 15}, "a*(r/100)", "39", "area covered by the canoe survey",
         (r"survey|canoe|mapped|調査|カヌー", r"area|extent|coverage|size|hectares?|how much land|面積|広さ|範囲|ヘクタール")),
    Pair("ja", "今朝観察したカモのうち、足環があったのは27羽だった。@ 今朝観察したカモのうち、足環があった割合は何%か。",
         "今朝観察したカモは全部で60羽だった。", "前日の夕方に観察したカモは全部で60羽だった。",
         {"a": 27, "b": 60}, "100*(a/b)", "45", "total number of ducks observed this morning",
         (r"morning|today|current|今朝|当日|今日", r"total|denominator|how many ducks|number of (?:observed )?ducks|ducks? (?:observed|count)|総数|合計|全体|羽数|カモの数|分母|観察数|全部で何羽")),
    Pair("en", "An arts society counts each person once per class list. There are 67 names on the sculpture list and 53 on the ceramics list. @ How many distinct people are on at least one of the lists?",
         "The society counted 18 names that appear on both lists.", "The society counted 18 names that were new to its newsletter.",
         {"a": 67, "b": 53, "c": 18}, "a+b-c", "102", "overlap between the two class lists",
         (r"overlap|intersection|double.?count|duplicat|both (?:of the |the |class )?(?:lists?|classes|courses|groups|workshops)|(?:names?|people|members?) in common|shared (?:names|members)|重複|両方|両(?:講座|クラス|名簿)|共通|掛け持ち",)),
    Pair("ja", "石材の発送で、荷物を載せた台車の総重量は142 kgだった。@ 構内で使うはかりは4台ある。荷物だけの重さは何kgか。",
         "空の状態の同じ台車は37 kgだった。", "空の状態の別の種類の台車は37 kgだった。",
         {"a": 142, "b": 37}, "a-b", "105", "empty weight of the cart that carried the cargo",
         (r"cart|trolley|dolly|tare|台車|風袋", r"empty|unloaded|tare|own weight|weight of (?:the |this |that |the same )?cart|自重|空の|空車|空荷|風袋|台車.*重[さ量]")),
    Pair("en", "A copy editor bills for 9 hours at 23 dollars an hour. The client has made a partial payment on this bill. @ How much is still due on this bill?",
         "The receipt shows a payment of 40 dollars toward this bill.", "The receipt shows a payment of 40 dollars toward an earlier bill.",
         {"a": 9, "b": 23, "c": 40}, "a*b-c", "167", "amount already paid on the current bill",
         (r"pay(?:ment)?|paid|deposit|credit|支払|支払っ|入金|前金|既払", r"amount|size|sum|how much|value|partial payment|current bill|this bill|payment (?:on|toward|for)|いくら|金額|支払額|入金額|一部|今回")),
    Pair("ja", "パン工房が試作した生地は962 gで、うち50 gを検査用に取り分けた。残りを同じ重さに丸めた。@ 丸めた生地1個分の重さは何gか。",
         "焼く前に数えると16個できていた。", "予備の天板には16個分のくぼみがあった。",
         {"a": 962, "b": 50, "c": 16}, "(a-b)/c", "57", "number of equal dough portions actually made",
         (r"portions?|pieces?|balls?|rolls?|units?|dough|buns?|個数|分割数|丸め|生地|等分",
          r"how many|number|count|quantity|何個|個数|いくつ|分割数|等分|作った数|できた数|生地の数")),
    Pair("en", "An audio guide has two rates. The standard rate is 84 dollars and the membership rate reduces that by 25%. @ How much was Sam charged for one guide, before tax?",
         "Sam used the membership rate for this purchase.", "Sam used the kiosk for this purchase.",
         {"a": 84, "r": 25}, "a*(1-r/100)", "63", "whether the membership rate applied to Sam's purchase",
         (r"membership|member\b|discount|which rate|what rate|rate (?:used|applied|charged)|会員|割引|どちらの料金",
          r"whether|status|eligib|qualif|applicab|membership status|which|what rate|used|appl(?:y|ied|ies)|member or|if sam|if .*member|かどうか|適用|会員資格|どちら|対象|有無")),
    Pair("ja", "海岸清掃で第1班は袋を34個集めた。第2班が集めたのは青い袋11個と赤い袋だった。@ 備品は集計に含めない。両班が集めた袋の合計は何個か。",
         "第2班が集めた赤い袋は16個だった。", "本部には交換用の赤い袋が16個あった。",
         {"a": 34, "b": 11, "c": 16}, "a+b+c", "61", "number of red bags collected by the second crew",
         (r"red|赤", r"count|number|quantity|how many|collected|何個|個数|数量|袋の数|赤い袋数|集めた数|回収数")),
)


def make(split):
    pairs = {"dev": DEV, "test": TEST}[split]
    cases = []
    for index, pair in enumerate(pairs):
        pair_id = f"calc-{split}-{index + 1:02}"
        for positive, detail in ((True, pair.settled), (False, pair.unresolved)):
            source = pair.frame.replace("@", detail)
            opaque = hashlib.sha256(source.encode()).hexdigest()[:12]
            gold = ({"value": pair.answer, "formula": pair.formula, "quantities": dict(pair.quantities)}
                    if positive else {"missing": pair.missing, "concepts": list(pair.concepts)})
            cases.append({"id": f"calc-{opaque}", "language": pair.language, "pair": pair_id,
                          "positive": positive, "job": {"task": "calculate", "source": source}, "gold": gold})
    return sorted(cases, key=lambda item: item["id"])


NUMBER = re.compile(r"[+-]?(?:[0-9]+/[1-9][0-9]*|(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE]([+-]?[0-9]+))?)\Z")


def _number(value):
    if type(value) not in (str, float, int):
        return None
    text = str(value).strip()
    m = NUMBER.fullmatch(text) if len(text) <= 180 else None
    if m is None or (m[1] and abs(int(m[1])) > 150):
        return None
    return Fraction(text)


def _parse(text):
    text = unicodedata.normalize("NFKC", text).translate(str.maketrans("×÷−", "*/-")).strip()
    if not text or len(text) > 768 or "**" in text:
        raise ValueError("invalid expression")
    text = text.replace("^", "**")
    root = ast.parse(text, mode="eval").body
    nodes = list(ast.walk(root))
    if len(nodes) > 128:
        raise ValueError("too many nodes")
    for node in nodes:
        if isinstance(node, ast.Constant):
            value = _number(ast.get_source_segment(text, node))
            if value is None:
                raise ValueError("invalid literal")
            node.value = value
    return root


OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def _calculate(node, names, literals):
    if isinstance(node, ast.Constant):
        return literals[node.value] if literals is not None else node.value
    if isinstance(node, ast.Name):
        return names[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _calculate(node.operand, names, literals)
        return -value if isinstance(node.op, ast.USub) else value
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "abs"
            and len(node.args) == 1 and not node.keywords):
        return abs(_calculate(node.args[0], names, literals))
    if not isinstance(node, ast.BinOp):
        raise ValueError("unsupported arithmetic")
    left = _calculate(node.left, names, literals)
    right = _calculate(node.right, names, literals)
    if isinstance(node.op, ast.Pow):
        if right.denominator != 1 or abs(right) > 12:
            raise ValueError("out of range")
        out = left ** int(right)
    else:
        out = OPS[type(node.op)](left, right)
    if out.numerator.bit_length() + out.denominator.bit_length() > 4096:
        raise ValueError("out of range")
    return out


def _rate_forms(rate):
    share = rate / 100
    return (share, 1 - share, 1 + share, 100 - rate, 100 + rate, 1 / share)


def _substitutions(original, trial, formula, fraction_form):
    # Every accepted derived literal has a specific interpretation in the source.
    mapping = {value: value for value in map(Fraction, (0, 1, 100))}
    mapping.update({value: trial[key] for key, value in original.items()})
    for term in ast.walk(formula):
        if term is not formula and isinstance(term, ast.BinOp):
            old = _calculate(term, original, None)
            mapping.setdefault(old, _calculate(term, trial, None))
    if "r" in original:
        rates = _rate_forms(original["r"])
        changed = _rate_forms(trial["r"])
        for old, new in zip(rates, changed):
            mapping.setdefault(old, new)
        if fraction_form is not None:
            # A written rate may be reduced, e.g. price*3/4 for a 25% discount.
            rate = rates[fraction_form]
            if rate.numerator > 1 and rate.denominator > 1:
                n, d = map(Fraction, (rate.numerator, rate.denominator))
                if n not in original.values() and d not in original.values():
                    mapping[n] = changed[fraction_form] * d
                    mapping[d] = d
    return mapping


def _supported(expression, gold):
    """Require a calculation, correct exact value, and the intended input roles.

    Replaying each operand with a different value distinguishes a sourced
    calculation from a numerical coincidence. Recognize proper intermediates,
    percent conventions, equivalent groupings, and supported unary signs.
    """
    if not isinstance(expression, str):
        return False
    try:
        model = _parse(expression)
        formula = _parse(gold["formula"])
        original = {key: Fraction(value) for key, value in gold["quantities"].items()}
        if _calculate(model, {}, None) != _number(gold["value"]):
            return False
        nodes = list(ast.walk(model))
        if not any(isinstance(node, ast.BinOp) for node in nodes):
            return False
        literal_values = {node.value for node in nodes if isinstance(node, ast.Constant)}
        if literal_values.isdisjoint(original.values()):
            return False
        probes = [original]
        for index, key in enumerate(original, 1):
            probes.append({**original, key: original[key] + index + 2})
        probes.append({key: value + 2 * i for i, (key, value) in enumerate(original.items(), 1)})
        forms = (None, 0, 1, 2) if "r" in original else (None,)
        for form in forms:
            try:
                if all(_calculate(model, {}, _substitutions(original, trial, formula, form))
                       == _calculate(formula, trial, None) for trial in probes):
                    return True
            except (ArithmeticError, KeyError, ValueError):
                continue
    except (ArithmeticError, KeyError, ValueError, SyntaxError, RecursionError):
        pass
    return False


GAP = re.compile(
    r"\b(missing|unknown|unreported|unrecorded|unspecified|unavailable|unclear|unprovided|unstated|"
    r"omitted|lacks?|absent|without|insufficient|need(?:s|ed)?|necessary|require[ds]?|whether|"
    r"no|not|never|cannot|only|which|depends)\b|n['’]t\b|"
    r"不足|不明|不詳|不定|必要|分から|わから|わかりま|分かりま|示され|記載され|"
    r"書かれて|記録され|与えられ|わかっていな|分かっていな|ない|ません|未[記確提示知]|不確|かどうか|なければ|欠け|どちら"
)


def _text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(f"{key} {_text(item)}" for key, item in value.items() if item is not None)
    return ""


def _reason_parts(record, result):
    # Never look in model attempts, metrics, source echoes or the original job.
    for container in (record, result):
        for key, value in container.items():
            explicit = key.startswith("missing") or key in ("required_input", "needed", "needed_inputs")
            if not explicit and key not in ("reason", "reasons", "explanation", "detail", "message", "notes"):
                continue
            for item in (value if isinstance(value, list) else [value]):
                raw = _text(item)
                if raw.strip():
                    normalized = unicodedata.normalize("NFKC", raw).casefold()
                    yield explicit, re.sub(r"[_-]+", " ", normalized)


def _specific_reason(record, result, concepts):
    for explicit, phrase in _reason_parts(record, result):
        # Keep presence and absence clauses apart. A noun phrase in a held
        # record may directly name the missing field, in either language.
        clauses = re.split(r"[。;；]|[.!?](?:\s|$)|\b(?:but|however|whereas)\b|一方|(?:だが|ですが|が)[、,]", phrase)
        for clause in clauses:
            if not all(re.search(term, clause) for term in concepts):
                continue
            named_only = (record.get("status") == "held" and len(clause) < 90
                          and not re.search(r"[0-9?？]|\b(?:is|are|was|were|know|known|provided|available|given)\b|判明|既知|記載済み", clause))
            if explicit or named_only or GAP.search(clause):
                return True
    return False


def grade(case, record, mode=None):
    """Score the same canonical AFMCLI record for every mode.

    A serious error means a numeric result was returned without a supported
    final answer. A safe but generic hold gets neither credit nor a serious
    error. A backend failure cannot establish a missing field.
    """
    record = record if isinstance(record, dict) else {}
    result = record.get("result")
    fields = result if isinstance(result, dict) else {}
    value = _number(fields.get("value", result))
    status = record.get("status")
    answered = value is not None
    same_value = case["positive"] and answered and value == _number(case["gold"]["value"])
    supported = bool(same_value and _supported(fields.get("expression"), case["gold"]))
    correct_number = supported and status in ("ok", "review")
    right_missing = (not case["positive"] and not answered and "result" in record
                     and (result is None or isinstance(result, dict))
                     and status in ("held", "ok", "review")
                     and not fields.get("expression")
                     and _specific_reason(record, fields, case["gold"]["concepts"]))
    return {"correct": bool(correct_number or right_missing), "answered": answered,
            "serious": bool(answered and not correct_number), "right_missing": bool(right_missing),
            "value_matches": bool(same_value), "supported_expression": supported}


def _self_test():
    seen = set()
    for split, expected in (("dev", 4), ("test", 12)):
        cases = make(split)
        assert len(cases) == 2 * expected
        for lang in ("en", "ja"):
            for answerable in (True, False):
                assert sum(c["language"] == lang and c["positive"] == answerable for c in cases) == expected // 2
        for case in cases:
            assert set(case["job"]) == {"task", "source"}
            assert case["job"]["task"] == "calculate" and "@" not in case["job"]["source"]
            assert case["id"] not in seen
            seen.add(case["id"])
            assert not grade(case, None)["correct"]
            assert not grade(case, {"status": "error", "result": None, "notes": ["backend failed"]})["correct"]
        for pair_id in sorted({case["pair"] for case in cases}):
            good, bad = sorted((case for case in cases if case["pair"] == pair_id),
                               key=lambda case: not case["positive"])
            gold = good["gold"]
            expression = re.sub(r"[abcr]", lambda m: str(gold["quantities"][m[0]]), gold["formula"])
            assert _calculate(_parse(gold["formula"]), {k: Fraction(v) for k, v in gold["quantities"].items()}, None) == Fraction(gold["value"])
            for status in ("ok", "review"):
                record = {"status": status, "result": {"expression": expression, "value": gold["value"]}}
                assert grade(good, record)["correct"]
                assert grade(bad, record)["serious"] and grade(bad, record)["answered"]
                record["result"]["expression"] = gold["value"]
                assert not grade(good, record)["correct"]
                part = next(iter(gold["quantities"].values()))
                record["result"] = {"expression": str(part), "value": str(part)}
                assert grade(good, record)["serious"]
            generic = {"status": "held", "result": None, "notes": ["no unambiguous supported expression was proposed"]}
            assert not any(grade(bad, generic).values())
            assert grade(bad, {"status": "held", "result": None, "notes": ["Missing: " + bad["gold"]["missing"]]})["correct"], pair_id
            assert grade(bad, {"status": "review", "result": {"missing_fields": [bad["gold"]["missing"]]}})["right_missing"]
            assert not grade(bad, {"status": "error", "result": None, "notes": ["Missing " + bad["gold"]["missing"]]})["right_missing"]
    good = {"quantities": {"a": 80, "r": 25}, "formula": "a*(1-r/100)", "value": "60"}
    for expression in ("80*(100-25)/100", "80−80÷4", "80×0.75", "80*3/4", "80-80*(25/100)"):
        assert _supported(expression, good), expression
    for expression in ("60", "60+0", "80-20+25-25", "80-25+5", "1/0", "__import__('os')"):
        assert not _supported(expression, good), expression
    jar = next(case for case in make("dev") if case["pair"] == "calc-dev-01" and not case["positive"])
    for explanation in ("The number of volunteers is missing.",
                        "The amount of cash in the yellow jar is known; the cost of cups is missing."):
        assert not grade(jar, {"status": "held", "result": None, "notes": [explanation]})["right_missing"]
    assert grade(jar, {"status": "held", "result": None, "notes": ["黄色い瓶に入った金額が不明です。"]})["right_missing"]
    assert _number("2/3") == Fraction(2, 3) and _number(None) is None and _number("NaN") is None
    print("calculator panel contracts passed (8 development, 24 holdout)")


if __name__ == "__main__":
    _self_test()
