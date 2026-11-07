import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from agent_lab.engine import Engine, Limits
from agent_lab.model import OpenAIModel, ModelError
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.support import ScriptedModel, final, tool


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        self.root.mkdir()
        self.database = Path(self.tmp.name) / 'state' / 'db.sqlite'
        self.store = Store(self.database)
        self.addCleanup(self.store.close)

    def test_expired_deadline_prevents_tools_and_survives_resume(self):
        model = ScriptedModel([tool('write_file', {'path': 'x', 'content': 'x'})])
        engine = Engine(self.store, Tools(self.root), model)
        tid = engine.create('x', Limits(max_seconds=0.01))
        time.sleep(0.02)
        state = engine.run(tid)
        self.assertEqual(state['reason'], 'time_limit')
        self.assertEqual(model.seen, [])
        self.assertFalse((self.root / 'x').exists())

    def test_context_cap_prevents_model_call(self):
        model = ScriptedModel([final()])
        engine = Engine(self.store, Tools(self.root), model)
        tid = engine.create('x', Limits(max_context_bytes=1))
        state = engine.run(tid)
        self.assertEqual(state['reason'], 'context_limit')
        self.assertEqual(model.seen, [])

    def test_missing_usage_under_cost_limit_stops_before_tool(self):
        response = tool('write_file', {'path': 'x', 'content': 'x'})
        response['usage'] = None
        engine = Engine(self.store, Tools(self.root), ScriptedModel([response]))
        tid = engine.create('x', Limits(max_cost_usd=1, input_price=1, output_price=1))
        self.assertEqual(engine.run(tid)['phase'], 'needs_confirmation')
        self.assertFalse((self.root / 'x').exists())

    def test_restricted_expression_hard_timeout(self):
        with self.assertRaises(Exception) as error:
            Tools(self.root).execute(Tools(self.root).prepare('python_expr', {'expression': '1+1'}), 0.00001)
        self.assertEqual(error.exception.code, 'tool_timeout')

    def test_cli_status_trace_and_abort_do_not_require_api_key(self):
        engine = Engine(self.store, Tools(self.root), ScriptedModel([]))
        tid = engine.create('PRIVATE_TASK_BODY')
        for command in ('status', 'trace', 'abort'):
            argv = [sys.executable, '-m', 'agent_lab', '--db', str(self.database), command, tid]
            if command == 'abort':
                argv += ['--workspace', str(self.root)]
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=5)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn('PRIVATE_TASK_BODY', proc.stdout)
        self.assertEqual(self.store.load(tid)['reason'], 'user_aborted')

    def test_cli_missing_key_fails_without_creating_task(self):
        env = dict(os.environ)
        env.pop('LAB_MISSING_KEY', None)
        proc = subprocess.run([sys.executable, '-m', 'agent_lab', '--db', str(self.database), 'run', 'x',
                               '--workspace', str(self.root), '--model', 'unset', '--api-key-env', 'LAB_MISSING_KEY', '--allow-api'],
                              env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0], 0)

    def test_answer_is_explicit_private_output_not_part_of_status_or_trace(self):
        engine = Engine(self.store, Tools(self.root), ScriptedModel([final('actual final answer')]))
        tid = engine.create('x')
        engine.run(tid)
        proc = subprocess.run([sys.executable, '-m', 'agent_lab', '--db', str(self.database), 'answer', tid],
                              capture_output=True, text=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {'answer': 'actual final answer'})

    def test_file_tools_have_real_wall_timeout_and_mutations_are_uncertain(self):
        from agent_lab.tools import ToolError, UncertainEffect
        tools = Tools(self.root)
        started = time.monotonic()
        with self.assertRaises(UncertainEffect):
            tools.execute(tools.prepare('write_file', {'path': 'out', 'content': 'x'}), 0.00001)
        self.assertLess(time.monotonic() - started, 1)
        with self.assertRaises(ToolError) as error:
            tools.execute(tools.prepare('list_files', {}), 0.00001)
        self.assertEqual(error.exception.code, 'tool_timeout')
