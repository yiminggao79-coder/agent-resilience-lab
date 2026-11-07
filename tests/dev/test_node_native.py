"""Fresh synthetic JavaScript in the actual native backend; no model requests."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_lab import sandbox
from agent_lab.coding_tools import CodingTools
from agent_lab.commands import prepare_command
from agent_lab.tools import ToolError
from agent_lab.tool_control import ToolCancelled


class NodeNativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'work'; self.root.mkdir()
        node = shutil.which('node')
        if not node or not Path('/usr/bin/sandbox-exec').is_file():
            self.skipTest('explicit host Node or macOS backend unavailable')
        probe = subprocess.run(['/usr/bin/sandbox-exec', '-p', '(version 1)(allow default)', '/usr/bin/true'], capture_output=True, timeout=2)
        if probe.returncode: self.skipTest('outer host disallows Seatbelt; no bare fallback')
        self.node = str(Path(node).resolve())

    def command(self, argv, timeout=6, readonly=()):
        intent = prepare_command(self.root, argv, node_executable=self.node, read_only_paths=readonly)
        return sandbox.run_command(self.root, intent, timeout, node_executable=self.node, read_only_paths=readonly)

    def test_actual_js_import_write_and_two_file_same_process_test_runner(self):
        (self.root / 'calc.cjs').write_text('exports.twice=x=>x*2;\n')
        (self.root / 'script.cjs').write_text("const fs=require('node:fs');fs.writeFileSync('result.txt',String(require('./calc.cjs').twice(21)));console.log('script completed');")
        result = self.command(['node', 'script.cjs'])
        self.assertEqual(result['exit_code'], 0, result)
        self.assertEqual((self.root / 'result.txt').read_text(), '42')
        for index in (1, 2):
            (self.root / ('test%s.cjs' % index)).write_text("const test=require('node:test'),assert=require('node:assert/strict');test('double',()=>assert.equal(require('./calc.cjs').twice(%d),%d));" % (index, index * 2))
        result = self.command(['node', '--test', '--test-isolation=none', '--test-reporter=tap', 'test1.cjs', 'test2.cjs'])
        self.assertEqual(result['exit_code'], 0, result)
        self.assertIn('# tests 2', result['stdout'])
        self.assertIn('# fail 0', result['stdout'])

    def test_private_files_child_process_and_network_are_denied_with_clean_env(self):
        private = self.root.parent / 'private.txt'; private.write_text('synthetic-private')
        (self.root / '.hidden').write_text('synthetic-hidden')
        (self.root / 'escape').symlink_to(private)
        body = """const fs=require('node:fs'),assert=require('node:assert/strict');
for(const p of [process.argv[2],'.hidden','escape'])assert.throws(()=>fs.readFileSync(p));
const child=require('node:child_process').spawnSync(process.execPath,['-e',\"require('node:fs').writeFileSync('spawned','bad')\"]);
assert(child.error || child.status!==0);assert(!fs.existsSync('spawned'));
for(const name of ['DEEPSEEK_API_KEY','NODE_OPTIONS','NODE_PATH','SYNTHETIC_KEY'])assert.equal(process.env[name],undefined);
const socket=require('node:net').connect({host:'127.0.0.1',port:9});
socket.on('connect',()=>{throw Error('network allowed')});
socket.on('error',error=>{assert(['EPERM','EACCES'].includes(error.code));console.log('boundaries verified')});
"""
        (self.root / 'check.cjs').write_text(body)
        with patch.dict(os.environ, {'NODE_OPTIONS': '--not-a-valid-option', 'NODE_PATH': '/untrusted', 'SYNTHETIC_KEY': 'synthetic-only'}):
            result = self.command(['node', 'check.cjs', str(private)])
        self.assertEqual(result['exit_code'], 0, result)
        self.assertIn('boundaries verified', result['stdout'])
        self.assertEqual(private.read_text(), 'synthetic-private')

    def test_whole_root_readonly_and_external_scratch_survive_node_grant(self):
        (self.root / 'keep.txt').write_text('original')
        body = """const fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
assert.equal(fs.readFileSync('keep.txt','utf8'),'original');
for(const operation of [()=>fs.writeFileSync('keep.txt','bad'),()=>fs.unlinkSync('keep.txt'),()=>fs.renameSync('keep.txt','moved'),()=>fs.writeFileSync('created','bad')])assert.throws(operation);
assert(!process.env.HOME.startsWith(process.cwd()+'/'));
fs.writeFileSync(path.join(process.env.TMPDIR,'scratch.txt'),'allowed');
console.log('readonly verified');
"""
        result = self.command(['node', '-e', body], readonly=('.',))
        self.assertEqual(result['exit_code'], 0, result)
        self.assertEqual((self.root / 'keep.txt').read_text(), 'original')
        self.assertFalse((self.root / 'created').exists())

    def test_timeout_joins_native_before_late_effect_and_output_stays_bounded(self):
        # Deadline includes trusted supervisor/probe/startup. Actual immediate
        # Node launch took 1.79s on this host, so .7s did not reach project code.
        # Keep launch required, then wait beyond every possible late timer.
        body = "require('node:fs').writeFileSync('started','yes');setTimeout(()=>require('node:fs').writeFileSync('late','bad'),4500)"
        with self.assertRaisesRegex(ToolError, 'tool_timeout'):
            self.command(['node', '-e', body], timeout=3)
        self.assertTrue((self.root / 'started').exists())
        time.sleep(4.7)
        self.assertFalse((self.root / 'late').exists())
        result = self.command(['node', '-e', "const fs=require('node:fs');for(let i=0;i<64;i++){fs.writeSync(1,Buffer.alloc(8192,120));fs.writeSync(2,Buffer.alloc(8192,121));}"])
        self.assertEqual(result['exit_code'], 0, result)
        for name in ('stdout', 'stderr'):
            self.assertLessEqual(len(result[name].encode()), 65536)
            self.assertTrue(result[name + '_truncated'])

    def test_tools_worker_and_trusted_supervisor_execute_node_through_original_channel(self):
        tools = CodingTools(self.root, node_executable=self.node)
        intent = tools.prepare('run_command', {'argv': ['node', '-e', "console.log('supervised node')"], 'cwd': '.'})
        result = tools.execute(intent, 8)
        self.assertEqual(result['exit_code'], 0, result)
        self.assertEqual(result['stdout'], 'supervised node\n')

    def test_cancel_joins_node_before_return_and_preserves_partial_effect(self):
        tools = CodingTools(self.root, node_executable=self.node)
        body = "const fs=require('node:fs');fs.writeFileSync('started',String(process.pid));setTimeout(()=>fs.writeFileSync('late','bad'),3000)"
        intent = tools.prepare('run_command', {'argv': ['node', '-e', body], 'cwd': '.'})
        with tools.cancellation_scope(lambda: (self.root / 'started').exists()), self.assertRaises(ToolCancelled) as caught:
            tools.execute(intent, 8)
        self.assertTrue(caught.exception.local_quiescent)
        pid = int((self.root / 'started').read_text())
        with self.assertRaises(ProcessLookupError): os.kill(pid, 0)
        self.assertFalse((self.root / 'late').exists())


if __name__ == '__main__': unittest.main()
