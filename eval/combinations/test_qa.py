"""Synthetic, model-free regression checks for composition and original UTF-8 citations."""

import json
import unittest

try:
    from . import qa
except ImportError:
    import qa


def job(source, question="Which platform was assigned to Orion?"):
    return {"task": "grounded_qa", "question": question, "source": source}


def original(value, source, last=False):
    raw = source.encode()
    pos = raw.rindex(value.encode()) if last else raw.index(value.encode())
    return qa.answer(value, pos, pos + len(value.encode()))


def claim(quote, value="", state="set", keys=("Orion", "platform")):
    def respond(material):
        n = next(n for n, text in material if quote in text)
        return {"state": state, "passage": n, "quote": quote, "value": value,
                "subject": keys[0], "field": keys[1]}
    return respond


class Fake:
    def __init__(self, baseline, *responses, status="ok"):
        self.baseline = baseline
        self.responses = list(responses)
        self.last_read_status = status
        self.calls = []
        self.lookups = 0

    def indexed(self, item):
        self.lookups += 1
        return self.baseline

    def read(self, prompt, schema, *, max_input):
        material = json.loads(prompt.split("\n", 1)[1])
        self.calls.append((prompt, material["passages"], schema, max_input))
        response = self.responses.pop(0)
        return response(material["passages"]) if callable(response) else response


class Cases(unittest.TestCase):
    def setUp(self):
        self.earlier = "Orion's platform was assigned Azure."
        self.newer = "Orion's platform was reassigned to Côte."
        self.void = "Orion's platform assignment was withdrawn."
        self.pending = "Orion's platform is pending."
        self.unknown = {"status": "abstained", "result": {"answer": None}}

    def test_independent_packet_offsets_and_matched_prompts(self):
        item = job("記録🔎\r\nAzure appeared in a draft.\r\n" + self.earlier)
        base = original("Azure", item["source"])
        responses = []
        results = []
        for arm in ("packet", "independent", "arbitrate"):
            io = Fake(base, claim(self.earlier, "Azure"))
            rec, notes = qa.execute(arm, item, io)
            self.assertEqual(rec, original("Azure", item["source"], last=True))
            self.assertEqual(io.lookups, int(arm != "packet"))
            self.assertEqual(len(io.calls), 1)
            self.assertEqual(io.calls[0][-1], 6200)
            for (_, shown), (a, b) in zip(io.calls[0][1], notes["packet"]["ranges"]):
                self.assertEqual(item["source"].encode()[a:b].decode(), shown)
            responses.append(io.calls[0][0])
            results.append(rec)
        self.assertEqual(len(set(responses)), 1)
        self.assertEqual(results[1], results[2])

    def test_selective_keeps_unflagged_and_overrides_only_explicit_withdrawal(self):
        base = original("Azure", self.earlier)
        for tail, state, expected in [(None, None, base), (self.void, "withdrawn", self.unknown),
                                      (self.pending, "unknown", base),
                                      ("Lyra's platform was withdrawn.", "withdrawn", base),
                                      ("Orion's badge decision was withdrawn.", "withdrawn", base)]:
            item = job(self.earlier + ("\n" + tail if tail else ""))
            responses = [claim(tail, state=state)] if tail else []
            io = Fake(base, *responses)
            rec, notes = qa.execute("selective", item, io)
            self.assertEqual(rec, expected)
            self.assertEqual(len(io.calls), int(tail is not None))
            if tail:
                anchored = Fake(base, claim(tail, state=state))
                qa.execute("anchored", item, anchored)
                self.assertEqual(anchored.calls[0][0], io.calls[0][0])
            if state == "unknown":
                self.assertEqual(notes["packet"]["kind"], "unknown")

    def test_structured_null_must_be_exact_current_and_scoped(self):
        bad = [
            "Orion's platform was approved, while Lyra's platform was withdrawn.",
            "Orion's platform was approved and Lyra's platform was withdrawn.",
            "Orion's platform was not withdrawn.",
            "Orion's platform might be withdrawn.",
            "Orion's platform withdrawal request is under review.",
            "Orion's platform is named “Withdrawn”.",
            self.void + "\n" + self.newer,
            "Orion's platform was withdrawn but its replacement is Quartz.",
        ]
        for source in bad:
            q = source.split("\n")[0]
            io = Fake(self.unknown, claim(q, state="withdrawn"))
            rec, _ = qa.execute("packet", job(source), io)
            self.assertEqual(rec["status"], "held", source)
        q = "「北杉」の会場は撤回された。"
        item = job("🦊\r\n" + q, "北杉の会場はどこですか？")
        rec, notes = qa.execute("packet", item, Fake(None, claim(q, state="withdrawn", keys=("北杉", "会場"))))
        self.assertEqual(rec, self.unknown)
        a, b = notes["packet"]["witness"]
        self.assertEqual(item["source"].encode()[a:b].decode(), q)

    def test_no_joins_no_ambiguous_quotes_no_status_as_value(self):
        item = job("Orion: Azure.\nLyra: Rose.\nOrion: Azure. Orion: Azure.")
        for quote, value, n in [("Orion: Azure. Lyra: Rose.", "Azure", 1),
                                ("Orion: Azure.", "Azure", 3), ("Orion: Azure.", "Rose", 1)]:
            rec, _ = qa.execute("packet", item, Fake(None, {"state": "set", "passage": n,
                                      "quote": quote, "value": value}))
            self.assertEqual(rec["status"], "held")
        rec, _ = qa.execute("packet", job(self.pending), Fake(None, claim(self.pending, "pending")))
        self.assertEqual(rec["status"], "held")

    def test_late_fusion_and_third_only_on_supported_conflict(self):
        item = job(self.earlier + "\n" + self.newer + "\n" + self.void)
        base = original("Azure", item["source"])
        for second, expected, third in [
            (claim(self.newer, "Côte"), "held", claim(self.newer, "Côte")),
            (claim(self.void, state="withdrawn"), "abstained", claim(self.void, state="withdrawn")),
        ]:
            rec, _ = qa.execute("independent", item, Fake(base, second))
            self.assertEqual(rec["status"], expected)
            io = Fake(base, second, third)
            rec, notes = qa.execute("arbitrate", item, io)
            self.assertEqual([call[-1] for call in io.calls], [6200, 850])
            self.assertIn(self.earlier, [s for _, s in io.calls[1][1]])
            self.assertIn(self.void, [s for _, s in io.calls[1][1]])
            self.assertIn(self.newer, [s for _, s in io.calls[1][1]])
            for (_, shown), (a, b) in zip(io.calls[1][1], notes["third"]["ranges"]):
                self.assertEqual(item["source"].encode()[a:b].decode(), shown)
            self.assertEqual(rec["status"], "ok" if expected == "held" else "abstained")
        recoverable = job(self.earlier + "\n" + self.newer)
        io = Fake(self.unknown, claim(self.newer, "Côte"))
        rec, notes = qa.execute("arbitrate", recoverable, io)
        self.assertEqual(rec, original("Côte", recoverable["source"]))
        self.assertEqual(len(io.calls), 1)
        self.assertEqual(notes["decision"], "recover_indexed_abstention")

    def test_preserve_operational_outcomes_and_indexed_control(self):
        item = job(self.earlier)
        for status, expected in [("over_budget", "held"), ("invalid_output", "held"),
                                 ("timeout", "backend_error"), ("count_error", "backend_error"),
                                 ("backend_error", "backend_error")]:
            rec, _ = qa.execute("packet", item, Fake(None, None, status=status))
            self.assertEqual(rec["status"], expected)
        for status in ("held", "refused", "backend_error", "error"):
            original_record = {"status": status, "result": None, "metrics": {"existing": True}}
            for arm in ("indexed", "anchored", "selective", "independent", "arbitrate"):
                io = Fake(original_record, claim(self.earlier, "Azure"))
                rec, _ = qa.execute(arm, item, io)
                self.assertIs(rec, original_record)
                self.assertEqual(len(io.calls), int(arm in ("independent", "arbitrate")))

    def test_anchoring_includes_an_otherwise_absent_cited_passage(self):
        rows = ["Shelf archive entry {}.".format(i) for i in range(100)]
        rows[45] = "The designation here was Sunburst."
        for i in range(5, 26):
            rows[i] = "Orion's platform was assigned marker {}.".format(i)
        rows[-3] = self.void
        item = job("\n".join(rows))
        base = original("Sunburst", item["source"])
        first, anchored = Fake(base, None), Fake(base, None)
        qa.execute("packet", item, first)
        qa.execute("anchored", item, anchored)
        self.assertNotIn(rows[45], [s for _, s in first.calls[0][1]])
        self.assertIn(rows[45], [s for _, s in anchored.calls[0][1]])
        self.assertIn(self.void, [s for _, s in anchored.calls[0][1]])


if __name__ == "__main__":
    unittest.main()
