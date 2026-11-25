"""Offline integrity checks for the independent, supplementary multi-entity pairs."""

from collections import Counter
import re
import unittest

try:
    from . import targeted as t
except ImportError:
    import targeted as t


def reply(value, spans=()):
    return {"status": "abstained" if value is None else "ok", "result": {
        "answer": None if value is None else {"value": value, "spans": [
            {"start_byte": a, "end_byte": b} for a, b in spans]}}}


class TargetedFixtures(unittest.TestCase):
    def test_balance_and_no_metadata_in_generation(self):
        cs = t.cases()
        self.assertEqual(len(cs), 12)
        self.assertEqual(len({c["id"] for c in cs}), 12)
        self.assertEqual(len({c["pair"] for c in cs}), 6)
        self.assertEqual(len({c["job"]["source"] for c in cs}), 12)
        for k, expected in (("group", {"named_decision":6, "cross_reference":6}),
                            ("language", {"ja":6, "en":6}), ("positive", {True:6, False:6}),
                            ("tier", {"8k":6,"16k":6})):
            self.assertEqual(Counter(c[k] for c in cs),expected)
        repeats=[]
        for c in cs:
            job=c["job"]
            self.assertEqual(set(job), {"source","question","task"})
            self.assertEqual(job["task"],"answer")
            for forbidden in (c["id"],c["pair"],"⟦","⟧"):
                self.assertNotIn(forbidden,job["source"])
            if c["positive"]:
                repeats.append(job["source"].count(c["gold"])>1)
        self.assertCountEqual(repeats,[True,True,True,False,False,False])

    def test_minimal_decision_change_and_official_bytes(self):
        cs=t.cases()
        for p,n in zip(cs[::2],cs[1::2]):
            self.assertEqual((p["pair"],p["filler_count"],p["job"]["question"]),
                             (n["pair"],n["filler_count"],n["job"]["question"]))
            pb,nb=(c["job"]["source"].encode() for c in (p,n))
            [(pa,pz)],[(na,nz)]=p["change_spans"],n["change_spans"]
            self.assertEqual(pb[:pa],nb[:na])
            self.assertEqual(pb[pz:],nb[nz:])
            self.assertNotEqual(pb[pa:pz],nb[na:nz])
            self.assertIn(b"\n",pb[pa:pz])
            self.assertIn(b"\n",nb[na:nz])
            self.assertGreater(pa/len(pb),.74)
            [(a,b)]=p["gold_spans"]
            self.assertTrue(pa <= a < b <= pz)
            self.assertEqual(pb[a:b].decode(),p["gold"])
            self.assertEqual(n["gold_spans"],[])
            for c,raw in ((p,pb),(n,nb)):
                for i,j in c["evidence_spans"]+c["context_spans"]:
                    self.assertTrue(0<=i<j<=len(raw))
                    self.assertTrue(raw[i:j].decode("utf-8").strip())
            self.assertEqual(t.grade(p,reply(p["gold"],[(a,b)]))["outcome"],"correct_settled")
            self.assertEqual(t.grade(p,reply(None))["outcome"],"missed_settled")
            self.assertEqual(t.grade(n,reply(None))["outcome"],"correct_null")
            self.assertEqual(t.grade(n,reply(p["gold"],[(na,nz)]))["outcome"],"false_nonnull")
            alternatives=[m.span() for m in re.finditer(re.escape(pb[a:b]),pb) if m.span()!=(a,b)]
            for old in alternatives:
                self.assertEqual(t.grade(p,reply(p["gold"],[old]))["outcome"],"invalid_evidence")

    def test_nonrepeated_multi_entity_background(self):
        for identity,s in t.MATERIAL.items():
            documents=list(t._background(s,t.pair_id(*identity),170))
            self.assertEqual(len(set(documents)),170)
            ids=[re.findall(r"\b[A-Z]{2}-[0-9]{4}\b",b) for b in documents] if s["language"]=="en" else [
                re.findall(r"[A-Z]{2}-[0-9]{4}",b) for b in documents]
            self.assertTrue(all(ids))
            self.assertEqual(len({x for row in ids for x in row}),170)
            self.assertTrue(any(len(set(row))>1 for row in ids))
            self.assertTrue(any("\n" in row for row in documents))
            case=t.make_case("targeted",*identity,True,filler_count=45)
            self.assertEqual(case,t.make_case("targeted",*identity,True,filler_count=45))


if __name__=="__main__":
    unittest.main()
