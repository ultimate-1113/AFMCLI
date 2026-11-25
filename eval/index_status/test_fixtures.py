"""Offline checks for panel balance, single-paragraph differences and exact UTF-8 scoring."""

from collections import Counter
import unittest

import fixtures as f


def record(value, *ranges):
    entry = None if value is None else {
        "value": value, "spans": [{"start_byte": a, "end_byte": b} for a, b in ranges]}
    return {"status": "abstained" if entry is None else "ok", "result": {"answer": entry}}


class FixtureChecks(unittest.TestCase):
    def test_balanced_disjoint_business_material(self):
        sources, questions, titles = set(), set(), set()
        for split, n in (("dev", 2), ("test", 8)):
            panel = f.cases(split)
            self.assertEqual(len(panel), len(f.TIERS) * n * 2)
            self.assertEqual(len({c["id"] for c in panel}), len(panel))
            self.assertEqual(len({c["pair"] for c in panel}), len(panel) // 2)
            counts = Counter((c["tier"], c["language"], c["positive"]) for c in panel)
            self.assertEqual(set(counts.values()), {n // 2})
            self.assertEqual(len(counts), 12)
            for tier in f.TIERS:
                groups = [c["group"] for c in panel if c["tier"] == tier and c["positive"]]
                if split == "test":
                    self.assertCountEqual(groups, f.GROUPS)
            for c in panel:
                job = c["job"]
                self.assertEqual(set(job), {"task", "source", "question"})
                self.assertEqual(job["task"], "answer")
                self.assertNotIn("⟦", job["source"])
                self.assertNotIn("⟧", job["source"])
                self.assertNotIn(c["pair"], job["source"])
                self.assertNotIn(job["source"], sources)
                sources.add(job["source"])
                if c["positive"]:
                    self.assertNotIn(job["question"], questions)
                    self.assertNotIn(job["source"].splitlines()[0], titles)
                    questions.add(job["question"])
                    titles.add(job["source"].splitlines()[0])

    def test_minimal_pairs_and_offsets(self):
        bins = set()
        for split in ("dev", "test"):
            panel = f.cases(split)
            for p, n in zip(panel[::2], panel[1::2]):
                self.assertEqual((p["pair"], p["filler_count"], p["job"]["question"]),
                                 (n["pair"], n["filler_count"], n["job"]["question"]))
                self.assertTrue(p["positive"] and not n["positive"])
                self.assertEqual(n["gold_spans"], [])
                self.assertIsNone(n["gold"])
                raw, other = p["job"]["source"].encode(), n["job"]["source"].encode()
                [(a, b)], [(c, d)] = p["evidence_spans"], n["evidence_spans"]
                self.assertEqual(raw[:a], other[:c])
                self.assertEqual(raw[b:], other[d:])
                self.assertNotEqual(raw[a:b], other[c:d])
                [(s, e)] = p["gold_spans"]
                self.assertTrue(a <= s < e <= b)
                self.assertEqual(raw[s:e].decode(), p["gold"])
                self.assertEqual(raw[a:b].count(raw[s:e]), 1)
                self.assertEqual(len(p["context_spans"]), 2)
                self.assertEqual(len(n["context_spans"]), 2)
                for (x, y), (xx, yy) in zip(p["context_spans"], n["context_spans"]):
                    self.assertEqual(raw[x:y], other[xx:yy])
                self.assertEqual(f.grade(p, record(p["gold"], (s, e)))["outcome"], "correct_settled")
                self.assertEqual(f.grade(n, record(None))["outcome"], "correct_null")
                bins.add(int(a / len(raw) * 10))
        self.assertGreaterEqual(len(bins), 5)

    def test_only_official_occurrence_is_an_oracle(self):
        p = f.make_case("test", "4k", 7, True, filler_count=61)
        self.assertEqual(p, f.make_case("test", "4k", 7, True, filler_count=61))
        raw = p["job"]["source"].encode()
        val, [(a, b)] = p["gold"], p["gold_spans"]
        at = raw.index(val.encode())
        old = at, at + len(val.encode())
        self.assertNotEqual(old, (a, b))
        self.assertEqual(f.grade(p, record(val, old))["outcome"], "invalid_evidence")
        combined = f.grade(p, record(val, old, (a, b)))
        self.assertTrue(combined["correct"])
        self.assertEqual(combined["extra_nonauthoritative_spans"], 1)
        self.assertFalse(combined["all_citations_authoritative"])
        self.assertTrue(f.grade(p, record(val, (a, b)))["all_citations_authoritative"])
        self.assertEqual(f.grade(p, record(val, (a, b + 1)))["outcome"], "invalid_evidence")

    def test_null_is_explicit_and_all_nonnulls_count(self):
        p, n = (f.make_case("dev", "4k", 1, state, 49) for state in (True, False))
        self.assertEqual(f.grade(p, record(None))["outcome"], "missed_settled")
        self.assertEqual(f.grade(p, record("別の題", (9, 15)))["outcome"], "wrong_settled")
        self.assertEqual(f.grade(n, record("旧題", (-1, 90000)))["outcome"], "false_nonnull")
        for entry in ("", "旧題", {}, 5, {"value": None}):
            self.assertTrue(f.grade(n, {"status": "ok", "result": {"answer": entry}})["false_nonnull"])
        for status in ("held", "refused", "error"):
            self.assertEqual(f.grade(n, {"status": status, "result": None})["outcome"], status)
        self.assertEqual(f.grade(n, {"status": "ok", "result": {"answer": None}})["outcome"], "invalid_output")
        self.assertEqual(f.grade(n, {"status": "abstained", "result": {}})["outcome"], "invalid_output")
        self.assertTrue(f.grade(n, {"status": "unverified", "result": '{"answer":"old"}'})["false_nonnull"])


if __name__ == "__main__":
    unittest.main()
