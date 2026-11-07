"""Bounded managed commands: immutable intent and real synthetic OS probes."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_lab import sandbox
from agent_lab.tools import ToolError


class CommandPreparationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('agent_lab.commands'),
                             'managed command preparation is not implemented')
        from agent_lab import commands
        self.commands = commands
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'work'
        self.root.mkdir()

    def test_exact_argv_and_workspace_bind_a_stable_json_intent(self):
        argv = ['python', '-c', 'print("x  y")']
        intent = self.commands.prepare_command(self.root, argv)
        self.assertEqual(intent['requested_argv'], argv)
        self.assertEqual(intent['argv'], [str(Path(sys.executable).resolve()), *argv[1:]])
        self.assertEqual(intent['policy'], 'unsafe')
        self.assertEqual(intent['workspace'], str(self.root))
        self.assertEqual(intent['cwd'], str(self.root))
        self.assertEqual(intent['workspace_identity']['inode'], self.root.stat().st_ino)
        self.assertEqual(intent, self.commands.prepare_command(self.root, argv))
        self.assertEqual(intent, self.commands.validate_command(self.root, json.loads(json.dumps(intent))))
        self.assertNotEqual(intent['command_digest'], self.commands.prepare_command(self.root, ['python', '-c', 'print("x y")'])['command_digest'])

    def test_rejects_invalid_commands_paths_and_ambient_executables(self):
        for argv in [[], 'python', ['python', None], ['python', 'a\0b'], ['unknown'], ['/usr/bin/env'], ['python'] * 129, ['python', 'x' * 32768]]:
            with self.subTest(argv=str(argv)[:80]), self.assertRaises(ToolError):
                self.commands.prepare_command(self.root, argv)
        (self.root / 'nested').mkdir()
        (self.root / 'linked').symlink_to(self.root / 'nested', target_is_directory=True)
        for cwd in ['..', '/tmp', './nested', 'nested/..', '.hidden', 'linked', 'missing']:
            with self.subTest(cwd=cwd), self.assertRaises(ToolError):
                self.commands.prepare_command(self.root, ['true'], cwd)
        intent = self.commands.prepare_command(self.root, ['true'], 'nested')
        self.assertEqual(intent['cwd'], str(self.root / 'nested'))

    def test_tampering_and_replaced_workspace_or_cwd_are_rejected(self):
        (self.root / 'nested').mkdir()
        intent = self.commands.prepare_command(self.root, ['echo', 'approved'], 'nested')
        for field, value in [('argv', ['/bin/echo', 'changed']), ('policy', 'read'), ('command_digest', '0' * 64), ('extra', True)]:
            changed = copy.deepcopy(intent)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ToolError):
                self.commands.validate_command(self.root, changed)
        (self.root / 'nested').rename(self.root / 'old')
        (self.root / 'nested').mkdir()
        with self.assertRaises(ToolError):
            self.commands.validate_command(self.root, intent)
        original = self.commands.prepare_command(self.root, ['true'])
        self.root.rename(self.root.with_name('old-work'))
        self.root.mkdir()
        with self.assertRaises(ToolError):
            self.commands.validate_command(self.root, original)

    def test_environment_policy_ignores_ambient_credentials_and_cannot_be_changed(self):
        with patch.dict(os.environ, {'SYNTHETIC_COMMAND_KEY': 'do-not-inherit', 'PATH': '/untrusted'}):
            intent = self.commands.prepare_command(self.root, ['true'])
        policy = intent['environment_policy']
        self.assertNotIn('SYNTHETIC_COMMAND_KEY', json.dumps(policy))
        self.assertEqual(policy['scratch_variables'], ['HOME', 'TMPDIR'])
        changed = copy.deepcopy(intent)
        changed['environment_policy']['fixed']['PATH'] = '/untrusted'
        with self.assertRaises(ToolError):
            self.commands.validate_command(self.root, changed)

    def test_visible_hard_link_alias_cannot_bypass_private_file_scope(self):
        outside = self.root.parent / 'private'
        outside.write_text('synthetic-private')
        os.link(outside, self.root / 'alias')
        with self.assertRaises(ToolError):
            self.commands.prepare_command(self.root, ['cat', 'alias'])

    def test_missing_backend_never_executes_project_code(self):
        intent = self.commands.prepare_command(self.root, ['python', '-c', 'open("effect", "w").write("bad")'])
        with patch.object(sandbox, 'BACKEND_PATH', '/missing/sandbox-exec'):
            with self.assertRaises(ToolError) as error:
                sandbox.run_command(self.root, intent, 2)
        self.assertEqual(error.exception.code, 'sandbox_unavailable')
        self.assertFalse((self.root / 'effect').exists())


class ManagedCommandSandboxTests(unittest.TestCase):
    setUp = CommandPreparationTests.setUp
    def command(self, argv, timeout=5, cwd='.'):
        self.host_capability()
        return sandbox.run_command(self.root, self.commands.prepare_command(self.root, argv, cwd), timeout)

    def host_capability(self):
        if not Path('/usr/bin/sandbox-exec').is_file():
            self.skipTest('Seatbelt unavailable; no unsandboxed fallback')
        probe = subprocess.run(['/usr/bin/sandbox-exec', '-p', '(version 1)(allow default)', '/usr/bin/true'], capture_output=True, timeout=2)
        if probe.returncode:
            self.skipTest('host denies Seatbelt; real command gate remains unverified')

    def test_real_command_can_import_project_and_report_exit(self):
        (self.root / 'calc.py').write_text('answer = 42\n')
        (self.root / 'check.py').write_text('import calc; print(calc.answer)\n')
        result = self.command(['python', 'check.py'])
        self.assertEqual(result['exit_code'], 0, result['stderr'])
        self.assertEqual(result['stdout'], '42\n')
        self.assertTrue(result['ok'])
        self.assertEqual(self.command(['false'])['exit_code'], 1)
        shell = self.command(['sh', '-c', 'printf builtin; exec /bin/echo final'])
        self.assertEqual(shell['exit_code'], 0, shell['stderr'])
        self.assertEqual(shell['stdout'], 'builtinfinal\n')

    def test_private_files_network_fork_and_directory_relocation_are_denied(self):
        outside = self.root.parent / 'outside'
        outside.write_text('synthetic-secret')
        (self.root / 'nested').mkdir()
        (self.root / 'nested' / '.env').write_text('synthetic-secret')
        (self.root / '.agent-lab.generation').write_text('generation')
        (self.root / '.git').mkdir()
        (self.root / '.git' / 'HEAD').write_text('main')
        (self.root / 'escape').symlink_to(outside)
        code = '''import pathlib, socket, os, subprocess, sys
def denied(f):
 try: f()
 except OSError: return
 raise AssertionError("boundary allowed")
for p in %r:
 denied(lambda p=p: pathlib.Path(p).read_text())
 denied(lambda p=p: pathlib.Path(p).write_text("bad"))
denied(lambda: pathlib.Path("nested").rename("relocated"))
denied(lambda: socket.socket().connect(("127.0.0.1", 9)))
denied(lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", ("127.0.0.1", 9)))
denied(os.fork)
denied(lambda: subprocess.Popen([sys.executable, "-c", "pass"]))
assert "SYNTHETIC_COMMAND_KEY" not in os.environ
pathlib.Path("visible").write_text("allowed")
''' % [str(outside), 'escape', 'nested/.env', '.agent-lab.generation', '.git/HEAD']
        with patch.dict(os.environ, {'SYNTHETIC_COMMAND_KEY': 'do-not-inherit'}):
            result = self.command(['python', '-c', code])
        self.assertEqual(result['exit_code'], 0, result['stderr'])
        self.assertEqual(outside.read_text(), 'synthetic-secret')
        self.assertEqual((self.root / 'visible').read_text(), 'allowed')

    def test_timeout_stops_effects_and_output_is_bounded(self):
        flood = self.command(['python', '-c', 'import os\nfor i in range(128): os.write(1,b"\\xff"*8192); os.write(2,b"x"*8192)'])
        self.assertEqual(flood['exit_code'], 0, flood['stderr'])
        for stream in ['stdout', 'stderr']:
            self.assertLessEqual(len(flood[stream].encode()), 65536)
            self.assertTrue(flood[stream + '_truncated'])
        with self.assertRaises(ToolError) as error:
            self.command(['python', '-c', 'import pathlib,time; pathlib.Path("started").write_text("yes"); time.sleep(3); pathlib.Path("late").write_text("bad")'], timeout=.6)
        self.assertEqual(error.exception.code, 'tool_timeout')
        self.assertTrue((self.root / 'started').exists())
        time.sleep(.8)
        self.assertFalse((self.root / 'late').exists())

    def test_trusted_supervisor_keeps_lease_after_worker_death(self):
        from agent_lab.process_guard import rotate_generation, ExecutionBusy
        self.host_capability()
        generation = rotate_generation(self.root)
        source_root = str(Path(__file__).resolve().parents[2])
        code = '''import sys
sys.path.insert(0, %r)
from agent_lab.process_guard import worker_guard
from agent_lab.commands import prepare_command
from agent_lab.sandbox import run_command
root=%r
intent=prepare_command(root,["python","-c",'import pathlib,time; pathlib.Path("started").write_text("yes"); time.sleep(1); pathlib.Path("finished").write_text("yes")'])
with worker_guard(root,%r): run_command(root,intent,3)
''' % (source_root, str(self.root), generation)
        worker = subprocess.Popen([sys.executable, '-I', '-c', code], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 4
            while not (self.root / 'started').exists() and time.monotonic() < deadline:
                if worker.poll() is not None:
                    self.fail(worker.stderr.read().decode())
                time.sleep(.02)
            self.assertTrue((self.root / 'started').exists())
            worker.kill(); worker.wait(timeout=2)
            with self.assertRaises(ExecutionBusy): rotate_generation(self.root)
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                try:
                    rotate_generation(self.root)
                    break
                except ExecutionBusy: time.sleep(.02)
            else: self.fail('supervisor retained lease beyond bound')
            self.assertTrue((self.root / 'finished').exists())
        finally:
            if worker.poll() is None: worker.kill(); worker.wait(timeout=2)
            worker.stderr.close()


if __name__ == '__main__':
    unittest.main()
