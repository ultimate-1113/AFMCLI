"""Matched, research-only combinations for extractive long-document QA.

execute consumes only the ordinary job, an indexed reader, and an independently
initialized structured reader. The same code makes routing and output decisions
in the live run and replay. Passage identifiers are one-based within each actual
prompt. All ranges in notes and answers are half-open original UTF-8 byte ranges.

The independent packet selects original passages by query-term rarity, changes,
decisions and source order. The anchored reader adds indexed citation locations
to that selection. Neither reader sees an upstream proposed value or verdict.
Selective invokes the anchored reader only on a positive with weak scope,
nearby withdrawal, or a later competing decision. Its only permitted override
is an explicit scoped withdrawal. Otherwise the original indexed record stands.

Late fusion accepts equal positives, a witnessed unset, or a packet positive
after an ordinary (unwitnessed) indexed abstention. Two different positives or
insufficient packet evidence are held. Backend failures remain operational
outcomes and an indexed hold remains a hold. Arbitration uses the same pair and
asks a third, fresh reader only for different positives or positive vs witnessed
unset. The reader sees short source excerpts supporting both conclusions and a
relevant change when available. An unsubstantiated third verdict keeps the
late-fusion decision. A backend failure attempting arbitration is reported.
"""

import bisect
import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass


ARMS = ("indexed", "packet", "anchored", "selective", "independent", "arbitrate")
PRIMARY_INPUT = 6200
ARBITER_INPUT = 850
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 32768
MAX_PACKET_BYTES = 5600
MAX_PASSAGES = 14
ROW_CHARS = 340

STOP = set("""a an the of by in on from for as at its is are was were who whom
whose where what which how much many did does do has have been be it to and or
that this according given give identify specify stated asked with currently
current now final finally official officially approved confirmed please
現在 現在の 最終 最終的 正式 どの どこ 何です ですか ますか 何か について
は何 か。 され です いる ですか""".split())
GENERIC_SUBJECT = set("""team group project unit section department record
document proposed assigned approved current official final チーム グループ 部門
計画 対象 資料 現在 正式 最終 承認 決定""".split())
JAPANESE = re.compile(r"[ぁ-ヿ㐀-鿿々ー]+")
WORDS = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")
WITHDRAW = re.compile(
    r"\b(?:withdraw(?:n|s|als?|ing)?|withdrew|retract\w*|revok\w*|revocation|rescind\w*|"
    r"cancel(?:led|ed|lation|s|ing|ling)?|void(?:ed|s)?|annul\w*|invalidat\w*|"
    r"no longer (?:valid|approved|authorized|assigned|in effect))\b|"
    r"撤回|取り消|取消|とりけし|無効|失効|破棄|取り下げ|取りやめ|中止", re.I)
UNKNOWN = re.compile(
    r"\b(?:unknown|pending|unresolved|undecided|unconfirmed|unapproved|unassigned|"
    r"undetermined|unavailable|awaiting (?:a |the )?(?:decision|approval)|"
    r"(?:not|never) (?:yet )?(?:been )?(?:decided|approved|assigned|confirmed|set|issued|known)|"
    r"(?:under|still in) (?:review|discussion|consideration)|on hold|"
    r"(?:no\s+|without\s+(?:an?\s+)?)(?:approved |confirmed |final )?(?:replacement|decision|assignment)|"
    r"only (?:an? )?(?:proposal|candidate|draft))\b|"
    r"未定|不明|未確定|未決|未承認|未発行|未割当|保留|検討中|審議中|"
    r"決まっていな|確定していな|承認されていな|代替.{0,10}(?:ない|なし)", re.I)
DECISION = re.compile(
    r"\b(?:(?:re)?approv\w*|(?:re)?confirm\w*|(?:re)?assign\w*|(?:re)?designat\w*|authori[sz]\w*|"
    r"choose|chose|chosen|decid\w*|select\w*|adopt\w*|ratif\w*|"
    r"settled|now (?:uses|is)|updat\w*|replac\w*|revis\w*|amend\w*|"
    r"supersed\w*|correct\w*|reinstat\w*|restor\w*|instead)\b|"
    r"承認|確定|決定|正式|指定|採用|選定|割り当て|訂正|変更|更新|差し替|改定|再開", re.I)
REVERSED = re.compile(
    r"\b(?:not|never)\s+(?:(?:been|officially|actually)\s+){0,2}"
    r"(?:withdrawn|revoked|cancelled|canceled|retracted|rescinded|voided)\b|"
    r"\b(?:withdrawal|cancellation|revocation)\s+(?:was |is |has been )?"
    r"(?:reversed|rejected|denied|cancelled|canceled)\b|"
    r"\b(?:may|might|could|would|will) (?:yet |also )?(?:be )?(?:revok\w*|withdraw\w*|retract\w*|cancel\w*)\b|"
    r"\b(?:withdrawal|cancellation|revocation) (?:request|proposal|plan)\b|"
    r"\b(?:request\w*|ask\w*|plan\w*|intend\w*) (?:to|for) (?:the )?(?:withdraw\w*|cancel\w*)\b|"
    r"\b(?:discuss\w*|propos\w*|consider\w*|reject\w*|prevent\w*|avoid\w*|risk of) "
    r"(?:the |a |an? possible )?(?:withdraw\w*|revocation|retract\w*|cancel\w*)\b|"
    r"(?:撤回|取消し?|取り消し?|中止)(?:は|を|され|し|さ)?(?:しない|ない|せず|ず|ていない|検討|予定|案)|"
    r"(?:撤回|取消|中止)(?:する|の)?(?:予定|申請|要請|要求|提案|検討)|未撤回|中止のおそれ", re.I)
HISTORIC = re.compile(r"\b(?:originally|initially|formerly|original (?:proposal|plan))\b|当初|最初の案|以前の案", re.I)
REPORT = re.compile(r"\b(?:formerly|previously|historical|example|draft|propos\w*|tentative)\b|当初|過去|以前|草案|仮案|提案|候補", re.I)
HEADER = re.compile(r"^(?:#{1,5}\s|\[[^\]]+\]$|(?:document|section|subject|topic|文書|件名|議題|節)\s*[:：])", re.I)
REFER = re.compile(r"^(?:this|that|its|the same|it\b|this decision)\b|^(?:同件|この|その|先の|当該|前記)", re.I)
SENTENCE = re.compile(r"[^.!?。！？;；\n]+(?:[.!?。！？;；]+|$)")
QUOTED = re.compile(r'''"[^"]*"|“[^”]*”|「[^」]*」|『[^』]*』''')
CLAUSE_BREAK = re.compile(r"[,，、]|\b(?:but|while|whereas|although|and|however)\b|"
                         r"一方で?|ただし|しかし|他方|これに対し", re.I)


def normalized(text):
    return unicodedata.normalize("NFKC", text).casefold()


def terms(text):
    text = normalized(text)
    found = set(WORDS.findall(text)) - STOP
    for run in JAPANESE.findall(text):
        if len(run) == 1:
            if "㐀" <= run <= "鿿":
                found.add(run)
            continue
        for n in (2, 3):
            found.update(run[i:i + n] for i in range(len(run) - n + 1))
    return found - STOP


def has_key(text, key):
    text, key = normalized(text), normalized(key)
    if not key:
        return False
    if key.isascii() and key[0].isalnum() and key[-1].isalnum():
        return re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", text) is not None
    return key in text


def cues(text):
    clean = mask_names(text)
    return bool(WITHDRAW.search(clean) or UNKNOWN.search(clean)), bool(DECISION.search(clean))


def mask_names(text):
    # Keep indices into the quoted passage intact while ignoring status words in names.
    return QUOTED.sub(lambda m: " " * len(m.group()), text)


@dataclass(frozen=True)
class Passage:
    row: int
    start: int
    text: str

    @property
    def end(self):
        return self.start + len(self.text.encode())


class Corpus:
    def __init__(self, job):
        self.question = job["question"]
        self.raw = job["source"].encode()
        self.rows = []
        self.reason = None
        if len(self.raw) > MAX_SOURCE_BYTES:
            self.reason = "source_limit"
            return
        start = 0
        for physical in job["source"].splitlines(keepends=True):
            body = physical.rstrip("\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029")
            cursor = 0
            offset = start
            while cursor < len(body):
                stop = min(cursor + ROW_CHARS, len(body))
                if stop < len(body):
                    breaks = list(re.finditer(r"[。！？.!?;；,、\s]", body[cursor + ROW_CHARS // 2:stop]))
                    if breaks:
                        stop = cursor + ROW_CHARS // 2 + breaks[-1].end()
                piece = body[cursor:stop]
                if piece.strip():
                    self.rows.append(Passage(len(self.rows), offset, piece))
                    if len(self.rows) > MAX_ROWS:
                        self.reason = "row_limit"
                        return
                offset += len(piece.encode())
                cursor = stop
            start += len(physical.encode())
        if not self.rows:
            self.reason = "no_source_passages"
            return
        self.starts = [row.start for row in self.rows]
        query = terms(self.question)
        hits = [terms(p.text) & query for p in self.rows]
        counts = Counter(term for hit in hits for term in hit)
        weights = {term: math.log1p(len(self.rows) / freq) for term, freq in counts.items()}
        self.direct = [sum(weights[t] for t in sorted(hit)) for hit in hits]
        self.header = []
        header = None
        for p in self.rows:
            if HEADER.search(p.text.strip()):
                header = p.row
            self.header.append(header)
        self.score = list(self.direct)
        for i, header in enumerate(self.header):
            if header is not None and i != header:
                self.score[i] += self.direct[header] * .35
        self.flags = [cues(p.text) for p in self.rows]

    def locate(self, start, end):
        i = bisect.bisect_right(self.starts, start) - 1
        if i >= 0 and start < end <= self.rows[i].end:
            return i
        return None

    def context_score(self, i):
        score = self.score[i]
        for near in (i - 1, i + 1):
            if 0 <= near < len(self.rows) and self.header[near] == self.header[i]:
                score = max(score, self.score[near] * .45)
        return score

    def packet(self, anchors):
        indexes = range(len(self.rows))
        ranked = sorted(indexes, key=lambda i: (self.score[i], i), reverse=True)
        evidence = [i for i in indexes if any(self.flags[i])]
        contextual = {i: self.context_score(i) for i in evidence}
        by_cue = sorted(evidence, key=lambda i: (contextual[i], i), reverse=True)
        relevant = [i for i in evidence if contextual[i] > 0]
        latest = list(reversed(relevant or evidence))
        neg = [i for i in by_cue if self.flags[i][0]]
        positive = [i for i in by_cue if self.flags[i][1] and not self.flags[i][0]]
        anchor_ids = sorted({a[0] for a in anchors}, key=lambda i: (self.score[i], i), reverse=True)
        priority = []
        priority.extend(anchor_ids[:2])
        priority.extend(latest[:2])
        priority.extend(neg[:2])
        priority.extend(positive[:2])
        priority.extend(ranked[:4])
        priority.extend(by_cue[:5])
        if anchor_ids:
            priority.append(max(anchor_ids))
        priority.extend(i for i in reversed(range(len(self.rows))) if self.direct[i] > 0)
        primary = list(dict.fromkeys(priority))[:10]
        neighbors = [j for i in primary for j in (i - 1, i + 1) if 0 <= j < len(self.rows)]
        # Preserve room for immediate references even in a long repetitive document.
        candidates = primary + [self.header[i] for i in primary if self.header[i] is not None]
        candidates += neighbors[:6] + priority + [0, len(self.rows) - 1] + ranked
        selected = {}
        size = 0
        for i in dict.fromkeys(candidates):
            p = self.rows[i]
            cost = p.end - p.start
            if size + cost > MAX_PACKET_BYTES:
                continue
            selected[i] = p
            size += cost
            if len(selected) >= MAX_PASSAGES:
                break
        return [selected[i] for i in sorted(selected)]

    def suspicion(self, anchors, value):
        ids = {a[0] for a in anchors}
        reasons = []
        top = max(self.score)
        if not ids or not any(self.score[i] > 0 and self.score[i] >= .25 * top for i in ids):
            reasons.append("weak_cited_scope")
        if not ids:
            return reasons
        for i in ids:
            if self.flags[i][0]:
                reasons.append("cited_unset_cue")
                break
            if any(self.flags[j][0] and (self.score[j] > 0 or REFER.search(self.rows[j].text.strip()))
                   for j in range(max(0, i - 2), min(len(self.rows), i + 3))):
                reasons.append("nearby_unset_cue")
                break
        top_anchor = max(self.score[j] for j in ids)
        scoped = [i for i in ids if self.score[i] > 0 and self.score[i] >= .5 * top_anchor]
        last = max(scoped or ids)
        for i in range(last + 1, len(self.rows)):
            negative, decision = self.flags[i]
            if not (negative or decision) or not negative and value in self.rows[i].text:
                continue
            if self.score[i] > 0 and self.score[i] >= .45 * top:
                reasons.append("later_competing_decision")
                break
        return reasons


def schema(scoped):
    props = {"state": {"type": "string", "enum": ["set", "withdrawn", "unknown", "missing"]},
             "passage": {"type": "integer"}, "quote": {"type": "string"}, "value": {"type": "string"}}
    if scoped:
        props.update(subject={"type": "string"}, field={"type": "string"})
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


PRIMARY_SCHEMA = schema(True)
THIRD_SCHEMA = schema(False)
PRIMARY_GUIDE = (
    "Answer from the numbered original passages in source order; gaps may exist. Track the exact "
    "subject, property and time in the question. A proposal, example or notice about another subject "
    "or property cannot replace an applicable decision. set: copy the shortest exact answer as value. "
    "withdrawn: the requested decision was explicitly revoked without replacement. unknown: the source "
    "explicitly leaves that fact unresolved. missing: insufficient evidence. For each conclusion copy "
    "one contiguous decisive clause (including the subject and property when possible) as quote and its "
    "passage number. For withdrawn/unknown also copy the shortest identifying subject name and property "
    "word shared by the question and evidence into subject and field. For missing use passage 0. "
    "Leave unused strings empty. Preserve original spelling and characters."
)
THIRD_GUIDE = (
    "Read these numbered source excerpts in order. Which decision governs the exact subject and field "
    "asked about? set: copy the exact value. withdrawn/unknown require an explicit applicable notice. "
    "Otherwise missing. Give the passage number and one exact supporting clause. Leave unused strings empty."
)


def prompt(guide, question, pieces):
    material = [[i, p.text] for i, p in enumerate(pieces, 1)]
    return guide + "\n" + json.dumps({"question": question, "passages": material},
                                     ensure_ascii=False, separators=(",", ":"))


def ranges(pieces):
    return [[p.start, p.end] for p in pieces]


def held():
    return {"status": "held", "result": None}


def answer(value, start, end):
    return {"status": "ok", "result": {"answer": {"value": value,
            "spans": [{"start_byte": start, "end_byte": end}]}}}


@dataclass
class Reading:
    kind: str
    record: dict
    reason: str
    row: int = -1
    witness: tuple = ()
    keys: tuple = ()

    @property
    def value(self):
        return self.record["result"]["answer"]["value"] if self.kind == "set" else None

    @property
    def unset(self):
        return self.kind in ("withdrawn", "unknown")

    def note(self):
        note = {"kind": self.kind, "reason": self.reason}
        if self.witness:
            note["witness"] = list(self.witness)
        return note


def insufficient(reason):
    return Reading("held", held(), reason)


def indexed_reading(record, corpus):
    status = record.get("status")
    payload = record.get("result")
    if status == "abstained" and isinstance(payload, dict) and payload == {"answer": None}:
        return Reading("abstained", record, "indexed_null_without_witness"), []
    if status != "ok":
        if status in ("held", "error", "backend_error", "refused"):
            return Reading(status, record, "indexed_operational_outcome"), []
        return insufficient("invalid_indexed_record"), []
    entry = payload.get("answer") if isinstance(payload, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("value"), str) or not entry["value"]:
        return insufficient("invalid_indexed_answer"), []
    value = entry["value"].encode()
    spans = entry.get("spans")
    if not isinstance(spans, list) or not spans:
        return insufficient("missing_indexed_citations"), []
    anchors = []
    seen = set()
    for span in spans:
        if not isinstance(span, dict):
            return insufficient("invalid_indexed_citation"), []
        start, end = span.get("start_byte"), span.get("end_byte")
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(corpus.raw)
                or (start, end) in seen or corpus.raw[start:end] != value):
            return insufficient("invalid_indexed_citation"), []
        seen.add((start, end))
        row = corpus.locate(start, end)
        if row is not None:
            anchors.append((row, start, end))
    if not anchors:
        return insufficient("indexed_value_outside_one_passage"), []
    high = max(corpus.score[a[0]] for a in anchors)
    plausible = [a for a in anchors if corpus.score[a[0]] >= .65 * high]
    best = max(plausible, key=lambda a: (not corpus.flags[a[0]][0], corpus.flags[a[0]][1],
                                         corpus.score[a[0]], a[1]))
    return Reading("set", record, "checked_indexed_value", best[0], best[1:]), anchors


def scope_key(key, question):
    if not isinstance(key, str) or key != key.strip() or not key or len(key) > 100:
        return False
    k = normalized(key)
    if k in STOP or k in GENERIC_SUBJECT or not any(c.isalnum() for c in k):
        return False
    if k.isascii() and len(k) < 2:
        return False
    return has_key(question, key)


def later_set(corpus, row, end, subject, field):
    """A later direct affirmative for both lexical keys defeats a proposed current null."""
    for p in corpus.rows[row:]:
        if not corpus.flags[p.row][1]:
            continue
        text = corpus.raw[max(p.start, end):p.end].decode() if p.row == row else p.text
        for part in SENTENCE.finditer(text):
            part = part.group()
            negative, decision = cues(part)
            if decision and not negative and not REPORT.search(part) and has_key(part, subject) and has_key(part, field):
                return True
    return False


def null_witness(corpus, pieces, p, quote, offset, requested, keys):
    subject, field = keys
    if (not scope_key(subject, corpus.question) or not scope_key(field, corpus.question)
            or has_key(subject, field) or has_key(field, subject) or HISTORIC.search(corpus.question)):
        return None
    clean = mask_names(quote)
    if REVERSED.search(clean):
        return None
    withdrawal = WITHDRAW.search(clean)
    if requested == "withdrawn" and not withdrawal or not (withdrawal or UNKNOWN.search(clean)):
        return None
    cue = withdrawal or UNKNOWN.search(clean)
    # Sentence context, including a header actually shown to this reader, can
    # bind a short literal quote. Do not join unrelated source passages.
    for sentence in SENTENCE.finditer(p.text):
        left, right = sentence.span()
        if not left <= offset + cue.start() < right:
            continue
        context = sentence.group()
        relative = offset + cue.start() - left
        clause_start, clause_end = 0, len(context)
        for split in CLAUSE_BREAK.finditer(mask_names(context)):
            if split.end() <= relative:
                clause_start = split.end()
            elif split.start() > relative:
                clause_end = split.start()
                break
        clause = context[clause_start:clause_end]
        if REVERSED.search(context) or not has_key(clause, field):
            return None
        scope = has_key(clause, subject)
        if not scope and clause_start:
            # A single leading "For <subject>," can supply a subject for an
            # otherwise self-contained property clause. A competing statement cannot.
            preface = normalized(context[:clause_start]).strip(" ,，、")
            s = normalized(subject)
            scope = preface in ("for " + s, "regarding " + s, "as for " + s,
                                s + "について", s + "に関して", s + "は")
        header = corpus.header[p.row]
        if not scope and header is not None:
            scope = any(x.row == header and has_key(x.text, subject) for x in pieces)
            # Inherited scope may bind a reference; a new, explicitly named
            # subject on the same line requires a direct match.
            scope = scope and bool(REFER.search(clause.strip()) or clause.strip().casefold().startswith(field.casefold()))
        if not scope:
            return None
        # A replacement or reinstatement within the same statement makes an
        # unqualified withdrawal ambiguous. The reader must establish its value.
        tail = context[offset + cue.end() - left:]
        if re.search(r"\b(?:reinstated|restored|replaced (?:it |this )?(?:by|with)|"
                     r"replacement (?:is|was) (?!not\b)|instead (?:uses|approved))\b|"
                     r"再度承認|再承認|代わりに.{0,18}(?:決定|指定|承認)", tail, re.I):
            return None
        end = p.start + len(p.text[:right].encode())
        if later_set(corpus, p.row, end, subject, field):
            return None
        return requested
    return None


def checked(corpus, pieces, obj, keys=None):
    if not isinstance(obj, dict):
        return insufficient("invalid_output")
    state, n, quote, value = (obj.get(k) for k in ("state", "passage", "quote", "value"))
    if state == "missing":
        return insufficient("reader_missing")
    if (state not in ("set", "withdrawn", "unknown") or type(n) is not int or not 1 <= n <= len(pieces)
            or not isinstance(quote, str) or not quote.strip() or not isinstance(value, str)):
        return insufficient("invalid_claim")
    p = pieces[n - 1]
    at = p.text.find(quote)
    if at < 0 or p.text.find(quote, at + 1) >= 0:
        return insufficient("quote_not_unique_in_passage")
    start = p.start + len(p.text[:at].encode())
    witness = (start, start + len(quote.encode()))
    if corpus.raw[slice(*witness)] != quote.encode():
        return insufficient("quote_offset_mismatch")
    if state == "set":
        pos = quote.find(value)
        if (not value.strip() or value != value.strip() or value == quote or pos < 0
                or quote.find(value, pos + 1) >= 0):
            return insufficient("value_not_unique_in_clause")
        if not re.search(r"\b(?:status|state)\b|状況|状態", corpus.question, re.I):
            if UNKNOWN.fullmatch(value) or WITHDRAW.fullmatch(value):
                return insufficient("status_used_as_value")
        a = start + len(quote[:pos].encode())
        b = a + len(value.encode())
        if corpus.raw[a:b] != value.encode():
            return insufficient("value_offset_mismatch")
        return Reading("set", answer(value, a, b), "exact_value_in_exact_clause", p.row, witness)
    if value:
        return insufficient("nonnull_value_for_unset")
    keys = keys if keys is not None else (obj.get("subject"), obj.get("field"))
    kind = null_witness(corpus, pieces, p, quote, at, state, keys)
    if not kind:
        return insufficient("unsubstantiated_current_unset")
    return Reading(kind, {"status": "abstained", "result": {"answer": None}},
                   "scoped_explicit_unset", p.row, witness, keys)


def read(corpus, pieces, io, third=False, keys=None):
    if not pieces:
        return insufficient("empty_packet")
    guide, spec, cap = ((THIRD_GUIDE, THIRD_SCHEMA, ARBITER_INPUT) if third else
                        (PRIMARY_GUIDE, PRIMARY_SCHEMA, PRIMARY_INPUT))
    obj = io.read(prompt(guide, corpus.question, pieces), spec, max_input=cap)
    if obj is None:
        status = getattr(io, "last_read_status", "unavailable")
        if status in ("backend_error", "timeout", "count_error", "error", "refused"):
            out = status if status in ("error", "refused") else "backend_error"
            return Reading(out, {"status": out, "result": None}, status)
        return insufficient("invalid_output" if status == "ok" else status)
    return checked(corpus, pieces, obj, keys)


def fuse(old, second):
    if old.kind in ("error", "backend_error", "refused", "held"):
        return old, "indexed_" + old.kind
    if second.kind in ("error", "backend_error", "refused", "held"):
        return second, "packet_" + second.kind
    if second.unset:
        return second, "supported_null_veto" if old.kind == "set" else "supported_null"
    if old.kind == "abstained" and second.kind == "set":
        return second, "recover_indexed_abstention"
    if old.kind == second.kind == "set" and old.value == second.value:
        return second, "agree_value"
    return insufficient("different_values"), "different_values"


def later_indexed_mention(corpus, anchors, withdrawal):
    """An explicit later same-subject/field indexed citation blocks a selective veto."""
    subject, field = withdrawal.keys
    for row, start, end in anchors:
        if start < withdrawal.witness[1]:
            continue
        p = corpus.rows[row]
        offset = len(corpus.raw[p.start:start].decode())
        for sentence in SENTENCE.finditer(p.text):
            text = sentence.group()
            if (sentence.start() <= offset < sentence.end() and has_key(text, subject) and has_key(text, field)
                    and not cues(text)[0] and not REPORT.search(text)):
                return True
    return False


def short_excerpt(corpus, p, a, b, limit=124):
    """Shorten one actual source passage around a verified byte range."""
    left = len(corpus.raw[p.start:a].decode())
    right = left + len(corpus.raw[a:b].decode())
    if right - left > limit:
        return None
    for sentence in SENTENCE.finditer(p.text):
        x, y = sentence.span()
        if x <= left and right <= y and y - x <= limit:
            return Passage(p.row, p.start + len(p.text[:x].encode()), p.text[x:y])
    x = max(0, min(left - (limit - (right - left)) // 2, len(p.text) - limit))
    y = min(len(p.text), x + limit)
    return Passage(p.row, p.start + len(p.text[:x].encode()), p.text[x:y])


def arbitration_packet(corpus, old, second):
    pieces = []
    for reading in (old, second):
        p = corpus.rows[reading.row]
        a, b = reading.witness
        if len(corpus.raw[a:b].decode()) > 124 and reading is second:
            if reading.kind == "set":
                span = reading.record["result"]["answer"]["spans"][0]
                a, b = span["start_byte"], span["end_byte"]
            else:
                # If the exact null clause is long, retain the actual status predicate.
                text = corpus.raw[a:b].decode()
                match = WITHDRAW.search(mask_names(text)) or UNKNOWN.search(mask_names(text))
                if not match:
                    return []
                base = a
                a = base + len(text[:match.start()].encode())
                b = base + len(text[:match.end()].encode())
        piece = short_excerpt(corpus, p, a, b)
        if piece is None:
            return []
        pieces.append(piece)
    changes = []
    for p in corpus.rows:
        relevance = corpus.context_score(p.row)
        if not relevance or not any(corpus.flags[p.row]):
            continue
        clean = mask_names(p.text)
        for pattern in (WITHDRAW, UNKNOWN, DECISION):
            for match in pattern.finditer(clean):
                a = p.start + len(p.text[:match.start()].encode())
                b = p.start + len(p.text[:match.end()].encode())
                if not any(x.start <= a and b <= x.end for x in pieces):
                    changes.append((relevance, a, b, p))
    if changes:
        high = max(item[0] for item in changes)
        _, a, b, p = max((item for item in changes if item[0] >= .65 * high), key=lambda item: item[1])
        piece = short_excerpt(corpus, p, a, b, limit=96)
        if piece:
            pieces.append(piece)
    return sorted(set(pieces), key=lambda p: (p.start, p.end))


def execute(arm, job, io):
    if arm not in ARMS:
        raise ValueError("unknown QA arm " + str(arm))
    if arm == "indexed":
        return io.indexed(job), {"decision": "indexed"}
    base = io.indexed(job) if arm != "packet" else None
    corpus = Corpus(job)
    if corpus.reason:
        keep = base is not None and (arm == "selective" or base.get("status") in
                                     ("held", "error", "backend_error", "refused"))
        return (base if keep else held()), {"decision": corpus.reason}
    old, anchors = indexed_reading(base, corpus) if base is not None else (None, [])
    notes = {}
    if old is not None:
        notes["indexed"] = old.note()
    if arm in ("anchored", "selective") and old.kind in ("error", "backend_error", "refused", "held"):
        notes["decision"] = "indexed_" + old.kind
        return old.record, notes
    if arm == "selective":
        if old.kind != "set":
            notes["decision"] = "keep_indexed_nonpositive"
            return base, notes
        triggers = corpus.suspicion(anchors, old.value)
        notes["triggers"] = triggers
        if not triggers:
            notes["decision"] = "keep_indexed_unflagged"
            return base, notes
    guided = anchors if arm in ("anchored", "selective") else []
    if guided:
        notes["anchors"] = [[a, b] for _, a, b in guided]
    pieces = corpus.packet(guided)
    second = read(corpus, pieces, io)
    notes["packet"] = dict(second.note(), ranges=ranges(pieces))
    if arm in ("packet", "anchored"):
        notes["decision"] = "fresh_" + second.kind
        return second.record, notes
    if arm == "selective":
        override = second.kind == "withdrawn" and not later_indexed_mention(corpus, anchors, second)
        notes["decision"] = "withdrawal_override" if override else "keep_indexed_after_check"
        return (second.record if override else base), notes
    combined, decision = fuse(old, second)
    notes["decision"] = decision
    conflict = old.kind == "set" and (second.unset or second.kind == "set" and old.value != second.value)
    if arm != "arbitrate" or not conflict:
        return combined.record, notes
    pieces = arbitration_packet(corpus, old, second)
    if not pieces:
        notes["third"] = {"kind": "held", "reason": "both_source_witnesses_did_not_fit", "ranges": []}
        return combined.record, notes
    third = read(corpus, pieces, io, third=True, keys=second.keys or ("", ""))
    notes["third"] = dict(third.note(), ranges=ranges(pieces))
    if third.kind in ("backend_error", "error", "refused"):
        notes["decision"] = "arbiter_" + third.kind
        return third.record, notes
    if (third.kind == "set" and third.value in (old.value, second.value)) or (third.unset and second.unset):
        notes["decision"] = "arbiter_" + third.kind
        return third.record, notes
    notes["decision"] = "arbiter_inconclusive_" + decision
    return combined.record, notes
