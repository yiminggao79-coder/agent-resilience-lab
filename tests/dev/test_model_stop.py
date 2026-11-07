"""Host cancellation contracts; scripted responses are not model quality evidence."""
import contextlib
import os
from unittest.mock import patch
import copy
from pathlib import Path
import tempfile
import unittest
import uuid

from agent_lab.engine import Engine, Limits
from agent_lab.coding_tools import CodingTools
from agent_lab.model import ModelError
from agent_lab.store import Store
from tests.support import ScriptedModel, final
from tests.dev.test_history import pair


class ScopedModel(ScriptedModel):
    def __init__(self, action):
        super().__init__([])
        self.action = action
        self.check = None
        self.on_text = lambda text: None

    @contextlib.contextmanager
    def cancellation_scope(self, check):
        previous, self.check = self.check, check
        try:
            yield
        finally:
            self.check = previous

    def complete(self, *args):
        self.seen.append(copy.deepcopy(args[0]))
        return self.action(self, *args)


class ModelStopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'; self.root.mkdir()
        self.path = Path(self.tmp.name) / 'state.sqlite'
        self.store = Store(self.path); self.addCleanup(self.store.close)
        self.tools = CodingTools(self.root)

    def request(self, tid):
        with Store(self.path) as sender:
            state = sender.load(tid)
            sender.request_stop(tid, state['turn_id'], uuid.uuid4().hex, str(self.root))

    def test_scoped_stop_discards_incomplete_response_but_retains_unknown_usage(self):
        def cancel(model, *args):
            self.assertTrue(callable(model.check)); self.assertFalse(model.check())
            self.request(tid); self.assertTrue(model.check())
            raise ModelError('model_request_cancelled', uncertain=True)
        model = ScopedModel(cancel); engine = Engine(self.store, self.tools, model)
        tid = engine.create('Work', Limits(input_price=1, output_price=1))
        before = self.store.load(tid); state = engine.run(tid)
        self.assertEqual((state['phase'], state['reason']), ('failed', 'user_stop_requested'))
        self.assertTrue(state['usage_unknown']); self.assertFalse(state.get('outcome_unresolved', False))
        self.assertIsNone(state['metrics']['input_tokens']); self.assertIsNone(state['metrics']['cost_usd'])
        self.assertGreater(state['metrics']['reserved_usd'], 0)
        self.assertEqual(state['deadline'], before['deadline']); self.assertEqual(state['metrics']['tool_attempts'], 0)
        notice = state['messages'][state['last_model_cancellation']['notice_index']]
        self.assertEqual(notice['_runtime_control'], 'model_response_discarded')
        self.assertEqual(notice['role'], 'user'); self.assertEqual(state['last_model_cancellation']['purpose'], 'agent_decision')
        self.assertEqual(self.store.stop_request(tid, state['turn_id'])['status'], 'observed')
        self.assertIsNone(model.check)
        follow = Engine(self.store, self.tools, ScriptedModel([final()]))
        follow.continue_turn(tid, 'Inspect and continue')
        from agent_lab.context import build_context
        from agent_lab.protocols import build_request
        archive=self.store.load(tid)['messages']
        view,_=build_context(archive,[],50000)
        _,wire=build_request('chat','synthetic',view,[],32)
        self.assertFalse(any('_runtime_control' in m for m in wire['messages']))
        self.assertTrue(any('_runtime_control' in m for m in archive))
        self.assertTrue(any('incomplete model response' in (m.get('content') or '') for m in wire['messages']))
        continued = follow.run(tid)
        self.assertEqual(continued['phase'], 'completed')
        self.assertIsNone(continued['prior_metrics']['input_tokens'])

    def test_cancel_without_persisted_stop_is_not_clean(self):
        model = ScopedModel(lambda *args: (_ for _ in ()).throw(ModelError('model_request_cancelled', uncertain=True)))
        engine = Engine(self.store, self.tools, model); tid = engine.create('Work')
        state = engine.run(tid)
        self.assertEqual(state['phase'], 'needs_confirmation'); self.assertTrue(state['outcome_unresolved'])
        self.assertNotIn('last_model_cancellation', state)

    def test_prior_unresolved_outcome_is_never_cleared(self):
        def cancel(*args):
            self.request(tid)
            raise ModelError('model_request_cancelled', uncertain=True)
        engine = Engine(self.store, self.tools, ScopedModel(cancel)); tid = engine.create('Work')
        state = self.store.load(tid); state['outcome_unresolved'] = True; self.store.save(state, 'fixture')
        state = engine.run(tid)
        self.assertEqual(state['phase'], 'needs_confirmation'); self.assertTrue(state['outcome_unresolved'])

    def test_delegate_wrapper_is_still_called(self):
        def cancel(model, *args):
            self.assertIsNotNone(model.check); self.request(tid)
            raise ModelError('model_request_cancelled', uncertain=True)
        class Wrapper:
            def __init__(self, delegate): self.delegate=delegate; self.identity=delegate.identity; self.calls=0
            def complete(self, *args): self.calls+=1; return self.delegate.complete(*args)
        wrapper = Wrapper(ScopedModel(cancel)); engine = Engine(self.store,self.tools,wrapper); tid=engine.create('Work')
        self.assertEqual(engine.run(tid)['reason'], 'user_stop_requested'); self.assertEqual(wrapper.calls,1)

    def test_summary_cancel_keeps_prior_context_and_restores_presentation(self):
        def cancel(model, messages, tools, *args):
            self.assertEqual(tools, []); self.assertIsNone(model.on_text)
            self.request(tid); self.assertTrue(model.check())
            raise ModelError('model_request_cancelled', uncertain=True)
        model=ScopedModel(cancel); callback=model.on_text
        engine=Engine(self.store,self.tools,model); tid=engine.create('Work',Limits(max_context_bytes=50000))
        state=self.store.load(tid)
        state['messages'] += [{'role':'assistant','content':'old '*2000}] + pair('one','read_file',{'path':'a'},{'ok':True}) + pair('two','read_file',{'path':'b'},{'ok':True})
        original=copy.deepcopy(state['messages']); self.store.save(state,'fixture')
        state=engine.compact(tid)
        self.assertEqual((state['phase'],state['reason']),('failed','user_stop_requested'))
        self.assertEqual(state['last_compaction']['status'],'cancelled'); self.assertNotIn('semantic_context',state)
        self.assertEqual(state['messages'][:-1],original); self.assertNotIn('model_purpose',state)
        self.assertEqual(state['last_model_cancellation']['purpose'],'semantic_compaction')
        self.assertIs(model.on_text,callback); self.assertIsNone(model.check); self.assertEqual(len(model.seen),1)

    def test_cancelled_new_summary_preserves_previously_installed_record(self):
        from tests.dev.test_compaction import summary
        calls=[]
        def action(model, *args):
            calls.append(True)
            if len(calls)==1: return summary()
            self.request(tid)
            raise ModelError('model_request_cancelled',uncertain=True)
        model=ScopedModel(action); engine=Engine(self.store,self.tools,model)
        tid=engine.create('Work',Limits(max_context_bytes=50000))
        state=self.store.load(tid)
        state['messages'] += [{'role':'assistant','content':'old '*2000}]+pair('one','read_file',{'path':'a'},{'ok':True})+pair('two','read_file',{'path':'b'},{'ok':True})
        self.store.save(state,'fixture'); first=engine.compact(tid)
        self.assertEqual(first['last_compaction']['status'],'installed')
        state=self.store.load(tid)
        state['messages'] += [{'role':'assistant','content':'new '*1000}]+pair('three','read_file',{'path':'c'},{'ok':True})
        self.store.save(state,'fixture')
        stopped=engine.compact(tid)
        self.assertEqual(stopped['last_compaction']['status'],'cancelled')
        self.assertEqual(stopped['semantic_context'],first['semantic_context'])
        self.assertEqual(len(calls),2); self.assertTrue(stopped['usage_unknown'])

    def test_clean_cancel_keeps_budget_reservation_unknown_across_reopen(self):
        from agent_lab.budget import BudgetLedger, BudgetedModel
        def cancel(model, *args):
            self.request(tid); self.assertTrue(model.check())
            raise ModelError('model_request_cancelled', uncertain=True)
        delegate=ScopedModel(cancel)
        delegate.base_url='https://api.deepseek.com'; delegate.api_key_env='DEEPSEEK_API_KEY'
        delegate.model='deepseek-flash'; delegate.protocol='chat'; delegate.token_parameter='max_tokens'
        ledger_path=Path(self.tmp.name)/'synthetic-budget.sqlite'
        ledger=BudgetLedger(ledger_path)
        engine=Engine(self.store,self.tools,BudgetedModel(delegate,ledger)); tid=engine.create('Work')
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'synthetic-budget-only'}):
            state=engine.run(tid)
        self.assertEqual(state['reason'],'user_stop_requested')
        report=BudgetLedger(ledger_path).report()
        self.assertEqual(len(report['requests']),1)
        request=report['requests'][0]
        self.assertEqual(request['status'],'unknown'); self.assertIsNone(request['usage'])
        self.assertEqual(request['reserved_cny'],request['charged_upper_cny'])
        self.assertGreater(report['committed_upper_cny'],2)

    def test_keyboard_interrupt_marks_model_usage_unknown(self):
        model=ScopedModel(lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
        engine=Engine(self.store,self.tools,model); tid=engine.create('Work')
        state=engine.run(tid)
        self.assertEqual(state['phase'],'needs_confirmation'); self.assertTrue(state['usage_unknown'])
        self.assertIsNone(state['metrics']['input_tokens']); self.assertIsNone(state['metrics']['output_tokens'])
        self.assertIsNone(model.check)
