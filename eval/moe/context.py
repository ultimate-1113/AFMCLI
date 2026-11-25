"""Deterministic long-document fixtures. All oracle data stays outside the submitted job."""
import datetime
import json
import random
import re
from pathlib import Path

ARMS = ("direct", "current", "once", "indexed")
FLAGS = {"direct": "--direct", "current": "--compact=records",
         "once": "--compact=index-once", "indexed": "--compact=indexed"}
HERE = Path(__file__).resolve().parent
TIERS = ("4k", "8k", "16k")
GROUPS = ("early", "early", "amendment", "amendment", "different_target",
          "different_target", "absent", "absent", "aggregate", "aggregate")
DEV_SLOTS = {"4k": (1, 8), "8k": (3, 9), "16k": (4, 6)}
EN_NAMES = ("Plover", "Oriole", "Mahogany", "Juniper", "Hornbeam", "Violet", "Cobalt",
            "Osprey", "Verbena", "Saffron", "Marigold", "Elm", "Alder", "Beryl",
            "Linden", "Willow", "Petrel", "Sorrel", "Clove", "Aster", "Rowan")
JA_NAMES = ("常盤", "桔梗", "山吹", "白樺", "久遠", "鳴海", "萌黄", "千鳥", "青嵐", "紫苑",
            "千草", "椿森", "柊野", "楠浜", "長月", "双葉", "若竹", "時雨", "桃花", "葵谷", "薄雲")
LOC_EN = ("Kite Hall", "Fennel Hall", "Slate Hall", "Vale Hall", "Iris Hall", "Glen Hall")
LOC_JA = ("翠風館", "白露館", "冬木館", "羽衣館", "木立館", "南渓館")
EN_PLACES = ("map room", "dispatch office", "workshop", "storage wing", "library", "front desk",
             "east annex", "workroom", "south gate", "supply room", "reading room", "gallery")
EN_ITEMS = ("receipt book", "binder", "route sheet", "index card", "checklist", "logbook",
            "station diagram", "supply list", "packing slip", "display card", "order form")
EN_NAMES_FILL = ("Knox", "Reed", "Page", "Frey", "Lowe", "Quinn", "Fox", "Arden", "Wren", "Moss")
JA_PLACES = ("資料室", "連絡所", "作業場", "収蔵庫", "図書室", "受付", "東別館", "工房", "南門", "備品庫", "閲覧室", "展示室")
JA_ITEMS = ("受領簿", "資料綴り", "巡回表", "索引カード", "点検票", "当番日誌", "配置図", "備品表", "荷札", "展示札", "注文書")
JA_NAMES_FILL = ("牧", "川辺", "芦田", "米原", "関谷", "小山", "上野", "原田", "皆川", "瀬戸")


def filler_line(rng, index, ja, style):
    if style == "copies":
        return "閉館時に帳簿を収納し、鍵を守衛に返した。" if ja else "At closing the clerk filed the logbook and returned the key to the caretaker."
    if style == "numeric":
        return (f"巡回票 {index:04d} は棚 {index % 73:02d} の照合を記録した。" if ja else
                f"Patrol slip {index:04d} records the inspection of shelf {index % 73:02d} before closing.")
    day = datetime.date(2025, 1, 1) + datetime.timedelta(days=index // 7)
    if ja:
        place, item, name = (rng.choice(a) for a in (JA_PLACES, JA_ITEMS, JA_NAMES_FILL))
        forms = (f"{name}は{place}で{item}の汚れを確認し、当番への引継ぎ欄に作業を記入した。",
                 f"{place}から戻った{name}が{item}を照合し、記載漏れを担当の係に知らせた。",
                 f"{item}を{place}へ戻す際、{name}は表紙のラベルを貼り直した。",
                 f"{place}の戸締まりを終えた{name}が、{item}の控えを連絡箱に入れた。",
                 f"{name}の当番報告によると、{place}で見つけた{item}は同日のうちに返却された。")
    else:
        place, item, name = (rng.choice(a) for a in (EN_PLACES, EN_ITEMS, EN_NAMES_FILL))
        forms = (f"{name} checked the {item} in the {place} and recorded its condition at handover.",
                 f"The {place} sent its {item} back to {name} with a note about the missing label.",
                 f"Before closing, {name} put the {item} on file for collection by staff from the {place}.",
                 f"The clerk at the {place} asked {name} to flag the {item} for an ordinary filing check.",
                 f"After the round, {name} took the {item} to the {place} for the next shift.")
    return f"{day.isoformat()} / {index + 1:04d}  {rng.choice(forms)}"


def facts(seed, slot, ja):
    rng = random.Random(seed)
    target, other = rng.sample(JA_NAMES if ja else EN_NAMES, 2)
    old, new, extra = rng.sample(LOC_JA if ja else LOC_EN, 3)
    code = f"{rng.choice(('HP', 'JL', 'RC', 'QF'))}-{rng.randrange(200, 980)}"
    code2 = f"SX-{rng.randrange(200, 980)}"
    events = []

    def add(pos, sentence, value=None):
        events.append((pos, sentence, value))

    if slot == 1:
        q = f"{target}班の引渡しに使う受領符号は何ですか？" if ja else f"What receipt code did the {target} team approve for handover?"
        add(.06, f"{target}班は引渡し用の受領符号を {code} と決定した。" if ja else f"The {target} team approved receipt code {code} for handover.", code)
    elif slot == 2:
        q = f"{target}班が予約した説明会場はどこですか？" if ja else f"Which hall did the {target} team book for its briefing?"
        add(.10, f"{target}班が説明会場として予約したのは{old}だった。" if ja else f"The {target} team booked {old} for its briefing.", old)
    elif slot == 3:
        q = f"{target}班の資料受取会場は最終的にどこに決まりましたか？" if ja else f"Which hall is the final file collection venue for the {target} team?"
        add(.07, f"{target}班は資料受取会場に{old}を指定した。" if ja else f"The {target} team designated {old} as its file collection venue.")
        add(.91, f"訂正。{target}班の資料受取会場は{new}に変更された。" if ja else f"Amendment to the {target} team file collection venue: the new location is {new}.", new)
    elif slot == 4:
        q = f"{target}班が使える現在の受領符号は何ですか？" if ja else f"What receipt code is currently valid for the {target} team?"
        add(.05, f"{target}班に受領符号 {code} の使用を認めた。" if ja else f"The {target} team was authorized to use receipt code {code}.")
        add(.93, f"{target}班の受領符号を撤回した。代わりの符号は未定である。" if ja else f"The receipt code for the {target} team has been withdrawn without a replacement.")
    elif slot == 5:
        q = f"{target}班が現在指定している発送会場はどこですか？" if ja else f"What is the current dispatch venue for the {target} team?"
        add(.09, f"{target}班の発送会場には{old}を指定した。" if ja else f"The dispatch venue for the {target} team is {old}.", old)
        add(.86, f"変更通知。{other}班の発送会場を{new}に変更した。" if ja else f"Correction: the dispatch venue for the {other} team is now {new}.")
    elif slot == 6:
        q = f"{target}班の資料受取会場はどこですか？" if ja else f"What is the file collection venue for the {target} team?"
        add(.12, f"{target}班が資料の受取会場に決めたのは{old}である。" if ja else f"The {target} team set its file collection venue at {old}.", old)
        add(.89, f"{target}班は講演会場だけを{extra}に変更した。" if ja else f"The {target} team moved only its lecture venue to {extra}.")
    elif slot == 7:
        q = f"{target}班の受領符号は何ですか？" if ja else f"What receipt code was assigned to the {target} team?"
        add(.15, f"{other}班の受領符号として {code2} を発行した。" if ja else f"Receipt code {code2} was issued to the {other} team.")
        add(.73, f"{target}班の識別色は灰色に決まった。" if ja else f"The {target} team was assigned the color grey.")
    elif slot == 8:
        q = f"{target}班が承認した説明会場はどこですか？" if ja else f"Which briefing venue did the {target} team approve?"
        add(.19, f"{target}班は説明会場として{old}を提案した。" if ja else f"The {target} team proposed {old} for its briefing.")
        add(.79, f"{target}班の説明会場は審議中で、承認された会場はまだない。" if ja else f"The {target} briefing venue remains under discussion; no venue has been approved.")
    else:
        highest = slot == 9
        extreme = ("最大" if highest else "最小") if ja else ("highest" if highest else "lowest")
        q = (f"{target}便の全ての観測所を比較すると、積荷の重量は{extreme}で何kgでしたか？ 数値のみ。" if ja else
             f"Across all stations in the {target} shipment log, what was the {extreme} recorded load in kilograms? Give just the number.")
        weights = rng.sample(range(31, 97), 4)
        answer = max(weights) if highest else min(weights)
        places = ("川上", "北浜", "島中", "谷下") if ja else ("Bankside", "Moorfield", "Hilltop", "Sedgewick")
        for pos, weight, place in zip((.04, .31, .63, .94), weights, places):
            add(pos, f"{target}便の積荷を{place}観測所で計量した結果は {weight} kg だった。" if ja else
                f"The {place} station recorded a load of {weight} kilograms for the {target} shipment.",
                str(weight) if weight == answer else None)
    return target, q, events


def make_case(split, tier, slot, filler_count=None):
    """tier is 4k/8k/16k, slot is 1..10. Length calibration needs no model answers."""
    if split not in ("dev", "test") or tier not in TIERS or not 1 <= slot <= 10:
        raise ValueError((split, tier, slot))
    ti = TIERS.index(tier)
    case_id = f"ctx-{split}-{tier}-{slot:02d}"
    seed = (761000 if split == "dev" else 382000) + 151 * ti + 19 * slot
    ja = (slot + ti + (split == "dev")) % 2 == 0
    style = "copies" if slot == 2 else "numeric" if slot == 7 else "varied"
    if filler_count is None:
        path = HERE / "context_lengths.json"
        counts = json.loads(path.read_text()) if path.is_file() else {}
        # These coarse estimates permit initial calibration on an unmeasured host.
        target_tokens = 4096 << ti
        per_line = {"varied": 34 if ja else 27, "copies": 20 if ja else 16,
                    "numeric": 19 if ja else 17}[style]
        filler_count = counts.get(case_id, round(target_tokens / per_line))
    if filler_count < 10:
        raise ValueError("document needs at least ten filler records")
    target, question, events = facts(seed, slot, ja)
    rng = random.Random(100003 + seed)
    title = f"# 業務引継ぎ台帳 {seed}" if ja else f"# Operations handover register {seed}"
    rows = [title]
    spans, gold_spans = [], []
    cursor = len(title.encode()) + 1
    markers = {round(pos * (filler_count - 1)): (sentence, gold)
               for pos, sentence, gold in events}
    for i in range(filler_count):
        if i in markers:
            sentence, value = markers[i]
            rows.append(sentence)
            data = sentence.encode()
            spans.append([cursor, cursor + len(data)])
            if value is not None:
                start = cursor + data.index(value.encode())
                gold_spans.append([start, start + len(value.encode())])
            cursor += len(data) + 1
        if style == "varied" and i % 23 == 0:
            date = datetime.date(2025, 1, 1) + datetime.timedelta(days=i // 7)
            section = f"## {date.isoformat()} — " + ("当番記録" if ja else "Duty notes")
            rows.append(section)
            cursor += len(section.encode()) + 1
        line = filler_line(rng, i, ja, style)
        rows.append(line)
        cursor += len(line.encode()) + 1
    source = "\n".join(rows)
    gold = next((value for _, _, value in events if value is not None), None)
    return {"id": case_id, "group": GROUPS[slot - 1], "tier": tier, "language": "ja" if ja else "en",
            "source_style": style, "filler_count": filler_count,
            "job": {"task": "answer", "source": source, "question": question},
            "gold": gold, "gold_spans": gold_spans, "evidence_spans": spans}


def cases(split):
    slots = DEV_SLOTS if split == "dev" else {tier: range(1, 11) for tier in TIERS}
    return [make_case(split, tier, slot) for tier, indices in slots.items() for slot in indices]


def run(case, arm, invoke):
    return invoke(case["job"], flags=(FLAGS[arm],))


def direct_value(raw):
    """Give the unguided answer credit for clear plain text or a single JSON answer."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("missing response")
    text = raw.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.S | re.I)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        if text.startswith(("{", "[", "```")):
            raise ValueError("malformed response")
        return text
    if isinstance(parsed, dict):
        parsed = parsed["answer"]
        if parsed is None:
            return ""
    if isinstance(parsed, str):
        return parsed.strip()
    if isinstance(parsed, int) and not isinstance(parsed, bool):
        return str(parsed)
    raise ValueError("missing answer")


def covers(ranges, span):
    return any(lo <= span[0] and span[1] <= hi for lo, hi in ranges)


def grade(case, arm, record):
    source = case["job"]["source"].encode()
    usage = record.get("metrics", {}).get("context") or {}
    retrieval = usage.get("retrieval") or {}
    rounds = retrieval.get("rounds", [])
    # The index coverage measures proposed original segments, before the full-window fallback.
    round_recalls = []
    sent_recalls = []
    for turn in rounds:
        ranges = [(r["start_byte"], r["end_byte"]) for r in turn["ranges"]]
        round_recalls.append(sum(covers(ranges, span) for span in case["evidence_spans"])
                             / len(case["evidence_spans"]))
        if turn["sent"]:
            sent_recalls.append(round_recalls[-1])
    method = usage.get("method")
    # Legacy records output exposes selection counts, not selected IDs. Full-source and
    # lossless readers do have known coverage; mark partial legacy selections unobservable.
    reader_recall = None
    if record.get("metrics", {}).get("calls", 0):
        if method is None or method in ("repeat_dictionary", "repeat_blocks", "numbered_rows"):
            reader_recall = 1.0
        elif method == "indexed_excerpts" and sent_recalls:
            reader_recall = sent_recalls[-1]
        elif method == "evidence_windows" and not usage.get("retained_segments") and record["status"] == "abstained":
            reader_recall = 0.0
    result = {"correct": False, "serious": False, "outcome": record["status"],
              "retrieval_recall": round_recalls[-1] if round_recalls else None,
              "retrieval_recalls": round_recalls, "retrieval_rounds": len(rounds),
              "reader_evidence_recall": reader_recall, "sent_retrieval_recalls": sent_recalls,
              "fallback": retrieval.get("fallback"), "context_method": usage.get("method")}
    evidence = None
    status = record["status"]
    if status in ("held", "error", "refused"):
        return result
    try:
        if arm == "direct" and status == "unverified":
            value = direct_value(record["result"])
            if value.casefold().strip(".。") in ("unknown", "not specified", "not stated", "not provided",
                                                   "no approved venue", "未定", "不明", "記載なし"):
                value = ""
        elif status == "abstained" and record["result"]["answer"] is None:
            value = ""
        elif status == "ok":
            evidence = record["result"]["answer"]
            value = evidence["value"]
            if not isinstance(value, str) or not value:
                raise ValueError("invalid answer")
        else:
            raise ValueError("unexpected status")
    except (KeyError, ValueError, TypeError):
        return dict(result, outcome="invalid")
    if not value:
        good = case["gold"] is None
        return dict(result, correct=good, outcome="correct_abstention" if good else "missed_answer")
    if value != case["gold"]:
        return dict(result, serious=True, outcome="wrong_answer" if case["gold"] else "unsupported_answer")
    if evidence is not None:
        try:
            ranges = [(s["start_byte"], s["end_byte"]) for s in evidence["spans"]]
            valid = ranges and all(type(lo) is int and type(hi) is int and 0 <= lo < hi <= len(source)
                                   and source[lo:hi] == value.encode() for lo, hi in ranges)
            cited = any([lo, hi] in case["gold_spans"] for lo, hi in ranges)
            if not valid or not cited:
                raise ValueError("invalid citation")
        except (KeyError, ValueError, TypeError):
            return dict(result, outcome="wrong_citation", serious=True)
    return dict(result, correct=True, outcome="correct_answer")
