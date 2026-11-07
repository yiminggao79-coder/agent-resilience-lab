"""Cooperative host control contracts, not real-model task quality."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.model import ModelError
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.support import ScriptedModel,tool,final


def batch(*responses):
    value=copy.deepcopy(responses[0])
    value['message']['tool_calls']=[call for response in responses for call in response['message']['tool_calls']]
    return value


class StopTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'work';self.root.mkdir();self.path=Path(self.tmp.name)/'state.sqlite'
        self.store=Store(self.path);self.addCleanup(self.store.close)
        self.tools=CodingTools(self.root)

    def request(self,tid):
        with Store(self.path) as sender:
            return sender.request_stop(tid,sender.load(tid)['turn_id'],uuid.uuid4().hex,str(self.root))

    def test_stop_before_sampling_never_calls_model_and_observation_is_idempotent(self):
        model=ScriptedModel([]);engine=Engine(self.store,self.tools,model)
        tid=engine.create('Do work');receipt=self.request(tid)
        state=engine.run(tid)
        self.assertEqual((state['phase'],state['reason']),('failed','user_stop_requested'))
        self.assertEqual(model.seen,[]);self.assertEqual(state['metrics']['model_calls'],0)
        control=self.store.stop_request(tid,state['turn_id'])
        self.assertEqual(control['status'],'observed');self.assertEqual(control['request_id'],receipt['request_id'])
        events=self.store.events(tid)
        self.assertEqual(engine.run(tid),state);self.assertEqual(self.store.events(tid),events)

    def test_stop_during_claimed_model_preserves_known_usage_and_cancels_unstarted_tools(self):
        owner=self
        class Model(ScriptedModel):
            def complete(model,*args):
                owner.request(tid)
                return super().complete(*args)
        model=Model([batch(tool('write_file',{'path':'first','content':'x'}),tool('write_file',{'path':'second','content':'y'}))])
        engine=Engine(self.store,self.tools,model);tid=engine.create('Write files')
        state=engine.run(tid)
        self.assertEqual(state['reason'],'user_stop_requested')
        self.assertEqual(state['metrics']['input_tokens'],100)
        self.assertEqual(state['metrics']['tool_attempts'],0)
        self.assertFalse((self.root/'first').exists());self.assertFalse((self.root/'second').exists())
        results=[json.loads(m['content']) for m in state['messages'] if m['role']=='tool']
        self.assertEqual([r['error'] for r in results],['stopped_before_execution']*2)
        self.assertIsNone(state['pending']);self.assertEqual(state['tool_queue'],[])

    def test_stop_winning_model_final_commit_preserves_answer_and_usage_without_extra_sampling(self):
        owner=self
        class Model(ScriptedModel):
            def complete(model,*args):owner.request(tid);return super().complete(*args)
        model=Model([final('tentative answer')]);engine=Engine(self.store,self.tools,model)
        tid=engine.create('Answer');state=engine.run(tid)
        self.assertEqual(state['reason'],'user_stop_requested')
        self.assertEqual(state['messages'][-1],{'role':'assistant','content':'tentative answer'})
        self.assertEqual(state['metrics']['output_tokens'],20)
        self.assertEqual(len(model.seen),1)
        self.assertFalse(state.get('outcome_unresolved',False))

    def test_current_tool_finishes_and_next_batch_tool_never_starts(self):
        queued=[]
        def hook(event,state):
            if event=='after_tool_effect' and not queued:queued.append(self.request(tid))
        model=ScriptedModel([batch(tool('write_file',{'path':'first','content':'x'}),tool('write_file',{'path':'second','content':'y'}))])
        engine=Engine(self.store,self.tools,model,hook);tid=engine.create('Write files');state=engine.run(tid)
        self.assertEqual(state['reason'],'user_stop_requested')
        self.assertEqual((self.root/'first').read_text(),'x');self.assertFalse((self.root/'second').exists())
        self.assertEqual(state['metrics']['tool_attempts'],1)
        results=[json.loads(m['content']) for m in state['messages'] if m['role']=='tool']
        self.assertTrue(results[0]['ok']);self.assertEqual(results[1]['error'],'stopped_before_execution')

    def test_partial_patch_stops_after_confirmed_prefix_and_closes_one_result(self):
        queued=[]
        def hook(event,state):
            if event=='after_tool_effect' and not queued:queued.append(self.request(tid))
        body='*** Begin Patch\n*** Add File: first\n+x\n*** Add File: second\n+y\n*** End Patch'
        engine=Engine(self.store,self.tools,ScriptedModel([tool('apply_patch_text',{'patch':body})]),hook)
        tid=engine.create('Patch');state=engine.run(tid)
        self.assertEqual(state['reason'],'user_stop_requested')
        self.assertEqual((self.root/'first').read_text(),'x\n');self.assertFalse((self.root/'second').exists())
        results=[json.loads(m['content']) for m in state['messages'] if m['role']=='tool']
        self.assertEqual(len(results),1);self.assertTrue(results[0]['partial'])
        self.assertEqual(len(results[0]['files']),1)
        self.assertIsNone(state['pending']);self.assertFalse(state.get('outcome_unresolved',False))

    def test_stop_before_model_or_tool_claim_reloads_unclaimed_snapshot(self):
        for claim in ('model_started','tool_started','patch_step_started','compaction_started'):
            with self.subTest(claim=claim):
                root=self.root/claim;root.mkdir();tools=CodingTools(root)
                if claim=='model_started':responses=[]
                elif claim=='compaction_started':
                    from tests.dev.test_compaction import summary
                    responses=[summary()]
                else:responses=[tool('write_file',{'path':'file','content':'x'})] if claim=='tool_started' else [tool('apply_patch_text',{'patch':'*** Begin Patch\n*** Add File: file\n+x\n*** End Patch'})]
                model=ScriptedModel(responses);engine=Engine(self.store,tools,model)
                tid=engine.create('Work',Limits(auto_compact_bytes=1) if claim=='compaction_started' else None)
                if claim=='compaction_started':
                    from tests.dev.test_history import pair
                    state=self.store.load(tid);state['messages'] += [{'role':'assistant','content':'old '*2000}]+pair('old','read_file',{'path':'a'},{'ok':True})+pair('new','read_file',{'path':'b'},{'ok':True})
                    self.store.save(state,'fixture')
                original=self.store.save;once=[]
                def save(state,kind,meta=None,**kwargs):
                    if kind==claim and not once:
                        once.append(True)
                        with Store(self.path) as sender:sender.request_stop(tid,state['turn_id'],uuid.uuid4().hex,str(root))
                    return original(state,kind,meta,**kwargs)
                with patch.object(self.store,'save',side_effect=save):state=engine.run(tid)
                self.assertEqual(state['reason'],'user_stop_requested')
                self.assertFalse((root/'file').exists())
                if claim in ('model_started','compaction_started'):
                    self.assertEqual(model.seen,[])
                    self.assertEqual(state['metrics']['model_calls'],0)
                self.assertFalse(state.get('outcome_unresolved',False))

    def test_idle_approval_can_be_stopped_without_model_or_command_and_new_turn_is_allowed(self):
        model=ScriptedModel([tool('run_command',{'argv':['echo','PRIVATE command'],'cwd':'.'})])
        engine=Engine(self.store,self.tools,model);tid=engine.create('Command');waiting=engine.run(tid)
        self.assertEqual(waiting['phase'],'awaiting_approval');self.request(tid)
        stopped=Engine(self.store,Tools(self.root),None).observe_stop(tid,waiting['turn_id'])
        self.assertEqual(stopped['reason'],'user_stop_requested')
        self.assertEqual(stopped['metrics']['tool_attempts'],0)
        followup=Engine(self.store,self.tools,ScriptedModel([final('new turn')]))
        followup.continue_turn(tid,'New request');self.assertEqual(followup.run(tid)['phase'],'completed')
        self.assertNotEqual(self.store.load(tid)['turn_id'],waiting['turn_id'])

    def test_unconfirmed_running_tool_stop_never_calls_recover_or_adds_effect(self):
        (self.root/'file').write_text('old')
        model=ScriptedModel([tool('write_file',{'path':'file','content':'new'})]);engine=Engine(self.store,self.tools,model)
        tid=engine.create('Write');original=self.store.save
        def crash(state,kind,meta=None,**kwargs):
            original(state,kind,meta,**kwargs)
            if kind=='tool_started':raise SystemExit('unit boundary')
        with patch.object(self.store,'save',side_effect=crash),self.assertRaises(SystemExit):engine.run(tid)
        self.request(tid)
        with patch.object(self.tools,'recover',side_effect=AssertionError('must not replay')):state=engine.run(tid)
        self.assertEqual((state['phase'],state['reason']),('needs_confirmation','stop_requested_unconfirmed_effect'))
        self.assertEqual((self.root/'file').read_text(),'old')
        self.assertTrue(state['outcome_unresolved']);self.assertIsNotNone(state['pending'])

    def test_unconfirmed_model_stop_keeps_usage_unknown_without_request(self):
        model=ScriptedModel([]);engine=Engine(self.store,self.tools,model);tid=engine.create('Decide')
        original=self.store.save
        def crash(state,kind,meta=None,**kwargs):
            original(state,kind,meta,**kwargs)
            if kind=='model_started':raise SystemExit('unit boundary')
        with patch.object(self.store,'save',side_effect=crash),self.assertRaises(SystemExit):engine.run(tid)
        self.request(tid);state=engine.run(tid)
        self.assertEqual(state['reason'],'stop_requested_unconfirmed_model')
        self.assertIsNone(state['metrics']['input_tokens']);self.assertTrue(state['usage_unknown'])
        self.assertEqual(model.seen,[])

    def test_actual_unknown_error_reason_and_flags_survive_stop_observation(self):
        owner=self
        class Model(ScriptedModel):
            def complete(model,*args):owner.request(tid);raise ModelError('transport_unknown',uncertain=True)
        model=Model([]);engine=Engine(self.store,self.tools,model);tid=engine.create('Decide')
        state=engine.run(tid)
        self.assertEqual((state['phase'],state['reason']),('needs_confirmation','transport_unknown'))
        self.assertTrue(state['usage_unknown']);self.assertTrue(state['outcome_unresolved'])
        self.assertEqual(self.store.stop_request(tid,state['turn_id'])['observed_phase'],'needs_confirmation')

    def test_stop_winning_recovery_claim_cannot_trigger_missing_file_effect(self):
        (self.root/'file').write_text('old')
        model=ScriptedModel([tool('write_file',{'path':'file','content':'new'})])
        engine=Engine(self.store,self.tools,model);tid=engine.create('Write');original=self.store.save
        def crash(state,kind,meta=None,**kwargs):
            original(state,kind,meta,**kwargs)
            if kind=='tool_started':raise SystemExit('unit boundary')
        with patch.object(self.store,'save',side_effect=crash),self.assertRaises(SystemExit):engine.run(tid)
        queued=[]
        def racing_stop(state,kind,meta=None,**kwargs):
            if kind=='tool_recovery' and not queued:queued.append(self.request(tid))
            return original(state,kind,meta,**kwargs)
        with patch.object(self.store,'save',side_effect=racing_stop),patch.object(self.tools,'recover',side_effect=AssertionError('no replay')):
            state=engine.run(tid)
        self.assertEqual(state['reason'],'stop_requested_unconfirmed_effect')
        self.assertEqual((self.root/'file').read_text(),'old')

    def test_stopped_turn_cannot_start_explicit_compaction_or_discard_pending_user_feedback(self):
        owner=self
        class Model(ScriptedModel):
            def complete(model,*args):
                with Store(owner.path) as sender:
                    state=sender.load(tid)
                    sender.queue_input(tid,state['turn_id'],'unapplied feedback',uuid.uuid4().hex)
                owner.request(tid)
                return super().complete(*args)
        engine=Engine(self.store,self.tools,Model([final()]));tid=engine.create('Answer')
        state=engine.run(tid)
        self.assertEqual(state['reason'],'user_stop_requested')
        self.assertEqual(self.store.inputs(tid)[0]['status'],'pending')
        with self.assertRaisesRegex(ValueError,'turn_stop_requested'):engine.compact(tid)
        with self.assertRaisesRegex(ValueError,'followup_pending_inputs'):engine.continue_turn(tid,'next')

    def test_explicit_ready_compaction_observes_stop_after_preserving_known_summary(self):
        from tests.dev.test_compaction import summary
        from tests.dev.test_history import pair
        owner=self
        class Model(ScriptedModel):
            def complete(model,*args):owner.request(tid);return super().complete(*args)
        model=Model([summary()]);engine=Engine(self.store,self.tools,model);tid=engine.create('Continue work')
        state=self.store.load(tid)
        state['messages'] += [{'role':'assistant','content':'old discussion '*800}]+pair('old','read_file',{'path':'old'},{'ok':True})+pair('latest','read_file',{'path':'latest'},{'ok':True})
        self.store.save(state,'fixture')
        state=engine.compact(tid)
        self.assertEqual((state['phase'],state['reason']),('failed','user_stop_requested'))
        self.assertEqual(state['last_compaction']['status'],'installed')
        self.assertEqual(state['metrics']['input_tokens'],100)
        self.assertEqual(len(model.seen),1)
        self.assertEqual(self.store.stop_request(tid,state['turn_id'])['status'],'observed')
