"""Read-only checkpoint diagnostics; no operation verification or model call."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import uuid
import threading
from unittest.mock import patch

from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.support import ScriptedModel


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'work';self.root.mkdir();self.path=Path(self.tmp.name)/'state.sqlite'
        self.store=Store(self.path);self.addCleanup(self.store.close)
        self.engine=Engine(self.store,Tools(self.root),ScriptedModel([]))
        self.tid=self.engine.create('PRIVATE initial user requirement')
        self.state=self.store.load(self.tid)

    def report(self,state=None,show_intent=False):
        self.assertIsNotNone(importlib.util.find_spec('agent_lab.diagnostics'),'diagnostic formatter is missing')
        from agent_lab.diagnostics import describe_pending
        return describe_pending(*(self.store.diagnostic_view(self.tid) if state is None else (state,[])),show_intent=show_intent)

    def pending_state(self):
        state=copy.deepcopy(self.state);state.update(phase='needs_confirmation',reason='tool_outcome_unknown',outcome_unresolved=True)
        state['messages']+=[{'role':'assistant','content':'PRIVATE assistant','reasoning_content':'PRIVATE reasoning',
                            'native_content':[{'encrypted_content':'PRIVATE native'}]}]
        state['pending']={'operation_id':uuid.uuid4().hex,'status':'running','call_id':'private_call',
                          'intent':{'name':'write_file','args':{'path':'PRIVATE/path.py','content':'PRIVATE desired file'},
                                    'policy':'verify','before':'a'*64,'desired':'b'*64,'before_mode':0o755,'desired_mode':0o755}}
        return state

    def test_default_metadata_has_confirmation_boundary_and_no_private_bodies(self):
        state=self.pending_state();self.store.save(state,'test_unknown')
        before=self.store.load(self.tid);events=self.store.events(self.tid)
        result=self.report()
        self.assertEqual(result['observed_revision'],state['revision'])
        self.assertEqual(result['operation']['operation_id'],state['pending']['operation_id'])
        self.assertEqual(result['operation']['before_mode'],0o755)
        self.assertEqual(result['operation']['recovery_policy'],'verify_target_before_replay')
        self.assertTrue(result['outcome_unresolved'])
        self.assertEqual(result['runner_liveness'],'not_checked')
        self.assertEqual(result['filesystem_verification'],'not_performed')
        self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertEqual(self.store.load(self.tid),before)
        self.assertEqual(self.store.events(self.tid),events)
        private=self.report(show_intent=True)
        self.assertEqual(private['intent'],state['pending']['intent'])
        self.assertNotIn('PRIVATE reasoning',json.dumps(private))
        self.assertNotIn('PRIVATE native',json.dumps(private))
        self.assertNotIn('PRIVATE initial',json.dumps(private))

    def test_running_model_is_not_diagnosed_as_confirmed_lost_request(self):
        state=copy.deepcopy(self.state);state.update(phase='model_pending',model_purpose='semantic_compaction')
        result=self.report(state)
        self.assertEqual(result['model_request'],'pending_unconfirmed')
        self.assertFalse(result['outcome_unresolved'])
        self.assertEqual(result['runner_liveness'],'not_checked')
        state.update(phase='failed',reason='user_aborted',outcome_unresolved=True,usage_unknown=True)
        result=self.report(state)
        self.assertTrue(result['outcome_unresolved']);self.assertTrue(result['usage_unknown'])
        self.assertIn('unknown_outcome_blocks_automatic_continuation',result['guidance'])

    def test_approval_unsafe_and_partial_patch_views_never_claim_effects_verified(self):
        state=self.pending_state();state['phase']='awaiting_approval'
        state['pending']['intent']={'name':'run_command','args':{'argv':['echo','PRIVATE command'],'cwd':''},'policy':'unsafe'}
        state['pending']['approval']={'request_id':uuid.uuid4().hex,'digest':'a'*64,'status':'awaiting'}
        result=self.report(state)
        self.assertEqual(result['operation']['recovery_policy'],'never_replay_unknown_effect')
        self.assertIn('review_exact_approval',result['guidance'])
        self.assertNotIn('PRIVATE',json.dumps(result))
        state['phase']='needs_confirmation';state['pending']['intent']={'name':'apply_patch_text','args':{},'policy':'verify_patch',
                           'steps':[{'file_intent':{'args':{'path':'PRIVATE source'}}},{'file_intent':{'args':{'path':'PRIVATE target'}}}]}
        state['pending'].update(patch_cursor=1,patch_inflight=True,patch_receipts=[{'path':'PRIVATE source','ok':True}])
        result=self.report(state)
        self.assertEqual(result['operation']['patch'],{'cursor':1,'total_steps':2,'inflight':True,'confirmed_receipts':1})
        self.assertNotIn('PRIVATE',json.dumps(result))

    def test_pending_input_metadata_is_preserved_and_not_automatically_withdrawn(self):
        receipt=self.store.queue_input(self.tid,self.state['turn_id'],'PRIVATE queued feedback',uuid.uuid4().hex)
        result=self.report()
        self.assertEqual(result['pending_inputs'][0]['request_id'],receipt['request_id'])
        self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertEqual(self.store.inputs(self.tid)[0]['status'],'pending')
        self.assertIn('pending_feedback_waits_for_sampling_boundary',result['guidance'])

    def test_concurrent_admission_cannot_mix_two_database_observation_times(self):
        ready=threading.Barrier(2);read_started=threading.Event();writer_staged=threading.Event()
        writer_done=threading.Event();views=[];errors=[]
        def writer():
            try:
                with Store(self.path) as sender:
                    sender.db.set_trace_callback(lambda sql: writer_staged.set() if sql.startswith('INSERT INTO steering_inputs') else None)
                    ready.wait(2)
                    if not read_started.wait(2):raise AssertionError('reader did not start')
                    sender.queue_input(self.tid,self.state['turn_id'],'concurrent input',uuid.uuid4().hex)
                    writer_done.set()
            except Exception as error:errors.append(type(error).__name__)
        def reader():
            try:
                with Store(self.path) as inspector:
                    load=inspector.load
                    def hold_snapshot(tid):
                        state=load(tid);read_started.set()
                        if not writer_staged.wait(2):raise AssertionError('writer did not stage')
                        # The writer has inserted but cannot commit while this
                        # explicit rollback-journal read transaction is active.
                        if writer_done.wait(.1):raise AssertionError('mixed snapshot window')
                        return state
                    ready.wait(2)
                    with patch.object(inspector,'load',side_effect=hold_snapshot):
                        views.append(inspector.diagnostic_view(self.tid))
            except Exception as error:errors.append(type(error).__name__)
        threads=[threading.Thread(target=writer),threading.Thread(target=reader)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors,[])
        self.assertTrue(writer_done.is_set())
        self.assertEqual(views[0][1],[])
        self.assertEqual(self.store.inputs(self.tid)[0]['status'],'pending')

    def test_malformed_optional_metadata_is_omitted_without_private_exception_text(self):
        state=self.pending_state();state['metrics']=['PRIVATE malformed']
        state['reason']={'PRIVATE reason':True}
        state['pending']['intent'].update(name='PRIVATE invalid tool',policy={'PRIVATE policy':True})
        result=self.report(state)
        self.assertIsNone(result['reason'])
        self.assertEqual(result['operation']['recovery_policy'],'unknown_policy_requires_review')
        self.assertNotIn('PRIVATE',json.dumps(result))
