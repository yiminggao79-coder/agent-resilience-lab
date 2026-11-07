import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from agent_lab.__main__ import main
from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.support import ScriptedModel


class StopCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'work';self.root.mkdir();self.db=Path(self.tmp.name)/'state.sqlite'
        with Store(self.db) as store:
            self.tid=Engine(store,Tools(self.root),ScriptedModel([])).create('PRIVATE request')
            self.turn=store.load(self.tid)['turn_id']

    def call(self,args):
        out=io.StringIO();err=io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):code=main(['--db',str(self.db)]+args)
        return code,out.getvalue(),err.getvalue()

    def test_idle_stop_observes_without_model_then_canonical_retry_and_readonly_status(self):
        rid=uuid.uuid4().hex
        args=['stop',self.tid,'--turn-id',self.turn,'--request-id',rid,'--workspace',str(self.root)]
        with patch('agent_lab.__main__.OpenAIModel',side_effect=AssertionError('No model')):
            code,out,_=self.call(args)
            self.assertEqual(code,0);result=json.loads(out)
            self.assertEqual(result['request']['status'],'observed')
            self.assertEqual(result['request']['observed_reason'],'user_stop_requested')
            self.assertFalse(result['current_call_preempted'])
            self.assertNotIn('PRIVATE',out)
            with Store(self.db) as store:events=store.events(self.tid)
            code,out,_=self.call(args);self.assertEqual(code,0)
            self.assertEqual(json.loads(out)['request']['status'],'observed')
            code,out,_=self.call(['stop-status',self.tid,'--turn-id',self.turn])
            self.assertEqual(code,0);self.assertEqual(json.loads(out)['status'],'observed')
            with Store(self.db) as store:self.assertEqual(store.events(self.tid),events)

    def test_busy_runner_gets_durable_request_without_false_stop_confirmation(self):
        with Store(self.db) as owner,owner.lock(self.root):
            code,out,_=self.call(['stop',self.tid,'--turn-id',self.turn,'--workspace',str(self.root)])
            self.assertEqual(code,0);result=json.loads(out)
            self.assertEqual(result['request']['status'],'requested')
            self.assertEqual(result['observation'],'deferred_until_checkpoint')
            self.assertIsNone(result['request']['observed_phase'])

    def test_wrong_workspace_rejects_before_touching_other_root_and_missing_status_never_creates_db(self):
        other=Path(self.tmp.name)/'other';other.mkdir()
        code,out,err=self.call(['stop',self.tid,'--turn-id',self.turn,'--workspace',str(other)])
        self.assertEqual(code,2);self.assertEqual(list(other.iterdir()),[])
        with Store(self.db) as store:self.assertIsNone(store.stop_request(self.tid,self.turn))
        missing=Path(self.tmp.name)/'absent.sqlite'
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            code=main(['--db',str(missing),'stop-status',self.tid,'--turn-id',self.turn])
        self.assertEqual(code,2);self.assertFalse(missing.exists())
