"""Session discovery is readonly metadata, never implicit execution/resume."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.__main__ import main
from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.support import ScriptedModel,final


class SessionListingCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.db=self.base/'state.sqlite'

    def call(self,*options,database=None):
        out,err=io.StringIO(),io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):
            try:code=main(['--db',str(database or self.db),'sessions',*options])
            except SystemExit as error:code=error.code
        return code,out.getvalue(),err.getvalue()

    def test_missing_database_does_not_create_parent_or_succeed_with_empty_list(self):
        missing=self.base/'absent'/'state.sqlite'
        code,out,err=self.call(database=missing)
        self.assertEqual(code,2)
        self.assertEqual(out,'')
        self.assertFalse(missing.parent.exists())
        self.assertEqual(json.loads(err),{'error':'configuration_or_storage_error'})

    def test_readonly_cli_never_initializes_store_model_tools_or_workspace_lock(self):
        root=self.base/'work';root.mkdir()
        with Store(self.db) as store:
            engine=Engine(store,Tools(root),ScriptedModel([final('PRIVATE_ANSWER')]))
            tid=engine.create('PRIVATE_TASK_TARGET')
            state=engine.run(tid)
            sid=state['session_id'];turn=state['turn_id']
        os.chmod(self.db,0o640)
        before=self.db.read_bytes();mode=stat.S_IMODE(self.db.stat().st_mode)
        entries=sorted(str(p.relative_to(self.base)) for p in self.base.rglob('*'))
        with patch('agent_lab.__main__.OpenAIModel',side_effect=AssertionError('model must not be built')),patch('agent_lab.__main__.Tools',side_effect=AssertionError('tools must not be built')),patch.object(Store,'__init__',side_effect=AssertionError('readonly must skip migration')),patch.object(Store,'lock',side_effect=AssertionError('no workspace lock')):
            code,out,err=self.call('--workspace',str(root),'--phase','completed')
        self.assertEqual(code,0,err);body=json.loads(out)
        self.assertEqual(len(body['items']),1);item=body['items'][0]
        self.assertEqual((item['task_id'],item['session_id'],item['turn_id']),(tid,sid,turn))
        self.assertEqual(item['phase'],'completed')
        self.assertFalse(body['history_checked']);self.assertEqual(body['process_liveness'],'not_checked')
        for private in ('PRIVATE',str(root),str(self.db),'messages','model_identity','workspace'):
            self.assertNotIn(private,out)
        self.assertEqual(before,self.db.read_bytes());self.assertEqual(mode,stat.S_IMODE(self.db.stat().st_mode))
        self.assertEqual(entries,sorted(str(p.relative_to(self.base)) for p in self.base.rglob('*')))

    def test_legacy_tasks_only_pagination_does_not_migrate_or_read_private_body(self):
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY,state TEXT NOT NULL)')
            for value in (1,2,3):
                tid='%032x'%value
                db.execute('INSERT INTO tasks VALUES (?,?)',(tid,json.dumps({'id':tid,'created_at':10,
                    'phase':'failed','messages':[{'content':'PRIVATE_LEGACY_CONTENT'}]})))
        before=self.db.read_bytes()
        code,out,err=self.call('--limit','2');self.assertEqual(code,0,err);first=json.loads(out)
        self.assertEqual([r['task_id'] for r in first['items']],['%032x'%3,'%032x'%2])
        self.assertIsNotNone(first['next_cursor'])
        code,out,err=self.call('--limit','2','--cursor',first['next_cursor']);self.assertEqual(code,0,err)
        second=json.loads(out);self.assertEqual([r['task_id'] for r in second['items']],['%032x'%1])
        self.assertIsNone(second['next_cursor']);self.assertNotIn('PRIVATE',out)
        self.assertEqual(before,self.db.read_bytes())
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),[('tasks',)])

    def test_invalid_cursor_limit_and_phase_are_fixed_errors_without_state_change(self):
        with sqlite3.connect(self.db) as db:db.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY,state TEXT NOT NULL)')
        before=self.db.read_bytes()
        for options in (('--limit','0'),('--limit','101'),('--cursor','PRIVATE_INVALID_CURSOR'),('--phase','PRIVATE_FAKE_PHASE')):
            with self.subTest(options=options):
                code,out,err=self.call(*options)
                self.assertEqual(code,2);self.assertEqual(out,'')
                self.assertEqual(json.loads(err),{'error':'configuration_or_storage_error'})
        self.assertEqual(before,self.db.read_bytes())
