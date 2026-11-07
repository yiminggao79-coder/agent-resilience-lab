"""Invocation errors, failing verification and genuine repair are distinct."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.store import Store
from tests.support import ScriptedModel,tool,final


class CodingFailureFeedbackTests(unittest.TestCase):
    def test_real_failed_test_and_two_bad_expressions_leave_room_for_code_repair(self):
        with tempfile.TemporaryDirectory(prefix='coding-error-budget-') as temp:
            base=Path(temp);root=base/'project';root.mkdir()
            (root/'calc.py').write_text('def value():\n return 0\n')
            protected=root/'test_original.py'
            protected.write_text('import unittest\nfrom calc import value\nclass Check(unittest.TestCase):\n def test_value(self): self.assertEqual(value(),1)\n')
            before=hashlib.sha256(protected.read_bytes()).hexdigest()
            model=ScriptedModel([tool('run_tests',{},'red'),
                tool('python_expr',{'expression':'__import__("calc").value()'},'bad1'),
                tool('python_expr',{'expression':'object()'},'bad2'),
                tool('apply_patch',{'path':'calc.py','old':'return 0','new':'return 1'},'fix'),
                tool('run_tests',{},'green'),final()])
            with Store(base/'tasks.sqlite') as store:
                engine=Engine(store,CodingTools(root,read_only_paths=('test_original.py',)),model)
                task=engine.create('Repair value and run tests.',Limits(max_steps=10,max_seconds=60,tool_timeout=10))
                result=engine.run(task);events=store.events(task)
            self.assertEqual(result['phase'],'completed')
            tests=[e for e in events if e['kind']=='tool_finished' and e.get('tool')=='run_tests']
            self.assertEqual([e['exit_code'] for e in tests],[1,0])
            self.assertFalse(tests[0]['ok']);self.assertEqual(tests[0]['code'],'process_exit_nonzero')
            self.assertEqual(before,hashlib.sha256(protected.read_bytes()).hexdigest())
            self.assertIsNone(result['verified_success'])
            bad=[json.loads(m['content']) for m in result['messages'] if m.get('role')=='tool'
                 and json.loads(m['content']).get('error')=='invalid_expression']
            self.assertEqual(len(bad),2)
            self.assertTrue(all('run_tests' in b.get('hint','') for b in bad))
            self.assertTrue(all('approval' in b['hint'] for b in bad))

    def test_three_genuine_invalid_invocations_still_stop_before_more_model_calls(self):
        with tempfile.TemporaryDirectory(prefix='coding-bad-budget-') as temp:
            base=Path(temp);root=base/'project';root.mkdir()
            model=ScriptedModel([tool('python_expr',{'expression':'object()'},'bad'+str(i)) for i in range(3)]+[final()])
            with Store(base/'tasks.sqlite') as store:
                engine=Engine(store,CodingTools(root),model)
                result=engine.run(engine.create('Evaluate.',Limits(max_steps=8,max_seconds=60)))
            self.assertEqual(result['phase'],'failed');self.assertEqual(result['reason'],'error_limit')
            self.assertEqual(result['metrics']['model_calls'],3);self.assertEqual(result['errors'],3)
