import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from agent_lab.__main__ import main
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,public_status
from agent_lab.interactive import chat_loop
from agent_lab.store import Store
from tests.support import ScriptedModel,tool,final


class PlanViewTests(unittest.TestCase):
    def test_raw_plan_is_available_only_through_explicit_private_views(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'project';root.mkdir();db=Path(temp)/'state.sqlite'
            private='inspect private fixture detail'
            with Store(db) as store:
                engine=Engine(store,CodingTools(root),ScriptedModel([tool('update_plan',{'plan':[{'step':private,'status':'in_progress'}]}),final('done')]))
                tid=engine.create('Task');state=engine.run(tid)
                self.assertNotIn(private,json.dumps(public_status(state)))
                output=[];values=iter(['/plan','/exit'])
                chat_loop(engine,task_id=tid,read=lambda _:next(values),write=output.append)
                self.assertTrue(any(private in value for value in output))
                self.assertNotIn(private,json.dumps(store.events(tid)))
            output=io.StringIO()
            with contextlib.redirect_stdout(output):self.assertEqual(main(['--db',str(db),'plan',tid]),0)
            self.assertEqual(json.loads(output.getvalue())['plan']['plan'][0]['step'],private)

    def test_no_plan_is_an_explicit_null_without_starting_a_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'project';root.mkdir();db=Path(temp)/'state.sqlite'
            with Store(db) as store:
                tid=Engine(store,CodingTools(root),ScriptedModel([])).create('Task')
            output=io.StringIO()
            with contextlib.redirect_stdout(output):self.assertEqual(main(['--db',str(db),'plan',tid]),0)
            self.assertEqual(json.loads(output.getvalue()),{'plan':None})
