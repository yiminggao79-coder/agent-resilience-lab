import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from agent_lab.__main__ import main
from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.support import ScriptedModel


class SteeringCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'work';self.root.mkdir();self.path=Path(self.tmp.name)/'state.sqlite'
        with Store(self.path) as store:
            self.tid=Engine(store,Tools(self.root),ScriptedModel([])).create('initial')
            self.turn=store.load(self.tid)['turn_id']

    def invoke(self,args,text=''):
        out=io.StringIO();err=io.StringIO()
        with patch('sys.stdin',io.StringIO(text)),contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            code=main(['--db',str(self.path)]+args)
        return code,out.getvalue(),err.getvalue()

    def test_stdin_receipt_metadata_private_text_and_withdraw_without_model(self):
        rid=uuid.uuid4().hex
        with patch('agent_lab.__main__.OpenAIModel',side_effect=AssertionError('No model construction')):
            code,out,_=self.invoke(['steer',self.tid,'--turn-id',self.turn,'--request-id',rid],'PRIVATE direct feedback')
            self.assertEqual(code,0);receipt=json.loads(out)
            self.assertEqual(receipt['request_id'],rid);self.assertEqual(receipt['status'],'pending')
            self.assertNotIn('PRIVATE',out)
            code,out,_=self.invoke(['inputs',self.tid]);self.assertEqual(code,0);self.assertNotIn('PRIVATE',out)
            code,out,_=self.invoke(['inputs',self.tid,'--show-text']);self.assertEqual(code,0)
            self.assertEqual(json.loads(out)[0]['text'],'PRIVATE direct feedback')
            code,out,_=self.invoke(['input-withdraw',self.tid,rid]);self.assertEqual(code,0)
            self.assertEqual(json.loads(out)['status'],'withdrawn')
            _,trace,_=self.invoke(['trace',self.tid]);self.assertNotIn('PRIVATE',trace)

    def test_stale_turn_and_oversized_stdin_fail_without_text_leak(self):
        code,out,err=self.invoke(['steer',self.tid,'--turn-id',uuid.uuid4().hex],'PRIVATE stale')
        self.assertEqual(code,2);self.assertNotIn('PRIVATE',out+err)
        code,out,err=self.invoke(['steer',self.tid,'--turn-id',self.turn],'x'*65537)
        self.assertEqual(code,2);self.assertNotIn('xxx',out+err)
