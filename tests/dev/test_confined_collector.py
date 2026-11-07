"""Owned native fixtures, not model quality or arbitrary project execution."""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent_lab import sandbox
from agent_lab.native_job_runtime import JobTracker, _CURRENT
from agent_lab.process_group import GroupObservationError, OwnedGroup
from agent_lab.tool_control import inherited_owner, OWNER_ENV, NativeCancelled
from agent_lab.tools import ToolError
try:
    from agent_lab.confined_collector import collect
except ImportError:
    collect = None


class ConfinedCollectorTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(collect, 'candidate collector not implemented')
        self.assertEqual(platform.system(), 'Darwin', 'native fixture requires macOS')
        self.tmp = tempfile.TemporaryDirectory(prefix='agentlab-collector-', dir='/private/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name); self.root = self.base/'work'; self.root.mkdir()
        self.scratch = self.base/'scratch'; self.scratch.mkdir()
        self.native = Path(sys.base_prefix)/'Resources/Python.app/Contents/MacOS/Python'
        self.profile = sandbox._command_profile(self.root, self.scratch)
        self.profile += '\n(allow process-fork)\n(deny syscall-unix (syscall-number 66 82 147 244))'
        self.env = {'PATH': os.defpath, 'LANG': 'C.UTF-8', 'HOME': str(self.scratch),
                    'TMPDIR': str(self.scratch), 'PYTHONDONTWRITEBYTECODE': '1'}
        self.tracker = JobTracker({}, 'synthetic-owned-job', 'supervisor')
        token = _CURRENT.set(self.tracker); self.addCleanup(_CURRENT.reset, token)

    def run_body(self, body, **kwargs):
        return collect([sandbox.BACKEND_PATH, '-p', self.profile, str(self.native), '-I', '-c', body],
                       self.root, self.env, kwargs.pop('timeout', 3), **kwargs)

    def joined(self):
        self.assertFalse(self.tracker.native_pending)
        self.assertEqual(self.tracker.native_processes, set())

    def stable(self, pattern='effect*'):
        before = {p.name: p.read_bytes() for p in self.root.glob(pattern)}
        self.assertTrue(before, 'fixture did not produce an effect')
        time.sleep(.12)
        self.assertEqual({p.name: p.read_bytes() for p in self.root.glob(pattern)}, before)

    def test_native_fork_exec_pipeline_and_exit_status_without_poll(self):
        original = subprocess.Popen
        def start(*args, **kwargs):
            proc = original(*args, **kwargs)
            proc.poll = lambda: self.fail('collector polled/reaped the owned leader early')
            return proc
        body = '''import subprocess,sys,json
a=subprocess.run([sys.executable,'-I','-c','print("child")'],capture_output=True)
b=subprocess.run(['/bin/sh','-c','echo pipeline | cat'],capture_output=True)
print(json.dumps([a.returncode,a.stdout.decode(),b.returncode,b.stdout.decode()]))
raise SystemExit(7)
'''
        with patch('agent_lab.confined_collector.subprocess.Popen', side_effect=start):
            result = self.run_body(body)
        self.assertFalse(result['ok']); self.assertEqual(result['exit_code'], 7)
        self.assertEqual(json.loads(result['stdout']), [0, 'child\n', 0, 'pipeline\n'])
        self.joined()

    def test_parent_exit_and_closed_stdio_double_fork_are_drained(self):
        body = '''import os,time
from pathlib import Path
if os.fork():
 while not Path('effect-ready').exists():time.sleep(.002)
 os._exit(0)
for fd in (0,1,2):os.close(fd)
if os.fork():os._exit(0)
Path('effect-ready').write_text('ready')
until=time.monotonic()+1.5
while time.monotonic()<until:
 Path('effect-counter').write_text(str(time.monotonic_ns()));time.sleep(.005)
'''
        result = self.run_body(body)
        self.assertEqual(result['exit_code'], 0); self.stable(); self.joined()

    def test_timeout_cleans_live_writer_before_error(self):
        body = '''import time
from pathlib import Path
until=time.monotonic()+1.5
while time.monotonic()<until:
 Path('effect-counter').write_text(str(time.monotonic_ns()));time.sleep(.005)
'''
        with self.assertRaisesRegex(ToolError, '^tool_timeout$'):
            self.run_body(body, timeout=.25)
        self.stable(); self.joined()

    def test_real_owner_eof_cancels_and_does_not_reach_guest(self):
        reader, writer = os.pipe()
        timer = threading.Timer(.3, os.close, (writer,))
        timer.start(); self.addCleanup(timer.join)
        with patch.dict(os.environ, {OWNER_ENV: str(reader)}), inherited_owner():
            with self.assertRaisesRegex(NativeCancelled, '^tool_cancelled$'):
                self.run_body('''import os,time
from pathlib import Path
assert 'AGENT_LAB_TOOL_OWNER_FD' not in os.environ
until=time.monotonic()+1.5
while time.monotonic()<until:
 Path('effect-counter').write_text(str(time.monotonic_ns()));time.sleep(.005)
''')
        self.stable(); self.joined()

    def test_two_flooded_streams_are_bounded_even_for_invalid_utf8(self):
        result = self.run_body("import os;os.write(1,b'\\xff'*1048576);os.write(2,b'e'*1048576)", output_limit=128)
        self.assertTrue(result['ok'])
        for name in ('stdout', 'stderr'):
            self.assertTrue(result[name+'_truncated'])
            self.assertLessEqual(len(result[name].encode('utf-8')), 128)
            self.assertTrue(result[name])
        self.joined()

    def test_finite_competing_births_do_not_survive_parent(self):
        result = self.run_body('''import os,time
from pathlib import Path
for worker in range(2):
 if os.fork()==0:
  until=time.monotonic()+1.5
  for index in range(3):
   if os.fork()==0:
    while time.monotonic()<until:
     Path('effect-%d-%d'%(worker,index)).write_text(str(time.monotonic_ns()));time.sleep(.005)
    os._exit(0)
   time.sleep(.006)
  os._exit(0)
while not any(Path('.').glob('effect-*')):time.sleep(.002)
os._exit(0)
''')
        self.assertTrue(result['ok']); self.stable(); self.joined()

    def test_observation_failure_never_emits_join_or_cancel_success(self):
        class BrokenGroup(OwnedGroup):
            def anchor_only_zombie(self):
                raise GroupObservationError()
        with patch('agent_lab.confined_collector.OwnedGroup', BrokenGroup):
            with self.assertRaisesRegex(ToolError, '^native_group_unconfirmed$'):
                self.run_body('print("finite")', cleanup_timeout=.06)
        self.assertFalse(self.tracker.native_pending)
        self.assertEqual(len(self.tracker.native_processes), 1, 'failed observation must preserve quarantine')

    def test_invalid_limits_do_not_launch_or_register(self):
        for options in ({'timeout': True}, {'timeout': float('inf')}, {'timeout': 0},
                        {'output_limit': True}, {'output_limit': 65537},
                        {'cleanup_timeout': 0}, {'cleanup_timeout': 6}):
            with self.subTest(options=options), patch('agent_lab.confined_collector.subprocess.Popen') as start:
                with self.assertRaisesRegex(ToolError, '^invalid_collector_limits$'):
                    self.run_body('raise SystemExit(99)', **options)
                start.assert_not_called(); self.joined()


if __name__ == '__main__':
    unittest.main()
