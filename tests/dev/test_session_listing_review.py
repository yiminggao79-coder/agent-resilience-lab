"""Independent synthetic checks of listing snapshots and database binding."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from agent_lab.session_listing import list_sessions
from agent_lab.store import Store, StorageUnavailable


def tid(number):
    return '%032x' % number


def put(connection, number, at=10):
    row_id = tid(number) if isinstance(number, int) else number
    state = {'id': row_id, 'session_id': tid(100), 'turn_id': tid(200),
             'phase': 'ready', 'profile': 'coding', 'created_at': at}
    connection.execute('INSERT INTO tasks VALUES(?,?)', (row_id, json.dumps(state)))
    connection.commit()


class SessionListingReviewTests(unittest.TestCase):
    def database(self, path, *, wal=False):
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        if wal:
            connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE tasks(id TEXT PRIMARY KEY,state TEXT NOT NULL)')
        connection.commit()
        return connection

    def test_single_page_uses_one_snapshot_and_later_page_is_live(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.sqlite'
            writer = self.database(path, wal=True)
            for number in (1, 2, 3):
                put(writer, number)
            with Store.open_readonly(path) as reader:
                inserted = []

                def between_queries(statement):
                    if statement.lstrip().startswith('WITH source') and not inserted:
                        inserted.append(True)
                        put(writer, 99, at=20)  # Above the eventual anchor.
                        put(writer, 4, at=5)    # Below it: visible on a later page.
                        put(writer, 'legacy-row')

                reader.db.set_trace_callback(between_queries)
                first = list_sessions(reader, limit=1, phase='ready')
                reader.db.set_trace_callback(None)
                self.assertTrue(inserted)
                self.assertEqual([item['task_id'] for item in first['items']], [tid(3)])
                self.assertEqual(first['excluded_noncanonical_ids'], 0)
                second = list_sessions(reader, cursor=first['next_cursor'], phase='ready')
                self.assertEqual([item['task_id'] for item in second['items']], [tid(2), tid(1), tid(4)])
                self.assertEqual(second['excluded_noncanonical_ids'], 1)
                self.assertEqual(list_sessions(reader)['items'][0]['task_id'], tid(99))
                with self.assertRaisesRegex(ValueError, '^invalid_session_listing$'):
                    list_sessions(reader, cursor=first['next_cursor'], phase='failed')

    def test_existing_readonly_transaction_keeps_its_snapshot_after_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.sqlite'
            writer = self.database(path, wal=True)
            put(writer, 1)
            with Store.open_readonly(path) as reader:
                reader.db.execute('BEGIN')
                self.assertEqual(reader.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 1)
                put(writer, 2)
                with self.assertRaisesRegex(ValueError, '^invalid_session_listing$'):
                    list_sessions(reader)
                self.assertTrue(reader.db.in_transaction)
                self.assertEqual(reader.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 1)
                reader.db.rollback()
                self.assertEqual(len(list_sessions(reader)['items']), 2)

    def test_replaced_database_path_cannot_rebind_cursor_from_old_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.sqlite'
            original = self.database(path)
            for number in (1, 2, 3):
                put(original, number)
            original.close()
            with Store.open_readonly(path) as old_reader:
                path.rename(path.with_suffix('.old'))
                replacement = self.database(path)
                for number in (1, 2, 9):
                    put(replacement, number)
                # A connection to the old inode must not mint a cursor bearing
                # the replacement inode's identity, accepted by its new reader.
                with self.assertRaisesRegex(StorageUnavailable, '^storage_unavailable$'):
                    list_sessions(old_reader, limit=1)
