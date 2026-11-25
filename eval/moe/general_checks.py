"""Offline probes for routing, AFMCLI result shapes, citation offsets and factual rewrites."""

import json
import unittest

import general


def span(text, value, at=None):
    raw = text.encode("utf-8")
    start = raw.index(value.encode("utf-8")) if at is None else at
    return {"value": value, "spans": [{"start_byte": start, "end_byte": start + len(value.encode("utf-8"))}]}


class GeneralChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dev = {c["id"]: c for c in general.cases("dev")}

    def test_route_depends_only_on_job_and_falls_back(self):
        job = {"task": "answer", "source": "The badge is round.", "question": "What shape is it?"}
        for arm, expected in (("direct", ("--direct",)), ("current", ()), ("selected", ()),
                              ("common", ("--skill", general.COMMON))):
            self.assertEqual(general.run({"job": job}, arm, lambda j, flags=(): (j, flags)), (job, expected))
        self.assertEqual(general.selected_hint({**job, "source": "North: copper.\nSouth: steel."}),
                         "subject-scope")
        self.assertEqual(general.selected_hint({**job, "source": "The north claim was withdrawn."}),
                         "revision-scope")
        self.assertIsNone(general.selected_hint({"task": "ask", "source": "Several claims were withdrawn."}))

    def test_exact_utf8_and_supporting_occurrence(self):
        source = "赤: とき。\n青: とき。"
        job = dict(task="answer", source=source, question="青は？")
        case = dict(job=job, gold=["とき"], evidence={"answer": ["青: とき。"]})
        first = span(source, "とき")
        second = span(source, "とき", source.encode().rindex("とき".encode()))
        score = lambda x: general.grade(case, "current", {"status": "ok", "result": {"answer": x}})
        self.assertTrue(score(second)["correct"])
        self.assertTrue(score(first)["serious"])
        self.assertTrue(score(span(source, "とき", source.index("とき")))["serious"])
        self.assertTrue(score({"value": "とき", "spans": [*first["spans"], *second["spans"]]})["correct"])
        self.assertTrue(general.grade(case, "direct", {"status": "unverified", "result": '{"answer":"とき"}'})["correct"])

    def test_partial_extraction_and_unsupported_extras(self):
        case = self.dev["general-dev-verifiers"]
        source = case["job"]["source"]
        result = {"readers": [span(source, v) for v in case["gold"]["readers"]], "price": []}
        self.assertTrue(general.grade(case, "selected", {"status": "partial", "result": result})["correct"])
        result["readers"].append(span(source, "Ana Rill"))
        self.assertTrue(general.grade(case, "current", {"status": "partial", "result": result})["serious"])
        omitted = {"price": [], "readers": ["Nel Penn"]}
        score = general.grade(case, "direct", {"status": "unverified", "result": json.dumps(omitted)})
        self.assertFalse(score["correct"])
        self.assertFalse(score["serious"])

    def test_direct_schema_and_abstention(self):
        case = self.dev["general-dev-pier"]
        for text in ('{"label":""}', '```json\n{"label":""}\n```'):
            self.assertTrue(general.grade(case, "direct", {"status": "unverified", "result": text})["correct"])
        for text in ('{"label":null}', '{"label":"hire","label":""}'):
            self.assertFalse(general.grade(case, "direct", {"status": "unverified", "result": text})["correct"])
        self.assertTrue(general.grade(case, "direct", {"status": "unverified", "result": '{"label":"hire"}'})["serious"])
        self.assertTrue(general.grade(case, "common", {"status": "abstained", "result": {"label": None}})["correct"])

    def test_rewrite_binding_limit_and_variants(self):
        case = self.dev["general-dev-rink"]
        score = lambda c, text: general.grade(c, "current", {"status": "ok", "result": text})
        for text in ("Mat hire is £8 for adults and £3 for juniors.",
                     "Juniors rent a mat for 3 pounds. Adults pay 8 pounds.",
                     "Adults and juniors rent mats for 8 and 3 pounds respectively."):
            self.assertTrue(score(case, text)["correct"], text)
        for text in ("Mat hire costs £3 for adults and £8 for juniors.", "Mat hire costs £8 or £4."):
            self.assertTrue(score(case, text)["serious"], text)
        copied = score(case, case["job"]["source"])
        self.assertFalse(copied["correct"])
        self.assertFalse(copied["serious"])
        paper = self.dev["general-dev-paper"]
        self.assertTrue(score(paper, "上質紙は6枚以下です。包装紙は対象外です。")["correct"])
        self.assertTrue(score(paper, "上質紙は6枚以上です。包装紙は対象外です。")["serious"])
        self.assertTrue(score(paper, "上質紙は6枚までです。")["serious"])


if __name__ == "__main__":
    unittest.main()
