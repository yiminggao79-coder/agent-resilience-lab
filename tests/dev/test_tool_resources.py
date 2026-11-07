"""Explicit Host data scopes and safe sysctl names, no model/provider calls."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.command_runtime import runtime_policy
from agent_lab.commands import prepare_command,validate_command
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.tools import ToolError,_worker_operation
from tests.support import ScriptedModel


class ToolResourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='agentlab-resource-',dir='/private/tmp');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir()
        self.resources=self.base/'resources';self.resources.mkdir()
        self.file=self.resources/'value.txt';self.file.write_text('owned resource')

    def test_default_shape_and_file_scope_hash_versus_directory_scope_identity(self):
        self.assertIsNone(runtime_policy(self.root))
        file=runtime_policy(self.root,tool_read_paths=[str(self.file)])['read_resources']['paths'][0]
        self.assertEqual(file['kind'],'file');self.assertEqual(len(file['file']['sha256']),64)
        directory=runtime_policy(self.root,tool_read_paths=[str(self.resources)])['read_resources']['paths'][0]
        self.assertEqual(directory['kind'],'directory')
        self.assertEqual(directory['contents'],'trusted_host_read_scope_not_snapshot')

    def test_reject_home_coarse_workspace_alias_special_and_overlapping_scopes(self):
        inside=self.root/'inside';inside.mkdir()
        route=self.base/'route';route.symlink_to(inside,target_is_directory=True)
        link=inside/'back';link.symlink_to(self.resources,target_is_directory=True)
        host=self.base/'host';host.symlink_to(link,target_is_directory=True)
        pipe=self.base/'pipe';os.mkfifo(pipe)
        cases=[['/sYsTeM'],['/lIbRaRy'],['/uSr'],[str(Path.home())],['/'],['/Users'],[str(self.base)],[str(inside)],[str(host)],
               [str(pipe)],[str(self.resources),str(self.file)],[str(self.file)]*2,['relative']]
        for paths in cases:
            with self.subTest(paths=paths),self.assertRaisesRegex(ToolError,'^command_runtime_not_allowed$'):
                runtime_policy(self.root,tool_read_paths=paths)

    def test_safe_named_reads_are_explicit_and_cannot_write_or_wildcard(self):
        policy=runtime_policy(self.root,tool_sysctl_read=['hw.pagesize_compat'])
        self.assertEqual(policy['tool_sysctl_read'],['hw.pagesize_compat'])
        self.assertNotIn('node',policy)
        for names in (['hw.*'],['kern.bootargs'],['hw.pagesize_compat']*2,True,'hw.pagesize_compat'):
            with self.subTest(names=names),self.assertRaises(ToolError):runtime_policy(self.root,tool_sysctl_read=names)

    def test_all_commands_tests_and_cached_state_bind_file_content_and_resource_scope(self):
        tools=CodingTools(self.root,tool_read_paths=[str(self.file)],tool_sysctl_read=['hw.pagesize_compat'])
        echo=tools.prepare('run_command',{'argv':['echo','owned'],'cwd':'.'})
        self.assertIn('read_resources',echo['command_runtime_policy'])
        tests=tools.prepare('run_tests',{});self.assertEqual(tests['command_runtime_policy'],tools.command_runtime_policy)
        old=self.file.stat();self.file.write_text('other resource');os.utime(self.file,ns=(old.st_atime_ns,old.st_mtime_ns))
        with self.assertRaises(ToolError):tools.validate_command_runtime()
        with self.assertRaises(ToolError):validate_command(self.root,echo,tool_read_paths=[str(self.file)],tool_sysctl_read=['hw.pagesize_compat'])

    def test_directory_data_is_explicitly_not_a_content_snapshot(self):
        tools=CodingTools(self.root,tool_read_paths=[str(self.resources)])
        before=tools.command_runtime_policy
        self.file.write_text('changed existing child')
        self.assertEqual(tools.validate_command_runtime(),before)

    def test_forged_worker_and_missing_resume_configuration_are_refused(self):
        tools=CodingTools(self.root,tool_read_paths=[str(self.file)])
        request={'root':str(self.root),'profile':'coding','operation':'prepare','payload':{'name':'run_tests','args':{}},
                 'tool_read_paths':[str(self.file)],'command_runtime_policy':None}
        with patch('agent_lab.sandbox.subprocess.Popen') as launch:
            with self.assertRaisesRegex(ToolError,'^command_runtime_changed$'):_worker_operation(request,None)
            launch.assert_not_called()
        with Store(self.base/'state.sqlite') as store:
            task=Engine(store,tools,ScriptedModel([])).create('owned resource scope')
            with self.assertRaises(ValueError):Engine(store,CodingTools(self.root),None).run(task)


if __name__=='__main__':unittest.main()
