"""New CLI processes, synthetic model, actual resource read; no paid calls."""
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


class ToolResourceCLIProcessTests(unittest.TestCase):
    def test_cli_repeat_resource_and_named_read_on_approval_resume_fork(self):
        with tempfile.TemporaryDirectory(prefix='agentlab-resource-cli-',dir='/private/tmp') as directory:
            base=Path(directory);root=base/'work';root.mkdir();db=base/'state.sqlite'
            resource=base/'value';resource.write_text('owned CLI resource')
            env={'PATH':os.defpath,'LANG':'C','PYTHONDONTWRITEBYTECODE':'1','AGENT_LAB_FIXTURE_KEY':'synthetic-no-network'}
            def invoke(args,responses=()):
                return subprocess.run([sys.executable,'-I','-c',HARNESS,str(REPO),'--db',str(db),*args],
                    env={**env,'AGENT_LAB_FIXTURE_RESPONSES':json.dumps(responses)},capture_output=True,text=True,timeout=8)
            workspace=['--workspace',str(root)]
            grant=[*workspace,'--tool-read-path',str(resource),'--tool-sysctl-read','hw.pagesize_compat']
            flags=['--profile','coding','--model','synthetic','--base-url','https://example.invalid/v1','--api-key-env','AGENT_LAB_FIXTURE_KEY','--allow-api']
            started=invoke(['run','Read owned tool resource',*grant,*flags],[tool('run_command',{'argv':['cat',str(resource)],'cwd':'.'},'resource')])
            self.assertEqual(started.returncode,3,started.stderr)
            task=json.loads(started.stdout.splitlines()[0])['task_id']
            request=invoke(['approval',task,*grant]);self.assertEqual(request.returncode,0,request.stderr)
            approval=json.loads(request.stdout)
            denied=invoke(['approve',task,approval['request_id'],*workspace,'--digest',approval['digest']]);self.assertEqual(denied.returncode,2)
            approved=invoke(['approve',task,approval['request_id'],*grant,'--digest',approval['digest']]);self.assertEqual(approved.returncode,0,approved.stderr)
            resumed=invoke(['resume',task,*grant,*flags],[final('owned read complete')]);self.assertEqual(resumed.returncode,0,resumed.stderr)
            self.assertEqual(native_jobs.inspect_workspace(root,db)['proof_kind'],'all_native_joined')
            forked=invoke(['fork',task,'Owned follow-up',*grant]);self.assertEqual(forked.returncode,0,forked.stderr)
            child=json.loads(forked.stdout)['id']
            with Store(db) as store:
                state=store.load(task);self.assertEqual(state['command_runtime_policy'],store.load(child)['command_runtime_policy'])
                self.assertEqual(json.loads(next(m['content'] for m in state['messages'] if m['role']=='tool'))['stdout'],'owned CLI resource')
                self.assertNotIn('node',state['command_runtime_policy'])

    def test_runtime_help_lists_read_scope_and_named_metadata(self):
        for command in ('run','resume','continue','compact','chat','fork','approval','approve','deny'):
            result=subprocess.run([sys.executable,'-m','agent_lab',command,'--help'],cwd=REPO,capture_output=True,text=True,timeout=3)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('--tool-read-path',result.stdout);self.assertIn('--tool-sysctl-read',result.stdout)


if __name__=='__main__':unittest.main()
