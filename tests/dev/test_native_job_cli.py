"""Native job metadata inspection must not create state or disclose paths/tokens."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.__main__ import main
from agent_lab import native_jobs
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine
from agent_lab.process_guard import current_generation
from agent_lab.store import Store
from tests.support import ScriptedModel,tool


class NativeJobCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir();self.db=self.base/'state.sqlite'

    def call(self,database=None):
        out,err=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            try:code=main(['--db',str(database or self.db),'job-status','--workspace',str(self.root)])
            except SystemExit as error:code=error.code
        return code,out.getvalue(),err.getvalue()

    def claim(self):
        with Store(self.db) as store:
            engine=Engine(store,CodingTools(self.root),ScriptedModel([]));tid=engine.create('PRIVATE task')
            state=store.load(tid);engine._response(state,tool('run_tests',{},'fixture'),0);engine._prepare_next_tool(state)
            state['pending']['status']='running';store.save(state,'fixture')
            return native_jobs.claim_job(self.root,self.db,tid,state['turn_id'],state['pending']['operation_id'],current_generation(self.root),state['pending']['intent'])

    def test_absent_job_and_database_remain_absent_without_model(self):
        with patch('agent_lab.__main__.OpenAIModel',side_effect=AssertionError('No model')):
            code,out,err=self.call()
        self.assertEqual(code,0,err);self.assertEqual(json.loads(out)['state'],'none')
        self.assertFalse(self.db.exists());self.assertEqual(list(self.root.iterdir()),[])

    def test_claimed_job_metadata_is_readonly_and_omits_private_paths_or_worker_token(self):
        ref=self.claim();native_jobs.register_worker(ref,'a'*32)
        before=self.db.read_bytes();marker=(self.root/native_jobs.MARKER).read_bytes()
        code,out,err=self.call();self.assertEqual(code,0,err);body=json.loads(out)
        self.assertTrue(body['workspace_quarantined']);self.assertEqual(body['job_id'],ref['job_id'])
        self.assertNotIn('PRIVATE',out);self.assertNotIn(str(self.db),out);self.assertNotIn(str(self.root),out)
        self.assertNotIn('a'*32,out);self.assertNotIn('worker_token',out)
        self.assertEqual(self.db.read_bytes(),before);self.assertEqual((self.root/native_jobs.MARKER).read_bytes(),marker)

    def test_foreign_database_is_not_followed_and_malformed_marker_does_not_echo_data(self):
        self.claim();foreign=self.base/'foreign.txt';foreign.write_text('PRIVATE foreign file')
        with patch.object(native_jobs,'_connection',side_effect=AssertionError('Foreign DB must not be opened')):
            code,out,err=self.call(foreign)
        self.assertEqual(code,0,err);body=json.loads(out);self.assertFalse(body['database_checked']);self.assertTrue(body['workspace_quarantined'])
        self.assertEqual(foreign.read_text(),'PRIVATE foreign file')
        (self.root/native_jobs.MARKER).write_text('{"PRIVATE":"must not echo"}')
        code,out,err=self.call();self.assertEqual(code,0,err)
        self.assertEqual(json.loads(out)['state'],'unavailable');self.assertNotIn('PRIVATE',out)
