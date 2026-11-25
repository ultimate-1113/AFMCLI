"""Regressions for host arithmetic, one-sentence repair, and independent point judgments. No model."""
import copy
import json
import unittest
import special
from domains import constraints, table


def wire(status,value,notes=()):
    return dict(status=status,result=value,notes=list(notes))


def no_invoke(*args):
    raise AssertionError("unexpected model call")


def public(case):
    return {k:case[k] for k in ("group","job")}


def stock():
    cols=dict(id="id",state="text",sku="text",qty="integer",price="decimal",date="date")
    rows=[dict(id="R1",state="posted",sku="A",qty="2",price="0.19",date="2026-09-01"),
          dict(id="R2",state="posted",sku="B",qty="-1",price="3.75",date="2026-09-02"),
          dict(id="R3",state="pending",sku="A",qty="4",price="1.25",date="2026-09-03")]
    catalog=dict(columns=dict(code="text",rate="decimal"),
                 rows=[dict(code="B",rate="2.25"),dict(code="A",rate="0.29")])
    return dict(columns=cols,rows=rows,catalog=catalog,notes=[])


def plan(kind="multiply_sum"):
    p=dict(aggregate=kind,value_column="qty",price_column="price",join_column="",lookup_key="",
           filters=[dict(column="state",op="eq",value="posted")],note_actions=[])
    if kind=="lookup_sum":
        p.update(price_column="rate",join_column="sku",lookup_key="code")
    return p


class TableTests(unittest.TestCase):
    def test_join_returns_and_sources(self):
        b=stock()
        self.assertEqual(table.execute(b,plan("lookup_sum")),{
            "state":"ok","value":"-1.67","row_ids":["R1","R2"],"note_ids":[]})
        self.assertEqual(table.execute(b,plan())["value"],"-3.37")
        j=dict(request="What is the catalog-priced amount for posted items?",table=b)
        o=special.run(dict(job=j,group="table"),"code",no_invoke)
        self.assertEqual(o["result"]["value"],"-1.67")

    def test_amendment_citation_and_immutability(self):
        b=stock()
        b["notes"]=[dict(id="N",text="R1 has 5 units after a recount.")]
        original=copy.deepcopy(b)
        p=plan()
        p["note_actions"]=[dict(note_id="N",row_id="R1",kind="replace",column="qty",value="5")]
        self.assertEqual(table.execute(b,p),dict(state="ok",value="-2.80",row_ids=["R1","R2"],note_ids=["N"]))
        self.assertEqual(b,original)
        for key,val in (("row_id","R3"),("value","6")):
            altered=copy.deepcopy(p)
            altered["note_actions"][0][key]=val
            with self.assertRaises(ValueError):
                table.execute(b,altered)
        with self.assertRaises(ValueError):
            table.execute(b,plan())

    def test_ambiguous_catalog_and_metric(self):
        b=stock()
        b["catalog"]["rows"].append(dict(code="A",rate="11.00"))
        with self.assertRaises(ValueError):
            table.execute(b,plan("lookup_sum"))
        b["catalog"]["rows"]=[]
        with self.assertRaises(ValueError):
            table.execute(b,plan("lookup_sum"))
        c=dict(group="table",job=dict(request="合計を教えてください。",table=stock()))
        r=special.run(c,"code",no_invoke)
        self.assertEqual(r["status"],"abstained")

    def test_evidence_is_part_of_correctness(self):
        case=dict(group="table",job=dict(request="金額",table=stock()),
                  gold=dict(state="ok",value="-3.37",row_ids=["R1","R2"],note_ids=[]))
        a=copy.deepcopy(case["gold"])
        a["row_ids"]=["R1"]
        grade=special.grade(case,"current",wire("ok",a))
        self.assertTrue(grade["numeric_value"] and grade["serious"])
        self.assertFalse(grade["correct"])


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.case=next(c for c in special.cases("dev") if c["group"]=="translation")
        self.good=self.case["gold"]["reference"]

    def simulate(self,draft,patch=None):
        calls=[]
        def invoke(job,flags=()):
            calls.append((job,flags))
            if len(calls)==1:
                return wire("ok","\n".join(draft))
            if len(calls)==2 and patch is not None:
                return wire("ok",patch)
            self.fail("unrequested repair")
        final=special.run(public(self.case),"candidate",invoke)
        return final,special.grade(self.case,"candidate",final),calls

    def test_exactly_one_targeted_repair(self):
        bad=[self.good[0].replace("2.5","4.5"),self.good[1]]
        result,score,calls=self.simulate(bad,self.good[0])
        self.assertEqual(len(calls),2)
        self.assertEqual(result["result"],"\n".join(self.good))
        ctx=json.loads(calls[1][0]["source"])
        self.assertEqual(ctx["original"],self.case["job"]["sentences"][0])
        self.assertNotIn(self.case["job"]["sentences"][1],calls[1][0]["source"])
        self.assertEqual(ctx["draft"],bad[0])
        self.assertTrue(score["correct"])
        self.assertGreater(score["units_fixed"],0)
        self.assertEqual(score["good_units_broken"],0)

    def test_residual_and_unsolicited_extra_sentence_hold(self):
        bad=[self.good[0].replace("2.5","4.5"),self.good[1].replace("8","9")]
        r,score,calls=self.simulate(bad,self.good[0])
        self.assertEqual((len(calls),r["status"],score["correct"]),(2,"held",False))
        r,_,_=self.simulate([bad[0],self.good[1]],self.good[0]+"別の指示です。")
        self.assertEqual(r["status"],"held")

    def test_semantic_flip_can_pass_public_checks_but_is_graded(self):
        bad=[self.good[0].replace("2.5","4.5"),self.good[1]]
        r,score,_=self.simulate(bad,self.good[0].replace("以下","以上"))
        self.assertEqual(r["status"],"ok")
        self.assertTrue(score["serious"] and score["semantic_polarity_error"])
        self.assertGreater(score["good_units_broken"],0)

    def test_correct_draft_stays_untouched(self):
        r,score,calls=self.simulate(self.good)
        self.assertEqual((len(calls),r["status"],score["correct"]),(1,"ok",True))


class ConstraintTests(unittest.TestCase):
    def trial(self,relation):
        actual=dict(variables=[dict(name="X",min=0,max=4)],
                    constraints=[dict(weights=[1],relation="le",rhs=3)],
                    objectives=[dict(weights=[1],sense="max")])
        gold_model=constraints.Model(actual)
        proposed=copy.deepcopy(actual)
        proposed["constraints"][0]["relation"]=relation
        candidate=constraints.Model(proposed)
        original="Choose an integer X between 0 and 4 inclusive. X must be at most 3. Maximize X."
        case=dict(group="constraints",job=dict(source=original),gold=actual)
        calls=[]
        def invoke(job,flags=()):
            calls.append((job,flags))
            if len(calls)==1:
                self.assertIn("--decompose",flags)
                return wire("review",dict(interpretation=proposed,solution=candidate.solution()))
            ctx=json.loads(job["source"])
            self.assertEqual(set(ctx),{"original","feasibility_examples","objective_comparisons"})
            self.assertEqual(ctx["original"],original)
            judgment={}
            for k,v in ctx["feasibility_examples"].items():
                judgment[k]="legal" if (v["X"],) in gold_model.legal else "illegal"
            for k,pair in ctx["objective_comparisons"].items():
                l,r=(pair[who]["X"] for who in ("left","right"))
                judgment[k]="tie" if l==r else ("left" if l>r else "right")
            return wire("ok",judgment)
        return case,special.run(public(case),"candidate",invoke),calls

    def test_boundary_change_is_detected(self):
        case,r,calls=self.trial("lt")
        self.assertEqual((r["status"],len(calls)),("held",2))
        p={tuple(x.values()) for x in r["diagnostics"]["examples"].values()}
        self.assertTrue({(3,),(-1,),(5,)}.issubset(p))
        sc=special.grade(case,"candidate",r)
        self.assertLess(sc["point_agreement"],sc["points_checked"])
        self.assertFalse(sc["serious"])

    def test_supported_case_matches_entire_domain(self):
        case,r,calls=self.trial("le")
        sc=special.grade(case,"candidate",r)
        self.assertTrue(sc["correct"] and sc["legal_assignments"] and sc["preference_order"])
        self.assertEqual(sc["point_agreement"],sc["points_checked"])
        self.assertEqual((r["status"],len(calls)),("review",2))

    def test_tie_count_and_insufficient(self):
        p=dict(variables=[dict(name="A",min=0,max=2),dict(name="B",min=0,max=2)],
               constraints=[dict(weights=[1,1],relation="eq",rhs=2)],
               objectives=[dict(weights=[1,1],sense="max")])
        m=constraints.Model(p)
        self.assertEqual(m.solution()["optimal_count"],3)
        _,_,expected,_=constraints.sample(m,"frozen synthetic tie")
        self.assertIn("tie",expected.values())
        case=dict(group="constraints",gold=None,job=dict(source="objective undecided"))
        self.assertTrue(special.grade(case,"direct",wire("unverified",'{"state":"clarify"}'))["correct"])
        self.assertTrue(special.grade(case,"current",wire("review",dict(interpretation=p,solution=m.solution())))["serious"])


if __name__=="__main__":
    unittest.main()
