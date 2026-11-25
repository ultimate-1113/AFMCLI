"""Independent paired QA documents, original-byte oracles and final-response scoring.

Only case["job"] is public task input. Each pair changes a single contiguous paragraph.
evidence_spans/change_spans identify that paragraph in the corresponding version. For a
silent omission it identifies the comparison site, not a sentence proving a universal
negative. The two shared contextual paragraphs are recorded separately. Gold offsets
come from a marker placed by the author in the authoritative statement, never from a
document-wide search for a repeated answer string.
"""

from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import random


HERE = Path(__file__).resolve().parent
TIERS = ("4k", "8k", "16k")
GROUPS = ("proposal", "withdrawn", "unassigned", "silent_omission", "other_subject",
          "other_attribute", "redecision", "attribution")

# The genres use different voices. The objects, repeated subject references and marginal
# observations are specific to each independently authored source. None depends on polarity.
EN = {
    "letters": (
        "I have sent you the leaf for {a}. Please keep its copy with the drawing of the {b}.",
        "Your question about the {a} came in yesterday. I have attached the note on {b} for comparison.",
        "Please use the wide margin for comments about the {a}; the marks by the {b} belong to the previous copy.",
        "Thank you for the picture of the {a}. We saw the detail about the {b} once we held it to the light.",
        "The card for the {a} is enclosed. I will put the sheet describing the {b} in with the next batch.",
        "I read the notes on {a} aloud. One reader asked for a larger detail of the {b}.",
        "Keep your copy of the {a} sketch flat. I found the carbon for the {b} in a stiff paper sleeve.",
    ),
    "cards": (
        "Working card. Front: {a}. Reverse: space for an illustration of the {b}.",
        "Owner's copy / {a}. The cross-reference to the {b} belongs in the side pocket.",
        "Handling note. The picture of the {a} was cropped close. Use the wide shot for the {b}.",
        "Inspection copy. File the pencil sketch of the {a} after the detail of the {b}.",
        "Beside the {a} entry a second hand added a mark. Compare the same mark next to the {b}.",
        "Caption slip. Keep the description of the {a} short. Give the {b} drawing enough space for a key.",
        "Duplicate for the workbench. Marks on the {a} photograph are clearer on the print kept opposite the {b}.",
    ),
    "journal": (
        "I spent the morning on the {a} drawing. The detail I needed turned up on the page for the {b}.",
        "By the window I could see a mark on the picture of the {a}. I wrote it beside the sketch of the {b}.",
        "When the book came back the sheet for the {a} was folded over the entry for the {b}. I flattened both.",
        "Tried a finer pencil on the {a} sheet today. It works better than the one I used for the {b}.",
        "Someone had mixed the pencilled notes about the {a} with the copy for the {b}. I put in a divider.",
        "After lunch I found a different view of the {a}. The same view may help when I come to the {b}.",
        "The drawing of the {a} was hard to follow from my desk. I moved it beside the enlargement of the {b}.",
    ),
    "catalog": (
        "Description of the {a}: see the facing photograph. The smaller inset shows the {b}.",
        "Entry under {a}. A marginal pencil stroke points to an earlier drawing of the {b}.",
        "The first album places the {a} at the foot of the page. In this copy the {b} is used as the facing illustration.",
        "A small paper tab marks the {a}. It has the same cut edge as the tab opposite the {b}.",
        "The index has a separate entry for the {a}. The {b} appears in the index of illustrations.",
        "Back of mount: a light outline of the {a}. The envelope for the {b} carries a matching crease.",
        "The detail view of the {a} was copied on thin paper. A fuller view accompanies the record of the {b}.",
    ),
    "minutes": (
        "The meeting began with the drawing of the {a}. A second speaker referred to the working sketch of the {b}.",
        "The group compared the photograph of the {a} with the view of the {b}. Both were passed around the table.",
        "The note-taker left a line after the entry for the {a}. Comments about the {b} go on the facing page.",
        "A reader asked for an enlargement of the {a}. The clerk put its request beside the one for the {b}.",
        "Two copies of the {a} illustration were circulated and a copy of the {b} sketch was pinned by the door.",
        "The discussion returned to the {a}. The desk copy of the {b} supplied the missing orientation mark.",
        "For the minutes, the chair asked for a photograph of the {a} and a legible copy of the caption for the {b}.",
    ),
}
JA = {
    "letters": (
        "{a}の図をお送りします。{b}の写しと一緒に平らな場所でご覧ください。",
        "{a}についてのお便りが届きました。見比べていただけるよう、{b}の欄の控えを添えます。",
        "{a}に関する書き入れは紙の余白にお願いします。{b}の脇の小さい字は前の写しから残ったものです。",
        "{a}の写真を拝見しました。日の当たる窓際で見ると、{b}の細部まで読み取れました。",
        "同封の紙片に{a}の説明があります。{b}の説明を記した紙は次の便でお届けします。",
        "{a}の書き込みを読み上げたところ、{b}の図も大きく見たいという声がありました。",
        "{a}の写しは折らずに置いてください。{b}の控えには厚い台紙を入れてあります。",
    ),
    "cards": (
        "作業控え　表には{a}の見取り図。裏は{b}の写真を貼るため空けておく。",
        "手元用の札　{a}の脇には小さい見出しを設けた。{b}を参照するときは横の紙挟みを開くこと。",
        "取扱欄　{a}の写真は端を切りそろえた。{b}については広く写った方を使う。",
        "照合用　{a}の鉛筆書きを綴じる位置は、{b}の細部を写した紙の次。",
        "{a}の欄の脇に別の筆跡で印がある。{b}の欄に付いた印と照らし合わせた。",
        "写真の説明札　{a}の説明は短く書く。{b}の図には凡例を置く分の余白を取る。",
        "作業台に置く控え　{a}の写真の印は、{b}の向かいに挟んだ紙焼きの方が鮮明。",
    ),
    "journal": (
        "午前は{a}の図を描いた。探していた細部は、{b}の頁に残っていた。",
        "窓の前に持っていくと{a}の写真の筋が見えた。忘れぬうちに{b}の写しの横に書き留めた。",
        "帳面が戻ったとき、{a}の紙が{b}の項の上に折れ込んでいた。両方とも平らにしておく。",
        "きょうは{a}の欄を細い鉛筆で書いた。{b}の細部を書いたときよりも線の重なりがよく見える。",
        "{a}の覚え書きが{b}の控えに紛れていたので、厚紙を一枚しおりに入れた。",
        "昼過ぎ、別の角度から撮った{a}の写真が見つかった。{b}も同じ向きから眺めてみる。",
        "席からは{a}の図の向きが分かりにくい。{b}の拡大写真の横にいったん移した。",
    ),
    "catalog": (
        "{a}については見開きの写真を参照。右下の小さい図に写るのは{b}。",
        "{a}の項。欄外の鉛筆の線は、以前描いた{b}の図の所在を示す。",
        "初めの写真帳では{a}を頁の下に載せた。この写しでは{b}を見開きの図とした。",
        "{a}を示す小さな紙片が余白に貼ってある。{b}の向かいの紙片と切り口がそろう。",
        "{a}には独立した見出しがある。{b}の所在は巻末の挿図索引に載せた。",
        "台紙の裏に{a}の輪郭が薄く残る。{b}の袋にはその輪郭と重なる折り目がある。",
        "{a}の細部を薄紙に写した。全体を見渡せる図は{b}の項に挟んだ。",
    ),
    "minutes": (
        "会は{a}の見取り図を広げて始めた。続いて別の出席者が{b}の下図を取り出した。",
        "一同で{a}の写真と{b}の図を見比べた。机の端から端へ両方を回した。",
        "書記は{a}の項の後を一行空けた。{b}に関する発言は見開きの向かいに記す。",
        "{a}の拡大がほしいという発言があった。書記は{b}の依頼の脇にその旨を書いた。",
        "{a}の図を二枚回覧した。{b}の写しは出入口近くに貼り、帰り際にも見られるようにした。",
        "話が{a}に戻った。机に置いた{b}の控えを参考に、向きを表す印を書き足した。",
        "会議録には{a}の写真と、字が読み取れる大きさの{b}の説明札を添えることになった。",
    ),
}

DIVIDERS = {
    "en": ("Bench copies", "From the first album", "Loose pages", "Notes in the margin",
           "The reading copy", "Details in the photographs", "Inserts from the folder"),
    "ja": ("作業台に置いた控え", "最初の写真帳から", "別添の紙片", "欄外の書き込み",
           "手元に残した写し", "写真の細部", "袋に挟まれた覚え書き"),
}
FOCUS = {
    "en": ("The {subject} file also has a sheet for the {a}.",
           "This copy was kept with the papers for {subject}.",
           "The {a} sketch appears in the {subject} folder too.",
           "Among the {subject} papers, the pencilled index points to the {b}."),
    "ja": ("{subject}の綴りにも{a}の紙がある。", "この写しは{subject}の記録と一緒に保管した。",
           "{subject}の袋にも{a}の図を挟んだ。", "{subject}の古い索引で参照先をたどると{b}の図がある。"),
}


@lru_cache(maxsize=2)
def _materials(split):
    if split not in ("dev", "test"):
        raise ValueError("split must be dev or test")
    rows = json.loads((HERE / "fixtures" / (split + ".json")).read_text(encoding="utf-8"))
    return {(s["tier"], s["pair_index"]): s for s in rows}


def pair_id(split, tier, pair_index):
    return f"index-{split}-{tier}-{pair_index:02d}"


def _seed(label):
    return int.from_bytes(sha256(label.encode("utf-8")).digest()[:8], "big")


def _layout(key, n):
    rng = random.Random(_seed("folio locations " + key))
    # The changed note may occur early, midway through, or late in the folder.
    pivot = rng.uniform(.135, .865)
    return (int(n * rng.uniform(.015, pivot - .055)),
            int(n * pivot), int(n * rng.uniform(pivot + .055, .985)))


def _default_count(key, tier, lang):
    path = HERE / "lengths.json"
    if path.is_file():
        counts = json.loads(path.read_text(encoding="utf-8"))
        if key in counts:
            return counts[key]
    target = 4096 << TIERS.index(tier)
    return max(32, round(target / (40 if lang == "ja" else 27)))


def _filler(material, n, key):
    lang = material["language"]
    templates = (EN if lang == "en" else JA)[material["style"]]
    rng = random.Random(_seed("unmarked back matter " + key))
    headings = DIVIDERS[lang]
    next_divider = rng.randrange(11, 20)
    section = rng.randrange(len(headings))
    # Two original observations are printed in the margins of this particular source.
    observations = {rng.randrange(n // 5, 2 * n // 5): material["notes"][0],
                    rng.randrange(3 * n // 5, 4 * n // 5): material["notes"][1]}
    for i in range(n):
        a, b = rng.sample(material["terms"], 2)
        text = rng.choice(templates).format(a=a, b=b)
        if rng.randrange(7) == 0:
            text += (" " if lang == "en" else "") + rng.choice(FOCUS[lang]).format(
                a=a, b=b, subject=rng.choice(material["focus"]))
        rows = []
        if i == next_divider:
            rows.append(headings[section % len(headings)])
            section += 1
            next_divider = i + rng.randrange(11, 22)
        rows.append(text)
        if i in observations:
            rows.append(observations[i])
        yield rows


def make_case(split, tier, pair_index, positive, filler_count=None):
    if tier not in TIERS or type(pair_index) is not int or type(positive) is not bool:
        raise ValueError("invalid tier, pair index or polarity")
    try:
        s = _materials(split)[tier, pair_index]
    except KeyError:
        raise ValueError(f"no fixture for {split}, {tier}, pair {pair_index}") from None
    key = pair_id(split, tier, pair_index)
    n = _default_count(key, tier, s["language"]) if filler_count is None else filler_count
    if type(n) is not int or n < 20:
        raise ValueError("filler_count must be an integer of at least 20")
    before, marked = s["settled"].split("⟦")
    value, after = marked.split("⟧")
    if not value or not value.strip() == value:
        raise ValueError("invalid authored answer marker")
    decisive = before + value + after if positive else s["unsettled"]
    a, m, b = _layout(key, n)
    inserts = {a: ("context", s["lead"]), m: ("decision", decisive), b: ("context", s["tail"])}
    rows = [s["title"]]
    cursor = len(s["title"].encode()) + 1
    change, context, gold = [], [], []
    for index, additions in enumerate(_filler(s, n, key)):
        if index in inserts:
            kind, paragraph = inserts[index]
            end = cursor + len(paragraph.encode("utf-8"))
            if kind == "decision":
                change.append([cursor, end])
                if positive:
                    start = cursor + len(before.encode("utf-8"))
                    gold.append([start, start + len(value.encode("utf-8"))])
            else:
                context.append([cursor, end])
            rows.append(paragraph)
            cursor = end + 1
        rows.extend(additions)
        cursor += sum(len(row.encode("utf-8")) + 1 for row in additions)
    assert len(change) == 1 and len(context) == 2 and len(gold) == int(positive)
    return {"id": key + ("-p" if positive else "-n"), "pair": key, "pair_index": pair_index,
            "positive": positive, "group": s["group"], "tier": tier, "language": s["language"],
            "filler_count": n, "job": {"task": "answer", "source": "\n".join(rows),
                                       "question": s["question"]}, "gold": value if positive else None,
            "gold_spans": gold, "evidence_spans": change, "change_spans": [x[:] for x in change],
            "context_spans": context}


def cases(split):
    materials = _materials(split)
    return [make_case(split, tier, pair, polarity) for tier in TIERS
            for pair in sorted(i for (t, i) in materials if t == tier) for polarity in (True, False)]


def _citations(case, entry):
    if not isinstance(entry, dict) or not isinstance(entry.get("value"), str) or not entry["value"]:
        return False, 0, 0
    spans = entry.get("spans")
    if not isinstance(spans, list) or not spans:
        return False, 0, 0
    raw = case["job"]["source"].encode("utf-8")
    needle = entry["value"].encode("utf-8")
    official = {tuple(s) for s in case["gold_spans"]}
    found = set()
    matches = 0
    for span in spans:
        if not isinstance(span, dict):
            return False, matches, len(found) - matches
        a, b = span.get("start_byte"), span.get("end_byte")
        if (type(a) is not int or type(b) is not int or not 0 <= a < b <= len(raw)
                or raw[a:b] != needle or (a, b) in found):
            return False, matches, len(found) - matches
        found.add((a, b))
        matches += int((a, b) in official)
    return True, matches, len(found) - matches


def grade(case, record):
    """Only an explicit abstained/null earns a null hit; every supplied nonnull is counted.

    Exact source bytes are checked for every returned citation. A correct settled value
    must cite its authoritative occurrence. AFMCLI can enumerate every occurrence, so
    additional byte-exact but nonauthoritative occurrences are separately reported.
    """
    status = record.get("status") if isinstance(record, dict) else None
    payload = record.get("result") if isinstance(record, dict) else None
    if status == "unverified" and isinstance(payload, str):
        # Unverified text never receives evidence credit, but a direct nonnull is observable.
        try:
            decoded = json.loads(payload)
        except ValueError:
            decoded = payload
        payload = decoded if isinstance(decoded, dict) else {"answer": decoded}
    has_slot = isinstance(payload, dict) and "answer" in payload
    entry = payload.get("answer") if has_slot else None
    nonnull = has_slot and entry is not None
    valid, supported, extras = _citations(case, entry) if nonnull else (None, 0, 0)
    settled = case["gold"] is not None
    if nonnull and not settled:
        outcome = "false_nonnull"
    elif status in ("held", "refused", "error", "backend_error"):
        outcome = status
    elif not has_slot or set(payload) != {"answer"}:
        outcome = "invalid_output"
    elif status == "abstained" and entry is None:
        outcome = "missed_settled" if settled else "correct_null"
    elif status != "ok" or not isinstance(entry, dict) or not isinstance(entry.get("value"), str) or not entry["value"]:
        outcome = "invalid_output"
    elif entry["value"] != case["gold"]:
        outcome = "wrong_settled"
    elif not valid or not supported:
        outcome = "invalid_evidence"
    else:
        outcome = "correct_settled"
    return {"correct": outcome in ("correct_settled", "correct_null"), "outcome": outcome,
            "false_nonnull": outcome == "false_nonnull", "nonnull": nonnull,
            "serious": outcome in ("false_nonnull", "wrong_settled", "invalid_evidence"),
            "spans_verbatim": valid, "authoritative_spans": supported,
            "extra_nonauthoritative_spans": extras, "all_citations_authoritative": bool(valid and supported and not extras)}
