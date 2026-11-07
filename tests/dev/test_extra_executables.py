"""Host explicit executable authority; no model/provider/unknown user projects."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.command_runtime import runtime_policy
from agent_lab.commands import prepare_command, validate_command
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.tools import ToolError, _worker_operation
from tests.support import ScriptedModel
try:
    from agent_lab.executables import executable_paths, executable_descriptors, parse_executable_options
except ImportError:
    executable_paths=executable_descriptors=parse_executable_options=None


class ExtraExecutableTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(executable_paths,'explicit executable registration not implemented')
        self.tmp=tempfile.TemporaryDirectory(prefix='agentlab-executables-',dir='/private/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir()
        self.binary=self.base/'checker';self.binary.write_text('#!/bin/sh\necho first\n');self.binary.chmod(0o755)
        self.mapping={'checker':str(self.binary)}

    def test_default_shapes_unchanged_and_explicit_mapping_canonical_and_hashed(self):
        self.assertEqual(executable_paths(self.root,None),{})
        self.assertIsNone(runtime_policy(self.root,tool_executables={}))
        policy=runtime_policy(self.root,tool_executables=self.mapping)
        self.assertEqual(policy,{'version':1,'executables':executable_descriptors(self.root,self.mapping)})
        self.assertEqual(policy['executables']['checker']['path'],str(self.binary.resolve()))
        self.assertEqual(len(policy['executables']['checker']['sha256']),64)

    def test_alias_shape_reserved_count_and_nonmapping_rejected(self):
        for selected in ({'python':str(self.binary)},{'node':str(self.binary)},
                         {'../x':str(self.binary)},{'bad name':str(self.binary)},{'bad\n':str(self.binary)},
                         {'tool'+str(n):str(self.binary) for n in range(17)},True,[],str(self.binary)):
            with self.subTest(selected=selected),self.assertRaisesRegex(ToolError,'^command_runtime_not_allowed$'):
                executable_descriptors(self.root,selected)

    def test_inside_nonexec_hardlink_and_parent_alias_workspace_routes_rejected(self):
        inside=self.root/'checker';inside.write_text('owned');inside.chmod(0o755)
        nonexec=self.base/'nonexec';nonexec.write_text('owned');nonexec.chmod(0o600)
        owned_dir=self.root/'routing';owned_dir.mkdir();(owned_dir/'entry').symlink_to(self.binary)
        route=self.base/'route';route.symlink_to(owned_dir,target_is_directory=True)
        hard_source=self.base/'hard-source';hard_source.write_text('owned');hard_source.chmod(0o755)
        hard=self.base/'hard';os.link(hard_source,hard)
        for path in (inside,nonexec,route/'entry',hard,self.base/'missing',Path('relative'),self.base):
            with self.subTest(path=str(path)),self.assertRaises(ToolError):
                executable_descriptors(self.root,{'checked':str(path)})

    def test_every_command_including_echo_binds_whole_mapping_and_mutations(self):
        tools=CodingTools(self.root,tool_executables=self.mapping)
        intent=tools.prepare('run_command',{'argv':['echo','owned'],'cwd':'.'})
        explicit=prepare_command(self.root,['checker','arg'],tool_executables=self.mapping)
        self.assertEqual(explicit['argv'][0],str(self.binary))
        self.assertIn('executables',intent['command_runtime_policy'])
        old=self.binary.stat();self.binary.write_text('#!/bin/sh\necho other\n');os.utime(self.binary,ns=(old.st_atime_ns,old.st_mtime_ns))
        with self.assertRaisesRegex(ToolError,'^command_runtime_changed$'):tools.validate_command_runtime()
        with self.assertRaisesRegex(ToolError,'^command_changed$'):
            validate_command(self.root,intent,tool_executables=self.mapping)

    def test_cli_option_parse_and_dynamic_schema_keep_model_args_unchanged(self):
        self.assertIsNone(parse_executable_options([]))
        self.assertEqual(parse_executable_options(['checker='+str(self.binary)]),self.mapping)
        for values in (['bad'],['checker='],['checker='+str(self.binary)]*2,['python='+str(self.binary)]):
            with self.subTest(values=values),self.assertRaises(ValueError):parse_executable_options(values)
        function=next(s['function'] for s in CodingTools(self.root,tool_executables=self.mapping).schemas() if s['function']['name']=='run_command')
        self.assertIn('checker',function['description'])
        self.assertEqual(set(function['parameters']['properties']),{'argv','cwd'})

    def test_worker_tampering_and_run_tests_intent_are_bound_before_launch(self):
        tools=CodingTools(self.root,tool_executables=self.mapping)
        policy=copy.deepcopy(tools.command_runtime_policy);policy['executables']['checker']['sha256']='0'*64
        request={'root':str(self.root),'profile':'coding','operation':'prepare','tool_executables':self.mapping,
                 'command_runtime_policy':policy,'payload':{'name':'run_command','args':{'argv':['checker'],'cwd':'.'}}}
        with patch('agent_lab.sandbox.subprocess.Popen') as launch:
            with self.assertRaisesRegex(ToolError,'^command_runtime_changed$'):_worker_operation(request,None)
            launch.assert_not_called()
        intent=tools.prepare('run_tests',{})
        self.assertEqual(intent['command_runtime_policy'],tools.command_runtime_policy)
        intent.pop('command_runtime_policy')
        with patch('agent_lab.sandbox.run_tests') as launch:
            with self.assertRaisesRegex(ToolError,'^command_runtime_changed$'):tools._execute(intent,3)
            launch.assert_not_called()

    def test_session_restore_rejects_missing_registered_mapping(self):
        with Store(self.base/'state.sqlite') as store:
            tools=CodingTools(self.root,tool_executables=self.mapping)
            task=Engine(store,tools,ScriptedModel([])).create('owned runtime')
            with self.assertRaises(ValueError):Engine(store,CodingTools(self.root),None).run(task)
            self.assertEqual(store.load(task)['command_runtime_policy'],tools.command_runtime_policy)

    def test_parent_directory_symlink_chain_must_not_hide_workspace_routing(self):
        external=self.base/'external-bin';external.mkdir()
        binary=external/'checker';binary.write_text('#!/bin/sh\nexit 0\n');binary.chmod(0o755)
        controlled=self.root/'directory-route';controlled.symlink_to(external,target_is_directory=True)
        host=self.base/'host-directory';host.symlink_to(controlled,target_is_directory=True)
        doubled=self.base/'host-double-root';doubled.symlink_to('//'+str(controlled).lstrip('/'),target_is_directory=True)
        for directory in (host,doubled):
            with self.subTest(directory=directory.name),self.assertRaisesRegex(ToolError,'^command_runtime_not_allowed$'):
                executable_descriptors(self.root,{'checker':str(directory/'checker')})

    def test_runtime_announcement_does_not_invent_node_for_registered_alias(self):
        with Store(self.base/'state.sqlite') as store:
            tools=CodingTools(self.root,tool_executables=self.mapping)
            task=Engine(store,tools,ScriptedModel([])).create('owned runtime announcement')
            text=store.load(task)['messages'][0]['content']
            self.assertNotIn('Host enabled the node executable alias',text)
            self.assertIn('checker',text)


if __name__=='__main__':unittest.main()
