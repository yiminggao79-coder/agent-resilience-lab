import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from agent_lab.engine import Engine, Limits
from agent_lab.store import Store
from agent_lab.coding_tools import CodingTools
from tests.support import ScriptedModel, tool, final


class CodingRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'work';self.root.mkdir()
        self.store=Store(Path(self.tmp.name)/'private'/'state.sqlite');self.addCleanup(self.store.close)

    def test_coding_worker_routes_search_patch_and_instructions(self):
        (self.root/'calc.py').write_text('def add(a,b):\n    return a-b\n')
        (self.root/'AGENTS.md').write_text('Preserve the add API.')
        model=ScriptedModel([tool('list_files',{}),tool('search_files',{'query':'return'}),
                             tool('apply_patch',{'path':'calc.py','old':'return a-b','new':'return a+b'}),
                             tool('read_file',{'path':'calc.py'}),final()])
        engine=Engine(self.store,CodingTools(self.root),model)
        tid=engine.create('Fix add and verify its contents')
        state=engine.run(tid)
        self.assertEqual(state['phase'],'completed')
        self.assertIn('return a+b',(self.root/'calc.py').read_text())
        self.assertIn('Preserve the add API.',json.dumps(model.seen[0]))
        self.assertEqual(state['instruction_manifest'][0]['path'],'AGENTS.md')
        results=[json.loads(m[-1]['content']) for m in model.seen[1:]]
        self.assertEqual(results[1]['matches'][0]['path'],'calc.py')

    def test_cli_exposes_protocol_profile_and_structured_events(self):
        proc=subprocess.run([sys.executable,'-m','agent_lab','run','--help'],text=True,capture_output=True,timeout=5)
        self.assertEqual(proc.returncode,0)
        for flag in ('--protocol','--profile','--stream','--events','--skill'):
            self.assertIn(flag,proc.stdout)

    def test_large_repeated_read_continues_with_bounded_context(self):
        (self.root/'data').write_text('x'*65536)
        model=ScriptedModel([tool('read_file',{'path':'data'}) for _ in range(6)]+[final()])
        engine=Engine(self.store,CodingTools(self.root),model)
        tid=engine.create('Inspect data several times',Limits(max_context_bytes=40000))
        state=engine.run(tid)
        self.assertEqual(state['phase'],'completed')
        self.assertGreater(len(json.dumps(state['messages'])),300000)
        self.assertTrue(all(len(json.dumps(m,ensure_ascii=False).encode())<40000 for m in model.seen))
        self.assertIn('context_compacted',[e['kind'] for e in self.store.events(tid)])
