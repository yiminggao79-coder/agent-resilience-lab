"""Fresh CLI authority/approval/resume with actual registered executable."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from .test_confined_backend_cli_process import HARNESS, REPO
from agent_lab import native_jobs
from agent_lab.store import Store
from tests.support import tool, final


class ExtraExecutableCLIProcessTests(unittest.TestCase):
    def test_registered_runtime_is_repeated_on_approval_resume_and_fork(self):
        with tempfile.TemporaryDirectory(prefix='agentlab-extra-cli-',dir='/private/tmp') as directory:
            base=Path(directory);root=base/'work';root.mkdir();db=base/'state.sqlite'
            clean={'PATH':os.defpath,'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1',
                   'AGENT_LAB_FIXTURE_KEY':'synthetic-no-network'}
            def invoke(args,responses=()):
                return subprocess.run([sys.executable,'-I','-c',HARNESS,str(REPO),'--db',str(db),*args],
                    env={**clean,'AGENT_LAB_FIXTURE_RESPONSES':json.dumps(responses)},capture_output=True,text=True,timeout=8)
            workspace=['--workspace',str(root)]
            runtime=[*workspace,'--tool-executable','ruby=/usr/bin/ruby']
            flags=['--profile','coding','--model','synthetic','--base-url','https://example.invalid/v1',
                   '--api-key-env','AGENT_LAB_FIXTURE_KEY','--allow-api']
            args={'argv':['ruby','--disable-gems','-e',"File.write('effect.txt','owned'); puts 'registered'"],'cwd':'.'}
            started=invoke(['run','Owned generic runtime',*runtime,*flags],[tool('run_command',args,'ruby')])
            self.assertEqual(started.returncode,3,started.stderr)
            task=json.loads(started.stdout.splitlines()[0])['task_id']
            self.assertFalse((root/'effect.txt').exists())
            request=invoke(['approval',task,*runtime]);self.assertEqual(request.returncode,0,request.stderr)
            approval=json.loads(request.stdout)
            rejected=invoke(['approve',task,approval['request_id'],*workspace,'--digest',approval['digest']])
            self.assertEqual(rejected.returncode,2)
            approved=invoke(['approve',task,approval['request_id'],*runtime,'--digest',approval['digest']])
            self.assertEqual(approved.returncode,0,approved.stderr)
            resumed=invoke(['resume',task,*runtime,*flags],[final('complete')])
            self.assertEqual(resumed.returncode,0,resumed.stderr)
            self.assertEqual((root/'effect.txt').read_text(),'owned')
            self.assertEqual(native_jobs.inspect_workspace(root,db)['proof_kind'],'all_native_joined')
            forked=invoke(['fork',task,'Owned next task',*runtime]);self.assertEqual(forked.returncode,0,forked.stderr)
            child=json.loads(forked.stdout)['id']
            with Store(db) as store:
                self.assertEqual(store.load(task)['command_runtime_policy'],store.load(child)['command_runtime_policy'])
                self.assertEqual(store.load(task)['command_runtime_policy']['executables']['ruby']['path'],'/usr/bin/ruby')

    def test_all_runtime_cli_commands_advertise_the_same_host_option(self):
        for command in ('run','resume','continue','compact','chat','fork','approval','approve','deny'):
            result=subprocess.run([sys.executable,'-m','agent_lab',command,'--help'],cwd=REPO,
                                  env={'PATH':os.defpath,'LANG':'C'},capture_output=True,text=True,timeout=3)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('--tool-executable ALIAS=ABS_PATH',result.stdout)


if __name__=='__main__':unittest.main()
