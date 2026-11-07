"""Fresh CLI approval/resume executes captured helper even after file removal."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from .test_confined_backend_cli_process import HARNESS,REPO
from agent_lab import native_jobs
from agent_lab.store import Store
from tests.support import tool,final


class SkillScriptCLIProcessTests(unittest.TestCase):
    def test_approval_and_new_cli_resume_use_persisted_source_not_changed_skill_file(self):
        with tempfile.TemporaryDirectory(prefix='agentlab-skill-cli-',dir='/private/tmp') as directory:
            base=Path(directory);root=base/'work';root.mkdir();db=base/'state.sqlite'
            skill=root/'.agents/skills/owned';(skill/'scripts').mkdir(parents=True)
            (skill/'SKILL.md').write_text('---\nname: owned\ndescription: Explicit owned helper\ndisable-model-invocation: true\n---\nRun scripts/helper.py.\n')
            script=skill/'scripts/helper.py';content='from __future__ import annotations\nfrom pathlib import Path\nwith Path("effect").open("a") as output:output.write("original snapshot\\n")\n'
            script.write_text(content)
            env={'PATH':os.defpath,'LANG':'C','PYTHONDONTWRITEBYTECODE':'1','AGENT_LAB_FIXTURE_KEY':'synthetic-no-network'}
            def invoke(args,responses=()):
                return subprocess.run([sys.executable,'-I','-c',HARNESS,str(REPO),'--db',str(db),*args],
                    env={**env,'AGENT_LAB_FIXTURE_RESPONSES':json.dumps(responses)},capture_output=True,text=True,timeout=8)
            workspace=['--workspace',str(root)]
            flags=['--profile','coding','--model','synthetic','--base-url','https://example.invalid/v1','--api-key-env','AGENT_LAB_FIXTURE_KEY','--allow-api']
            started=invoke(['run','Run explicitly selected helper',*workspace,*flags,'--skill','owned'],
                [tool('run_skill_script',{'name':'owned','resource':'scripts/helper.py','args':[],'cwd':'.'},'helper')])
            self.assertEqual(started.returncode,3,started.stderr);task=json.loads(started.stdout.splitlines()[0])['task_id']
            approval=invoke(['approval',task,*workspace]);self.assertEqual(approval.returncode,0,approval.stderr);request=json.loads(approval.stdout)
            script.unlink()
            approved=invoke(['approve',task,request['request_id'],*workspace,'--digest',request['digest']]);self.assertEqual(approved.returncode,0,approved.stderr)
            resumed=invoke(['resume',task,*workspace,*flags],[final('owned helper complete')]);self.assertEqual(resumed.returncode,0,resumed.stderr)
            self.assertEqual((root/'effect').read_text(),'original snapshot\n')
            with Store(db) as store:
                state=store.load(task);self.assertEqual(state['selected_skills'],['owned'])
                feedback=next(m for m in state['messages'] if m['role']=='tool')
                self.assertEqual(feedback['_skill_script_snapshot']['content'],content)
                self.assertEqual(feedback['_skill_script_snapshot']['sha256'],hashlib.sha256(content.encode()).hexdigest())
            self.assertEqual(native_jobs.inspect_workspace(root,db)['proof_kind'],'all_native_joined')


if __name__=='__main__':unittest.main()
