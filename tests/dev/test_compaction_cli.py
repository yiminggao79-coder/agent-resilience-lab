import contextlib
import copy
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.__main__ import main
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,public_status
from agent_lab.interactive import chat_loop
from agent_lab.store import Store
from tests.dev.test_session_cli import TerminalModel


class SummaryModel(TerminalModel):
    def complete(self,messages,tools,timeout,max_tokens):
        response=super().complete(messages,tools,timeout,max_tokens)
        response['message']['content']='Observed test evidence. '*300 if tools else 'Project examined; preserve the user request.'
        return {**response,'finish_reason':'stop'}


class CompactionCLITests(unittest.TestCase):
    def test_explicit_cli_compaction_uses_model_without_replacing_private_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'project';root.mkdir();db=Path(temp)/'state.sqlite'
            with Store(db) as store:
                engine=Engine(store,CodingTools(root),SummaryModel())
                tid=engine.create('Keep original requirements.');before=engine.run(tid)
                archive=copy.deepcopy(before['messages'])
            with patch('agent_lab.__main__.OpenAIModel',return_value=SummaryModel()),patch.dict(os.environ,{'SYNTHETIC_KEY':'fake'}),contextlib.redirect_stdout(io.StringIO()):
                code=main(['--db',str(db),'compact',tid,'--workspace',str(root),'--model','test','--allow-api'])
            self.assertEqual(code,0)
            with Store(db) as store:
                state=store.load(tid)
                self.assertEqual(state['phase'],'completed')
                self.assertEqual(state['messages'],archive)
                self.assertTrue(state.get('semantic_context'))
                status=public_status(state)
                self.assertEqual(status['compaction']['status'],'installed')
                self.assertEqual(status['context']['generation'],1)
                self.assertNotIn('Project examined',str(status))
                self.assertEqual(len(store.turns(tid)),1)

    def test_chat_compact_does_not_create_a_user_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'project';root.mkdir()
            with Store(Path(temp)/'state.sqlite') as store:
                engine=Engine(store,CodingTools(root),SummaryModel())
                tid=engine.create('Initial request');before=engine.run(tid)
                values=iter(['/compact','/exit']);output=[]
                chat_loop(engine,task_id=tid,read=lambda _:next(values),write=output.append)
                after=store.load(tid)
                self.assertEqual(after['turn_id'],before['turn_id'])
                self.assertTrue(after.get('semantic_context'))
                self.assertEqual(after['metrics']['model_calls'],2)

    def test_rejected_summary_has_nonzero_cli_exit_even_when_original_turn_completed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'project';root.mkdir();db=Path(temp)/'state.sqlite'
            with Store(db) as store:
                engine=Engine(store,CodingTools(root),TerminalModel())
                tid=engine.create('Task');engine.run(tid)
            output=io.StringIO()
            with patch('agent_lab.__main__.OpenAIModel',return_value=TerminalModel()),patch.dict(os.environ,{'SYNTHETIC_KEY':'fake'}),contextlib.redirect_stdout(output):
                code=main(['--db',str(db),'compact',tid,'--workspace',str(root),'--model','test','--allow-api'])
            self.assertEqual(code,2)
            self.assertIn('compaction_invalid_summary',output.getvalue())
