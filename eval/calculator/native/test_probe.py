"""Offline regression checks for separating AFM text from real calculator callbacks."""
from fractions import Fraction
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run import grade, read_events


def callback(value, expression="1+1", source="framework"):
    return {"event": "callback", "source": source, "tool": "scientificCalculator", "expression": expression,
            "value": value, "backend_status": "ok" if value is not None else "invalid"}


class GradeTest(unittest.TestCase):
    def test_last_success_before_error_and_model_mistranscription(self):
        calls = [callback("1", "1"), callback("2"), callback(None, "bad")]
        transcript = [{"kind": "tool_call", "tool": c["tool"], "arguments": {"expression": c["expression"]}}
                      for c in calls]
        transcript += [{"kind": "tool_output", "tool": c["tool"], "segments": [c["value"]]} for c in calls[:2]]
        row = grade("test", Fraction(2), [*calls, {"event": "final", "answer": "3", "transcript": transcript}])
        self.assertEqual((row["matched_dispatch_count"], row["matched_output_count"]), (3, 2))
        self.assertEqual((row["callback_count"], row["successful_callback_count"]), (3, 2))
        self.assertTrue(row["tool_direct_correct"])
        self.assertFalse(row["correct"])

    def test_model_claim_or_offline_call_is_not_a_dispatch(self):
        row = grade("test", Fraction(2), [callback("2", source="offline-check"),
                                         {"event": "final", "answer": "I called the calculator."}])
        self.assertEqual(row["callback_count"], 0)
        self.assertIsNone(row["tool_direct_correct"])
        self.assertFalse(row["correct"])

    def test_completed_callback_survives_truncated_final(self):
        events, errors = read_events(b'{"event":"callback","source":"framework","tool":"T","value":"2","backend_status":"ok"}\n{"event":')
        self.assertEqual(len(errors), 1)
        row = grade("test", Fraction(2), events)
        self.assertTrue(row["tool_direct_correct"])
        self.assertIsNone(row["output"])
        self.assertEqual(row["matched_dispatch_count"], 0)
