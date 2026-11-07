"""Connection-open identity failures close the database and stay diagnostic."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.store import Store,StorageUnavailable


class StoreConnectionIdentityTests(unittest.TestCase):
    def test_disappearing_path_during_open_closes_both_constructor_connections(self):
        for readonly in (False,True):
            with self.subTest(readonly=readonly),tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'state.sqlite'
                sqlite3.connect(path).close()
                original=sqlite3.connect;opened=[]
                def changed(*args,**kwargs):
                    connection=original(*args,**kwargs)
                    opened.append(connection);self.addCleanup(connection.close)
                    path.unlink()
                    return connection
                with patch('agent_lab.store.sqlite3.connect',side_effect=changed):
                    with self.assertRaisesRegex(StorageUnavailable,'^storage_unavailable$'):
                        (Store.open_readonly if readonly else Store)(path)
                self.assertEqual(len(opened),1)
                with self.assertRaises(sqlite3.ProgrammingError):opened[0].execute('SELECT 1')
