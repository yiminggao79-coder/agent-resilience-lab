"""Execution-review contracts; scripted decisions establish no live quality claim."""
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.store import Store
from tests.support import ScriptedModel,tool,final


class ExecutionReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'project';self.root.mkdir()
        (self.root/'value.py').write_text('def value(): return 1\n')
        (self.root/'test_original.py').write_text('import unittest\nfrom value import value\nclass Check(unittest.TestCase):\n def test_value(self): self.assertEqual(value(),1)\n')
        self.store=Store(self.base/'tasks.sqlite');self.addCleanup(self.store.close)

    def run_responses(self,responses,*,steps=40):
        model=ScriptedModel(responses)
        engine=Engine(self.store,CodingTools(self.root,read_only_paths=('test_original.py',)),model)
        task=engine.create('Inspect the implementation and finish the requested work.',Limits(max_steps=steps,max_seconds=60))
        result=engine.run(task)
        return engine,task,result,model

    def reads(self,count):
        return [tool('read_file',{'path':'value.py'}) for i in range(count)]

    def reviews(self,task):
        return [e for e in self.store.events(task) if e['kind']=='execution_review']

    def test_known_test_then_stalled_reads_reaches_next_request_without_success_inference(self):
        _,task,result,model=self.run_responses([tool('run_tests',{},'verify')]+self.reads(7)+[final()])
        self.assertEqual(result['phase'],'completed')
        self.assertIsNone(result['verified_success'])
        events=self.reviews(task);self.assertEqual(len(events),1)
        self.assertEqual(events[0]['exit_code'],0)
        visible=[m for m in model.seen[-1] if (m.get('content') or '').startswith('Execution review:')]
        self.assertEqual(len(visible),1)
        self.assertIn('32 model requests remain',visible[0]['content'])
        self.assertIn('do not establish task completion',visible[0]['content'])
        calls={c['id'] for m in result['messages'] for c in m.get('tool_calls',[])}
        replies={m['tool_call_id'] for m in result['messages'] if m['role']=='tool'}
        self.assertEqual(calls,replies)

    def test_failed_tests_stay_failed_and_are_not_retried_by_review(self):
        (self.root/'value.py').write_text('def value(): return 0\n')
        _,task,result,model=self.run_responses([tool('run_tests',{},'verify')]+self.reads(7)+[final()])
        events=self.reviews(task);self.assertEqual(len(events),1)
        self.assertEqual(events[0]['exit_code'],1)
        verification=[json.loads(m['content']) for m in result['messages'] if m.get('tool_call_id')=='verify']
        self.assertFalse(verification[0]['ok']);self.assertEqual(verification[0]['exit_code'],1)
        self.assertEqual(sum(e['kind']=='tool_started' and e.get('tool')=='run_tests' for e in self.store.events(task)),1)
        self.assertIsNone(result['verified_success'])

    def test_two_reviews_maximum_and_no_extra_model_requests(self):
        _,task,result,model=self.run_responses([tool('run_tests',{},'verify')]+self.reads(23)+[final()])
        self.assertEqual(len(self.reviews(task)),2)
        self.assertEqual(result['metrics']['model_calls'],25)
        self.assertEqual(len(model.seen),25)

    def test_no_verification_and_stale_verification_after_edit_do_not_trigger(self):
        _,task,_,_=self.run_responses(self.reads(9)+[final()])
        self.assertEqual(self.reviews(task),[])
        _,task,_,_=self.run_responses([tool('run_tests',{},'verify'),
            tool('apply_patch',{'path':'value.py','old':'return 1','new':'return 2'},'change')]+self.reads(9)+[final()])
        self.assertEqual(self.reviews(task),[])

    def test_mutation_after_tests_in_same_batch_invalidates_that_verification(self):
        verify=tool('run_tests',{},'verify')
        edit=tool('apply_patch',{'path':'value.py','old':'return 1','new':'return 2'},'change')
        verify['message']['tool_calls'] += edit['message']['tool_calls']
        _,task,_,_=self.run_responses([verify]+self.reads(9)+[final()])
        self.assertEqual(self.reviews(task),[])

    def test_new_turn_gets_new_allowance_without_rewriting_previous_notice(self):
        engine,task,first,_=self.run_responses([tool('run_tests',{},'verify')]+self.reads(15)+[final()])
        self.assertEqual(len(self.reviews(task)),2)
        prefix=json.loads(json.dumps(first['messages']))
        engine.model=ScriptedModel([tool('run_tests',{},'nextverify')]+self.reads(7)+[final()])
        engine.continue_turn(task,'Continue checking the same project.')
        second=engine.run(task)
        self.assertEqual(len(self.reviews(task)),3)
        self.assertEqual(second['messages'][:len(prefix)],prefix)
        self.assertNotEqual(self.reviews(task)[0]['turn_id'],self.reviews(task)[-1]['turn_id'])

    def test_sigkill_after_notice_keeps_allowance_in_fresh_process_resume(self):
        marker=self.base/'killed.json'
        source="""import json,os,signal,sys
from pathlib import Path
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.store import Store
from tests.support import ScriptedModel,tool
root,db,marker=map(Path,sys.argv[1:])
def hook(event,state):
 if event=='after_execution_review':
  marker.write_text(json.dumps({'task':state['id'],'pid':os.getpid()}))
  os.kill(os.getpid(),signal.SIGKILL)
model=ScriptedModel([tool('run_tests',{})]+[tool('read_file',{'path':'value.py'}) for _ in range(7)])
with Store(db) as store:
 engine=Engine(store,CodingTools(root,read_only_paths=('test_original.py',)),model,hook=hook)
 engine.run(engine.create('Check and finish the implementation.',Limits(max_steps=40,max_seconds=60)))
"""
        child=subprocess.run([sys.executable,'-c',source,str(self.root),str(self.base/'tasks.sqlite'),str(marker)],
            env={'PATH':os.defpath,'LANG':'C.UTF-8','PYTHONPATH':str(Path(__file__).resolve().parents[2])},
            capture_output=True,timeout=60)
        self.assertEqual(child.returncode,-9)
        saved=json.loads(marker.read_text());self.assertNotEqual(saved['pid'],os.getpid())
        before=self.store.load(saved['task']);self.assertEqual(before['execution_review']['notices'],1)
        model=ScriptedModel(self.reads(16)+[final()])
        engine=Engine(self.store,CodingTools(self.root,read_only_paths=('test_original.py',)),model)
        result=engine.run(saved['task'])
        self.assertEqual(result['phase'],'completed')
        self.assertEqual(len(self.reviews(saved['task'])),2)
        self.assertEqual(result['messages'][:len(before['messages'])],before['messages'])
        self.assertEqual(result['metrics']['model_calls'],25)

    def test_existing_checkpoint_without_feature_marker_is_preserved(self):
        model=ScriptedModel([tool('run_tests',{},'verify')]+self.reads(9)+[final()])
        engine=Engine(self.store,CodingTools(self.root),model)
        task=engine.create('Continue legacy work.')
        state=self.store.load(task);state.pop('execution_review',None)
        self.store.save(state,'legacy_fixture')
        result=engine.run(task)
        self.assertEqual(self.reviews(task),[])
        self.assertNotIn('execution_review',result)
