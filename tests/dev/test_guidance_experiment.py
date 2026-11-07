import json
from pathlib import Path
import tempfile
import unittest


class GuidanceExperimentTests(unittest.TestCase):
    def test_skill_body_is_absent_initially_and_success_requires_actual_loading(self):
        from scripts.evaluate_guidance import make_fixture,guidance_evidence
        from agent_lab.engine import Engine
        from agent_lab.coding_tools import CodingTools
        from agent_lab.store import Store
        from tests.support import ScriptedModel
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);root=base/'project'
            case=make_fixture(root,'synthetic-private-skill-receipt')
            with Store(base/'state.sqlite') as store:
                engine=Engine(store,CodingTools(root),ScriptedModel([]))
                state=store.load(engine.create(case['task']))
            self.assertIn('regression-check',json.dumps(state['messages']))
            self.assertNotIn(case['canary'],json.dumps(state['messages']))
            messages=state['messages']+[{'role':'assistant','content':case['canary']}]
            proof=guidance_evidence(messages,case['canary'])
            self.assertFalse(proof['skill_loaded'])
            self.assertFalse(proof['scope_rules_read'])
            self.assertFalse(proof['guidance_verified'])

    def test_only_successful_tool_feedback_counts_and_skill_body_must_be_used(self):
        from scripts.evaluate_guidance import guidance_evidence
        def step(name,args,result):
            return [{'role':'assistant','tool_calls':[{'id':name,'function':{'name':name,'arguments':json.dumps(args)}}]},
                    {'role':'tool','tool_call_id':name,'content':json.dumps(result)}]
        messages=step('project_rules',{'path':'labelkit/normalize.py'},{'ok':True})
        failed=step('load_skill',{'name':'regression-check'},{'ok':False})
        self.assertFalse(guidance_evidence(messages+failed+[{'role':'assistant','content':'canary'}],'canary')['guidance_verified'])
        loaded=step('load_skill',{'name':'regression-check'},{'ok':True,'content':'canary'})
        self.assertFalse(guidance_evidence(messages+loaded,'canary')['guidance_verified'])
        self.assertTrue(guidance_evidence(messages+loaded+[{'role':'assistant','content':'canary'}],'canary')['guidance_verified'])
