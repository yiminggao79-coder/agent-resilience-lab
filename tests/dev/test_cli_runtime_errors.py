"""CLI admission failure is fixed metadata, never arbitrary exception output."""
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.__main__ import main
from agent_lab.tools import ToolError


class CLIRuntimeErrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'work'; self.root.mkdir()
        self.db = self.root.parent / 'state.sqlite'

    def call(self, error, command='run'):
        output, errors = io.StringIO(), io.StringIO()
        args = (['run', 'Inspect only'] if command == 'run' else ['approval', 'a' * 32])
        target = 'agent_lab.coding_tools.CodingTools' if command == 'run' else 'agent_lab.__main__.Tools'
        with patch(target, side_effect=error), \
             patch('agent_lab.__main__.OpenAIModel', side_effect=AssertionError('admission must precede model construction')), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(['--db', str(self.db), *args, '--workspace', str(self.root)])
        return code, output.getvalue(), errors.getvalue()

    def test_actual_invalid_node_selections_return_json_without_traceback_or_api(self):
        nonexec = self.root.parent / 'nonexec'; nonexec.write_text('synthetic'); nonexec.chmod(0o600)
        inside = self.root / 'node'; inside.write_text('synthetic'); inside.chmod(0o755)
        for selected in (self.root.parent / 'missing', nonexec, inside, Path('relative-node')):
            with self.subTest(selected=selected):
                proc = subprocess.run([sys.executable, '-m', 'agent_lab', '--db', str(self.db),
                    'run', 'Inspect only', '--workspace', str(self.root), '--node-executable', str(selected)],
                    cwd=Path(__file__).resolve().parents[2], env={'PATH': os.defpath, 'LANG': 'C.UTF-8'},
                    capture_output=True, text=True, timeout=5)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stdout, '')
                result = json.loads(proc.stderr)
                self.assertEqual(result['error'], 'command_runtime_not_allowed')
                self.assertIn('--node-executable', result['hint'])
                self.assertNotIn(str(self.root), proc.stderr); self.assertNotIn('Traceback', proc.stderr)
        with sqlite3.connect(self.db) as db: self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0], 0)
        self.assertEqual(inside.read_text(), 'synthetic')

    def test_known_runtime_errors_are_fixed_for_coding_and_approval_factories(self):
        for code in ('command_runtime_not_allowed', 'command_runtime_changed'):
            for command in ('run', 'approval'):
                with self.subTest(code=code, command=command):
                    status, out, err = self.call(ToolError(code), command)
                    self.assertEqual(status, 2); self.assertEqual(out, '')
                    result = json.loads(err); self.assertEqual(result['error'], code)
                    self.assertEqual(set(result), {'error', 'hint'})
                    self.assertIn('--node-executable', result['hint'])

    def test_arbitrary_nonstring_and_unbounded_codes_cannot_escape(self):
        for code in ('PRIVATE_SYNTHETIC_SECRET\ncontrol', {'secret': 'PRIVATE_SYNTHETIC_SECRET'}, 'PRIVATE_' * 1000):
            with self.subTest(kind=type(code).__name__):
                status, out, err = self.call(ToolError(code))
                self.assertEqual(status, 2); self.assertEqual(out, '')
                result = json.loads(err); self.assertEqual(result['error'], 'tool_error')
                self.assertEqual(set(result), {'error', 'hint'})
                self.assertNotIn('PRIVATE', err); self.assertNotIn('Traceback', err)

    def test_previous_generic_value_error_privacy_stays_compatible(self):
        status, out, err = self.call(ValueError('PRIVATE_SYNTHETIC_SECRET'))
        self.assertEqual(status, 2); self.assertEqual(out, '')
        self.assertEqual(json.loads(err), {'error': 'configuration_or_storage_error'})


if __name__ == '__main__': unittest.main()
