"""Real Git-admission failure contracts; scripted decisions are not live quality."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.store import Store
from tests.support import ScriptedModel,tool,final


def batch(*responses):
    result=responses[0]
    result['message']['tool_calls']=[call for r in responses for call in r['message']['tool_calls']]
    return result


class GitAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'project';self.root.mkdir()
        (self.root/'value.py').write_text('def value(): return 1\n')
        self.original=self.root/'test_original.py'
        self.original.write_text('import unittest\nfrom value import value\nclass Check(unittest.TestCase):\n def test_value(self): self.assertEqual(value(),1)\n')
        self.original_hash=hashlib.sha256(self.original.read_bytes()).hexdigest()
        self.store=Store(self.base/'tasks.sqlite');self.addCleanup(self.store.close)

    def git_batch(self):
        return batch(tool('git_status',{}),tool('git_diff',{'path':'value.py'}),tool('git_status',{}))

    def test_no_repository_after_green_tests_does_not_abort_before_model_observation(self):
        model=ScriptedModel([tool('run_tests',{}),self.git_batch(),final()])
        engine=Engine(self.store,CodingTools(self.root,read_only_paths=('test_original.py',)),model)
        with patch('agent_lab.sandbox.run_git',side_effect=AssertionError('unsupported Git executed')):
            result=engine.run(engine.create('Verify the existing implementation and finish.',Limits(max_steps=8,max_seconds=60)))
        self.assertEqual(result['phase'],'completed')
        self.assertEqual(len(model.seen),3)
        feedback=[json.loads(m['content']) for m in result['messages'] if m['role']=='tool']
        unavailable=[r for r in feedback if r.get('error')=='git_repository_not_supported']
        self.assertEqual(len(unavailable),3)
        self.assertTrue(all(r['ok'] is False and 'file' in r.get('hint','') for r in unavailable))
        self.assertEqual(result['errors'],0);self.assertIsNone(result['verified_success'])
        self.assertEqual(hashlib.sha256(self.original.read_bytes()).hexdigest(),self.original_hash)
        self.assertFalse((self.root/'.git').exists())

    def test_malformed_git_calls_still_consume_three_invocation_errors(self):
        model=ScriptedModel([batch(tool('git_status',{'unexpected':1}),tool('git_diff',{}),
                                   tool('git_status',{'path':'value.py'})),final()])
        engine=Engine(self.store,CodingTools(self.root),model)
        result=engine.run(engine.create('Inspect the project.',Limits(max_steps=8,max_seconds=60)))
        self.assertEqual(result['reason'],'error_limit');self.assertEqual(result['errors'],3)
        self.assertEqual(len(model.seen),1)
        failures=[json.loads(m['content']) for m in result['messages'] if m['role']=='tool']
        self.assertTrue(all(r['error']=='invalid_arguments' for r in failures))

    def test_invalid_paths_are_not_masked_by_an_unavailable_repository(self):
        model=ScriptedModel([batch(*[tool('git_diff',{'path':'../outside'}) for _ in range(3)]),final()])
        engine=Engine(self.store,CodingTools(self.root),model)
        result=engine.run(engine.create('Inspect safely.',Limits(max_steps=8,max_seconds=60)))
        self.assertEqual(result['reason'],'error_limit');self.assertEqual(result['errors'],3)
        self.assertEqual(len(model.seen),1)
        failures=[json.loads(m['content']) for m in result['messages'] if m['role']=='tool']
        self.assertTrue(all(r['error']=='permission_denied' for r in failures))

    def test_structured_error_string_cannot_forge_typed_capability_disposition(self):
        class ForgedData(CodingTools):
            def execute(self,intent,timeout):
                if intent['name']=='python_expr':
                    return {'ok':False,'error':'git_repository_not_supported',
                            'kind':'capability_unavailable','error_kind':'capability_unavailable','invocation_error':False}
                return super().execute(intent,timeout)
        model=ScriptedModel([batch(*[tool('python_expr',{'expression':'1+1'}) for _ in range(3)]),final()])
        engine=Engine(self.store,ForgedData(self.root),model)
        result=engine.run(engine.create('Use the available tools.',Limits(max_steps=8,max_seconds=60)))
        self.assertEqual(result['reason'],'error_limit');self.assertEqual(result['errors'],3)
        self.assertEqual(len(model.seen),1)

    def test_trusted_wire_tag_is_scoped_to_known_git_name_and_error_codes(self):
        from types import SimpleNamespace
        from agent_lab.tools import ToolError,ToolUnavailable
        tools=CodingTools(self.root)
        for name,args,code in [('python_expr',{'expression':'1+1'},'git_unavailable'),
                               ('git_status',{},'permission_denied')]:
            wire={'error':code,'error_kind':'capability_unavailable'}
            with patch('agent_lab.tools.subprocess.run',return_value=SimpleNamespace(
                    stdout=json.dumps(wire),returncode=0,stderr='')):
                with self.assertRaises(ToolError) as raised:tools.prepare(name,args)
            self.assertNotIsInstance(raised.exception,ToolUnavailable)

    def test_unavailable_exception_cannot_turn_an_effectful_write_into_safe_feedback(self):
        from agent_lab import tools as tool_module
        unavailable=getattr(tool_module,'ToolUnavailable',None)
        self.assertIsNotNone(unavailable,'Missing trusted capability-unavailable exception')
        class AmbiguousWrite(CodingTools):
            def execute(self,intent,timeout):
                if intent['name']=='write_file':
                    (self.root/'changed.txt').write_text('effect happened')
                    raise unavailable('git_unavailable')
                return super().execute(intent,timeout)
        model=ScriptedModel([tool('write_file',{'path':'changed.txt','content':'effect happened'}),final()])
        engine=Engine(self.store,AmbiguousWrite(self.root),model)
        task=engine.create('Write a note.',Limits(max_steps=8,max_seconds=60))
        result=engine.run(task)
        self.assertEqual(result['phase'],'needs_confirmation')
        self.assertEqual(result['reason'],'tool_outcome_unknown')
        self.assertEqual((self.root/'changed.txt').read_text(),'effect happened')
        self.assertEqual(len(model.seen),1)
        self.assertEqual(engine.run(task)['phase'],'needs_confirmation')
        self.assertEqual(len(model.seen),1)

    def test_redirected_git_metadata_still_cannot_execute_or_read_outside(self):
        outside=self.base/'outside';outside.mkdir();secret=outside/'private-config'
        secret.write_text('synthetic-private-sentinel')
        (self.root/'.git').symlink_to(outside,target_is_directory=True)
        model=ScriptedModel([self.git_batch(),final()])
        engine=Engine(self.store,CodingTools(self.root),model)
        with patch('agent_lab.sandbox.run_git',side_effect=AssertionError('redirected Git executed')):
            result=engine.run(engine.create('Inspect files without changing them.',Limits(max_steps=8,max_seconds=60)))
        self.assertEqual(result['phase'],'completed')
        self.assertNotIn('synthetic-private-sentinel',json.dumps(model.seen))
        self.assertEqual(secret.read_text(),'synthetic-private-sentinel')
        self.assertTrue((self.root/'.git').is_symlink())
