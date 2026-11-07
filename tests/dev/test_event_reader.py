"""Committed bounded metadata pages from synthetic task/event SQLite only."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.store import Store, StorageUnavailable

TASK = 'a' * 32
OTHER = 'b' * 32


class EventReaderTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('agent_lab.event_reader'),
                             'bounded committed metadata event reader is missing')
        self.reader = importlib.import_module('agent_lab.event_reader')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'legacy.sqlite'
        with sqlite3.connect(self.path) as db:
            db.executescript('CREATE TABLE tasks(id TEXT PRIMARY KEY,state TEXT NOT NULL);'
                             'CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT,at REAL,kind TEXT,meta TEXT);')
            db.executemany('INSERT INTO tasks VALUES(?,?)', [(TASK, '{"PRIVATE_HISTORY":"' + 'private' * 200000 + '"}'), (OTHER, '{}')])
        self.store = Store.open_readonly(self.path); self.addCleanup(self.store.close)

    def event(self, seq, *, task=TASK, at=1.0, kind='tool_finished', meta=None, raw=None):
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO events VALUES(?,?,?,?,?)',
                       (seq, task, at, kind, raw if raw is not None else json.dumps(meta or {})))

    def read(self, **kwargs): return self.reader.read_events(self.store, TASK, **kwargs)

    def test_interleaved_global_seek_pages_do_not_lose_or_repeat_task_events(self):
        for seq in (1, 4, 7, 12): self.event(seq, meta={'steps': seq})
        for seq in (2, 5, 8): self.event(seq, task=OTHER)
        first = self.read(limit=2)
        self.assertEqual(set(first), {'version', 'task_id', 'database_identity', 'after', 'last_seq', 'has_more', 'events'})
        self.assertEqual(first['version'], 1)
        self.assertEqual(first['task_id'], TASK)
        self.assertEqual(first['after'], 0)
        self.assertEqual(first['last_seq'], 4)
        self.assertTrue(first['has_more'])
        self.assertRegex(first['database_identity'], '^[0-9a-f]{64}$')
        second = self.read(after=first['last_seq'], limit=2, expected_database=first['database_identity'])
        self.assertFalse(second['has_more'])
        self.assertEqual([e['seq'] for e in first['events'] + second['events']], [1, 4, 7, 12])
        self.assertEqual(second['last_seq'], 12)
        for event in first['events']:
            self.assertEqual(set(event), {'seq', 'at', 'kind', 'metadata', 'diagnostics', 'filtered_fields'})
            self.assertEqual(event['diagnostics'], [])
            self.assertEqual(event['filtered_fields'], 0)

    def test_exhausted_page_can_be_followed_by_newly_committed_rows(self):
        empty = self.read(after=9)
        self.assertEqual(empty['events'], [])
        self.assertEqual(empty['last_seq'], 9)
        self.assertFalse(empty['has_more'])
        self.event(10)
        page = self.read(after=empty['last_seq'], expected_database=empty['database_identity'])
        self.assertEqual([e['seq'] for e in page['events']], [10])
        with self.assertRaisesRegex(ValueError, '^task_not_found$'):
            self.reader.read_events(self.store, 'c' * 32)

    def test_metadata_is_nested_whitelisted_and_cannot_override_row_identity(self):
        self.event(1, meta={'seq': 999, 'at': 999, 'kind': 'PRIVATE_KIND', 'content': 'PRIVATE_CONTENT',
                            'phase': 'ready', 'steps': 3, 'ok': True, 'finish_reason': 'tool_calls',
                            'diagnostic': 'phase_ipc,timeout', 'tool': None})
        event = self.read()['events'][0]
        self.assertEqual(event['seq'], 1)
        self.assertEqual(event['at'], 1.0)
        self.assertEqual(event['kind'], 'tool_finished')
        self.assertEqual(event['metadata'], {'phase': 'ready', 'steps': 3, 'ok': True,
                                            'finish_reason': 'tool_calls', 'diagnostic': 'phase_ipc,timeout', 'tool': None})
        self.assertEqual(event['filtered_fields'], 4)
        self.assertEqual(event['diagnostics'], ['filtered_metadata'])
        self.assertNotIn('PRIVATE', json.dumps(event))

    def test_invalid_known_fields_unknown_fields_and_null_values_are_counted(self):
        self.event(1, meta={'ok': 1, 'recovered': 'true', 'local_quiescent': [], 'steps': True,
                            'duration_ms': -1, 'code': 'PRIVATE/path', 'phase': 'x' * 129,
                            'diagnostic': 'a,b,c,d', 'tool': {'private': 'body'}, 'unknown': None,
                            'revision': None, 'input_tokens': 0, 'cost_usd': 1.5})
        event = self.read()['events'][0]
        self.assertEqual(event['metadata'], {'revision': None, 'input_tokens': 0, 'cost_usd': 1.5})
        self.assertEqual(event['filtered_fields'], 10)
        self.assertEqual(event['diagnostics'], ['filtered_metadata'])

    def test_invalid_duplicate_nan_deep_and_oversize_metadata_never_escape(self):
        raw_values = ('{malformed PRIVATE', '[]', '{"ok":true,"ok":false}',
                      '{"steps":NaN}', '{"unknown":{"a":1,"a":2}}',
                      '{"unknown":' + '[' * 40 + '0' + ']' * 40 + '}')
        for seq, raw in enumerate(raw_values, 1): self.event(seq, raw=raw)
        self.event(20, raw='{"PRIVATE_OVERSIZE":"' + 'é' * 8192 + '"}')
        page = self.read()
        for event in page['events']:
            self.assertEqual(event['metadata'], {})
            self.assertEqual(event['filtered_fields'], 0)
            self.assertEqual(event['diagnostics'], ['metadata_too_large' if event['seq'] == 20 else 'invalid_metadata'])
        self.assertNotIn('PRIVATE', json.dumps(page))

    def test_raw_time_and_kind_are_sql_sanitized_and_bad_metadata_does_not_override_them(self):
        for seq, (at, kind) in enumerate(((-1, 'PRIVATE/kind'), (float('inf'), 'A'),
                                        ('PRIVATE_TIME' * 10000, 'x' * 20000), (None, None),
                                        (0, 'valid_kind'), (1.25, 'also_valid')), 1):
            self.event(seq, at=at, kind=kind)
        events = self.read()['events']
        for event in events[:4]:
            self.assertIsNone(event['at']); self.assertIsNone(event['kind'])
            self.assertEqual(event['diagnostics'], ['invalid_event_time', 'invalid_event_kind'])
        self.assertEqual(events[-2]['at'], 0)
        self.assertEqual(events[-1]['at'], 1.25)
        self.assertEqual(events[-1]['kind'], 'also_valid')

    def test_sql_projection_bounds_python_memory_and_never_materializes_task_history(self):
        self.event(1, raw='{"phase":"ready"}')
        self.event(2, raw='x' * 1000000)
        self.event(3, kind='PRIVATE_KIND' * 100000, at='PRIVATE_TIME' * 100000)
        def bounded(cursor, row):
            for value in row:
                if isinstance(value, (str, bytes)):
                    self.assertLessEqual(len(value), 8192, 'unbounded/private SQL scalar entered Python')
            return row
        self.store.db.row_factory = bounded
        actual_loads = json.loads
        def loads(raw, *args, **kwargs):
            self.assertLessEqual(len(raw.encode('utf-8') if isinstance(raw, str) else raw), 8192)
            return actual_loads(raw, *args, **kwargs)
        with patch.object(self.reader.json, 'loads', side_effect=loads):
            page = self.read()
        self.assertEqual(len(page['events']), 3)
        self.assertNotIn('PRIVATE', json.dumps(page))

    def test_invalid_arguments_fingerprint_binding_and_stale_file_are_fixed(self):
        for changes in ({'after': True}, {'after': -1}, {'after': 1 << 63}, {'after': 1.0},
                        {'limit': True}, {'limit': 0}, {'limit': 101}, {'limit': 1.0},
                        {'expected_database': ''}, {'expected_database': 'A' * 64}, {'expected_database': []}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, '^invalid_event_query$'):
                self.read(**changes)
        for task in ('PRIVATE', '', None, 'A' * 32):
            with self.subTest(task=task), self.assertRaisesRegex(ValueError, '^invalid_event_query$'):
                self.reader.read_events(self.store, task)
        with self.assertRaisesRegex(ValueError, '^invalid_event_query$'):
            self.read(expected_database='0' * 64)
        self.path.rename(self.path.with_suffix('.old'))
        with sqlite3.connect(self.path) as db: db.execute('CREATE TABLE replacement(x)')
        with self.assertRaisesRegex(StorageUnavailable, '^storage_unavailable$'): self.read()

    def test_active_caller_transaction_is_not_committed_or_rolled_back(self):
        self.store.db.close(); self.store.db = sqlite3.connect(self.path)
        self.store.db.execute('INSERT INTO tasks VALUES(?,?)', ('c' * 32, 'pending private write'))
        with self.assertRaisesRegex(ValueError, '^invalid_event_query$'): self.read()
        self.assertTrue(self.store.db.in_transaction)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 3)
        with sqlite3.connect(self.path) as independent:
            self.assertEqual(independent.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 2)
        self.store.db.rollback()

    def test_wal_page_is_one_snapshot_when_writer_appends_after_task_existence_read(self):
        with sqlite3.connect(self.path) as db: db.execute('PRAGMA journal_mode=WAL')
        self.event(1)
        actual = self.store.db
        test = self
        class ReadBarrier:
            @property
            def in_transaction(self): return actual.in_transaction
            def __enter__(self): actual.__enter__(); return self
            def __exit__(self, *args): return actual.__exit__(*args)
            def execute(self, sql, params=()):
                cursor = actual.execute(sql, params)
                if sql.lstrip().upper().startswith('SELECT 1 FROM TASKS'):
                    test.event(2)
                return cursor
        self.store.db = ReadBarrier()
        try:
            page = self.read(limit=1)
        finally:
            self.store.db = actual
        self.assertEqual([e['seq'] for e in page['events']], [1])
        self.assertFalse(page['has_more'])
        self.assertEqual([e['seq'] for e in self.read(after=1)['events']], [2])

    def test_readonly_bytes_mode_schema_unchanged_and_storage_fault_fixed(self):
        self.event(1)
        before = (self.path.read_bytes(), self.path.stat().st_mode,
                  sorted(p.name for p in self.path.parent.iterdir()))
        self.read()
        after = (self.path.read_bytes(), self.path.stat().st_mode,
                 sorted(p.name for p in self.path.parent.iterdir()))
        self.assertEqual(after, before)
        with sqlite3.connect(self.path) as db: db.execute('DROP TABLE events')
        with self.assertRaisesRegex(StorageUnavailable, '^storage_unavailable$'): self.read()
        self.store.db.close()
        with self.assertRaisesRegex(StorageUnavailable, '^storage_unavailable$'): self.read()
        self.store.db = sqlite3.connect(self.path)


if __name__ == '__main__': unittest.main()
