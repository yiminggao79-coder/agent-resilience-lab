"""Fresh CLI processes, scripted model protocol, actual owned sandbox execution."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent_lab import native_jobs
from agent_lab.store import Store
from tests.support import tool, final

REPO=Path(__file__).resolve().parents[2]
HARNESS=r'''
import json,os,sys
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
from agent_lab import __main__ as cli
from tests.support import ScriptedModel
model=ScriptedModel(json.loads(os.environ.pop('AGENT_LAB_FIXTURE_RESPONSES')))
model.api_key_env='AGENT_LAB_FIXTURE_KEY'
with patch.object(cli,'OpenAIModel',return_value=model):
 raise SystemExit(cli.main(sys.argv[2:]))
'''


class ConfinedBackendCLIProcessTests(unittest.TestCase):
    def test_actual_cli_approve_resume_fork_and_continue_preserve_backend(self):
        with tempfile.TemporaryDirectory(prefix='agentlab-backend-cli-',dir='/private/tmp') as directory:
            base=Path(directory);root=base/'work';root.mkdir();db=base/'state.sqlite'
            env={'PATH':os.defpath,'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1',
                 'AGENT_LAB_FIXTURE_KEY':'synthetic-no-network'}
            def invoke(args,responses=()):
                return subprocess.run([sys.executable,'-I','-c',HARNESS,str(REPO),'--db',str(db),*args],
                     env={**env,'AGENT_LAB_FIXTURE_RESPONSES':json.dumps(responses)},
                     capture_output=True,text=True,timeout=8)
            runtime=['--workspace',str(root),'--command-backend','confined-fork']
            model_flags=['--profile','coding','--model','synthetic','--base-url','https://example.invalid/v1',
                         '--api-key-env','AGENT_LAB_FIXTURE_KEY','--allow-api']
            args={'argv':['sh','-c','echo owned | cat > effect.txt'],'cwd':'.'}
            started=invoke(['run','Owned CLI pipe',*runtime,*model_flags],[tool('run_command',args,'pipe')])
            self.assertEqual(started.returncode,3,started.stderr)
            task=json.loads(started.stdout.splitlines()[0])['task_id']
            self.assertFalse((root/'effect.txt').exists())
            request=invoke(['approval',task,*runtime]);self.assertEqual(request.returncode,0,request.stderr)
            approval=json.loads(request.stdout)
            omitted=invoke(['approve',task,'--workspace',str(root),approval['request_id'],'--digest',approval['digest']])
            self.assertEqual(omitted.returncode,2)
            approved=invoke(['approve',task,*runtime,approval['request_id'],'--digest',approval['digest']])
            self.assertEqual(approved.returncode,0,approved.stderr)
            resumed=invoke(['resume',task,*runtime,*model_flags],[final('owned CLI complete')])
            self.assertEqual(resumed.returncode,0,resumed.stderr)
            self.assertEqual((root/'effect.txt').read_text(),'owned\n')
            self.assertEqual(native_jobs.inspect_workspace(root,db)['proof_kind'],'all_native_joined')
            forked=invoke(['fork',task,'Owned follow-up',*runtime]);self.assertEqual(forked.returncode,0,forked.stderr)
            child=json.loads(forked.stdout)['id']
            with Store(db) as store:
                self.assertNotEqual(task,child)
                self.assertEqual(store.load(task)['command_runtime_policy'],store.load(child)['command_runtime_policy'])
            continued=invoke(['continue',task,'Observe prior effect',*runtime,*model_flags],
                             [tool('read_file',{'path':'effect.txt'},'read'),final('observed')])
            self.assertEqual(continued.returncode,0,continued.stderr)
            self.assertEqual((root/'effect.txt').read_text(),'owned\n')


if __name__=='__main__':unittest.main()
