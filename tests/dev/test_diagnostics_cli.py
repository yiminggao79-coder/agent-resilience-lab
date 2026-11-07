import contextlib
import io
import json
from pathlib import Path
import tempfile
import hashlib
import stat
import unittest
from unittest.mock import patch

from agent_lab.__main__ import main
from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.support import ScriptedModel


class DiagnosticCLITests(unittest.TestCase):
    def test_inspection_never_creates_a_database_or_migrates_legacy_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'work';root.mkdir();db=Path(temp)/'state.sqlite'
            with Store(db) as store:
                tid=Engine(store,Tools(root),ScriptedModel([])).create('legacy task')
                store.db.execute('DROP TABLE steering_inputs');store.db.commit()
            db.chmod(0o640)
            digest=hashlib.sha256(db.read_bytes()).hexdigest()
            entries={p.name for p in Path(temp).iterdir()}
            out=io.StringIO()
            with contextlib.redirect_stdout(out):
                code=main(['--db',str(db),'pending',tid])
            self.assertEqual(code,0)
            self.assertEqual(json.loads(out.getvalue())['pending_inputs'],[])
            self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(),digest)
            self.assertEqual(stat.S_IMODE(db.stat().st_mode),0o640)
            self.assertEqual({p.name for p in Path(temp).iterdir()},entries)
            missing=Path(temp)/'absent.sqlite'
            with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                code=main(['--db',str(missing),'pending',tid])
            self.assertEqual(code,2)
            self.assertFalse(missing.exists())

    def test_pending_reads_private_db_without_constructing_tools_or_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'work';root.mkdir();db=Path(temp)/'state.sqlite'
            with Store(db) as store:
                tid=Engine(store,Tools(root),ScriptedModel([])).create('PRIVATE task')
                state=store.load(tid);state['phase']='needs_confirmation';state['outcome_unresolved']=True
                state['pending']={'operation_id':'a'*32,'status':'running','intent':
                                 {'name':'append_file','policy':'unsafe','args':{'path':'PRIVATE file','content':'PRIVATE data'}}}
                store.save(state,'test_pause')
                events=store.events(tid)
            for private in (False,True):
                out=io.StringIO()
                with patch('agent_lab.__main__.OpenAIModel',side_effect=AssertionError('No model')),\
                     patch('agent_lab.__main__.Tools',side_effect=AssertionError('No tools')),\
                     contextlib.redirect_stdout(out):
                    code=main(['--db',str(db),'pending',tid]+(['--show-intent'] if private else []))
                self.assertEqual(code,0);result=json.loads(out.getvalue())
                self.assertEqual('intent' in result,private)
                self.assertEqual('PRIVATE data' in out.getvalue(),private)
            with Store(db) as store:self.assertEqual(store.events(tid),events)
