"""Regression checks for what the three pilot arms see and what earns evidence credit."""
import json
from unittest import TestCase,main
from unittest.mock import patch

import handoff


class HandoffTests(TestCase):
    def setUp(self):
        self.case={"id":"control","size":"short","goal":"どの札が正式ですか。",
                   "packets":[[{"id":"A1","text":"古い原文の確認札は紫。"},{"id":"A2","text":"別の作業の名前は針金。"}],
                              [{"id":"B1","text":"今回の作業を続ける。"}],[{"id":"C1","text":"別件に変更がある。"}]],
                   "gold":{"value":"紫","evidence":["A1"]}}
        self.memo={"memo":"確認札は紫。","refs":["A1"]}

    def test_previous_and_reopened_views(self):
        full,_=handoff.prompt_for(self.case,"history",1,[self.memo])
        prev,visible=handoff.prompt_for(self.case,"previous",1,[self.memo])
        extra,_=handoff.prompt_for(self.case,"reopen",1,[self.memo])
        self.assertIn("針金",full)
        self.assertNotIn("古い原文",prev)
        self.assertNotIn("針金",prev)
        self.assertIn("古い原文",extra)
        self.assertNotIn("針金",extra)
        self.assertEqual(visible,{"A1","B1"})
        self.assertEqual(handoff.pick_refs(["C1","A1","A1"],visible),["A1"])
        self.assertEqual(len({handoff.prompt_for(self.case,a,0,[])[0] for a in handoff.ARMS}),1)

    def recorded(self,final):
        answers=iter([self.memo,self.memo,final])
        def reply(req):
            return {"status":"ok","sent":True,"input_tokens":9,"model":"system",
                    "raw_output":json.dumps(next(answers),ensure_ascii=False),
                    "confirmed_user_sha256":handoff.backend.sha(req["prompt"].encode())}
        with patch.object(handoff.backend,"run_structured",side_effect=reply):
            obs=handoff.execute(self.case,"previous")
        self.assertEqual(obs,handoff.execute(self.case,"previous",archive=obs["events"]))
        return obs

    def test_valid_id_in_previous_output_can_earn_full_credit(self):
        obs=self.recorded({"state":"確定","value":"紫","evidence_id":"A1"})
        self.assertTrue(obs["correct"])
        self.assertEqual((obs["sent_tokens"],obs["generations"]),(27,3))

    def test_invented_id_never_earns_full_credit(self):
        obs=self.recorded({"state":"確定","value":"紫","evidence_id":"Z9"})
        self.assertEqual(obs["status"],"unknown_source_id")
        self.assertFalse(obs["correct"])

    def test_budget_stop_is_not_counted_as_a_generation_or_abstention(self):
        with patch.object(handoff.backend,"run_structured",return_value={"status":"over_budget","input_tokens":7000,"sent":False}):
            obs=handoff.execute(self.case,"history")
        self.assertEqual(obs["status"],"over_budget")
        self.assertEqual((obs["generations"],obs["peak_sent"],obs["correct"]),(0,0,False))


if __name__=="__main__":
    main()
