"""Explicit Host config admission, no provider/model use."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.__main__ import main


class RuntimeConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='agentlab-runtime-config-',dir='/private/tmp');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir();self.config=self.base/'runtime.json'
        self.resource=self.base/'value';self.resource.write_text('owned resource')
        self.save({'tool_read_paths':[str(self.resource)],'tool_sysctl_read':['hw.pagesize_compat'],'read_only_paths':['tests']})

    def save(self,options):self.config.write_text(json.dumps({'version':1,**options}))

    def call(self,args):
        out,err=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            try:code=main(args)
            except SystemExit as e:code=e.code
        return code,out.getvalue(),err.getvalue()

    def test_load_returns_existing_kwargs_not_source_path_or_new_authority(self):
        from agent_lab.runtime_config import load_runtime_config
        config=load_runtime_config(self.config,self.root)
        self.assertEqual(config,{'tool_read_paths':[str(self.resource)],'tool_sysctl_read':['hw.pagesize_compat'],'read_only_paths':['tests']})

    def test_actual_file_change_during_fd_read_rejects_instead_of_accepting_mixed_config(self):
        from agent_lab.runtime_config import load_runtime_config,RuntimeConfigurationError
        real_fstat=os.fstat;calls=[]
        def change(descriptor):
            calls.append(descriptor)
            if len(calls)==2:self.config.write_text('{"version":1,"allow_workspace_executables":true}')
            return real_fstat(descriptor)
        with patch('agent_lab.runtime_config.os.fstat',side_effect=change):
            with self.assertRaises(RuntimeConfigurationError):load_runtime_config(self.config,self.root)
        self.assertEqual(len(calls),2)

    def test_strict_version_fields_types_duplicate_and_bounded_regular_file(self):
        from agent_lab.runtime_config import load_runtime_config,RuntimeConfigurationError
        cases=['{}','[]','{"version":true}','{"version":2}','{"version":1,"version":1}',
               '{"version":1,"api_key":"PRIVATE_SENTINEL"}','{"version":1,"budget_ledger":"PRIVATE_SENTINEL"}',
               '{"version":1,"command_backend":false}','{"version":1,"allow_workspace_executables":1}',
               '{"version":1,"tool_read_paths":"PRIVATE_SENTINEL"}','{"version":1,"read_only_paths":[1]}',
               '{"version":1,"tool_executables":[]}','{"version":1,"node_executable":null}',
               '{"version":1,"tool_sysctl_read":[true]}','{"version":1,"tool_sysctl_read":[NaN]}',
               '{"version":1,"tool_executables":{"x":"/usr/bin/true","x":"/usr/bin/false"}}',
               '{"version":1,"tool_read_paths":'+json.dumps(['/tmp/x']*9)+'}',
               '{"version":1,"read_only_paths":'+json.dumps(['tests']*129)+'}',
               ' '*32769]
        for raw in cases:
            self.config.write_text(raw)
            with self.subTest(raw=raw[:60]),self.assertRaises(RuntimeConfigurationError) as caught:load_runtime_config(self.config,self.root)
            self.assertEqual(caught.exception.code,'invalid_runtime_config')
            self.assertNotIn('PRIVATE',str(caught.exception))

    def test_config_rejects_workspace_direct_intermediate_alias_symlink_hardlink_fifo(self):
        from agent_lab.runtime_config import load_runtime_config,RuntimeConfigurationError
        internal=self.root/'config.json';internal.write_bytes(self.config.read_bytes())
        direct=self.base/'direct';direct.symlink_to(self.config)
        route=self.base/'route';route.symlink_to(self.root,target_is_directory=True)
        back=self.root/'back';back.symlink_to(self.base,target_is_directory=True)
        hard=self.base/'hard';os.link(self.config,hard)
        pipe=self.base/'pipe';os.mkfifo(pipe)
        for selected in (internal,direct,hard,pipe,route/'back'/'runtime.json',Path('runtime.json')):
            with self.subTest(path=selected),self.assertRaises(RuntimeConfigurationError):load_runtime_config(selected,self.root)

    def test_runtime_check_validates_without_store_key_model_or_subprocess(self):
        db=self.base/'must-not-exist.sqlite'
        with patch('agent_lab.__main__.Store',side_effect=AssertionError('Store opened')),patch('agent_lab.__main__.OpenAIModel',side_effect=AssertionError('Model constructed')),patch('agent_lab.sandbox.subprocess.Popen',side_effect=AssertionError('executed')):
            code,out,err=self.call(['--db',str(db),'runtime-check','--workspace',str(self.root),'--runtime-config',str(self.config)])
        self.assertEqual((code,err),(0,''),err)
        result=json.loads(out);self.assertFalse(result['tool_execution_performed']);self.assertFalse(result['model_called'])
        self.assertIn('read_resources',result['command_runtime_policy']);self.assertEqual(result['read_only_scopes'],['tests'])
        self.assertFalse(db.exists());self.assertEqual(list(self.root.iterdir()),[])

    def test_explicit_default_and_abbreviated_flags_conflict_before_config_read_or_store(self):
        from agent_lab.runtime_config import RuntimeConfigurationError
        cases=[['--command-backend','nofork'],['--command-b','nofork'],['--allow-workspace-executables'],
               ['--node-executable','/missing'],['--read-only-path','tests'],['--tool-sysctl-read','hw.pagesize_compat'],
               ['--tool-executable','x=/missing'],['--tool-read-path','/missing'],['--python-environment','/missing']]
        for flags in cases:
            with self.subTest(flags=flags),patch('agent_lab.runtime_config.load_runtime_config',side_effect=AssertionError('config read')),patch('agent_lab.__main__.Store',side_effect=AssertionError('Store opened')):
                code,out,err=self.call(['runtime-check','--workspace',str(self.root),'--runtime-config',str(self.config),*flags])
            self.assertEqual((code,out),(2,''));self.assertEqual(json.loads(err)['error'],'runtime_config_flag_conflict')

    def test_invalid_config_fails_before_deepseek_key_loading_and_store(self):
        self.config.write_text('{"version":1,"api_key":"PRIVATE_SENTINEL"}')
        with patch('agent_lab.budget.load_first_line_key',side_effect=AssertionError('key read')),patch('agent_lab.__main__.Store',side_effect=AssertionError('Store opened')):
            code,out,err=self.call(['run','Owned task','--workspace',str(self.root),'--runtime-config',str(self.config),'--load-deepseek-key','--allow-api'])
        self.assertEqual((code,out),(2,''));self.assertEqual(json.loads(err)['error'],'invalid_runtime_config')
        self.assertNotIn('PRIVATE',err)

    def test_default_runtime_check_has_legacy_none_and_no_hidden_writes(self):
        code,out,err=self.call(['runtime-check','--workspace',str(self.root)])
        self.assertEqual((code,err),(0,''),err);self.assertIsNone(json.loads(out)['command_runtime_policy'])
        self.assertEqual(list(self.root.iterdir()),[])


if __name__=='__main__':unittest.main()
