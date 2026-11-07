import json
from pathlib import Path
import tempfile
import unittest
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine, Limits
from agent_lab.store import Store
from tests.support import ScriptedModel,tool,final


class HistoryRuntimeTests(unittest.TestCase):
    def test_model_can_recall_omitted_evidence_from_same_private_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'project';root.mkdir()
            model=ScriptedModel([tool('history_lookup',{'index':3,'offset':8192,'limit':4096}),final()])
            with Store(Path(temp)/'state.sqlite') as store:
                engine=Engine(store,CodingTools(root),model)
                # Reserve the same transcript headroom as tool schemas grow.
                # The 20KiB archived result still cannot fit; the 4KiB lookup can.
                schema_bytes=len(json.dumps({'messages':[],'tools':engine._schemas()},ensure_ascii=False).encode())
                tid=engine.create('Retrieve earlier evidence before answering.',Limits(max_context_bytes=schema_bytes+8000))
                state=store.load(tid)
                state['messages'] += [
                    {'role':'assistant','content':None,'tool_calls':[{'id':'old','type':'function','function':{'name':'read_file','arguments':'{"path":"source.txt"}'}}]},
                    {'role':'tool','tool_call_id':'old','content':json.dumps({'ok':True,'content':'a'*10000+'CRITICAL_FACT'+'b'*10000})}]
                store.save(state,'test_archive')
                state=engine.run(tid)
                self.assertEqual(state['phase'],'completed')
                self.assertNotIn('CRITICAL_FACT',json.dumps(model.seen[0]))
                result=json.loads([m for m in state['messages'] if m['role']=='tool'][-1]['content'])
                self.assertTrue(result['ok'])
                self.assertIn('CRITICAL_FACT',result['fragment'])
                self.assertEqual(result['index'],3)
                self.assertLessEqual(len(result['fragment']),4096)
                self.assertIn('history_lookup',[schema['function']['name'] for schema in engine._schemas()])
