"""Actual resource confinement/compiler execution; scripted decisions, no LLM score."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab import native_jobs,sandbox
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.store import Store
from agent_lab.tools import ToolError
from tests.support import ScriptedModel,tool,final


class ToolResourcesNativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='agentlab-read-native-',dir='/private/tmp');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir()
        self.resources=self.base/'resources';self.resources.mkdir()
        self.file=self.resources/'value';self.file.write_text('owned resource')

    def test_actual_literal_and_directory_read_without_adjacent_private_write_or_handle_mutation(self):
        private=self.base/'private';private.write_text('private sentinel')
        for selected in ([str(self.file)],[str(self.resources)]):
            tools=CodingTools(self.root,tool_read_paths=selected,tool_sysctl_read=['hw.pagesize_compat'])
            code='''import errno,fcntl,json,os,sys
p,private=sys.argv[1:];result={'read':open(p).read()}
for name,operation in [('private',lambda:open(private).read()),('write',lambda:open(p,'w')),('link',lambda:os.link(p,'alias'))]:
 try:operation();result[name]=0
 except OSError as e:result[name]=e.errno
with open(p,'rb') as source:
 for command in (80,110):
  try:fcntl.fcntl(source.fileno(),command,0);result[str(command)]=0
  except OSError as e:result[str(command)]=e.errno
print(json.dumps(result))
'''
            result=tools.execute(tools.prepare('run_command',{'argv':['python','-I','-c',code,str(self.file),str(private)],'cwd':'.'}),6)
            self.assertEqual(result['exit_code'],0,result)
            self.assertEqual(json.loads(result['stdout']),{'read':'owned resource','private':1,'write':1,'link':1,'80':1,'110':1})
            self.assertEqual(self.file.read_text(),'owned resource')

    def test_resources_do_not_register_an_executable_or_rehash_during_profile_generation(self):
        program=self.resources/'program';program.write_text('#!/bin/sh\necho unexpected\n');program.chmod(0o755)
        tools=CodingTools(self.root,tool_read_paths=[str(self.resources)])
        with self.assertRaises(ToolError):tools.prepare('run_command',{'argv':[str(program)],'cwd':'.'})
        with patch('agent_lab.tool_resources.resource_policy',side_effect=AssertionError('profile rehash')),patch('agent_lab.command_runtime.executable_descriptor',side_effect=AssertionError('profile executable hash')):
            profile=sandbox._command_profile(self.root,self.base/'scratch',tool_read_paths=[str(self.resources)])
        self.assertIn('(subpath '+json.dumps(str(self.resources))+')',profile)
        self.assertNotIn('(allow process-exec (literal '+json.dumps(str(program)),profile)

    def test_strict_v3_supervisor_binds_grant_before_any_subprocess(self):
        tools=CodingTools(self.root,tool_read_paths=[str(self.file)],tool_sysctl_read=['hw.pagesize_compat'])
        request=sandbox._runtime_request('run_tests',self.root,4,(),None,None,'nofork',None,
                tools.command_runtime_policy,start_dir='.',tool_read_paths=[str(self.file)],tool_sysctl_read=['hw.pagesize_compat'])
        self.assertEqual(request['version'],3)
        with patch('agent_lab.sandbox.subprocess.Popen') as launch:
            for change in ({'command_runtime_policy':None},{'tool_sysctl_read':['hw.*']},{'tool_read_paths':str(self.file)},
                           {'version':2},{'allow_workspace_executables':1},{'unknown':True}):
                with self.subTest(change=change),self.assertRaises(ToolError):sandbox._runtime_supervisor({**request,**change})
            launch.assert_not_called()

    def test_actual_unittest_resource_only_runtime_uses_extended_supervisor(self):
        (self.root/'test_resource.py').write_text('import unittest\nclass Check(unittest.TestCase):\n def test_value(self):\n  with open('+repr(str(self.file))+') as source:self.assertEqual(source.read(),"owned resource")\n')
        tools=CodingTools(self.root,tool_read_paths=[str(self.file)])
        result=tools.execute(tools.prepare('run_tests',{}),6)
        self.assertEqual(result['exit_code'],0,result)

    def test_resource_or_sysctl_only_tests_do_not_gain_command_alias_exec(self):
        bootstrap='import os;os.execv("/bin/echo",["echo","unintended exec grant"])'
        for grants in ({'tool_read_paths':[str(self.file)]},{'tool_sysctl_read':['hw.pagesize_compat']}):
            tools=CodingTools(self.root,**grants)
            result=sandbox._run_tests(self.root,4,bootstrap=bootstrap,command_runtime_policy=tools.command_runtime_policy,**grants)
            self.assertNotEqual(result['exit_code'],0,result)
            self.assertNotIn('unintended exec grant',result['stdout'])

    def test_engine_actual_sandbox_multifile_compile_link_fail_patch_rebuild_and_verify(self):
        cc=Path('/Library/Developer/CommandLineTools/usr/bin/clang')
        ld=cc.with_name('ld');sdk=Path('/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk').resolve()
        includes=Path('/Library/Developer/CommandLineTools/usr/lib/clang/15.0.0')
        paths=[str(sdk),str(includes)]+[str(cc.parent.parent/'lib'/name) for name in ('libtapi.dylib','libLTO.dylib','libswiftDemangle.dylib')]
        self.assertTrue(all(Path(p).exists() for p in [str(cc),str(ld),*paths]),'recorded local toolchain required')
        (self.root/'calc.c').write_text('int add(int a,int b){return a-b;}\n')
        main='#include <stdio.h>\nint add(int,int);\nint main(void){int value=add(2,3);printf("result=%d\\n",value);return value==5?0:1;}\n'
        (self.root/'check.c').write_text(main)
        def compile(name):return ['cc','-cc1','-triple','x86_64-apple-macosx14.0.0','-fgnuc-version=4.2.1','-emit-obj','-x','c','-isysroot',str(sdk),'-internal-isystem',str(includes/'include'),'-internal-isystem',str(sdk/'usr/include'),name+'.c','-o',name+'.o']
        link=['linker','-arch','x86_64','-platform_version','macos','14.0.0','14.4','-syslibroot',str(sdk),'-lSystem','calc.o','check.o','-o','program']
        def command(argv,id):return tool('run_command',{'argv':argv,'cwd':'.'},id)
        model=ScriptedModel([tool('list_files',{},'list'),tool('read_file',{'path':'calc.c'},'read'),
             command(compile('calc'),'compile-calc'),command(compile('check'),'compile-check'),command(link,'link'),command(['./program'],'fail'),
             tool('apply_patch',{'path':'calc.c','old':'return a-b;','new':'return a+b;'},'patch'),
             command(compile('calc'),'recompile'),command(link,'relink'),command(['./program'],'verify'),final('protocol complete')])
        tools=CodingTools(self.root,tool_executables={'cc':str(cc),'linker':str(ld)},tool_read_paths=paths,
                          tool_sysctl_read=['hw.pagesize_compat'],command_backend='confined-fork',allow_workspace_executables=True,read_only_paths=['check.c'])
        approvals=0;db=self.base/'state.sqlite'
        with Store(db) as store:
            engine=Engine(store,tools,model);task=engine.create('Repair and build owned multifile C project; check.c immutable',Limits(max_steps=30,max_seconds=600,tool_timeout=20))
            for _ in range(15):
                state=engine.run(task)
                if state['phase']!='awaiting_approval':break
                request=engine.approval_request(task);engine.decide_approval(task,request['request_id'],request['digest'],True);approvals+=1
            self.assertEqual(state['phase'],'completed',state.get('reason'))
            results=[json.loads(m['content']) for m in state['messages'] if m['role']=='tool']
            commands=[r for r in results if 'exit_code' in r]
            self.assertEqual([r['exit_code'] for r in commands],[0,0,0,1,0,0,0],commands)
            self.assertEqual(commands[3]['stdout'],'result=-1\n');self.assertEqual(commands[-1]['stdout'],'result=5\n')
            self.assertEqual(approvals,7)
            self.assertEqual((self.root/'check.c').read_text(),main)
            self.assertEqual(native_jobs.inspect_workspace(self.root,db)['proof_kind'],'all_native_joined')
            self.assertIn('read_resources',store.load(task)['command_runtime_policy'])
            print(json.dumps({'experiment':'sandbox_multifile_c_build_repair','model_kind':model.identity,'real_llm_success_rate':None,
                'command_exit_codes':[r['exit_code'] for r in commands],'exact_approvals':approvals,'final_stdout':commands[-1]['stdout'],
                'immutable_check_sha256':hashlib.sha256(main.encode()).hexdigest(),'binary_sha256':hashlib.sha256((self.root/'program').read_bytes()).hexdigest(),
                'native_proof':'all_native_joined','host_precompiled_fixture':False}))


if __name__=='__main__':unittest.main()
