import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from agent_lab.tools import Tools, ToolError, UncertainEffect
from agent_lab.store import Store, Busy


class ToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        self.root.mkdir()
        self.tools = Tools(self.root)

    def test_write_read_and_verified_recovery(self):
        intent = self.tools.prepare('write_file', {'path': 'result.txt', 'content': 'hello'})
        result = self.tools.execute(intent, 2)
        self.assertTrue(result['ok'])
        before = (self.root / 'result.txt').stat().st_mtime_ns
        recovered = self.tools.recover(intent, 2)
        self.assertTrue(recovered['ok'])
        self.assertEqual(before, (self.root / 'result.txt').stat().st_mtime_ns)
        self.assertEqual(self.tools.execute(self.tools.prepare('read_file', {'path': 'result.txt'}), 2)['content'], 'hello')

    def test_changed_target_blocks_recovery(self):
        intent = self.tools.prepare('write_file', {'path': 'a', 'content': 'wanted'})
        (self.root / 'a').write_text('external')
        with self.assertRaises(UncertainEffect):
            self.tools.recover(intent, 2)
        self.assertEqual((self.root / 'a').read_text(), 'external')

    def test_append_never_replayed_after_unknown_outcome(self):
        intent = self.tools.prepare('append_file', {'path': 'a', 'content': 'one\n'})
        self.tools.execute(intent, 2)
        with self.assertRaises(UncertainEffect):
            self.tools.recover(intent, 2)
        self.assertEqual((self.root / 'a').read_text(), 'one\n')

    def test_path_boundaries_and_special_files(self):
        (self.root / 'link').symlink_to(Path(self.tmp.name))
        os.mkfifo(str(self.root / 'fifo'))
        for path in ('../outside', '/tmp/x', '.env', 'x/../a', 'link/file', 'fifo'):
            with self.subTest(path=path), self.assertRaises(ToolError):
                self.tools.prepare('read_file', {'path': path})

    def test_strict_arguments_and_size(self):
        for name, args in [('read_file', {'path': 3}), ('read_file', {'path': 'x', 'extra': True}),
                           ('write_file', {'path': 'a', 'content': 'x' * 70000}), ('unknown', {})]:
            with self.subTest(name=name, args=str(args)[:60]), self.assertRaises(ToolError):
                self.tools.prepare(name, args)

    def test_csv_stats_and_bad_data(self):
        (self.root / 'data.csv').write_text('name,value\na,2\nb,8\nc,5\n')
        result = self.tools.execute(self.tools.prepare('csv_stats', {'path': 'data.csv', 'column': 'value'}), 2)
        self.assertEqual({k: result[k] for k in ('count', 'sum', 'mean', 'min', 'max')},
                         {'count': 3, 'sum': 15.0, 'mean': 5.0, 'min': 2.0, 'max': 8.0})
        (self.root / 'data.csv').write_text('value\nNaN\n')
        with self.assertRaises(ToolError):
            self.tools.execute(self.tools.prepare('csv_stats', {'path': 'data.csv', 'column': 'value'}), 2)

    def test_expression_runs_in_restricted_child(self):
        intent = self.tools.prepare('python_expr', {'expression': 'sum([2, 3, 7]) / 4'})
        self.assertEqual(self.tools.execute(intent, 2)['value'], 3.0)
        for expr in ('__import__("os").system("id")', '(1).__class__', '2 ** 99999999', '[1] * 999999999'):
            with self.subTest(expr=expr), self.assertRaises(ToolError):
                self.tools.execute(self.tools.prepare('python_expr', {'expression': expr}), 2)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state' / 'tasks.sqlite'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)

    def test_snapshot_and_trace_survive_reopen_without_content_in_trace(self):
        task = {'id': 'test', 'phase': 'ready', 'messages': [{'content': 'secret-file-body'}]}
        self.store.save(task, 'created', {'steps': 0})
        with Store(self.path) as second:
            self.assertEqual(second.load('test'), task)
            trace = second.events('test')
            self.assertEqual(trace[0]['kind'], 'created')
            self.assertNotIn('secret-file-body', json.dumps(trace))
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_trace_rejects_arbitrary_payload(self):
        with self.assertRaises(ValueError):
            self.store.save({'id': 'a'}, 'error', {'content': 'secret'})

    def test_exclusive_workspace_and_database_locks(self):
        root = Path(self.tmp.name) / 'work'
        root.mkdir()
        other = Store(Path(self.tmp.name) / 'other' / 'tasks.sqlite')
        self.addCleanup(other.close)
        with self.store.lock(root):
            with self.assertRaises(Busy), other.lock(root):
                pass

    def test_database_inside_tool_root_is_rejected(self):
        with self.assertRaises(ValueError), self.store.lock(self.path.parent):
            pass


if __name__ == '__main__':
    unittest.main()
