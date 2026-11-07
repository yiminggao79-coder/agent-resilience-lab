"""Real approved helper execution; scripted decisions, not LLM task scores."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab import native_jobs
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.store import Store
from tests.support import ScriptedModel,tool,final

SCRIPT='''from __future__ import annotations
import json,os,sys
from pathlib import Path
data=json.loads(Path(sys.argv[1]).read_text())
value=sum(data['values'])
Path(sys.argv[2]).write_text(json.dumps({'sum':value}))
try:
 Path(__file__).read_text()
 hidden_read_denied=False
except PermissionError:
 hidden_read_denied=True
print(json.dumps({'sum':value,'hidden_read_denied':hidden_read_denied,
 'key_in_env':'DEEPSEEK_API_KEY' in os.environ,'argv0_matches_file':sys.argv[0]==__file__}))
'''


class SkillResourcesNativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='agentlab-skill-native-',dir='/private/tmp');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir()
        self.skill=self.root/'.agents/skills/owned';(self.skill/'scripts').mkdir(parents=True)
        (self.skill/'references').mkdir();(self.skill/'assets').mkdir()
        (self.skill/'SKILL.md').write_text('---\nname: owned\ndescription: Reuse provided standalone helper\n---\nRead references/rules.md and assets/template.json. Run scripts/helper.py with workspace input/output paths.\n')
        (self.skill/'references/rules.md').write_text('Use integer values and preserve input.\n')
        (self.skill/'assets/template.json').write_text('{"sum": 0}')
        self.script=self.skill/'scripts/helper.py';self.script.write_text(SCRIPT)
        (self.root/'input.json').write_text('{"values":[2,3]}')

    def test_real_script_approval_resource_reads_future_imports_and_sandbox_boundaries(self):
        before={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        model=ScriptedModel([tool('load_skill',{'name':'owned'},'load'),
             tool('read_skill_resource',{'name':'owned','resource':'references/rules.md'},'reference'),
             tool('read_skill_resource',{'name':'owned','resource':'assets/template.json'},'asset'),
             tool('run_skill_script',{'name':'owned','resource':'scripts/helper.py','args':['input.json','output.json'],'cwd':'.'},'helper'),final('protocol complete')])
        db=self.base/'state.sqlite'
        with Store(db) as store,patch.dict(os.environ,{'DEEPSEEK_API_KEY':'synthetic-not-forwarded'}):
            engine=Engine(store,CodingTools(self.root,read_only_paths=['input.json']),model)
            task=engine.create('Use owned resources/helper',Limits(tool_timeout=8))
            state=engine.run(task);self.assertEqual(state['phase'],'awaiting_approval',state.get('reason'))
            self.assertFalse((self.root/'output.json').exists())
            request=engine.approval_request(task)
            self.assertIn(hashlib.sha256(SCRIPT.encode()).hexdigest(),request['action']['argv'][3])
            engine.decide_approval(task,request['request_id'],request['digest'],True)
            state=engine.run(task);self.assertEqual(state['phase'],'completed',state.get('reason'))
            result=json.loads([m for m in state['messages'] if m['role']=='tool'][-1]['content'])
            self.assertEqual(result['exit_code'],0,result)
            self.assertEqual(json.loads(result['stdout']),{'sum':5,'hidden_read_denied':True,'key_in_env':False,'argv0_matches_file':True})
            self.assertEqual(json.loads((self.root/'output.json').read_text()),{'sum':5})
            for name,content in before.items():self.assertEqual((self.root/name).read_bytes(),content)
            self.assertEqual(native_jobs.inspect_workspace(self.root,db)['proof_kind'],'all_native_joined')
            source=state['messages'][-2]['_skill_script_snapshot']
            self.assertEqual(source['content'],SCRIPT);self.assertEqual(source['command_intent']['name'],'run_command')
            self.assertNotIn('_skill_script_snapshot',json.dumps(model.seen))
            self.assertNotIn('import json,os,sys',json.dumps(store.events(task)))

    def test_source_changed_after_preparation_does_not_retarget_approved_snapshot(self):
        self.script.write_text('from __future__ import annotations\nprint("original prepared helper")\n')
        original=self.script.read_text();model=ScriptedModel([tool('run_skill_script',{'name':'owned','resource':'scripts/helper.py','args':[],'cwd':'.'},'snapshot'),final()])
        db=self.base/'state.sqlite'
        with Store(db) as store:
            engine=Engine(store,CodingTools(self.root),model);task=engine.create('Run original prepared helper',Limits(tool_timeout=8))
            self.assertEqual(engine.run(task)['phase'],'awaiting_approval')
            request=engine.approval_request(task)
            self.script.write_text('print("unexpected retarget")\n')
            engine.decide_approval(task,request['request_id'],request['digest'],True)
            state=engine.run(task);self.assertEqual(state['phase'],'completed',state.get('reason'))
            feedback=next(m for m in state['messages'] if m['role']=='tool');result=json.loads(feedback['content'])
            self.assertEqual((result['exit_code'],result['stdout']),(0,'original prepared helper\n'))
            self.assertEqual(feedback['_skill_script_snapshot']['content'],original)
            self.assertEqual(native_jobs.inspect_workspace(self.root,db)['proof_kind'],'all_native_joined')


if __name__=='__main__':unittest.main()
