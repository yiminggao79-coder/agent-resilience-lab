"""Explicit new-task context option; synthetic model, real file tools."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.__main__ import main
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine
from agent_lab.interactive import chat_loop
from agent_lab.store import Store
from tests.dev.test_read_only_cli import Model
from tests.support import ScriptedModel, tool, final


class FileContextCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'work'; self.root.mkdir()
        (self.root / 'source.py').write_text('visible source\n')
        self.db = self.root.parent / 'state.sqlite'

    def call(self, args, responses, stdin=''):
        out, err = io.StringIO(), io.StringIO()
        with patch('agent_lab.__main__.OpenAIModel', return_value=Model(responses)), \
             patch.dict(os.environ, {'SYNTHETIC_KEY': 'synthetic-only'}), patch('sys.stdin', io.StringIO(stdin)), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try: code = main(['--db', str(self.db), *args, '--workspace', str(self.root), '--model', 'synthetic', '--allow-api'])
            except SystemExit as error: code = error.code
        return code, out.getvalue(), err.getvalue()

    def test_run_opt_in_persists_actual_read_and_resume_needs_no_new_flag(self):
        code, out, err = self.call(['run', 'Read', '--track-file-context'],
                                  [tool('read_file', {'path': 'source.py'}), final()])
        self.assertEqual(code, 0, err)
        with Store.open_readonly(self.db) as store:
            tid = store.db.execute('SELECT id FROM tasks').fetchone()[0]
            self.assertEqual(store.load(tid)['file_context']['reads'][0]['path'], 'source.py')
        self.assertEqual(self.call(['resume', tid], [])[0], 0)

    def test_new_chat_opt_in_and_existing_chat_flag_rejection(self):
        code, out, err = self.call(['chat', '--track-file-context'],
                                  [tool('read_file', {'path': 'source.py'}), final()], 'Read\n/exit\n')
        self.assertEqual(code, 0, err)
        with Store.open_readonly(self.db) as store:
            tid = store.db.execute('SELECT id FROM tasks').fetchone()[0]; before = store.load(tid)
            self.assertEqual(before['file_context']['reads'][0]['path'], 'source.py')
        code, out, err = self.call(['chat', '--task-id', tid, '--track-file-context'], [], '/exit\n')
        self.assertEqual(code, 2)
        with Store.open_readonly(self.db) as store: self.assertEqual(store.load(tid), before)

    def test_interactive_default_call_shape_and_saved_task_option_are_compatible(self):
        with Store(self.db) as store:
            engine = Engine(store, CodingTools(self.root), ScriptedModel([final()]))
            original = engine.create
            with patch.object(engine, 'create', wraps=original) as create:
                inputs = iter(['Default new task', '/exit'])
                tid = chat_loop(engine, read=lambda _: next(inputs), write=lambda _: None)
            self.assertEqual(create.call_args.kwargs, {})
            self.assertNotIn('file_context', store.load(tid))
            with self.assertRaises(ValueError):
                chat_loop(engine, task_id=tid, track_file_context=True,
                          read=lambda _: self.fail('must reject before input'), write=lambda _: None)


if __name__ == '__main__': unittest.main()
