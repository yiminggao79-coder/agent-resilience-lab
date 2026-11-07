"""Host venv state/approval/native contracts; no third-party deps or model API."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import venv
from unittest.mock import patch

from agent_lab import __main__ as cli,native_jobs
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.store import Store
from agent_lab.tools import ToolError
from tests.support import ScriptedModel,tool,final


class PythonEnvironmentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name).resolve();self.root=self.base/'work';self.root.mkdir()
        self.env=self.base/'env';venv.EnvBuilder(with_pip=False,symlinks=True).create(self.env)
        self.packages=self.env/'lib'/('python%d.%d'%sys.version_info[:2])/'site-packages'
        (self.packages/'public_dependency.py').write_text('VALUE=42\n')
        self.db=self.base/'state.sqlite';self.store=Store(self.db);self.addCleanup(self.store.close)
    def tools(self):return CodingTools(self.root,python_environment=self.env)
    def native(self):
        if not Path('/usr/bin/sandbox-exec').is_file():self.skipTest('backend unavailable; no bare fallback')
        p=subprocess.run(['/usr/bin/sandbox-exec','-p','(version 1)(allow default)','/usr/bin/true'],capture_output=True,timeout=2)
        if p.returncode:self.skipTest('host denies sandbox-exec; no bare fallback')
    def test_prepare_and_state_pin_selected_environment_and_changed_bytes_reject(self):
        tools=self.tools();intent=tools._prepare('run_command',{'argv':['python','-m','public_dependency'],'cwd':'.'})
        self.assertEqual(intent['argv'][0],str(self.env/'bin'/'python'))
        self.assertEqual(intent['environment_policy']['fixed']['VIRTUAL_ENV'],str(self.env))
        self.assertIn('python_environment_policy',tools._prepare('run_tests',{}))
        engine=Engine(self.store,tools,ScriptedModel([]));tid=engine.create('Bound environment')
        self.assertEqual(self.store.load(tid)['command_runtime_policy'],tools.command_runtime_policy)
        with self.assertRaises(ValueError):Engine(self.store,CodingTools(self.root),ScriptedModel([])).run(tid)
        (self.packages/'public_dependency.py').write_text('VALUE=43\n')
        with self.assertRaises(ToolError):tools.validate_command_runtime()
        with self.assertRaises(ValueError):Engine(self.store,self.tools(),ScriptedModel([])).run(tid)
    def test_cli_opt_in_reaches_state_without_model_network(self):
        model=ScriptedModel([final()]);model.api_key_env='SYNTHETIC_KEY';out=io.StringIO()
        with patch.object(cli,'OpenAIModel',return_value=model),patch.dict(os.environ,{'SYNTHETIC_KEY':'fixture'}), \
             contextlib.redirect_stdout(out):
            code=cli.main(['--db',str(self.db),'run','Synthetic CLI','--workspace',str(self.root),
                '--python-environment',str(self.env),'--model','synthetic','--base-url','https://example.invalid/v1',
                '--api-key-env','SYNTHETIC_KEY','--allow-api'])
        self.assertEqual(code,0)
        row=self.store.db.execute('SELECT state FROM tasks').fetchone()
        self.assertEqual(json.loads(row[0])['command_runtime_policy']['python_environment']['path'],str(self.env))
    def test_actual_selected_dependency_unittest_and_quiescent_receipt(self):
        self.native()
        (self.root/'test_public.py').write_text('''import unittest,public_dependency,sys,os,socket
from pathlib import Path
class T(unittest.TestCase):
 def test_value(self):
  self.assertEqual(public_dependency.VALUE,42)
  self.assertEqual(sys.prefix,%r)
  self.assertNotIn('DEEPSEEK_API_KEY',os.environ)
  with self.assertRaises(OSError): Path(%r).write_text('bad')
  with self.assertRaises(OSError): os.fork()
  with self.assertRaises(OSError): socket.socket().connect(('127.0.0.1',9))
'''%(str(self.env),str(self.packages/'public_dependency.py')))
        model=ScriptedModel([tool('run_tests',{}),final()]);engine=Engine(self.store,self.tools(),model)
        tid=engine.create('Selected dependency import',Limits(tool_timeout=10))
        state=engine.run(tid)
        self.assertEqual(state['phase'],'completed',state.get('reason'))
        result=json.loads(model.seen[-1][-1]['content']);self.assertEqual(result.get('exit_code'),0,result)
        self.assertEqual(native_jobs.inspect_workspace(self.root,self.db)['proof_kind'],'all_native_joined')
    def test_actual_approved_command_retains_venv_semantics(self):
        self.native();code='import sys,public_dependency; print(public_dependency.VALUE); print(sys.prefix)'
        model=ScriptedModel([tool('run_command',{'argv':['python','-c',code],'cwd':'.'}),final()])
        engine=Engine(self.store,self.tools(),model);tid=engine.create('Explicit interpreter',Limits(tool_timeout=10))
        self.assertEqual(engine.run(tid)['phase'],'awaiting_approval')
        request=engine.approval_request(tid)
        engine.decide_approval(tid,request['request_id'],request['digest'],True)
        self.assertEqual(engine.run(tid)['phase'],'completed')
        result=json.loads(model.seen[-1][-1]['content'])
        self.assertEqual(result.get('exit_code'),0,result)
        self.assertEqual(result['stdout'].splitlines(),['42',str(self.env)])
        self.assertEqual(native_jobs.inspect_workspace(self.root,self.db)['state'],'quiescent')


if __name__=='__main__':unittest.main()
