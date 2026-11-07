"""Actor proofs and managed native lifecycle; no model API or real credentials."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine
from agent_lab import native_jobs
from agent_lab.native_job_runtime import worker_job,supervisor_job,supervisor_payload,current_tracker,SUPERVISOR_ENV
from agent_lab.process_guard import current_generation
from agent_lab.store import Store
from tests.support import ScriptedModel,tool,final


class NativeJobRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'work';self.root.mkdir();self.db=Path(self.tmp.name)/'state.sqlite'
        self.store=Store(self.db);self.addCleanup(self.store.close);self.tools=CodingTools(self.root)

    def reference(self):
        engine=Engine(self.store,self.tools,ScriptedModel([]));tid=engine.create('Controlled job')
        state=self.store.load(tid);engine._response(state,tool('run_tests',{},'fixture'),0);engine._prepare_next_tool(state)
        state['pending']['status']='running';self.store.save(state,'fixture')
        return native_jobs.claim_job(self.root,self.db,tid,state['turn_id'],state['pending']['operation_id'],current_generation(self.root),state['pending']['intent'])

    def test_registered_worker_can_confirm_no_supervisor_launch_without_processes(self):
        ref=self.reference()
        with worker_job(ref,self.root):pass
        native_jobs.require_quiescent(ref)
        self.assertEqual(native_jobs.inspect_workspace(self.root,self.db)['proof_kind'],'no_supervisor_launch')
        self.assertIsNone(current_tracker())

    def test_uncertain_spawn_registration_gap_never_generates_cleanup_receipt(self):
        ref=self.reference()
        with worker_job(ref,self.root) as worker:
            worker.supervisor_begin()
            with patch.dict(os.environ,{SUPERVISOR_ENV:supervisor_payload()}):
                with self.assertRaises(RuntimeError):
                    with supervisor_job(self.root):
                        current_tracker().native_begin()
                        raise RuntimeError('synthetic spawn/registration interruption')
        with self.assertRaises(native_jobs.JobBlocked):native_jobs.require_quiescent(ref)
        self.assertTrue(native_jobs.inspect_workspace(self.root,self.db)['workspace_quarantined'])
        self.assertIsNone(current_tracker())

    def test_wrong_worker_token_cannot_confirm_or_start_supervisor_scope(self):
        ref=self.reference()
        with worker_job(ref,self.root) as worker:
            worker.supervisor_begin();body=json.loads(supervisor_payload());body['worker_token']='f'*32
            with patch.dict(os.environ,{SUPERVISOR_ENV:json.dumps(body)}),self.assertRaises(native_jobs.JobStateError):
                with supervisor_job(self.root):self.fail('unowned supervisor entered')
        with self.assertRaises(native_jobs.JobBlocked):native_jobs.require_quiescent(ref)
        self.assertIsNone(current_tracker())

    def test_real_native_normal_completion_has_durable_quiescence_and_trace_job_id(self):
        (self.root/'test_pass.py').write_text('''import unittest,json,os
from pathlib import Path
class Pass(unittest.TestCase):
 def test_pass(self):
  report={'job_env_present':'AGENT_LAB_NATIVE_JOB' in os.environ,
          'api_env_present':any(name in os.environ for name in ('DEEPSEEK_API_KEY','OPENAI_API_KEY','ANTHROPIC_API_KEY'))}
  marker=Path('.agent-lab.native-job')
  try: marker.read_text();report['read_denied']=False
  except PermissionError: report['read_denied']=True
  try: marker.write_text('forged');report['write_denied']=False
  except PermissionError: report['write_denied']=True
  Path('guest-report.json').write_text(json.dumps(report))
  self.assertFalse(report['job_env_present']);self.assertFalse(report['api_env_present'])
  self.assertTrue(report['read_denied']);self.assertTrue(report['write_denied'])
''')
        model=ScriptedModel([tool('run_tests',{},'native'),final('done')]);engine=Engine(self.store,self.tools,model)
        tid=engine.create('Run tests')
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'synthetic-job-only','OPENAI_API_KEY':'synthetic-job-only'}):
            state=engine.run(tid)
        self.assertEqual(state['phase'],'completed',state['reason'])
        guest=json.loads((self.root/'guest-report.json').read_text())
        self.assertEqual(guest,{'job_env_present':False,'api_env_present':False,'read_denied':True,'write_denied':True})
        summary=native_jobs.inspect_workspace(self.root,self.db)
        self.assertEqual(summary['state'],'quiescent');self.assertEqual(summary['proof_kind'],'all_native_joined')
        self.assertEqual(state['last_native_job']['job_id'],summary['job_id']);self.assertTrue(state['last_native_job']['local_quiescent'])
        event=next(e for e in self.store.events(tid) if e['kind']=='tool_finished')
        self.assertEqual(event['native_job_id'],summary['job_id'])
        self.assertNotIn('worker_token',json.dumps(summary));self.assertNotIn(str(self.db),json.dumps(summary))
