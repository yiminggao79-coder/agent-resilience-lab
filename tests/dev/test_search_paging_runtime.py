"""Actual leased file worker + Engine; scripted decisions are contract-only."""
import json
from pathlib import Path
import tempfile
import unittest

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.store import Store
from tests.support import ScriptedModel,tool,final


class SearchPagingRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'work';self.root.mkdir()
        for i in range(240):(self.root/('source_%03d.py'%i)).write_text('value = '+str(i)+'\n')
        for i in (12,229,239):(self.root/('source_%03d.py'%i)).write_text('value = '+str(i)+'\n# target_symbol\n')
        self.tools=CodingTools(self.root)

    def test_schema_is_additive_and_large_workspace_pages_reach_all_matches(self):
        names=[s['function']['name'] for s in self.tools.schemas()]
        self.assertIn('search_files_page',names)
        self.assertIn('search_files',names)
        args={'query':'target_symbol','limit':2}
        first=self.tools.execute(self.tools.prepare('search_files_page',args),5)
        self.assertTrue(first['ok']);self.assertFalse(first['eof'])
        second=self.tools.execute(self.tools.prepare('search_files_page',{**args,'cursor':first['next_cursor']}),5)
        self.assertTrue(second['eof'])
        matches=first['matches']+second['matches']
        self.assertEqual([m['path'] for m in matches],['source_012.py','source_229.py','source_239.py'])
        self.assertEqual([m['line'] for m in matches],[2,2,2])
        self.assertEqual(first['total_matches'],3)

    def test_engine_feedback_contains_actual_search_result_without_bypassing_worker(self):
        model=ScriptedModel([tool('search_files_page',{'query':'target_symbol'}),final('inspected')])
        with Store(Path(self.tmp.name)/'state.sqlite') as store:
            engine=Engine(store,self.tools,model);state=engine.run(engine.create('Find the symbol',Limits(tool_timeout=5)))
            self.assertEqual(state['phase'],'completed')
            result=json.loads(model.seen[1][-1]['content'])
            self.assertTrue(result['ok']);self.assertEqual(result['total_matches'],3)
            self.assertEqual(result['scanned_files'],240)
            self.assertEqual(state['metrics']['tool_attempts'],1)
            self.assertIsNone(state['verified_success'])
