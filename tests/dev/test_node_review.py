"""Independent optional-runtime boundaries; synthetic state, no provider calls."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine
from agent_lab.registry import MCPToolRegistry
from agent_lab.store import Store
from agent_lab.tools import ToolError
from tests.support import ScriptedModel, tool


class NodeReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base/'work'; self.root.mkdir()
        self.node = self.base/'synthetic-node'; self.node.write_bytes(b'synthetic runtime identity')
        self.node.chmod(0o755)

    def test_same_content_and_mtime_replacement_invalidates_python_approval(self):
        cached = CodingTools(self.root,node_executable=self.node)
        model = ScriptedModel([tool('run_command',{'argv':['python','-c','pass'],'cwd':'.'})])
        with Store(self.base/'state.sqlite') as store:
            engine = Engine(store,cached,model)
            task = engine.create('Run the approved Python command')
            self.assertEqual(engine.run(task)['phase'],'awaiting_approval')
            approval = engine.approval_request(task)
            before = store.load(task)
            stamp = self.node.stat()
            replacement = self.base/'replacement'
            replacement.write_bytes(self.node.read_bytes()); replacement.chmod(0o755)
            os.utime(replacement,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
            replacement.replace(self.node)
            self.assertNotEqual(self.node.stat().st_ino,stamp.st_ino)
            self.assertEqual(self.node.stat().st_mtime_ns,stamp.st_mtime_ns)
            for tools in (cached,CodingTools(self.root,node_executable=self.node)):
                changed = Engine(store,tools,model)
                actions = [lambda:changed.approval_request(task),
                           lambda:changed.decide_approval(task,approval['request_id'],approval['digest'],True),
                           lambda:changed.run(task)]
                for action in actions:
                    with self.subTest(cached=tools is cached), self.assertRaisesRegex(ValueError,'^resume_configuration_mismatch$'):
                        action()
            self.assertEqual(store.load(task),before)
            self.assertEqual(len(model.seen),1)

    def test_mcp_wrapper_forwards_runtime_validation_without_putting_path_in_prompt(self):
        calls = []
        class Client:
            protocol_version = '2024-11-05'
            def __init__(self,*args,**kwargs): pass
            def start(self): calls.append('start')
            def list_tools(self): return []
            def supports(self,feature): return feature=='tools'
            def close(self): calls.append('close')
        config = self.base/'synthetic-mcp.json'
        config.write_text(json.dumps({'servers':[{'name':'synthetic','command':[str(Path(sys.executable).resolve())],
                                                  'cwd':str(self.base)}]}))
        base = CodingTools(self.root,node_executable=self.node)
        with patch('agent_lab.registry.MCPClient',Client), MCPToolRegistry(base,config) as registry:
            self.assertEqual(CodingTools.schemas(),CodingTools(self.root).schemas())
            self.assertEqual(registry.schemas(),base.schemas())
            enabled_schema = next(item['function'] for item in registry.schemas()
                                  if item['function']['name']=='run_command')
            self.assertIn('node:test',enabled_schema['description'])
            self.assertNotIn(str(self.node),json.dumps(registry.schemas()))
            self.assertEqual(registry.command_runtime_policy,base.command_runtime_policy)
            self.assertEqual(registry.validate_command_runtime(),base.command_runtime_policy)
            intent = registry.prepare('run_command',{'argv':['python','-c','pass'],'cwd':'.'})
            self.assertEqual(intent['command_runtime_policy'],base.command_runtime_policy)
            with Store(self.base/'state.sqlite') as store:
                engine = Engine(store,registry,ScriptedModel([]))
                state = store.load(engine.create('Inspect the synthetic project'))
                self.assertEqual(state['command_runtime_policy'],base.command_runtime_policy)
                self.assertIn('node:test',state['messages'][0]['content'])
                self.assertNotIn(str(self.node),state['messages'][0]['content'])
            self.node.write_bytes(b'changed runtime')
            with self.assertRaisesRegex(ToolError,'^command_runtime_changed$'):
                registry.validate_command_runtime()
        self.assertEqual(calls,['start','close'])

    def test_python_exec_node_requires_the_optional_grant_in_actual_backend(self):
        selected = shutil.which('node')  # Explicit test-host selection, not runtime discovery.
        if not selected or not Path('/usr/bin/sandbox-exec').is_file():
            self.skipTest('explicit host Node/macOS backend unavailable; no bare fallback')
        probe = subprocess.run(['/usr/bin/sandbox-exec','-p','(version 1)(allow default)','/usr/bin/true'],
                               capture_output=True,timeout=2)
        if probe.returncode:
            self.skipTest('outer host denies Seatbelt; no bare fallback')
        node = str(Path(selected).resolve())
        javascript = "console.log(JSON.stringify({pid:process.pid,type:require('node:os').type()}))"
        python = "import os,sys; print(os.getpid(),flush=True); os.execv(sys.argv[1],[sys.argv[1],'-e',sys.argv[2]])"
        args = {'argv':['python','-c',python,node,javascript],'cwd':'.'}
        default = CodingTools(self.root)
        denied = default.execute(default.prepare('run_command',args),8)
        self.assertNotEqual(denied['exit_code'],0)
        self.assertNotIn('"type"',denied['stdout'])
        enabled = CodingTools(self.root,node_executable=node,read_only_paths=['.'])
        intent = enabled.prepare('run_command',args)
        self.assertEqual(intent['command_runtime_policy'],enabled.command_runtime_policy)
        result = enabled.execute(intent,8)
        self.assertEqual(result['exit_code'],0,result)
        initial,observed = result['stdout'].splitlines()
        self.assertEqual(int(initial),json.loads(observed)['pid'])
        self.assertEqual(json.loads(observed)['type'],'Darwin')
