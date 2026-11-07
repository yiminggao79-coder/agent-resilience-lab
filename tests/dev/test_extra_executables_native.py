"""Generic runtime registration through actual owned Coding tasks, not LLM scores."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_lab import native_jobs, sandbox, command_runtime
from agent_lab.coding_tools import CodingTools
from agent_lab.confined_backend import native_python_entry
from agent_lab.engine import Engine, Limits
from agent_lab.store import Store
from agent_lab.tools import ToolError
from tests.support import ScriptedModel, tool, final


class ExtraExecutableNativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='agentlab-extra-native-',dir='/private/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir()
        self.mapping={'ruby':'/usr/bin/ruby'}

    def test_real_ruby_multifile_failure_patch_and_success_with_exact_approvals(self):
        (self.root/'lib').mkdir()
        (self.root/'lib'/'names.rb').write_text('def names(values)\n values.map(&:strip).reject(&:empty?).map(&:downcase)\nend\n')
        tests='''require_relative 'lib/names'
cases=[[[" Alpha ","alpha"," BETA ",""],["alpha","beta"]],[["X"," x "],["x"]],[[],[]]]
cases.each_with_index do |(input,expected),index|
 actual=names(input)
 raise "case #{index}: #{actual.inspect} != #{expected.inspect}" unless actual==expected
end
raise "credential inherited" if ENV.keys.any?{|k|["DEEPSEEK_API_KEY","AGENT_LAB_NATIVE_JOB","AGENT_LAB_TOOL_OWNER_FD"].include?(k)}
puts "3 cases passed"
'''
        (self.root/'test_names.rb').write_text(tests)
        responses=[tool('read_file',{'path':'lib/names.rb'},'read'),
                   tool('run_command',{'argv':['ruby','--disable-gems','test_names.rb'],'cwd':'.'},'before'),
                   tool('apply_patch',{'path':'lib/names.rb','old':'.map(&:downcase)','new':'.map(&:downcase).uniq'},'fix'),
                   tool('run_command',{'argv':['ruby','--disable-gems','test_names.rb'],'cwd':'.'},'after'),final('done')]
        tools=CodingTools(self.root,tool_executables=self.mapping,read_only_paths=['test_names.rb'])
        with Store(self.base/'state.sqlite') as store:
            engine=Engine(store,tools,ScriptedModel(responses));task=engine.create('Owned Ruby fix',Limits(tool_timeout=6))
            state=engine.run(task);approved=0
            while state['phase']=='awaiting_approval' and approved<3:
                request=engine.approval_request(task);approved+=1
                engine.decide_approval(task,request['request_id'],request['digest'],True)
                state=engine.run(task)
            self.assertEqual((state['phase'],approved),('completed',2),state.get('reason'))
            feedback=[json.loads(m['content']) for m in state['messages'] if m['role']=='tool']
            runs=[r for r in feedback if 'exit_code' in r]
            self.assertEqual([r['exit_code'] for r in runs],[1,0],runs)
            self.assertEqual(runs[-1]['stdout'],'3 cases passed\n')
            self.assertEqual((self.root/'test_names.rb').read_text(),tests)
            self.assertEqual(native_jobs.inspect_workspace(self.root,self.base/'state.sqlite')['proof_kind'],'all_native_joined')

    def test_confined_tests_can_spawn_registered_runtime_with_selected_dependencies(self):
        env=self.base/'env'
        built=subprocess.run([str(native_python_entry()),'-I','-m','venv','--without-pip',str(env)],
                             env={'PATH':os.defpath},capture_output=True,timeout=5)
        self.assertEqual(built.returncode,0,built.stderr)
        packages=env/'lib'/'python3.9'/'site-packages'
        (packages/'owned_dependency.py').write_text('VALUE=42\n')
        (self.root/'test_registered.py').write_text('''import subprocess,unittest,owned_dependency
class Check(unittest.TestCase):
 def test_registered(self):
  result=subprocess.run(['/usr/bin/ruby','--disable-gems','-e','puts 6*7'],capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stderr)
  self.assertEqual(result.stdout,str(owned_dependency.VALUE)+'\\n')
''')
        tools=CodingTools(self.root,tool_executables=self.mapping,command_backend='confined-fork',python_environment=env)
        result=tools.execute(tools.prepare('run_tests',{}),6)
        self.assertEqual(result['exit_code'],0,result)

    def test_registered_entry_does_not_admit_adjacent_private_files_or_readonly_writes(self):
        private=self.base/'private.txt';private.write_text('owned sentinel')
        (self.root/'protected.txt').write_text('original')
        body='''require 'socket'
raise "environment inherited" unless ENV['SYNTHETIC_KEY'].nil?
begin; File.read(ARGV[0]); raise "private readable"; rescue Errno::EPERM,Errno::EACCES; end
begin; File.write('protected.txt','bad'); raise "readonly writable"; rescue Errno::EPERM,Errno::EACCES; end
begin; TCPSocket.new('127.0.0.1',9); raise "network allowed"; rescue Errno::EPERM,Errno::EACCES; end
puts "boundaries passed"
'''
        tools=CodingTools(self.root,tool_executables=self.mapping,read_only_paths=['protected.txt'])
        with patch.dict(os.environ,{'SYNTHETIC_KEY':'synthetic-only'}):
            result=tools.execute(tools.prepare('run_command',{'argv':['ruby','--disable-gems','-e',body,str(private)],'cwd':'.'}),6)
        self.assertEqual(result['exit_code'],0,result)
        self.assertEqual(result['stdout'],'boundaries passed\n')
        self.assertEqual((self.root/'protected.txt').read_text(),'original')

    def test_versioned_supervisor_envelope_validates_expected_policy_before_probe(self):
        self.assertTrue(hasattr(sandbox,'_runtime_supervisor'),'versioned supervisor dispatch missing')
        tools=CodingTools(self.root,tool_executables=self.mapping)
        intent=tools.prepare('run_command',{'argv':['ruby','--disable-gems','-e','puts 42'],'cwd':'.'})
        request={'version':1,'kind':'run_command','root':str(self.root),'timeout':3,
                 'read_only_paths':[],'node_executable':None,'python_environment':None,
                 'command_backend':'nofork','tool_executables':self.mapping,
                 'command_runtime_policy':copy.deepcopy(tools.command_runtime_policy),'intent':intent,'start_dir':None}
        request['command_runtime_policy']['executables']['ruby']['sha256']='0'*64
        with patch.object(sandbox,'_collect') as launch:
            with self.assertRaisesRegex(ToolError,'^command_runtime_changed$'):sandbox._runtime_supervisor(request)
            launch.assert_not_called()
        request['version']=True
        with patch.object(sandbox,'_collect') as launch:
            with self.assertRaisesRegex(ToolError,'^invalid_arguments$'):sandbox._runtime_supervisor(request)
            launch.assert_not_called()

    def test_profile_paths_do_not_repeat_executable_identity_hash(self):
        scratch=self.base/'scratch';scratch.mkdir()
        with patch.object(command_runtime,'executable_descriptor',wraps=command_runtime.executable_descriptor) as hashed:
            sandbox._command_profile(self.root,scratch,tool_executables=self.mapping)
        self.assertEqual(hashed.call_count,0)

    def test_executable_literal_does_not_grant_its_parent_directory_contents(self):
        directory=self.base/'external-tools';directory.mkdir()
        entry=directory/'checker';entry.write_text('#!/bin/sh\nexec /bin/cat "$1"\n');entry.chmod(0o755)
        private=directory/'private.txt';private.write_text('OWNED_PRIVATE_NEIGHBOR_SENTINEL')
        (self.root/'visible.txt').write_text('owned visible file')
        tools=CodingTools(self.root,tool_executables={'checked':str(entry)})
        good=tools.execute(tools.prepare('run_command',{'argv':['checked','visible.txt'],'cwd':'.'}),4)
        self.assertEqual((good['exit_code'],good['stdout']),(0,'owned visible file'))
        denied=tools.execute(tools.prepare('run_command',{'argv':['checked',str(private)],'cwd':'.'}),4)
        self.assertNotEqual(denied['exit_code'],0,denied)
        self.assertNotIn(private.read_text(),denied['stdout']+denied['stderr'])


if __name__=='__main__':unittest.main()
