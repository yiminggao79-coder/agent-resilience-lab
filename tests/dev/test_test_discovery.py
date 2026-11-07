"""Conventional unittest layouts; synthetic bootstrap contracts and native guard."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_lab import sandbox
from agent_lab.coding_tools import CodingTools
from agent_lab.tools import ToolError, UncertainEffect


TEST = '''import pathlib, unittest
class Example(unittest.TestCase):
 def test_executes(self):
  pathlib.Path("executed").write_text("ran")
  self.assertEqual(2 + 2, 4)
'''


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        self.root.mkdir()

    def bootstrap(self, start_dir):
        # Explicitly run only this test's own synthetic project. This is not a
        # production fallback when the OS sandbox is unavailable.
        return subprocess.run([sys.executable, '-I', '-c', sandbox.TEST_BOOTSTRAP, start_dir],
                              cwd=self.root, capture_output=True, text=True, timeout=5,
                              env={'PATH': os.defpath, 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'})

    def test_root_layout_executes_test_method(self):
        (self.root / 'test_example.py').write_text(TEST)
        result = self.bootstrap('.')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'executed').read_text(), 'ran')
        self.assertIn('Ran 1 test', result.stderr)

    def test_tests_directory_without_init_still_executes(self):
        (self.root / 'tests').mkdir()
        (self.root / 'tests/test_example.py').write_text(TEST)
        result = self.bootstrap('tests')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'executed').exists())
        self.assertFalse((self.root / 'tests/__init__.py').exists())

    def test_zero_discovery_is_failure_for_both_layouts(self):
        (self.root / 'tests').mkdir()
        for start in ('.', 'tests'):
            with self.subTest(start=start):
                result = self.bootstrap(start)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('no_tests_discovered', result.stderr)

    def test_discovery_import_error_and_premature_exit_are_failure(self):
        for content, expected in [('import missing_module_deliberately_absent\n', 'ModuleNotFoundError'),
                                  ('raise SystemExit(0)\n', 'SystemExit: 0')]:
            with self.subTest(content=content):
                (self.root / 'test_example.py').write_text(content)
                result = self.bootstrap('.')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertFalse((self.root / 'executed').exists())

    def test_prepared_choice_stays_root_when_tests_directory_appears(self):
        tools = CodingTools(self.root)
        intent = tools.prepare('run_tests', {})
        self.assertEqual(intent.get('test_start_dir'), '.')
        (self.root / 'tests').mkdir()
        # Calling the real _execute reaches the host launch boundary with the
        # prepared scope; the fake launcher executes no project code.
        with patch.object(sandbox, 'run_tests', return_value={'ok': True, 'exit_code': 0}) as launch:
            tools._execute(intent, 3)
        self.assertEqual(launch.call_args.kwargs.get('start_dir'), '.')
        with self.assertRaises(UncertainEffect):
            tools.recover(intent, 3)

    def test_host_selects_tests_directory_and_rejects_symlink(self):
        (self.root / 'tests').mkdir()
        tools = CodingTools(self.root)
        self.assertEqual(tools.prepare('run_tests', {}).get('test_start_dir'), 'tests')
        (self.root / 'tests').rmdir()
        (self.root / 'other').mkdir()
        (self.root / 'tests').symlink_to(self.root / 'other', target_is_directory=True)
        with self.assertRaises(ToolError) as error:
            tools.prepare('run_tests', {})
        self.assertEqual(error.exception.code, 'permission_denied')

    def test_invalid_scope_and_backend_failure_never_execute_project(self):
        (self.root / 'test_example.py').write_text(TEST)
        for start in ('../outside', 'nested', '', 1):
            with self.subTest(start=start), self.assertRaises(ToolError) as error:
                sandbox.run_tests(self.root, 3, start_dir=start)
            self.assertEqual(error.exception.code, 'invalid_test_start_dir')
        with patch.object(sandbox, 'BACKEND_PATH', '/no-such-sandbox-exec'):
            with self.assertRaises(ToolError) as error:
                sandbox.run_tests(self.root, 3, start_dir='.')
            self.assertEqual(error.exception.code, 'sandbox_unavailable')
        self.assertFalse((self.root / 'executed').exists())

    def test_readonly_native_restriction_before_project_start(self):
        (self.root / 'é').write_text('protected')
        (self.root / 'test_example.py').write_text(TEST)
        tools = CodingTools(self.root, read_only_paths=['é'])
        with self.assertRaises(ToolError) as error:
            tools.prepare('run_tests', {})
        self.assertEqual(error.exception.code, 'read_only_native_path_unsupported')
        self.assertFalse((self.root / 'executed').exists())

    def test_native_root_layout_uses_real_worker_and_os_policy(self):
        if not Path(sandbox.BACKEND_PATH).is_file():
            self.skipTest('sandbox-exec unavailable; no bare fallback')
        probe = subprocess.run([sandbox.BACKEND_PATH, '-p', '(version 1)(allow default)', '/usr/bin/true'],
                               capture_output=True, timeout=2)
        if probe.returncode:
            self.skipTest('host denies sandbox-exec; no bare fallback')
        (self.root / 'test_example.py').write_text(TEST)
        tools = CodingTools(self.root)
        result = tools.execute(tools.prepare('run_tests', {}), 5)
        self.assertTrue(result['ok'], result)
        self.assertEqual((self.root / 'executed').read_text(), 'ran')
        self.assertEqual(result['test_start_dir'], '.')

    def test_missing_pinned_tests_directory_does_not_fallback_to_root(self):
        (self.root / 'test_example.py').write_text(TEST)
        result = self.bootstrap('tests')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ImportError', result.stderr)
        self.assertFalse((self.root / 'executed').exists())

    def test_native_root_layout_obeys_readonly_scope(self):
        if not Path(sandbox.BACKEND_PATH).is_file():
            self.skipTest('sandbox-exec unavailable; no bare fallback')
        probe = subprocess.run([sandbox.BACKEND_PATH, '-p', '(version 1)(allow default)', '/usr/bin/true'],
                               capture_output=True, timeout=2)
        if probe.returncode:
            self.skipTest('host denies sandbox-exec; no bare fallback')
        (self.root / 'protected').write_text('original')
        (self.root / 'test_example.py').write_text(
            'import pathlib, unittest\nclass Example(unittest.TestCase):\n'
            ' def test_policy(self):\n'
            '  with self.assertRaises(OSError): pathlib.Path("protected").write_text("changed")\n'
            '  pathlib.Path("executed").write_text("ran")\n')
        tools = CodingTools(self.root, read_only_paths=['protected'])
        result = tools.execute(tools.prepare('run_tests', {}), 5)
        self.assertTrue(result['ok'], result)
        self.assertEqual((self.root / 'protected').read_text(), 'original')
        self.assertEqual((self.root / 'executed').read_text(), 'ran')


if __name__ == '__main__':
    unittest.main()
