"""Engine cancellation receipt contracts; no native/process proof here."""
import contextlib
from pathlib import Path
import tempfile
import unittest
import uuid

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine
from agent_lab.store import Store
from tests.support import ScriptedModel,tool,final
from tests.dev.test_tool_batches import batch


class ScopeTools(CodingTools):
    def __init__(self,root,action):super().__init__(root);self.action=action;self.check=None
    @contextlib.contextmanager
    def cancellation_scope(self,check):
        previous,self.check=self.check,check
        try:yield
        finally:self.check=previous
    def execute(self,intent,timeout):return self.action(self,intent,timeout)


class ToolStopTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'work';self.root.mkdir();self.path=Path(self.tmp.name)/'state.sqlite'
        self.store=Store(self.path);self.addCleanup(self.store.close)

    def request(self,tid):
        with Store(self.path) as sender:
            state=sender.load(tid)
            sender.request_stop(tid,state['turn_id'],uuid.uuid4().hex,str(self.root))

    def cancelling(self,tid_ref,quiescent=True):
        def execute(tools,intent,timeout):
            self.assertTrue(callable(tools.check));self.assertFalse(tools.check())
            self.request(tid_ref[0]);self.assertTrue(tools.check())
            from agent_lab.tool_control import ToolCancelled
            raise ToolCancelled(local_quiescent=quiescent)
        return execute

    def test_mutating_native_cancel_retains_unknown_effect_and_pending_after_local_cleanup(self):
        ref=[];tools=ScopeTools(self.root,self.cancelling(ref))
        model=ScriptedModel([batch(tool('run_tests',{},'current'),tool('write_file',{'path':'never','content':'x'},'tail'))])
        engine=Engine(self.store,tools,model);tid=engine.create('Test project');ref.append(tid)
        state=engine.run(tid)
        self.assertEqual((state['phase'],state['reason']),('needs_confirmation','tool_cancelled_effect_unknown'))
        self.assertTrue(state['outcome_unresolved']);self.assertEqual(state['pending']['call_id'],'current')
        self.assertEqual([c['id'] for c in state['tool_queue']],['tail'])
        self.assertEqual(state['last_tool_cancellation']['operation_id'],state['pending']['operation_id'])
        self.assertTrue(state['last_tool_cancellation']['local_quiescent'])
        self.assertTrue(state['last_tool_cancellation']['effects_unknown'])
        self.assertEqual(state['metrics']['input_tokens'],100);self.assertEqual(state['metrics']['tool_attempts'],1)
        self.assertFalse((self.root/'never').exists());self.assertIsNone(tools.check)
        self.assertEqual(self.store.stop_request(tid,state['turn_id'])['status'],'observed')
        event=next(e for e in self.store.events(tid) if e['kind']=='tool_cancelled')
        self.assertTrue(event['local_quiescent']);self.assertEqual(event['operation_id'],state['pending']['operation_id'])
        with self.assertRaises(ValueError):engine.continue_turn(tid,'Continue')

    def test_readonly_cancel_closes_result_and_unstarted_tail_without_fake_success(self):
        (self.root/'source').write_text('real')
        ref=[];tools=ScopeTools(self.root,self.cancelling(ref))
        model=ScriptedModel([batch(tool('read_file',{'path':'source'},'current'),tool('write_file',{'path':'never','content':'x'},'tail')),final('new')])
        engine=Engine(self.store,tools,model);tid=engine.create('Read project');ref.append(tid)
        state=engine.run(tid)
        self.assertEqual((state['phase'],state['reason']),('failed','user_stop_requested'))
        self.assertFalse(state.get('outcome_unresolved',False));self.assertIsNone(state['pending']);self.assertEqual(state['tool_queue'],[])
        import json
        results=[json.loads(m['content']) for m in state['messages'] if m['role']=='tool']
        self.assertEqual([r['error'] for r in results],['tool_cancelled','stopped_before_execution'])
        self.assertTrue(results[0]['local_quiescent']);self.assertFalse(state['last_tool_cancellation']['effects_unknown'])
        self.assertFalse((self.root/'never').exists());engine.continue_turn(tid,'New instruction')
        self.assertEqual(engine.run(tid)['phase'],'completed')

    def test_unconfirmed_cancel_never_unlocks_even_readonly_execution(self):
        (self.root/'source').write_text('real');ref=[];tools=ScopeTools(self.root,self.cancelling(ref,False))
        engine=Engine(self.store,tools,ScriptedModel([tool('read_file',{'path':'source'},'current')]))
        tid=engine.create('Read');ref.append(tid);state=engine.run(tid)
        self.assertEqual((state['phase'],state['reason']),('needs_confirmation','tool_cancel_unconfirmed'))
        self.assertTrue(state['outcome_unresolved']);self.assertIsNotNone(state['pending'])
        self.assertFalse(state['last_tool_cancellation']['local_quiescent'])

    def test_registry_style_base_wrapper_is_not_bypassed(self):
        ref=[];base=ScopeTools(self.root,self.cancelling(ref))
        class Wrapper:
            def __init__(self):self.base=base;self.calls=0
            def __getattr__(self,key):
                if key=='cancellation_scope':raise AttributeError(key)
                return getattr(self.base,key)
            def execute(self,*args):self.calls+=1;return self.base.execute(*args)
        tools=Wrapper();engine=Engine(self.store,tools,ScriptedModel([tool('run_tests',{},'current')]))
        tid=engine.create('Test');ref.append(tid);state=engine.run(tid)
        self.assertEqual(state['reason'],'tool_cancelled_effect_unknown');self.assertEqual(tools.calls,1)
        self.assertIsNone(base.check)
