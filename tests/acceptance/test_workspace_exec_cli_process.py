"""New CLI processes with a Host-compiled owned image; not an LLM build result."""
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
from tests.native_fixture import compile_c
from tests.support import tool, final


class WorkspaceExecCLIProcessTests(unittest.TestCase):
    def test_cli_scope_binds_approval_resume_and_session_fork(self):
        with tempfile.TemporaryDirectory(prefix='agentlab-workspace-cli-',dir='/private/tmp') as directory:
            base=Path(directory);root=base/'work';root.mkdir();db=base/'state.sqlite'
            compile_c(root,'program','#include <stdio.h>\nint main(void){puts("workspace image");return 0;}\n')
            env={'PATH':os.defpath,'LANG':'C','PYTHONDONTWRITEBYTECODE':'1','AGENT_LAB_FIXTURE_KEY':'synthetic-no-network'}
            def invoke(args,responses=()):
                return subprocess.run([sys.executable,'-I','-c',HARNESS,str(REPO),'--db',str(db),*args],
                    env={**env,'AGENT_LAB_FIXTURE_RESPONSES':json.dumps(responses)},capture_output=True,text=True,timeout=8)
            workspace=['--workspace',str(root)]
            scope=[*workspace,'--allow-workspace-executables']
            flags=['--profile','coding','--model','synthetic','--base-url','https://example.invalid/v1',
                   '--api-key-env','AGENT_LAB_FIXTURE_KEY','--allow-api']
            started=invoke(['run','Owned native CLI',*scope,*flags],[tool('run_command',{'argv':['./program'],'cwd':'.'},'native')])
            self.assertEqual(started.returncode,3,started.stderr)
            task=json.loads(started.stdout.splitlines()[0])['task_id']
            request=invoke(['approval',task,*scope]);self.assertEqual(request.returncode,0,request.stderr)
            approval=json.loads(request.stdout)
            denied=invoke(['approve',task,approval['request_id'],*workspace,'--digest',approval['digest']])
            self.assertEqual(denied.returncode,2)
            approved=invoke(['approve',task,approval['request_id'],*scope,'--digest',approval['digest']])
            self.assertEqual(approved.returncode,0,approved.stderr)
            resumed=invoke(['resume',task,*scope,*flags],[final('owned executed')])
            self.assertEqual(resumed.returncode,0,resumed.stderr)
            self.assertEqual(native_jobs.inspect_workspace(root,db)['proof_kind'],'all_native_joined')
            forked=invoke(['fork',task,'Owned next task',*scope]);self.assertEqual(forked.returncode,0,forked.stderr)
            child=json.loads(forked.stdout)['id']
            with Store(db) as store:
                self.assertEqual(store.load(task)['command_runtime_policy'],store.load(child)['command_runtime_policy'])
                self.assertIn('workspace_exec',store.load(child)['command_runtime_policy'])

    def test_runtime_cli_help_has_explicit_scope_and_primary_secondary_distinction(self):
        for command in ('run','resume','continue','compact','chat','fork','approval','approve','deny'):
            result=subprocess.run([sys.executable,'-m','agent_lab',command,'--help'],cwd=REPO,
                                  capture_output=True,text=True,timeout=3)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('--allow-workspace-executables',result.stdout)
            self.assertIn('secondary',result.stdout)


if __name__=='__main__':unittest.main()
