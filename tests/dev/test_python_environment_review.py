"""Independent explicit-venv checks; no dependency install or provider call."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import venv
from unittest.mock import patch

from agent_lab import sandbox
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine
from agent_lab.python_environment import environment_descriptor
from agent_lab.store import Store
from agent_lab.tools import ToolError
from tests.support import ScriptedModel, tool


class PythonEnvironmentReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name).resolve(); self.root=self.base/'work'; self.root.mkdir()
        self.env=self.base/'env'; venv.EnvBuilder(with_pip=False,symlinks=True).create(self.env)
        self.packages=self.env/'lib'/('python%d.%d'%sys.version_info[:2])/'site-packages'
        self.module=self.packages/'synthetic_dependency.py'; self.module.write_text('VALUE=42\n')

    def test_launcher_chain_cannot_route_through_workspace_directory_alias(self):
        (self.root/'runtime-hop').symlink_to(Path(sys.executable).resolve())
        alias=self.base/'outside-alias'; alias.symlink_to(self.root,target_is_directory=True)
        launcher=self.env/'bin'/'python'; launcher.unlink(); launcher.symlink_to(alias/'runtime-hop')
        with self.assertRaisesRegex(ToolError,'^command_runtime_not_allowed$'):
            environment_descriptor(self.root,self.env)

    def test_dependency_change_blocks_approved_other_command_and_old_test_intent(self):
        cached=CodingTools(self.root,python_environment=self.env)
        prepared_test=cached._prepare('run_tests',{})
        model=ScriptedModel([tool('run_command',{'argv':['echo','synthetic'],'cwd':'.'})])
        with Store(self.base/'state.sqlite') as store:
            engine=Engine(store,cached,model); task=engine.create('Approved non-Python command')
            self.assertEqual(engine.run(task)['phase'],'awaiting_approval')
            request=engine.approval_request(task)
            engine.decide_approval(task,request['request_id'],request['digest'],True)
            before=store.load(task)
            stamp=self.module.stat(); self.module.write_text('VALUE=43\n')
            os.utime(self.module,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
            fresh=CodingTools(self.root,python_environment=self.env)
            for selected in (cached,fresh):
                with self.subTest(cached=selected is cached), \
                     patch.object(sandbox,'run_tests',side_effect=AssertionError('no stale test launch')) as launch:
                    with self.assertRaisesRegex(ValueError,'^resume_configuration_mismatch$'):
                        Engine(store,selected,model).run(task)
                    with self.assertRaisesRegex(ToolError,'^command_runtime_changed$'):
                        selected._execute(prepared_test,2)
                    launch.assert_not_called()
            self.assertEqual(store.load(task),before)
            self.assertEqual(len(model.seen),1)

    def test_actual_dependency_read_grant_cannot_mutate_packages_or_read_private_venv_files(self):
        backend=Path('/usr/bin/sandbox-exec')
        if not backend.is_file(): self.skipTest('backend unavailable; no bare fallback')
        probe=subprocess.run([str(backend),'-p','(version 1)(allow default)','/usr/bin/true'],
                             capture_output=True,timeout=2)
        if probe.returncode: self.skipTest('outer host denies Seatbelt; no bare fallback')
        private=self.base/'private.txt'; private.write_text('SYNTHETIC_PRIVATE_CONTENT')
        env_private=self.env/'private.cfg'; env_private.write_text('SYNTHETIC_PRIVATE_CONFIG')
        self.assertEqual(self.root.stat().st_dev,self.module.stat().st_dev)
        body='''import fcntl,json,os
from pathlib import Path
module=Path(%r); outcomes={}
def check(label,operation):
 try: operation(); outcomes[label]='allowed'
 except PermissionError: outcomes[label]='denied'
 except OSError as error: outcomes[label]='os_error_'+str(error.errno)
check('write',lambda:module.write_text('VALUE=99\\n'))
def alias_write():
 os.link(module,'package-alias.py')
 Path('package-alias.py').write_text('VALUE=99\\n')
check('link_write',alias_write)
check('private',lambda:Path(%r).read_text())
check('env_private',lambda:Path(%r).read_text())
for operation in (80,110):
 fd=os.open(module,os.O_RDONLY)
 try:check('fcntl'+str(operation),lambda:fcntl.fcntl(fd,operation,0))
 finally:os.close(fd)
print(json.dumps(outcomes))
'''%(str(self.module),str(private),str(env_private))
        tools=CodingTools(self.root,python_environment=self.env)
        result=tools.execute(tools.prepare('run_command',{'argv':['python','-c',body],'cwd':'.'}),10)
        self.assertEqual(result['exit_code'],0,result)
        observed=json.loads(result['stdout'])
        self.assertEqual(observed,{name:'denied' for name in ('write','link_write','private','env_private','fcntl80','fcntl110')})
        self.assertEqual(self.module.read_text(),'VALUE=42\n')
        self.assertEqual(self.module.stat().st_nlink,1)
        self.assertEqual(private.read_text(),'SYNTHETIC_PRIVATE_CONTENT')
        self.assertEqual(env_private.read_text(),'SYNTHETIC_PRIVATE_CONFIG')
