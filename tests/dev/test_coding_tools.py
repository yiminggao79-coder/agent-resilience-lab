"""Behavior contracts for the opt-in coding profile."""
import os
from pathlib import Path
import tempfile
import unittest

from agent_lab.tools import ToolError, UncertainEffect
try:
    from agent_lab.coding_tools import CodingTools
except ImportError:
    CodingTools = None


class CodingToolsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(CodingTools, 'coding profile is not implemented')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        self.root.mkdir()
        self.tools = CodingTools(self.root)

    def call(self, name, args):
        return self.tools._execute(self.tools._prepare(name, args), 3)

    def test_recursive_listing_and_literal_search_exclude_hidden_and_links(self):
        (self.root / 'pkg').mkdir()
        (self.root / 'pkg' / 'a.py').write_text('one\nneedle.* here\nneedle elsewhere\n')
        (self.root / 'z.txt').write_text('needle.* last\n')
        (self.root / '.secret').write_text('needle.* hidden')
        (self.root / 'link').symlink_to(self.root / 'pkg', target_is_directory=True)
        (self.root / 'hard').hardlink_to(self.root / 'z.txt') if hasattr(Path, 'hardlink_to') else os.link(self.root / 'z.txt', self.root / 'hard')
        self.assertEqual(self.call('list_files', {})['files'], ['pkg/a.py'])
        self.assertEqual(self.call('search_files', {'query': 'needle.*'})['matches'], [
            {'path': 'pkg/a.py', 'line': 2, 'text': 'needle.* here'}])

    def test_listing_and_search_have_finite_result_limits(self):
        for i in range(201):
            (self.root / ('f%03d' % i)).write_text('x\n')
        with self.assertRaises(ToolError) as error:
            self.call('list_files', {})
        self.assertEqual(error.exception.code, 'listing_too_large')
        with self.assertRaises(ToolError):
            self.call('search_files', {'query': 'x'})

    def test_patch_prepare_is_pure_and_recovery_is_idempotent(self):
        path = self.root / 'a.py'
        path.write_text('value = 1\n')
        intent = self.tools._prepare('apply_patch', {'path': 'a.py', 'old': '1', 'new': '2'})
        self.assertEqual(path.read_text(), 'value = 1\n')
        self.assertEqual(intent['name'], 'write_file')
        self.assertEqual(intent['policy'], 'verify')
        self.assertEqual(intent['requested_tool'], 'apply_patch')
        self.assertEqual(intent['args'], {'path': 'a.py', 'content': 'value = 2\n'})
        self.tools._execute(intent, 2)
        modified = path.stat().st_mtime_ns
        self.assertTrue(self.tools._recover(intent, 2)['reconciled'])
        self.assertEqual(path.stat().st_mtime_ns, modified)

    def test_patch_conflict_ambiguous_empty_and_changed_target_are_rejected(self):
        path = self.root / 'a'
        path.write_text('aaa')
        for old in ('', 'a', 'missing'):
            with self.subTest(old=old), self.assertRaises(ToolError):
                self.tools._prepare('apply_patch', {'path': 'a', 'old': old, 'new': 'b'})
        intent = self.tools._prepare('apply_patch', {'path': 'a', 'old': 'aaa', 'new': 'b'})
        path.write_text('other editor')
        with self.assertRaises(UncertainEffect):
            self.tools._execute(intent, 2)
        self.assertEqual(path.read_text(), 'other editor')

    def test_patch_refuses_escape_binary_and_oversized_desired_content(self):
        (self.root / 'a').write_text('a')
        (self.root / 'binary').write_bytes(b'\xff')
        for args in ({'path': '../outside', 'old': 'a', 'new': 'b'},
                     {'path': 'binary', 'old': 'a', 'new': 'b'},
                     {'path': 'a', 'old': 'a', 'new': 'b' * 65537}):
            with self.subTest(args=str(args)[:60]), self.assertRaises(ToolError):
                self.tools._prepare('apply_patch', args)

    def test_test_execution_is_unsafe_and_legacy_tools_are_retained(self):
        self.assertEqual(self.tools._prepare('run_tests', {})['policy'], 'unsafe')
        with self.assertRaises(UncertainEffect):
            self.tools._recover(self.tools._prepare('run_tests', {}), 2)
        names = {schema['function']['name'] for schema in self.tools.schemas()}
        self.assertTrue({'read_file', 'write_file', 'append_file', 'csv_stats', 'python_expr',
                         'list_files', 'search_files', 'apply_patch', 'run_tests'} <= names)
        (self.root / 'a').write_text('ok')
        self.assertEqual(self.call('read_file', {'path': 'a'})['content'], 'ok')
        for name, args in [('run_tests', {'command': 'sh'}), ('search_files', {'query': ''}),
                           ('search_files', {'query': 1})]:
            with self.subTest(name=name, args=args), self.assertRaises(ToolError):
                self.tools._prepare(name, args)


if __name__ == '__main__':
    unittest.main()
