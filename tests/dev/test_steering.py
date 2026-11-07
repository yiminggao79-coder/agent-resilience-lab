"""Same-Turn user input contracts; deterministic models are not quality evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from agent_lab.engine import Engine,Limits
from agent_lab.model import ModelError
from agent_lab.store import Store,StorageUnavailable
from agent_lab.tools import Tools
from tests.support import ScriptedModel,final,tool


class SteeringTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'work';self.root.mkdir()
        self.path=Path(self.tmp.name)/'private.sqlite'
        self.store=Store(self.path);self.addCleanup(self.store.close)

    def queue(self,tid,text='PRIVATE USER FEEDBACK'):
        turn=self.store.load(tid)['turn_id']
        with Store(self.path) as sender:
            return sender.queue_input(tid,turn,text,uuid.uuid4().hex)

    def test_input_during_model_final_is_applied_before_a_new_decision(self):
        owner=self
        class Model(ScriptedModel):
            def complete(model,*args):
                if not model.seen:owner.queue(tid)
                return super().complete(*args)
        model=Model([final('tentative answer'),final('revised answer')])
        engine=Engine(self.store,Tools(self.root),model);tid=engine.create('initial')
        before=self.store.load(tid)
        state=engine.run(tid)
        self.assertEqual(state['phase'],'completed')
        self.assertEqual(len(model.seen),2)
        self.assertEqual(model.seen[1][-2:],[{'role':'assistant','content':'tentative answer'},
                                          {'role':'user','content':'PRIVATE USER FEEDBACK'}])
        self.assertEqual(state['turn_id'],before['turn_id'])
        self.assertEqual(state['deadline'],before['deadline'])
        self.assertEqual(self.store.inputs(tid)[0]['status'],'applied')
        self.assertNotIn('PRIVATE USER FEEDBACK',json.dumps(self.store.events(tid)))
        self.assertEqual(len([e for e in self.store.events(tid) if e['kind']=='model_yielded']),1)

    def test_batch_finishes_before_feedback_and_archive_pairs_stay_complete(self):
        (self.root/'a').write_text('A');(self.root/'b').write_text('B')
        batch=tool('read_file',{'path':'a'});batch['message']['tool_calls']+=tool('read_file',{'path':'b'})['message']['tool_calls']
        queued=[]
        def hook(event,state):
            if event=='after_tool_effect' and not queued:queued.append(self.queue(tid,'change direction'))
        model=ScriptedModel([batch,final('done')]);engine=Engine(self.store,Tools(self.root),model,hook=hook)
        tid=engine.create('inspect');state=engine.run(tid)
        suffix=model.seen[1][-3:]
        self.assertEqual([m['role'] for m in suffix],['tool','tool','user'])
        self.assertEqual(suffix[-1]['content'],'change direction')
        self.assertEqual(state['metrics']['tool_attempts'],2)
        self.assertEqual(state['metrics']['model_calls'],2)
        self.assertEqual(self.store.inputs(tid)[0]['message_index'],len(state['messages'])-2)

    def test_applied_receipt_and_history_survive_reopen_without_duplicate(self):
        model=ScriptedModel([final()]);engine=Engine(self.store,Tools(self.root),model)
        tid=engine.create('initial');self.queue(tid,'new constraint')
        state=engine.run(tid)
        with Store(self.path) as reopened:
            self.assertEqual(Engine(reopened,Tools(self.root),ScriptedModel([])).run(tid),state)
            self.assertEqual([m.get('content') for m in state['messages']].count('new constraint'),1)
            self.assertEqual(reopened.inputs(tid)[0]['status'],'applied')

    def test_unapplied_feedback_does_not_reset_quotas_or_cross_turns(self):
        model=ScriptedModel([]);engine=Engine(self.store,Tools(self.root),model)
        tid=engine.create('initial',Limits(max_steps=1));state=self.store.load(tid)
        state['metrics']['model_calls']=1;self.store.save(state,'test_limit')
        receipt=self.queue(tid,'pending at quota');state=engine.run(tid)
        self.assertEqual(state['reason'],'step_limit')
        self.assertEqual(self.store.inputs(tid)[0]['status'],'pending')
        with self.assertRaisesRegex(ValueError,'followup_pending_inputs'):engine.continue_turn(tid,'next')
        self.store.withdraw_input(tid,receipt['request_id'])
        engine.continue_turn(tid,'next')
        self.assertNotIn('pending at quota',json.dumps(self.store.load(tid)['messages']))

    def test_unknown_model_outcome_never_consumes_queued_input(self):
        engine=Engine(self.store,Tools(self.root),ScriptedModel([ModelError('transport_unknown',uncertain=True)]))
        tid=engine.create('initial')
        # Simulate a response failing after admission while model_pending.
        def hook(event,state):
            if event=='before_model_request':self.queue(tid,'feedback before disconnect')
        engine.hook=hook;state=engine.run(tid)
        self.assertEqual(state['phase'],'needs_confirmation')
        self.assertEqual(self.store.inputs(tid)[0]['status'],'pending')
        self.assertNotIn('feedback before disconnect',json.dumps(state['messages']))
        self.assertTrue(state['outcome_unresolved'])

    def test_application_storage_failure_cannot_ack_or_append_without_commit(self):
        model=ScriptedModel([final()]);engine=Engine(self.store,Tools(self.root),model)
        tid=engine.create('initial');self.queue(tid,'atomic feedback')
        original=self.store.save
        def fail(state,kind,meta=None,**kwargs):
            if kwargs.get('applied_inputs'):raise StorageUnavailable('storage_unavailable')
            return original(state,kind,meta,**kwargs)
        with patch.object(self.store,'save',side_effect=fail),self.assertRaises(StorageUnavailable):engine.run(tid)
        self.assertEqual(self.store.inputs(tid)[0]['status'],'pending')
        self.assertNotIn('atomic feedback',json.dumps(self.store.load(tid)['messages']))
        self.assertEqual(len(model.seen),0)
        state=engine.run(tid)
        self.assertEqual([m.get('content') for m in state['messages']].count('atomic feedback'),1)

    def test_withdrawal_winning_application_race_does_not_fail_or_apply_stale_copy(self):
        model=ScriptedModel([final()]);engine=Engine(self.store,Tools(self.root),model)
        tid=engine.create('initial');receipt=self.queue(tid,'withdraw me')
        original=self.store.pending_inputs;withdrawn=[]
        def racing_read(*args):
            rows=original(*args)
            if rows and not withdrawn:
                with Store(self.path) as sender:sender.withdraw_input(tid,receipt['request_id'])
                withdrawn.append(True)
            return rows
        with patch.object(self.store,'pending_inputs',side_effect=racing_read):state=engine.run(tid)
        self.assertEqual(state['phase'],'completed')
        self.assertNotIn('withdraw me',json.dumps(state['messages']))
        self.assertEqual(self.store.inputs(tid)[0]['status'],'withdrawn')
