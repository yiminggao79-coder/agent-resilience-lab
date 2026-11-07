"""Independent committed-page contracts using temporary SQLite only."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from agent_lab import __main__ as cli
from agent_lab.event_reader import read_events
from agent_lab.store import Store

TASK = 'a'*32


class EventReaderReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'synthetic.sqlite'
        self.writer = sqlite3.connect(self.path); self.addCleanup(self.writer.close)
        self.writer.execute('PRAGMA journal_mode=WAL')
        self.writer.executescript('CREATE TABLE tasks(id TEXT PRIMARY KEY,state TEXT);'
                                 'CREATE TABLE events(seq INTEGER PRIMARY KEY,task_id TEXT,at REAL,kind TEXT,meta TEXT);')
        self.writer.execute('INSERT INTO tasks VALUES(?,?)', (TASK,'PRIVATE_INVALID_HISTORY '*100000))
        self.writer.commit()

    def event(self, seq, raw='{}', *, commit=True):
        self.writer.execute('INSERT INTO events VALUES(?,?,?,?,?)', (seq,TASK,1,'tool_finished',raw))
        if commit:
            self.writer.commit()

    def test_exact_utf8_byte_cap_and_reserved_values_never_override_row(self):
        prefix = '{"phase":"ready","seq":999,"content":"PRIVATE_'
        suffix = '"}'
        remaining = 8192-len((prefix+suffix).encode())
        raw = prefix+'é'*(remaining//2)+'x'*(remaining%2)+suffix
        self.assertEqual(len(raw.encode()),8192)
        self.event(1,raw)
        self.event(2,raw+' ')  # Valid JSON but one byte beyond the SQL boundary.
        self.event(3,b'{"content":"PRIVATE_BLOB"}')
        with Store.open_readonly(self.path) as reader:
            page = read_events(reader,TASK)
        first,large,blob = page['events']
        self.assertEqual(first['seq'],1)
        self.assertEqual(first['metadata'],{'phase':'ready'})
        self.assertEqual(first['filtered_fields'],2)
        self.assertEqual(first['diagnostics'],['filtered_metadata'])
        self.assertEqual(large['metadata'],{})
        self.assertEqual(large['diagnostics'],['metadata_too_large'])
        self.assertEqual(blob['metadata'],{})
        self.assertEqual(blob['diagnostics'],['invalid_metadata'])
        self.assertNotIn('PRIVATE',json.dumps(page))

    def test_committed_live_pages_and_rejected_readonly_snapshot_stay_separate(self):
        with Store.open_readonly(self.path) as reader:
            self.event(101,commit=False)
            empty = read_events(reader,TASK,after=100)
            self.assertEqual(empty['events'],[])
            self.assertEqual(empty['last_seq'],100)
            self.assertFalse(empty['has_more'])
            self.writer.commit()
            first = read_events(reader,TASK,after=empty['last_seq'],expected_database=empty['database_identity'])
            self.assertEqual([event['seq'] for event in first['events']],[101])
            reader.db.execute('BEGIN')
            self.assertEqual(reader.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],1)
            self.event(102)
            with self.assertRaisesRegex(ValueError,'^invalid_event_query$'):
                read_events(reader,TASK,after=101)
            self.assertTrue(reader.db.in_transaction)
            self.assertEqual(reader.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],1)
            reader.db.rollback()
            self.assertEqual([event['seq'] for event in read_events(reader,TASK,after=101)['events']],[102])

    def test_cli_sql_authorizer_forbids_history_and_fingerprint_rejects_other_file(self):
        self.event(1,'{"phase":"ready"}')
        self.writer.close()  # Checkpoint WAL before byte comparisons/copying.
        with Store.open_readonly(self.path) as reader:
            fingerprint = read_events(reader,TASK)['database_identity']
            # The legacy API retains its flattened result on the same benign row.
            self.assertEqual(reader.events(TASK),[{'seq':1,'at':1.0,'kind':'tool_finished','phase':'ready'}])
        other = self.path.with_name('other.sqlite'); other.write_bytes(self.path.read_bytes())
        before = {path:(path.read_bytes(),path.stat().st_mode) for path in (self.path,other)}
        original_connect = sqlite3.connect
        reads = []

        def authorizer(action,table,column,database,trigger):
            if action == sqlite3.SQLITE_READ:
                reads.append((table,column))
                if table == 'tasks' and column == 'state':
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        def connect(*args,**kwargs):
            connection = original_connect(*args,**kwargs)
            connection.set_authorizer(authorizer)
            return connection

        def call(path):
            out,err = io.StringIO(),io.StringIO()
            with redirect_stdout(out),redirect_stderr(err):
                code = cli.main(['--db',str(path),'events',TASK,'--expected-database',fingerprint])
            return code,out.getvalue(),err.getvalue()

        with patch('agent_lab.store.sqlite3.connect',side_effect=connect), \
             patch.object(Store,'load',side_effect=AssertionError('history load forbidden')), \
             patch.object(Store,'lock',side_effect=AssertionError('runtime lock forbidden')), \
             patch.object(cli,'Tools',side_effect=AssertionError('tools forbidden')), \
             patch.object(cli,'OpenAIModel',side_effect=AssertionError('model forbidden')):
            code,out,err = call(self.path)
            self.assertEqual(code,0,err)
            self.assertEqual(json.loads(out)['events'][0]['metadata'],{'phase':'ready'})
            self.assertNotIn('PRIVATE',out)
            code,out,err = call(other)
            self.assertEqual(code,2)
            self.assertEqual(out,'')
            self.assertEqual(json.loads(err),{'error':'configuration_or_storage_error'})
        self.assertNotIn(('tasks','state'),reads)
        for path,identity in before.items():
            self.assertEqual((path.read_bytes(),path.stat().st_mode),identity)
