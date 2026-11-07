import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agent_lab.__main__ import main
from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.tools import Tools


class TerminalModel:
    identity = 'cli-session-model'
    api_key_env = 'SYNTHETIC_KEY'
    def complete(self, messages, tools, timeout, max_tokens):
        return {'message': {'role': 'assistant', 'content': 'Acknowledged'}, 'usage': {'input_tokens': 10, 'output_tokens': 2}}


class SessionCLITests(unittest.TestCase):
    def test_cli_followup_uses_persisted_history_and_exposes_turn_records(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'workspace'; root.mkdir()
            db = str(Path(temp)/'tasks.sqlite')
            with Store(db) as store:
                engine = Engine(store, Tools(root), TerminalModel())
                tid = engine.create('First request')
                initial = engine.run(tid)
            output = io.StringIO()
            with patch('agent_lab.__main__.OpenAIModel', return_value=TerminalModel()), patch.dict(os.environ, {'SYNTHETIC_KEY':'fake'}), contextlib.redirect_stdout(output):
                code = main(['--db', db, 'continue', tid, 'Second request', '--workspace', str(root), '--model', 'test', '--profile', 'local', '--allow-api'])
            self.assertEqual(code, 0)
            with Store(db) as store:
                state = store.load(tid)
                self.assertEqual(state['session_id'], initial['session_id'])
                self.assertEqual([m['content'] for m in state['messages'] if m['role']=='user'], ['First request','Second request'])
                self.assertEqual(len(store.turns(tid)), 2)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(['--db', db, 'turns', tid]), 0)
            records = json.loads(output.getvalue())
            self.assertEqual([r['phase'] for r in records], ['completed','completed'])

    def test_followup_without_limit_flags_preserves_session_limits(self):
        from agent_lab.engine import Limits
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'workspace'; root.mkdir()
            db = str(Path(temp)/'tasks.sqlite')
            original = Limits(max_steps=1, max_cost_usd=.01, input_price=1, output_price=1)
            with Store(db) as store:
                engine = Engine(store, Tools(root), TerminalModel())
                tid = engine.create('First', original); before = engine.run(tid)
                self.assertEqual(before['phase'],'completed')
            with patch('agent_lab.__main__.OpenAIModel', return_value=TerminalModel()), patch.dict(os.environ, {'SYNTHETIC_KEY':'fake'}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--db',db,'continue',tid,'Second','--workspace',str(root),'--model','test','--profile','local','--allow-api']),0)
            with Store(db) as store:
                self.assertEqual(store.load(tid)['limits'],before['limits'])
