"""Fresh CLI config reuse, real native tool, synthetic model protocol only."""
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


class RuntimeConfigCLIProcessTests(unittest.TestCase):
    def test_single_config_survives_approval_resume_fork_and_rejects_changed_grants(self):
        with tempfile.TemporaryDirectory(prefix='agentlab-runtime-cli-',dir='/private/tmp') as directory:
            base=Path(directory);root=base/'work';root.mkdir();db=base/'state.sqlite'
            (root/'tests').mkdir();(root/'tests'/'test_value.py').write_text('immutable owned test')
            resource=base/'value';resource.write_text('owned runtime config resource')
            config=base/'runtime.json'
            options={'version':1,'tool_executables':{'ruby':'/usr/bin/ruby'},'command_backend':'confined-fork',
                'tool_read_paths':[str(resource)],'tool_sysctl_read':['hw.pagesize_compat'],
                'allow_workspace_executables':True,'read_only_paths':['tests']}
            config.write_text(json.dumps(options))
            env={'PATH':os.defpath,'LANG':'C','PYTHONDONTWRITEBYTECODE':'1','AGENT_LAB_FIXTURE_KEY':'synthetic-no-network'}
            def invoke(args,responses=()):
                return subprocess.run([sys.executable,'-I','-c',HARNESS,str(REPO),'--db',str(db),*args],
                    env={**env,'AGENT_LAB_FIXTURE_RESPONSES':json.dumps(responses)},capture_output=True,text=True,timeout=10)
            workspace=['--workspace',str(root)];runtime=[*workspace,'--runtime-config',str(config)]
            flags=['--profile','coding','--model','synthetic','--base-url','https://example.invalid/v1','--api-key-env','AGENT_LAB_FIXTURE_KEY','--allow-api']
            started=invoke(['run','Read owned resource via registered tool',*runtime,*flags],
                [tool('run_command',{'argv':['ruby','--disable-gems','-e','STDOUT.write(File.read(ARGV[0]))',str(resource)],'cwd':'.'},'resource')])
            self.assertEqual(started.returncode,3,started.stderr)
            task=json.loads(started.stdout.splitlines()[0])['task_id']
            approval=invoke(['approval',task,*runtime]);self.assertEqual(approval.returncode,0,approval.stderr);request=json.loads(approval.stdout)
            missing=invoke(['approve',task,request['request_id'],*workspace,'--digest',request['digest']]);self.assertEqual(missing.returncode,2)
            config.write_text(json.dumps({**options,'allow_workspace_executables':False}))
            changed=invoke(['approve',task,request['request_id'],*runtime,'--digest',request['digest']]);self.assertEqual(changed.returncode,2)
            config.write_text(json.dumps(options))
            approved=invoke(['approve',task,request['request_id'],*runtime,'--digest',request['digest']]);self.assertEqual(approved.returncode,0,approved.stderr)
            resumed=invoke(['resume',task,*runtime,*flags],[final('owned resource observed')]);self.assertEqual(resumed.returncode,0,resumed.stderr)
            self.assertEqual(native_jobs.inspect_workspace(root,db)['proof_kind'],'all_native_joined')
            forked=invoke(['fork',task,'Owned new task',*runtime]);self.assertEqual(forked.returncode,0,forked.stderr);child=json.loads(forked.stdout)['id']
            with Store(db) as store:
                state=store.load(task);self.assertEqual(state['command_runtime_policy'],store.load(child)['command_runtime_policy'])
                result=json.loads(next(m['content'] for m in state['messages'] if m['role']=='tool'))
                self.assertEqual((result['exit_code'],result['stdout']),(0,'owned runtime config resource'))
                before=store.db.execute('SELECT state FROM tasks WHERE id=?',(task,)).fetchone()[0]
            resource.write_text('changed owned resource')
            rejected=invoke(['fork',task,'Must reject resource change',*runtime]);self.assertEqual(rejected.returncode,2)
            with Store(db) as store:self.assertEqual(store.db.execute('SELECT state FROM tasks WHERE id=?',(task,)).fetchone()[0],before)
            self.assertEqual((root/'tests'/'test_value.py').read_text(),'immutable owned test')

    def test_all_runtime_commands_advertise_explicit_config_and_check_never_creates_db(self):
        for command in ('run','resume','continue','compact','chat','fork','approval','approve','deny','runtime-check'):
            result=subprocess.run([sys.executable,'-m','agent_lab',command,'--help'],cwd=REPO,capture_output=True,text=True,timeout=3)
            self.assertEqual(result.returncode,0,result.stderr);self.assertIn('--runtime-config',result.stdout)
        with tempfile.TemporaryDirectory(prefix='agentlab-runtime-check-',dir='/private/tmp') as directory:
            base=Path(directory);root=base/'work';root.mkdir();db=base/'must-not-exist.sqlite'
            result=subprocess.run([sys.executable,'-m','agent_lab','--db',str(db),'runtime-check','--workspace',str(root)],
                                  cwd=REPO,env={'PATH':os.defpath},capture_output=True,text=True,timeout=3)
            self.assertEqual(result.returncode,0,result.stderr);self.assertFalse(db.exists())
            self.assertEqual(json.loads(result.stdout),{'command_runtime_policy':None,'read_only_scopes':[],'tool_execution_performed':False,'model_called':False})


if __name__=='__main__':unittest.main()
