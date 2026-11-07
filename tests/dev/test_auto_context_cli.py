import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.__main__ import main
from agent_lab.engine import Engine,Limits
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.dev.test_session_cli import TerminalModel


class AutoContextCLITests(unittest.TestCase):
    def test_explicit_disable_overrides_saved_setting_but_omission_preserves_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'project';root.mkdir();db=Path(temp)/'state.sqlite'
            with Store(db) as store:
                engine=Engine(store,Tools(root),TerminalModel())
                tid=engine.create('First',Limits(auto_compact_bytes=200000,max_auto_compactions=3))
                engine.run(tid)
            common=['--db',str(db),'continue',tid,'Follow-up','--workspace',str(root),'--profile','local','--model','test','--allow-api']
            with patch('agent_lab.__main__.OpenAIModel',return_value=TerminalModel()),patch.dict(os.environ,{'SYNTHETIC_KEY':'fake'}),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(common),0)
            with Store(db) as store:
                self.assertEqual(store.load(tid)['limits']['auto_compact_bytes'],200000)
            with patch('agent_lab.__main__.OpenAIModel',return_value=TerminalModel()),patch.dict(os.environ,{'SYNTHETIC_KEY':'fake'}),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(common+['--no-auto-compact']),0)
            with Store(db) as store:
                limits=store.load(tid)['limits']
                self.assertIsNone(limits['auto_compact_bytes'])
                self.assertEqual(limits['max_auto_compactions'],3)

    def test_run_threshold_and_attempt_limit_are_explicit_host_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'project';root.mkdir();db=Path(temp)/'state.sqlite'
            args=['--db',str(db),'run','Task','--workspace',str(root),'--profile','local','--model','test','--allow-api',
                  '--auto-compact-bytes','200000','--max-auto-compactions','1']
            with patch('agent_lab.__main__.OpenAIModel',return_value=TerminalModel()),patch.dict(os.environ,{'SYNTHETIC_KEY':'fake'}),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(args),0)
            with Store(db) as store:
                tid=store.db.execute('SELECT id FROM tasks').fetchone()[0]
                self.assertEqual(store.load(tid)['limits']['max_auto_compactions'],1)
                self.assertEqual(store.load(tid)['limits']['auto_compact_bytes'],200000)
