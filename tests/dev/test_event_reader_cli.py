"""Readonly event pages never load task transcripts or launch the runtime."""
from contextlib import redirect_stdout,redirect_stderr
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
from agent_lab.store import Store,StorageUnavailable


class EventReaderCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.path=self.base/'state.sqlite';self.tid='1'*32
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE tasks(id TEXT PRIMARY KEY,state TEXT NOT NULL)')
            db.execute('CREATE TABLE events(seq INTEGER PRIMARY KEY,task_id TEXT,at REAL,kind TEXT,meta TEXT)')
            db.execute('INSERT INTO tasks VALUES(?,?)',(self.tid,json.dumps({'id':self.tid,'messages':[{'content':'PRIVATE_TASK_HISTORY'}]})))
            for seq in range(1,6):
                meta={'phase':'ready','finish_reason':'tool_calls'}
                if seq==2:meta.update(seq=999,kind='PRIVATE_RESERVED',content='PRIVATE_TOOL_TEXT')
                db.execute('INSERT INTO events VALUES(?,?,?,?,?)',(seq,self.tid,seq,'created',json.dumps(meta)))

    def call(self,*args,database=None):
        out,err=io.StringIO(),io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):
            try:code=main(['--db',str(database or self.path),'events',self.tid,*args])
            except SystemExit as error:code=error.code
        return code,out.getvalue(),err.getvalue()

    def test_store_checked_identity_matches_open_and_rejects_replaced_path(self):
        with Store.open_readonly(self.path) as store:
            value=store.checked_file_identity()
            self.assertEqual(value,(str(self.path.resolve()),self.path.stat().st_dev,self.path.stat().st_ino))
            self.path.rename(self.path.with_suffix('.old'))
            sqlite3.connect(self.path).close()
            with self.assertRaisesRegex(StorageUnavailable,'^storage_unavailable$'):store.checked_file_identity()

    def test_cli_is_readonly_does_not_load_history_or_construct_tools_model(self):
        os.chmod(self.path,0o640)
        before=self.path.read_bytes();mode=stat.S_IMODE(self.path.stat().st_mode)
        entries=sorted(p.name for p in self.base.iterdir())
        with patch.object(Store,'__init__',side_effect=AssertionError('no migration')),patch.object(Store,'load',side_effect=AssertionError('no full history')),patch.object(Store,'lock',side_effect=AssertionError('no workspace lock')),patch('agent_lab.__main__.OpenAIModel',side_effect=AssertionError('no model')),patch('agent_lab.__main__.Tools',side_effect=AssertionError('no tools')):
            code,out,err=self.call('--limit','2')
        self.assertEqual(code,0,err);page=json.loads(out)
        self.assertEqual([item['seq'] for item in page['events']],[1,2])
        self.assertEqual(page['events'][1]['kind'],'created')
        self.assertEqual(page['events'][1]['metadata']['finish_reason'],'tool_calls')
        self.assertNotIn('seq',page['events'][1]['metadata'])
        self.assertNotIn('PRIVATE',out)
        self.assertEqual(before,self.path.read_bytes());self.assertEqual(mode,stat.S_IMODE(self.path.stat().st_mode))
        self.assertEqual(entries,sorted(p.name for p in self.base.iterdir()))

    def test_seek_pages_expected_database_and_live_append_after_exhaustion(self):
        code,out,err=self.call('--after','2','--limit','2');self.assertEqual(code,0,err);first=json.loads(out)
        self.assertEqual([e['seq'] for e in first['events']],[3,4]);self.assertTrue(first['has_more'])
        self.assertEqual(first['last_seq'],4);self.assertEqual(len(first['database_identity']),64)
        code,out,err=self.call('--after','4','--expected-database',first['database_identity'])
        self.assertEqual(code,0,err);self.assertEqual([e['seq'] for e in json.loads(out)['events']],[5])
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO events VALUES(?,?,?,?,?)',(6,self.tid,6,'tool_finished','{"ok":true}'))
        code,out,err=self.call('--after','5','--expected-database',first['database_identity'])
        self.assertEqual(code,0,err);self.assertEqual([e['seq'] for e in json.loads(out)['events']],[6])
        code,out,err=self.call('--after','6');self.assertEqual(code,0,err)
        self.assertEqual(json.loads(out)['last_seq'],6);self.assertEqual(json.loads(out)['events'],[])

    def test_signed_native_signal_exit_status_is_not_filtered_as_a_counter(self):
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE events SET meta=? WHERE seq=1',(json.dumps({'ok':False,'exit_code':-9}),))
        code,out,err=self.call('--limit','1');self.assertEqual(code,0,err)
        self.assertEqual(json.loads(out)['events'][0]['metadata'].get('exit_code'),-9)

    def test_missing_database_and_invalid_parameters_do_not_create_or_leak(self):
        missing=self.base/'absent'/'state.sqlite'
        code,out,err=self.call(database=missing);self.assertEqual(code,2)
        self.assertEqual(out,'');self.assertFalse(missing.parent.exists())
        for options in (('--after','-1'),('--after',str(2**63)),('--limit','0'),('--limit','101'),
                        ('--expected-database','PRIVATE_INVALID'),('--expected-database','f'*64)):
            with self.subTest(options=options):
                code,out,err=self.call(*options);self.assertEqual(code,2);self.assertEqual(out,'')
                self.assertNotIn('PRIVATE',err)
                self.assertEqual(json.loads(err),{'error':'configuration_or_storage_error'})
