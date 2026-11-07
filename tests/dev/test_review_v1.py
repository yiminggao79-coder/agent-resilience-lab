import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agent_lab.protocols import normalize_response
from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.tools import Tools, ToolError
from agent_lab import sandbox
from tests.support import ScriptedModel, tool, final


class ReviewProtocolTests(unittest.TestCase):
    def test_complete_bad_argument_json_returns_feedback_and_preserves_usage(self):
        body={'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'tool_calls':[
            {'id':'malformed','type':'function','function':{'name':'read_file','arguments':'{'}}]}}],
            'usage':{'prompt_tokens':7,'completion_tokens':3}}
        normalized=normalize_response('chat',body)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'work';root.mkdir();(root/'a').write_text('real data')
            with Store(Path(tmp)/'private'/'s.sqlite') as store:
                model=ScriptedModel([normalized,tool('read_file',{'path':'a'}),final()])
                engine=Engine(store,Tools(root),model);tid=engine.create('Read a')
                state=engine.run(tid)
                self.assertEqual(state['phase'],'completed')
                self.assertEqual(json.loads(model.seen[1][-1]['content']),{'ok':False,'error':'invalid_arguments'})
                self.assertEqual(state['metrics']['input_tokens'],207)


class ReviewOracleTests(unittest.TestCase):
    def test_real_function_values_are_returned_for_parent_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'calc.py').write_text('def add(a,b): return a+b\n')
            try:r=sandbox.observe_function(root,'calc','add',[[2,3],[-3,8],[0,0],[1.5,2.5]],5)
            except ToolError as e:
                if e.code=='sandbox_unavailable':self.skipTest('sandbox unavailable')
                raise
            self.assertTrue(r['complete'])
            self.assertEqual(r['observations'],[5,5,0,4.0])

    def test_early_exit_cannot_count_as_successful_observation(self):
        self.assertTrue(hasattr(sandbox,'observe_function'),'independent function observer missing')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'calc.py').write_text('import os\nos._exit(0)\ndef add(a,b): return a-b\n')
            try:r=sandbox.observe_function(root,'calc','add',[[2,3]],5)
            except ToolError as e:
                if e.code=='sandbox_unavailable':self.skipTest('sandbox unavailable')
                raise
            self.assertFalse(r['complete'])

    def test_assertion_monkeypatch_cannot_change_parent_value_comparison(self):
        self.assertTrue(hasattr(sandbox,'observe_function'),'independent function observer missing')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'calc.py').write_text('import unittest\nunittest.TestCase.assertEqual=lambda *a: None\ndef add(a,b): return a-b\n')
            try:r=sandbox.observe_function(root,'calc','add',[[2,3],[-3,8]],5)
            except ToolError as e:
                if e.code=='sandbox_unavailable':self.skipTest('sandbox unavailable')
                raise
            self.assertTrue(r['complete'])
            self.assertEqual(r['observations'],[-1,-11])
            self.assertNotEqual(r['observations'],[5,5])

    def test_live_evaluator_rejects_zero_assertion_candidate_without_vendor_calls(self):
        from scripts import evaluate_coding
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'probe';(root/'tests').mkdir(parents=True)
            (root/'tests'/'test_x.py').write_text('import unittest\nclass T(unittest.TestCase):\n def test_x(self): pass\n')
            try:sandbox.run_tests(root,5)
            except ToolError as e:
                if e.code=='sandbox_unavailable':self.skipTest('sandbox unavailable')
                raise
            report=Path(tmp)/'report.json'
            source='import os\nos._exit(0)\ndef add(a,b): return a-b\n'
            model=ScriptedModel([tool('read_file',{'path':'calc.py'}),tool('write_file',{'path':'calc.py','content':source}),tool('run_tests',{}),final()])
            with patch.dict(os.environ,{'REVIEW_FAKE_KEY':'fake'}),patch.object(evaluate_coding,'OpenAIModel',return_value=model):
                code=evaluate_coding.main(['--allow-api','--model','fake','--api-key-env','REVIEW_FAKE_KEY',
                    '--max-cost-usd','1','--input-price','1','--output-price','1','--output',str(report)])
            self.assertEqual(code,1)
            row=json.loads(report.read_text())['cases'][0]
            self.assertFalse(row['task_success'])
            self.assertFalse(row['independent_observations_complete'])
