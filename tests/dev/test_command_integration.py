import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine, Limits
from agent_lab.store import Store
from agent_lab.tools import UncertainEffect
from tests.support import ScriptedModel, tool, final


class CommandIntegrationTests(unittest.TestCase):
    def test_real_worker_approval_command_feedback_and_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'project'; root.mkdir()
            database = Path(temp)/'private'/'tasks.sqlite'
            model = ScriptedModel([tool('run_command', {'argv':['python','-c',"from pathlib import Path; Path('result.txt').write_text('real effect'); print('ok')"], 'cwd':'.'}), final()])
            with Store(database) as store:
                engine = Engine(store, CodingTools(root), model)
                tid = engine.create('Execute requested project operation', Limits(tool_timeout=5))
                paused = engine.run(tid)
                self.assertEqual(paused['phase'],'awaiting_approval')
                self.assertFalse((root/'result.txt').exists())
                pending = paused['pending']['approval']
                engine.decide_approval(tid,pending['request_id'],pending['digest'],True)
            with Store(database) as store:
                finished = Engine(store,CodingTools(root),model).run(tid)
                self.assertEqual(finished['phase'],'completed')
                self.assertEqual((root/'result.txt').read_text(),'real effect')
                result = json.loads([m for m in finished['messages'] if m['role']=='tool'][-1]['content'])
                self.assertEqual(result['exit_code'],0)
                self.assertEqual(result['stdout'],'ok\n')
                self.assertEqual(finished['metrics']['tool_attempts'],1)
                # No recovery replay of commands, even a normally successful one.
                prepared = CodingTools(root).prepare('run_command',{'argv':['echo','x'],'cwd':'.'})
                with self.assertRaises(UncertainEffect):
                    CodingTools(root).recover(prepared,2)
