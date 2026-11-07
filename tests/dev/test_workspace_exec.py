"""Host opt-in workspace primary-image authority, no native or model calls."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab import commands, sandbox
from agent_lab.command_runtime import runtime_policy
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.tools import ToolError, _worker_operation
from tests.support import ScriptedModel


class WorkspaceExecTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='agentlab-workspace-exec-',dir='/private/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir()
        self.program=self.root/'program';self.program.write_text('#!/bin/sh\necho owned\n');self.program.chmod(0o755)

    def test_default_denies_and_opt_in_binds_mode_image_and_scope(self):
        self.assertIsNone(runtime_policy(self.root))
        with self.assertRaises(ToolError):commands.prepare_command(self.root,['./program'])
        intent=commands.prepare_command(self.root,['./program'],allow_workspace_executables=True)
        self.assertEqual(intent['argv'][0],str(self.program))
        self.assertEqual(intent['workspace_executable'],{'path':'program','mode':0o755})
        self.assertIn('workspace_exec',intent['command_runtime_policy'])
        absolute=commands.prepare_command(self.root,[str(self.program)],allow_workspace_executables=True)
        self.assertEqual(absolute['executable'],intent['executable'])

    def test_changed_bytes_and_ordinary_mode_invalidate_old_intent(self):
        intent=commands.prepare_command(self.root,['./program'],allow_workspace_executables=True)
        self.program.chmod(0o744)
        with self.assertRaises(ToolError):commands.validate_command(self.root,intent,allow_workspace_executables=True)
        self.program.chmod(0o755)
        newer=commands.prepare_command(self.root,['./program'],allow_workspace_executables=True)
        before=self.program.stat();self.program.write_text('#!/bin/sh\necho other\n');os.utime(self.program,ns=(before.st_atime_ns,before.st_mtime_ns))
        with self.assertRaisesRegex(ToolError,'^command_changed$'):
            commands.validate_command(self.root,newer,allow_workspace_executables=True)

    def test_no_hidden_parent_symlink_external_bare_or_special_mode_programs(self):
        tools=CodingTools(self.root,allow_workspace_executables=True)
        hidden=self.root/'.hidden';hidden.write_text('#!/bin/sh\nexit 0\n');hidden.chmod(0o755)
        alias=self.root/'alias';alias.symlink_to(self.program)
        directory=self.root/'route';directory.symlink_to(self.root,target_is_directory=True)
        for path in ('.','program','./.hidden','./alias','./route/program','./../work/program',str(self.base/'missing')):
            with self.subTest(path=path),self.assertRaises(ToolError):tools.prepare('run_command',{'argv':[path],'cwd':'.'})
        self.program.chmod(0o4755)
        self.assertEqual(self.program.stat().st_mode&0o7777,0o4755,'fixture special mode was masked by outer environment')
        with self.assertRaises(ToolError):tools.prepare('run_command',{'argv':['./program'],'cwd':'.'})

    def test_scope_is_typed_host_setting_and_binds_echo_tests_and_restore(self):
        for bad in (1,None,'true'):
            with self.subTest(bad=bad),self.assertRaises(ToolError):runtime_policy(self.root,allow_workspace_executables=bad)
        tools=CodingTools(self.root,allow_workspace_executables=True)
        echo=tools.prepare('run_command',{'argv':['echo','owned'],'cwd':'.'})
        self.assertIn('workspace_exec',echo['command_runtime_policy'])
        tests=tools.prepare('run_tests',{})
        self.assertEqual(tests['command_runtime_policy'],tools.command_runtime_policy)
        with Store(self.base/'private.sqlite') as store:
            task=Engine(store,tools,ScriptedModel([])).create('owned scope')
            with self.assertRaises(ValueError):Engine(store,CodingTools(self.root),None).run(task)

    def test_worker_forgery_and_version2_envelope_fail_before_launch(self):
        tools=CodingTools(self.root,allow_workspace_executables=True)
        request={'root':str(self.root),'profile':'coding','operation':'prepare','payload':{'name':'run_command','args':{'argv':['./program'],'cwd':'.'}},
                 'allow_workspace_executables':True,'command_runtime_policy':None}
        with patch.object(sandbox,'_collect') as launch:
            with self.assertRaisesRegex(ToolError,'^command_runtime_changed$'):_worker_operation(request,None)
            launch.assert_not_called()

        intent=tools.prepare('run_command',{'argv':['./program'],'cwd':'.'})
        envelope={'version':2,'kind':'run_command','root':str(self.root),'timeout':3,'read_only_paths':[],
                  'node_executable':None,'python_environment':None,'command_backend':'nofork','tool_executables':None,
                  'allow_workspace_executables':True,'command_runtime_policy':copy.deepcopy(tools.command_runtime_policy),
                  'intent':intent,'start_dir':None}
        envelope['allow_workspace_executables']=1
        with patch.object(sandbox,'_collect') as launch:
            with self.assertRaisesRegex(ToolError,'^invalid_arguments$'):sandbox._runtime_supervisor(envelope)
            launch.assert_not_called()

    def test_default_tests_reject_preexisting_visible_alias_to_external_private_file(self):
        private=self.base/'private';private.write_text('OWNED_OUTSIDE_ALIAS_SENTINEL')
        os.link(private,self.root/'visible-alias')
        (self.root/'test_alias.py').write_text('''import unittest
from pathlib import Path
class Check(unittest.TestCase):
 def test_read(self):print(Path('visible-alias').read_text())
''')
        tools=CodingTools(self.root)
        with self.assertRaisesRegex(ToolError,'^permission_denied$'):
            tools.execute(tools.prepare('run_tests',{}),4)



if __name__=='__main__':unittest.main()
