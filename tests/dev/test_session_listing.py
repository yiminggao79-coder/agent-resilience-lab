"""Readonly scalar session metadata from synthetic SQLite, never history."""
import base64
import hashlib
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


def tid(n): return format(n, '032x')


class SessionListingTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('agent_lab.session_listing'),
                             'readonly session metadata listing is missing')
        self.listing = importlib.import_module('agent_lab.session_listing')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'legacy.sqlite'
        self.workspace = str((Path(self.tmp.name) / 'missing-workspace').resolve())
        with sqlite3.connect(self.path) as connection:
            connection.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY,state TEXT NOT NULL)')
        self.store = Store.open_readonly(self.path); self.addCleanup(self.store.close)

    def put(self, n, *, raw=None, **updates):
        row_id = tid(n) if isinstance(n, int) else n
        state = {'id': row_id, 'session_id': tid(100), 'turn_id': tid(200),
                 'created_at': 10, 'phase': 'ready', 'profile': 'coding', 'workspace': self.workspace}
        state.update(updates)
        with sqlite3.connect(self.path) as connection:
            connection.execute('INSERT OR REPLACE INTO tasks VALUES(?,?)',
                               (row_id, raw if raw is not None else json.dumps(state)))
        return row_id

    def page(self, **kwargs): return self.listing.list_sessions(self.store, **kwargs)

    def test_keyset_ties_null_tail_and_exact_output_schema(self):
        for n in range(1, 6): self.put(n)
        self.put(6, created_at=None)
        self.put(7, created_at='PRIVATE_TIME')
        seen, cursor = [], None
        while True:
            page = self.page(limit=2, cursor=cursor)
            self.assertEqual(set(page), {'items', 'next_cursor', 'sort', 'excluded_noncanonical_ids',
                                        'history_checked', 'process_liveness'})
            self.assertEqual(page['sort'], 'created_at_desc_task_id_desc')
            self.assertFalse(page['history_checked'])
            self.assertEqual(page['process_liveness'], 'not_checked')
            self.assertEqual(page['excluded_noncanonical_ids'], 0)
            self.assertLessEqual(len(page['items']), 2)
            for item in page['items']:
                self.assertEqual(set(item), {'task_id', 'session_id', 'turn_id', 'phase', 'profile',
                                             'created_at', 'metadata_error'})
            seen += page['items']; cursor = page['next_cursor']
            if cursor is None: break
        self.assertEqual([item['task_id'] for item in seen], [tid(n) for n in (5, 4, 3, 2, 1, 7, 6)])
        self.assertEqual([item['created_at'] for item in seen[-2:]], [None, None])
        self.assertEqual(seen[-1]['metadata_error'], 'incomplete_metadata')

    def test_corrupt_mismatch_legacy_invalid_fields_and_noncanonical_row_ids(self):
        self.put(1, raw='{malformed PRIVATE_JSON')
        self.put(2, id=tid(99))
        self.put(3, raw='{}')
        self.put(4, session_id='PRIVATE_SESSION' * 10000, turn_id=[], phase='PRIVATE_PHASE', profile='PRIVATE_PROFILE')
        self.put(5, raw='[]')
        for row_id in ('old-task', 'A' * 32, 'a' * 32 + '\x00PRIVATE', 'é' * 32): self.put(row_id)
        page = self.page()
        rows = {item['task_id']: item for item in page['items']}
        self.assertEqual(len(rows), 5)
        self.assertEqual(page['excluded_noncanonical_ids'], 4)
        self.assertEqual(rows[tid(1)]['metadata_error'], 'invalid_json')
        self.assertEqual(rows[tid(2)]['metadata_error'], 'task_id_mismatch')
        for n in (3, 4, 5): self.assertEqual(rows[tid(n)]['metadata_error'], 'incomplete_metadata')
        self.assertIsNone(rows[tid(4)]['session_id'])
        self.assertIsNone(rows[tid(4)]['phase'])
        self.assertNotIn('PRIVATE', json.dumps(page))

    def test_huge_private_json_and_scalar_values_never_materialize_in_python(self):
        self.put(1, messages=[{'role': 'user', 'content': 'PRIVATE_BODY' * 200000}],
                 task='PRIVATE_GOAL', reason='PRIVATE_REASON', model={'token': 'PRIVATE_MODEL'},
                 session_id='a' * 32 + '\x00PRIVATE_SUFFIX', turn_id={'private': 'x' * 300000},
                 phase='ready\x00PRIVATE_PHASE', profile='coding\x00PRIVATE_PROFILE')
        def bounded_row(cursor, row):
            for value in row:
                if isinstance(value, str): self.assertLessEqual(len(value), 80, 'full/private state crossed SQL boundary')
            return row
        self.store.db.row_factory = bounded_row
        with patch.object(self.listing.json, 'loads', side_effect=AssertionError('full-state Python decode forbidden')):
            page = self.page()
        self.assertEqual(len(page['items']), 1)
        self.assertIsNone(page['items'][0]['session_id'])
        self.assertIsNone(page['items'][0]['phase'])
        self.assertIsNone(page['items'][0]['profile'])
        self.assertNotIn('PRIVATE', json.dumps(page))
        self.assertNotIn(self.workspace, json.dumps(page))
        self.assertNotIn(str(self.path), json.dumps(page))

    def test_timestamp_range_finiteness_and_boolean_are_sanitized(self):
        invalid = [True, False, -1, 253402300800, '12', {}, None]
        for n, value in enumerate(invalid, 1): self.put(n, created_at=value)
        self.put(20, created_at=0); self.put(21, created_at=253402300799); self.put(22, created_at=1.25)
        self.put(23, raw='{"id":"' + tid(23) + '","created_at":1e999}')
        rows = {item['task_id']: item for item in self.page()['items']}
        for n in list(range(1, len(invalid) + 1)) + [23]: self.assertIsNone(rows[tid(n)]['created_at'])
        self.assertEqual(rows[tid(20)]['created_at'], 0)
        self.assertEqual(rows[tid(21)]['created_at'], 253402300799)
        self.assertEqual(rows[tid(22)]['created_at'], 1.25)

    def test_filters_cursor_scope_and_newer_inserts_do_not_shift_anchor(self):
        for n in range(1, 5): self.put(n)
        self.put(10, phase='failed'); self.put(11, workspace='/PRIVATE/other')
        first = self.page(limit=2, workspace=self.workspace + '/.', phase='ready')
        self.assertEqual([item['task_id'] for item in first['items']], [tid(4), tid(3)])
        self.put(49, workspace=self.workspace + '\x00PRIVATE_PATH')
        self.put(50, created_at=20)
        second = self.page(limit=1, cursor=first['next_cursor'], workspace=self.workspace, phase='ready')
        self.assertEqual([item['task_id'] for item in second['items']], [tid(2)])
        fresh = self.page(workspace=self.workspace, phase='ready')
        self.assertEqual(fresh['items'][0]['task_id'], tid(50))
        self.assertNotIn(tid(49), [item['task_id'] for item in fresh['items']])
        for changes in ({'phase': 'failed', 'workspace': self.workspace}, {'phase': 'ready'}, {}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, '^invalid_session_listing$'):
                self.page(cursor=first['next_cursor'], **changes)
        self.assertFalse(Path(self.workspace).exists())

    def test_cursor_is_bound_to_explicit_database_path_and_inode(self):
        self.put(1); self.put(2)
        cursor = self.page(limit=1)['next_cursor']
        other_path = Path(self.tmp.name) / 'other.sqlite'
        other_path.write_bytes(self.path.read_bytes())
        with Store.open_readonly(other_path) as other:
            with self.assertRaisesRegex(ValueError, '^invalid_session_listing$'):
                self.listing.list_sessions(other, cursor=cursor)
        saved = self.path.with_suffix('.old')
        self.path.rename(saved); self.path.write_bytes(saved.read_bytes())
        with Store.open_readonly(self.path) as replacement:
            with self.assertRaisesRegex(ValueError, '^invalid_session_listing$'):
                self.listing.list_sessions(replacement, cursor=cursor)

    def test_cursor_encoding_strict_duplicate_types_version_and_limits(self):
        self.put(1); self.put(2)
        cursor = self.page(limit=1)['next_cursor']
        raw = base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4))
        value = json.loads(raw)
        def encode(raw): return base64.urlsafe_b64encode(raw).decode().rstrip('=')
        malformed = [cursor + '=', '!', 'a' * 1025, '', b'bytes', encode(b'{"v":1,"v":1}'),
                     encode(b'[]'), encode(json.dumps({**value, 'v': True}).encode()),
                     encode(json.dumps({**value, 'v': 2}).encode()),
                     encode(json.dumps({**value, 'at': float('inf')}).encode()),
                     encode(json.dumps({**value, 'id': 'PRIVATE'}).encode())]
        for value in malformed:
            with self.subTest(cursor=str(value)[:30]), self.assertRaisesRegex(ValueError, '^invalid_session_listing$'):
                self.page(cursor=value)
        for options in ({'limit': True}, {'limit': 0}, {'limit': 101}, {'limit': 1.0},
                        {'phase': 'PRIVATE'}, {'phase': []}, {'workspace': ''}, {'workspace': 3}, {'workspace': 'a\x00b'}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, '^invalid_session_listing$'):
                self.page(**options)

    def test_legacy_readonly_database_bytes_mode_schema_and_single_read_transaction(self):
        self.put(1); self.put(2)
        before = (hashlib.sha256(self.path.read_bytes()).hexdigest(), self.path.stat().st_mode,
                  sorted(p.name for p in self.path.parent.iterdir()))
        statements = []; self.store.db.set_trace_callback(statements.append)
        self.page(limit=1)
        self.store.db.set_trace_callback(None)
        after = (hashlib.sha256(self.path.read_bytes()).hexdigest(), self.path.stat().st_mode,
                 sorted(p.name for p in self.path.parent.iterdir()))
        self.assertEqual(before, after)
        self.assertEqual(sum(sql.upper().startswith('BEGIN') for sql in statements), 1)
        self.assertTrue(any(sql.upper().startswith(('COMMIT', 'ROLLBACK')) for sql in statements))
        self.assertEqual([row[0] for row in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")], ['tasks'])

    def test_caller_active_transaction_is_rejected_without_commit_or_rollback(self):
        self.store.db.close()
        self.store.db = sqlite3.connect(self.path)
        self.store.db.execute('INSERT INTO tasks VALUES(?,?)', (tid(1), '{}'))
        self.assertTrue(self.store.db.in_transaction)
        with self.assertRaisesRegex(ValueError, '^invalid_session_listing$'): self.page()
        self.assertTrue(self.store.db.in_transaction)
        self.assertEqual(self.store.db.execute('SELECT id FROM tasks').fetchall(), [(tid(1),)])
        with sqlite3.connect(self.path) as independent:
            self.assertEqual(independent.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)
        self.store.db.rollback()

    def test_storage_faults_are_fixed_and_do_not_expose_sql_or_path(self):
        empty = Path(self.tmp.name) / 'empty.sqlite'
        sqlite3.connect(empty).close()
        with Store.open_readonly(empty) as store:
            with self.assertRaisesRegex(StorageUnavailable, '^storage_unavailable$'):
                self.listing.list_sessions(store)
        self.store.db.close()
        with self.assertRaisesRegex(StorageUnavailable, '^storage_unavailable$'): self.page()
        self.store.db = sqlite3.connect('file:' + str(self.path) + '?mode=ro', uri=True)


if __name__ == '__main__': unittest.main()
