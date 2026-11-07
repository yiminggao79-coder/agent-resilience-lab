"""Actual tools/worker/supervisor wiring with synthetic code, no LLM quality."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine, Limits
from agent_lab import native_jobs, sandbox
from agent_lab.confined_backend import native_python_entry
from agent_lab.store import Store
from agent_lab.tools import ToolError
from tests.support import ScriptedModel, tool, final

REPO = Path(__file__).resolve().parents[2]


class ConfinedBackendNativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='agentlab-backend-native-',dir='/private/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir()

    def test_cli_exposes_explicit_saved_backend_on_all_runtime_commands(self):
        for command in ('run','resume','continue','compact','chat','fork','approval','approve','deny'):
            result=subprocess.run([sys.executable,'-m','agent_lab',command,'--help'],cwd=REPO,
                                  capture_output=True,text=True,timeout=3)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('--command-backend {nofork,confined-fork}',result.stdout)

    def test_real_engine_test_failure_patch_and_success_use_subprocesses(self):
        (self.root/'calc.py').write_text('def result(): return 2\n')
        tests='''import subprocess,sys,unittest
from pathlib import Path
class Check(unittest.TestCase):
 def test_process(self):
  p=subprocess.run([sys.executable,'-I','-c','import sys;sys.path.insert(0,".");from calc import result;print(result())'],capture_output=True,text=True)
  self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(p.stdout.strip(),'3')
 def test_pipe(self):
  p=subprocess.run(['/bin/sh','-c','echo owned | cat'],capture_output=True,text=True)
  self.assertEqual((p.returncode,p.stdout),(0,'owned\\n'))
'''
        (self.root/'test_calc.py').write_text(tests)
        model=ScriptedModel([tool('run_tests',{},'first'),
                             tool('apply_patch',{'path':'calc.py','old':'return 2','new':'return 3'},'fix'),
                             tool('run_tests',{},'last'),final('done')])
        tools=CodingTools(self.root,command_backend='confined-fork')
        with Store(self.base/'state.sqlite') as store:
            engine=Engine(store,tools,model);task=engine.create('owned subprocess bug')
            state=engine.run(task)
            self.assertEqual(state['phase'],'completed',state.get('reason'))
            feedback=[json.loads(message['content']) for message in state['messages'] if message['role']=='tool']
            results=[r for r in feedback if 'exit_code' in r]
            self.assertEqual([r['exit_code'] for r in results],[1,0])
            self.assertEqual((self.root/'test_calc.py').read_text(),tests)
            self.assertEqual(native_jobs.inspect_workspace(self.root,self.base/'state.sqlite')['proof_kind'],'all_native_joined')

    def test_exact_command_approval_pipe_private_boundary_and_native_receipt(self):
        private=self.base/'private.txt';private.write_text('synthetic sentinel')
        args={'argv':['python','-I','-c',"import os,socket,subprocess;from pathlib import Path;"
              "assert not any(k in os.environ for k in ('DEEPSEEK_API_KEY','AGENT_LAB_NATIVE_JOB','AGENT_LAB_TOOL_OWNER_FD'));"
              "p=subprocess.run(['/bin/sh','-c','echo owned | cat'],capture_output=True);"
              "assert (p.returncode,p.stdout)==(0,b'owned\\n');"
              "Path('effect.txt').write_text('owned');"
              "\ntry:Path("+repr(str(private))+").read_text();raise AssertionError('private read')"
              "\nexcept PermissionError:pass"
              "\ntry:Path('protected.txt').write_text('bad');raise AssertionError('readonly write')"
              "\nexcept PermissionError:pass"
              "\nprint('native pipe and boundary checks passed')"],'cwd':'.'}
        (self.root/'protected.txt').write_text('original')
        tools=CodingTools(self.root,command_backend='confined-fork',read_only_paths=['protected.txt'])
        model=ScriptedModel([tool('run_command',args,'command'),final('done')])
        with Store(self.base/'state.sqlite') as store:
            engine=Engine(store,tools,model);task=engine.create('owned command')
            state=engine.run(task);self.assertEqual(state['phase'],'awaiting_approval')
            self.assertFalse((self.root/'effect.txt').exists())
            approval=engine.approval_request(task)
            engine.decide_approval(task,approval['request_id'],approval['digest'],True)
            with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'synthetic-never-a-real-key'}):state=engine.run(task)
            self.assertEqual(state['phase'],'completed',state.get('reason'))
            feedback=[json.loads(m['content']) for m in state['messages'] if m['role']=='tool']
            self.assertEqual(feedback[0]['exit_code'],0,feedback)
            self.assertEqual((self.root/'effect.txt').read_text(),'owned')
            self.assertEqual((self.root/'protected.txt').read_text(),'original')
            self.assertEqual(native_jobs.inspect_workspace(self.root,self.base/'state.sqlite')['proof_kind'],'all_native_joined')

    def test_native_entry_dependency_venv_runs_inside_profile(self):
        env=self.base/'env'
        created=subprocess.run([str(native_python_entry()),'-I','-m','venv','--without-pip',str(env)],
                               env={'PATH':os.defpath},capture_output=True,timeout=5)
        self.assertEqual(created.returncode,0,created.stderr)
        packages=env/'lib'/('python%d.%d'%sys.version_info[:2])/'site-packages'
        (packages/'owned_dependency.py').write_text('VALUE=37\n')
        (self.root/'test_dep.py').write_text('''import subprocess,sys,unittest,owned_dependency
class Check(unittest.TestCase):
 def test_dep(self):
  self.assertEqual(owned_dependency.VALUE,37)
  result=subprocess.run([sys.executable,'-I','-c','import owned_dependency;print(owned_dependency.VALUE)'],capture_output=True,text=True)
  self.assertEqual((result.returncode,result.stdout),(0,'37\\n'))
''')
        tools=CodingTools(self.root,command_backend='confined-fork',python_environment=env)
        result=tools.execute(tools.prepare('run_tests',{}),5)
        self.assertEqual(result['exit_code'],0,result)

    def test_changed_supervisor_backend_manifest_is_rejected_before_probe(self):
        tools=CodingTools(self.root,command_backend='confined-fork')
        intent=tools.prepare('run_command',{'argv':['echo','owned'],'cwd':'.'})
        manifest=dict(tools.command_runtime_policy['backend']);manifest['cleanup_seconds']=0
        with patch.object(sandbox,'_collect') as launch:
            with self.assertRaisesRegex(ToolError,'^command_runtime_changed$'):
                sandbox._run_command(self.root,intent,3,command_backend='confined-fork',backend_policy=manifest)
            launch.assert_not_called()

    def test_timeout_keeps_effect_unknown_after_confirmed_local_group_cleanup(self):
        body="""import os,time
from pathlib import Path
child=os.fork()
Path('started-'+str(os.getpid())).write_text('owned')
time.sleep(2)
Path('late-'+str(os.getpid())).write_text('unexpected')
"""
        tools=CodingTools(self.root,command_backend='confined-fork')
        with Store(self.base/'state.sqlite') as store:
            engine=Engine(store,tools,ScriptedModel([tool('run_command',{'argv':['python','-c',body],'cwd':'.'},'timeout')]))
            task=engine.create('owned bounded timeout',Limits(tool_timeout=.4))
            self.assertEqual(engine.run(task)['phase'],'awaiting_approval')
            request=engine.approval_request(task)
            engine.decide_approval(task,request['request_id'],request['digest'],True)
            state=engine.run(task)
            self.assertEqual(state['phase'],'needs_confirmation',state.get('reason'))
            self.assertTrue(state['outcome_unresolved'])
            self.assertTrue(list(self.root.glob('started-*')))
            self.assertFalse(list(self.root.glob('late-*')))
            self.assertEqual(native_jobs.inspect_workspace(self.root,self.base/'state.sqlite')['proof_kind'],'all_native_joined')


if __name__=='__main__':unittest.main()
