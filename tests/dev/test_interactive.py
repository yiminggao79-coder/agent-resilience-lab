import json
from pathlib import Path
import tempfile
import unittest
from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.support import ScriptedModel, final, tool
from tests.dev.test_approvals import CommandTools
try:
    from agent_lab.interactive import chat_loop
except ImportError:
    chat_loop = None


class InteractiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'project';self.root.mkdir()
        self.store=Store(Path(self.tmp.name)/'state.sqlite');self.addCleanup(self.store.close)

    def reader(self, lines):
        values=iter(lines)
        return lambda prompt:next(values)

    def test_two_natural_user_turns_share_saved_session(self):
        self.assertIsNotNone(chat_loop,'interactive session is missing')
        engine=Engine(self.store,Tools(self.root),ScriptedModel([final('first answer'),final('second answer')]))
        output=[]
        tid=chat_loop(engine,read=self.reader(['First request','Second request','/exit']),write=output.append)
        state=self.store.load(tid)
        self.assertEqual([m['content'] for m in state['messages'] if m['role']=='user'],['First request','Second request'])
        self.assertEqual(len(self.store.turns(tid)),2)
        self.assertIn('first answer',output)
        self.assertIn('second answer',output)

    def test_approval_requires_explicit_yes_before_actual_effect(self):
        self.assertIsNotNone(chat_loop,'interactive session is missing')
        engine=Engine(self.store,CommandTools(self.root),ScriptedModel([tool('run_command',{'argv':['echo','ok'],'cwd':'.'}),final('finished')]))
        def reply(prompt):
            if prompt.startswith('approve'):
                self.assertFalse((self.root/'effect.txt').exists())
                return 'yes'
            return next(tasks)
        tasks=iter(['Perform command','/exit'])
        tid=chat_loop(engine,read=reply,write=lambda _:None)
        self.assertEqual(self.store.load(tid)['phase'],'completed')
        self.assertTrue((self.root/'effect.txt').exists())

    def test_resume_existing_turn_does_not_append_user_message(self):
        self.assertIsNotNone(chat_loop,'interactive session is missing')
        engine=Engine(self.store,Tools(self.root),ScriptedModel([final()]))
        tid=engine.create('Persisted initial request'); turn=self.store.load(tid)['turn_id']
        result=chat_loop(engine,task_id=tid,read=self.reader(['/resume','/exit']),write=lambda _:None)
        self.assertEqual(result,tid)
        self.assertEqual(self.store.load(tid)['turn_id'],turn)
        self.assertEqual(len(self.store.turns(tid)),1)

    def test_cli_chat_uses_stdin_and_can_reopen_persisted_session(self):
        import contextlib,io,os
        from unittest.mock import patch
        from agent_lab.__main__ import main
        from tests.dev.test_session_cli import TerminalModel
        db=str(self.store.path)
        with patch('agent_lab.__main__.OpenAIModel',return_value=TerminalModel()), patch.dict(os.environ,{'SYNTHETIC_KEY':'fake'}), patch('sys.stdin',io.StringIO('Initial request\n/exit\n')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--db',db,'chat','--workspace',str(self.root),'--model','test','--profile','local','--allow-api']),0)
        tid=self.store.db.execute('SELECT id FROM tasks').fetchone()[0]
        with patch('agent_lab.__main__.OpenAIModel',return_value=TerminalModel()), patch.dict(os.environ,{'SYNTHETIC_KEY':'fake'}), patch('sys.stdin',io.StringIO('Follow-up\n/exit\n')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--db',db,'chat','--task-id',tid,'--workspace',str(self.root),'--model','test','--profile','local','--allow-api']),0)
        self.assertEqual(len(self.store.turns(tid)),2)

    def test_exit_at_approval_prompt_keeps_waiting_without_consuming_more_input(self):
        self.assertIsNotNone(chat_loop,'interactive session is missing')
        engine=Engine(self.store,CommandTools(self.root),ScriptedModel([tool('run_command',{'argv':['echo','ok'],'cwd':'.'})]))
        tid=chat_loop(engine,read=self.reader(['Perform command','exit']),write=lambda _:None)
        self.assertEqual(self.store.load(tid)['phase'],'awaiting_approval')
        self.assertFalse((self.root/'effect.txt').exists())
