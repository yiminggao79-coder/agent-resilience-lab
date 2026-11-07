"""Durable not-executed receipts for known unstarted calls, not model quality."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.store import Store,StorageUnavailable
from tests.support import ScriptedModel,tool,final
from tests.dev.test_tool_batches import batch


class TerminalHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'work';self.root.mkdir();self.path=Path(self.tmp.name)/'state.sqlite'
        self.store=Store(self.path);self.addCleanup(self.store.close)
        self.tools=CodingTools(self.root)

    def queued(self, limits=None):
        model=ScriptedModel([batch(tool('read_file',{'path':'missing'},'bad'),tool('write_file',{'path':'never','content':'no'},'tail')),final('new turn')])
        engine=Engine(self.store,self.tools,model);tid=engine.create('Work',limits or Limits(max_errors=1))
        return engine,tid,model

    def receipts(self,state):
        return [(m['tool_call_id'],json.loads(m['content'])) for m in state['messages'] if m['role']=='tool']

    def test_error_limit_closes_tail_durably_without_running_it_and_new_turn_works(self):
        engine,tid,model=self.queued();state=engine.run(tid)
        self.assertEqual((state['phase'],state['reason']),('failed','error_limit'))
        self.assertEqual(self.receipts(state)[-1],('tail',{'ok':False,'error':'not_executed','reason':'error_limit'}))
        self.assertEqual(state['tool_queue'],[]);self.assertIsNone(state['pending'])
        self.assertFalse((self.root/'never').exists());self.assertEqual(state['metrics']['model_calls'],1)
        self.assertEqual(self.store.events(tid)[-1]['unstarted_calls'],1)
        with Store.open_readonly(self.path) as reopened:self.assertEqual(reopened.load(tid),state)
        engine.continue_turn(tid,'Do a new task');self.assertEqual(engine.run(tid)['phase'],'completed')
        self.assertFalse((self.root/'never').exists());self.assertEqual(len(model.seen),2)
        from agent_lab.context import build_context
        from agent_lab.protocols import build_request
        view,_=build_context(model.seen[-1],[],50000);_,wire=build_request('chat','synthetic',view,[],32)
        self.assertEqual(engine._outstanding_calls(wire['messages']),[])
        self.assertFalse(any('_runtime_control' in m for m in wire['messages']))

    def test_planned_tool_deadline_has_no_side_effect_and_keeps_attempt_count_zero(self):
        engine=Engine(self.store,self.tools,ScriptedModel([tool('write_file',{'path':'never','content':'x'},'one')]))
        tid=engine.create('Work');state=self.store.load(tid)
        engine._response(state,tool('write_file',{'path':'never','content':'x'},'one'),0)
        engine._prepare_next_tool(state)
        state['deadline']=0;self.store.save(state,'fixture')
        state=engine.run(tid)
        self.assertIsNone(state['pending']);self.assertEqual(state['metrics']['tool_attempts'],0)
        self.assertEqual(self.receipts(state)[-1][1]['reason'],'time_limit');self.assertFalse((self.root/'never').exists())

    def test_abort_awaiting_approval_closes_current_and_tail_without_approval(self):
        response=batch(tool('run_command',{'argv':['echo','x'],'cwd':'.'},'command'),tool('write_file',{'path':'never','content':'no'},'tail'))
        engine=Engine(self.store,self.tools,ScriptedModel([response]));tid=engine.create('Work')
        self.assertEqual(engine.run(tid)['phase'],'awaiting_approval');state=engine.abort(tid)
        self.assertEqual([i for i,_ in self.receipts(state)],['command','tail'])
        self.assertTrue(all(r['error']=='not_executed' for _,r in self.receipts(state)))
        self.assertEqual(state['metrics']['tool_attempts'],0);self.assertIsNone(state['pending'])
        self.assertFalse((self.root/'never').exists())

    def test_failed_terminal_save_leaves_original_queue_for_safe_reopen_and_one_receipt(self):
        engine,tid,_=self.queued();original=self.store.save
        def fail(state,kind,*args,**kwargs):
            if kind=='terminated':raise StorageUnavailable('storage_unavailable')
            return original(state,kind,*args,**kwargs)
        with patch.object(self.store,'save',side_effect=fail),self.assertRaises(StorageUnavailable):engine.run(tid)
        durable=self.store.load(tid);self.assertEqual([c['id'] for c in durable['tool_queue']],['tail'])
        self.assertEqual(len(self.receipts(durable)),1)
        state=engine.run(tid);again=engine.run(tid)
        self.assertEqual(state,again);self.assertEqual([i for i,_ in self.receipts(state)],['bad','tail'])
        self.assertFalse((self.root/'never').exists())

    def test_inconsistent_history_is_not_closed_or_unlocked(self):
        engine,tid,_=self.queued();state=self.store.load(tid)
        response=batch(tool('read_file',{'path':'missing'},'bad'),tool('write_file',{'path':'never','content':'no'},'tail'))
        engine._response(state,response,0)
        state['messages'][-1]['tool_calls'][1]['id']='different';state['deadline']=0;self.store.save(state,'fixture')
        state=engine.run(tid)
        self.assertEqual((state['phase'],state['reason']),('needs_confirmation','terminal_history_mismatch'))
        self.assertEqual(len(state['tool_queue']),2);self.assertEqual(self.receipts(state),[])
        with self.assertRaises(ValueError):engine.continue_turn(tid,'Continue')

    def test_running_or_prior_unknown_is_never_classified_not_executed(self):
        for running,unknown in ((True,False),(False,True)):
            with self.subTest(running=running,unknown=unknown):
                engine,tid,_=self.queued();state=self.store.load(tid)
                engine._response(state,tool('write_file',{'path':'never','content':'x'},'one'),0);engine._prepare_next_tool(state)
                if running:state['pending']['status']='running'
                if unknown:state['outcome_unresolved']=True
                self.store.save(state,'fixture');state=engine.abort(tid)
                self.assertIsNotNone(state['pending']);self.assertEqual(self.receipts(state),[])
                with self.assertRaises(ValueError):engine.continue_turn(tid,'Continue')
                self.assertFalse((self.root/'never').exists())
