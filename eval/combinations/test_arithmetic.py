"""Deterministic contract tests. These small examples never call FM or use the study panel."""
import unittest

import arithmetic as ar


class Stub:
    def __init__(self, reads=(), values=None, final=None, callbacks=()):
        self.reads = list(reads)
        self.values = values or {}
        self.final = dict(final or ar.result("held"), callbacks=list(callbacks))
        self.events = []

    def read(self, prompt, schema, *, max_input):
        assert max_input == 3600
        self.events.append(("read", prompt, schema))
        return self.reads.pop(0)

    def native(self, question):
        self.events.append(("native", question))
        return self.final

    def calc(self, expression):
        self.events.append(("calc", expression))
        return self.values[expression]


Q = "🧵 紫の布は６分の５メートル。青の布はその３倍。価格は未定。青の布は何メートル？"
FACTS = {"target": "青の布は何メートル？", "relations": ["青の布はその３倍。"],
         "quantities": [{"quote": "紫の布は６分の５メートル。", "quantity": "６分の５"},
                        {"quote": "青の布はその３倍。", "quantity": "３"}]}
FORMULA = {"decision": "calculate", "expression": "A * B"}
UNKNOWN = {"decision": "unknown", "expression": ""}
VALUES = {"(5/6) * (3)": "2.5"}


class ArithmeticTests(unittest.TestCase):
    def test_direct_final_and_last_callback(self):
        for arm, expected in (("native", "17"), ("native_value", "3")):
            io = Stub(final=ar.result("number", "17"), callbacks=["1", "3"])
            self.assertEqual(ar.execute(arm, {"question": Q}, io)[0], ar.result("number", expected))
            self.assertEqual(io.events, [("native", Q)])

    def test_single_formula_rust_and_unknown(self):
        io = Stub([{"decision": "calculate", "expression": "8/3"}], {"8 / 3": "8/3"})
        self.assertEqual(ar.execute("formula", {"question": Q}, io)[0], ar.result("number", "8/3"))
        for packet, kind in ((UNKNOWN, "unknown"), (None, "held"),
                             ({"decision": "unknown", "expression": "2"}, "held"),
                             ({"decision": "calculate", "expression": "3**2"}, "held")):
            io = Stub([packet])
            self.assertEqual(ar.execute("formula", {"question": Q}, io)[0]["kind"], kind)
            self.assertEqual(len(io.events), 1)

    def test_source_slices_and_filtered_reader(self):
        io = Stub([FACTS, FORMULA], VALUES)
        val, notes = ar.execute("evidence_formula", {"question": Q}, io)
        self.assertEqual(val, ar.result("number", "2.5"))
        evidence = notes["evidence"]
        spans = [evidence["target"]] + evidence["relations"]
        for row in evidence["quantities"].values():
            spans.extend([row["quote"], row["quantity"]])
        for span in spans:
            self.assertEqual(Q[span["start"]:span["end"]], span["text"])
        self.assertEqual(evidence["quantities"]["A"]["quantity"]["start"], Q.index("６分の５"))
        self.assertNotIn("価格は未定", io.events[1][1])

    def test_grounding_and_numeric_surfaces(self):
        for text, expected in (("1½", "3/2"), ("two-thirds", "2/3"), ("−１．５", "-3/2"),
                               ("one hundred and ten", "110"), ("三百二十五", "325"),
                               ("１／４", "1/4"), ("10,050", "10050"), ("a quarter", "1/4")):
            with self.subTest(text=text):
                self.assertEqual(str(ar.source_number(text)), expected)
        for quote, term in (("billed 340 euros", "40"), ("use 1/7", "7"),
                            ("６分の５", "６"), ("two-thirds", "thirds"),
                            ("it was -9 degrees", "9"), ("twenty-one crates", "one")):
            raw = {"target": quote, "relations": [quote], "quantities": [{"quote": quote, "quantity": term}]}
            with self.subTest(quote=quote), self.assertRaises(ar.InvalidEvidence):
                ar.ground(quote, raw)
        with self.assertRaises(ar.InvalidEvidence):
            ar.ground(Q, dict(FACTS, relations=["布は無料だった。"]))
        with self.assertRaises(ar.InvalidEvidence):
            ar.ground(Q, dict(FACTS, quantities=FACTS["quantities"] * 2))

    def test_bounded_formula_and_constants(self):
        for expr in ("A+7", "1", "W*A", "A/**B", "A B", "A(1)", "(A", "A" * 201):
            io = Stub([FACTS, {"decision": "calculate", "expression": expr}])
            with self.subTest(expr=expr):
                self.assertEqual(ar.execute("evidence_formula", {"question": Q}, io)[0], ar.result("held"))
                self.assertEqual([e[0] for e in io.events], ["read", "read"])
        question = "The fee is 19% of sales. What fraction is the fee?"
        report = {"target": "What fraction is the fee?", "relations": ["The fee is 19% of sales."],
                  "quantities": [{"quote": "19% of sales", "quantity": "19%"}]}
        io = Stub([report, {"decision": "calculate", "expression": "A/100"}], {"(19) / 100": "0.19"})
        self.assertEqual(ar.execute("evidence_formula", {"question": question}, io)[0], ar.result("number", "0.19"))

    def test_parallel_agreement_and_abstention(self):
        for callback, expected in (("5/2", ar.result("number", "5/2")), ("2", ar.result("held"))):
            io = Stub([FACTS, FORMULA], VALUES, ar.result("number", "99"), [callback])
            self.assertEqual(ar.execute("parallel", {"question": Q}, io)[0], expected)
            self.assertEqual([e[0] for e in io.events], ["native", "read", "read", "calc"])
        for final, expected in (("unknown", "unknown"), ("held", "held"), ("number", "held")):
            io = Stub([FACTS, UNKNOWN], final=ar.result(final, "9" if final == "number" else None))
            self.assertEqual(ar.execute("parallel", {"question": Q}, io)[0]["kind"], expected)

    def test_selective_checks_only_when_triggered(self):
        io = Stub(final=ar.result("number", "2.5"), callbacks=["5/2"])
        self.assertEqual(ar.execute("selective", {"question": Q}, io)[0], ar.result("number", "5/2"))
        self.assertEqual(io.events, [("native", Q)])
        for final, callbacks, expected in ((ar.result("held"), [], "number"),
                                           (ar.result("number", "9"), [], "held"),
                                           (ar.result("unknown"), ["8"], "held")):
            io = Stub([FACTS, FORMULA], VALUES, final, callbacks)
            self.assertEqual(ar.execute("selective", {"question": Q}, io)[0]["kind"], expected)
            self.assertEqual([e[0] for e in io.events], ["native", "read", "read", "calc"])


if __name__ == "__main__":
    unittest.main()
