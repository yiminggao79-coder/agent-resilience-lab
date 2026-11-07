"""Archive fidelity must not keep stale legacy guidance active in prompts."""
from pathlib import Path
import tempfile
import unittest
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.context import build_context
from tests.support import ScriptedModel,final


class SessionForkContextTests(unittest.TestCase):
    def test_legacy_guidance_prefix_is_immutable_but_superseded_in_request_view(self):
        for current_file in (False,True):
            with self.subTest(current_file=current_file),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)/'work';root.mkdir();tools=CodingTools(root)
                with Store(Path(tmp)/'state.sqlite') as store:
                    engine=Engine(store,tools,ScriptedModel([final()]))
                    tid=engine.create('Original request');source=engine.run(tid)
                    old='Project instructions from AGENTS.md\nSTALE_LEGACY_GUIDANCE'
                    source['messages'].insert(1,{'role':'system','content':old})
                    source.pop('instruction_context_index',None);store.save(source,'legacy_fixture')
                    if current_file:(root/'AGENTS.md').write_text('CURRENT_PROJECT_GUIDANCE')
                    child_id=Engine(store,tools,None).fork_session(tid,'New request');child=store.load(child_id)
                    self.assertEqual(child['messages'][:len(source['messages'])],source['messages'])
                    self.assertEqual(store.load(tid),source)
                    view,_=build_context(child['messages'],tools.schemas(),262144,semantic=child.get('semantic_context'))
                    self.assertFalse(any(m.get('content')==old for m in view),'stale cached System guidance remains active')
                    if current_file:self.assertIn('CURRENT_PROJECT_GUIDANCE',str(view))
                    else:self.assertIn('Earlier generated project guidance no longer applies',str(view))
