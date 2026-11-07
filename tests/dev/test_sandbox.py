"""Real synthetic-project sandbox probes; no user project code is executed."""
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from agent_lab.tools import ToolError
try:
    from agent_lab import sandbox
except ImportError:
    sandbox = None


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(sandbox, 'OS test sandbox is not implemented')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        (self.root / 'tests').mkdir(parents=True)

    def project(self, body):
        (self.root / 'tests' / 'test_synthetic.py').write_text(body)

    def run_project(self, timeout=5):
        # A broken production policy must fail, rather than masquerade as a host
        # limitation. This fixed true capability probe never runs project code.
        if Path('/usr/bin/sandbox-exec').is_file():
            probe = subprocess.run(['/usr/bin/sandbox-exec', '-p',
                                    '(version 1)(allow default)', '/usr/bin/true'],
                                   capture_output=True, timeout=2)
            if probe.returncode:
                self.skipTest('host denies sandbox-exec; no bare fallback')
        else:
            self.skipTest('sandbox-exec unavailable; no bare fallback')
        return sandbox.run_tests(self.root, timeout)

    def test_unavailable_backend_never_executes_project(self):
        self.project("open('executed', 'w').write('bad')\n")
        with patch.object(sandbox, 'BACKEND_PATH', '/nonexistent/sandbox-exec'):
            with self.assertRaises(ToolError) as error:
                sandbox.run_tests(self.root, 3)
        self.assertEqual(error.exception.code, 'sandbox_unavailable')
        self.assertFalse((self.root / 'executed').exists())

    def test_real_unittest_runs_and_reports_failure_then_success(self):
        (self.root / 'calc.py').write_text('def add(a, b): return a - b\n')
        self.project('import unittest, calc\nclass T(unittest.TestCase):\n def test_value(self): self.assertEqual(calc.add(1, 1), 2)\n')
        result = self.run_project()
        self.assertEqual(result['exit_code'], 1)
        self.assertFalse(result['ok'])
        self.assertIn('FAIL', result['stderr'])
        (self.root / 'calc.py').write_text('def add(a, b): return a + b\n')
        result = self.run_project()
        self.assertEqual(result['exit_code'], 0)
        self.assertTrue(result['ok'])

    def test_denies_outside_read_write_network_and_control_mutation(self):
        outside = Path(self.tmp.name) / 'outside-sentinel'
        outside.write_text('synthetic-secret')
        target = Path(self.tmp.name) / 'outside-created'
        control = self.root / '.agent-lab.generation'
        control.write_text('generation')
        (self.root / 'nested').mkdir()
        nested_control = self.root / 'nested' / '.agent-lab.execution.lock'
        nested_control.write_text('lock')
        (self.root / 'escape').symlink_to(outside)
        (self.root / '.git').mkdir()
        git_control = self.root / '.git' / 'HEAD'
        git_control.write_text('ref: main')
        self.project('import unittest, pathlib, socket, os, tempfile\n'
                     'class T(unittest.TestCase):\n'
                     ' def test_boundaries(self):\n'
                     '  for p in %r:\n'
                     '   with self.assertRaises(OSError): pathlib.Path(p).read_text()\n'
                     '  for p in %r:\n'
                     '   with self.assertRaises(OSError): pathlib.Path(p).write_text("bad")\n'
                     '  with self.assertRaises(OSError): socket.socket().connect(("127.0.0.1", 9))\n'
                     '  self.assertNotIn("SYNTHETIC_CREDENTIAL", os.environ)\n'
                     '  with tempfile.TemporaryFile() as f: f.write(b"allowed")\n'
                     '  pathlib.Path("visible-result").write_text("allowed")\n'
                     % ([str(outside), str(self.root / 'escape'), str(control), str(nested_control)], [str(target), str(control), str(nested_control), str(git_control)]))
        with patch.dict(os.environ, {'SYNTHETIC_CREDENTIAL': 'never-pass-this'}):
            result = self.run_project()
        self.assertEqual(result['exit_code'], 0, result['stderr'])
        self.assertFalse(target.exists())
        self.assertEqual(control.read_text(), 'generation')
        self.assertEqual(nested_control.read_text(), 'lock')
        self.assertEqual((self.root / 'visible-result').read_text(), 'allowed')

    def test_output_collection_is_bounded_for_both_streams(self):
        self.project('import os, unittest\n'
                     'class T(unittest.TestCase):\n'
                     ' def test_output(self):\n'
                     '  for i in range(256):\n'
                     '   os.write(1, b"\\xff" * 8192); os.write(2, b"y" * 8192)\n')
        result = self.run_project()
        self.assertEqual(result['exit_code'], 0)
        self.assertLessEqual(len(result['stdout'].encode()), 65536)
        self.assertLessEqual(len(result['stderr'].encode()), 65536)
        self.assertTrue(result['stdout_truncated'])
        self.assertTrue(result['stderr_truncated'])

    def test_invalid_utf8_expansion_marks_output_truncated(self):
        self.project('import os, unittest\n'
                     'class T(unittest.TestCase):\n'
                     ' def test_output(self): os.write(1, b"\\xff" * 30000)\n')
        result = self.run_project()
        self.assertEqual(result['exit_code'], 0)
        self.assertLessEqual(len(result['stdout'].encode()), 65536)
        self.assertTrue(result['stdout_truncated'])

    def test_project_cannot_fork_or_spawn_to_escape_timeout(self):
        self.project('import os, subprocess, sys, unittest\n'
                     'class T(unittest.TestCase):\n'
                     ' def test_denied(self):\n'
                     '  with self.assertRaises(OSError): os.fork()\n'
                     '  with self.assertRaises(OSError): subprocess.Popen([sys.executable, "-c", "pass"])\n')
        result = self.run_project()
        self.assertEqual(result['exit_code'], 0, result['stderr'])

    def test_timeout_kills_descendant_before_late_write(self):
        self.project('import os, time, unittest, subprocess, sys\n'
                     'class T(unittest.TestCase):\n'
                     ' def test_sleep(self):\n'
                     '  with open("started", "w") as f: f.write("yes")\n'
                     '  with self.assertRaises(OSError): os.fork()\n'
                     '  with self.assertRaises(OSError): subprocess.Popen([sys.executable, "-c", "pass"])\n'
                     '  time.sleep(5)\n')
        started = time.monotonic()
        try:
            result = self.run_project(timeout=.7)
        except ToolError as error:
            self.assertEqual(error.code, 'tool_timeout')
        else:
            self.fail('hung project returned: %r' % result)
        self.assertTrue((self.root / 'started').exists(), 'timeout happened before project execution')
        self.assertLess(time.monotonic() - started, 2)
        time.sleep(1)
        self.assertFalse((self.root / 'late').exists())
    def test_supervisor_retains_lease_after_worker_is_killed(self):
        from agent_lab.process_guard import rotate_generation, ExecutionBusy
        # Establish host capability before starting a synthetic worker.
        self.project('import unittest\n')
        self.run_project()
        generation = rotate_generation(self.root)
        self.project('import pathlib, time, unittest, os\n'
                     'class T(unittest.TestCase):\n'
                     ' def test_wait(self):\n'
                     '  pathlib.Path("started").write_text("yes")\n'
                     '  time.sleep(1)\n'
                     '  pathlib.Path("finished").write_text("yes")\n')
        source_root = str(Path(__file__).resolve().parents[2])
        code = ('import sys; sys.path.insert(0, %r); '
                'from agent_lab.process_guard import worker_guard; '
                'from agent_lab.sandbox import run_tests; '
                'from pathlib import Path; '
                'root=Path(%r); '
                '\nwith worker_guard(root, %r): run_tests(root, 3)' %
                (source_root, str(self.root), generation))
        worker = subprocess.Popen([sys.executable, '-I', '-c', code],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 4
            while not (self.root / 'started').exists() and time.monotonic() < deadline:
                if worker.poll() is not None:
                    self.fail('synthetic worker exited: ' + worker.stderr.read().decode())
                time.sleep(.02)
            self.assertTrue((self.root / 'started').exists())
            worker.kill()
            worker.wait(timeout=2)
            with self.assertRaises(ExecutionBusy):
                rotate_generation(self.root)
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                try:
                    rotate_generation(self.root)
                    break
                except ExecutionBusy:
                    time.sleep(.02)
            else:
                self.fail('supervisor did not release the execution lease')
            self.assertTrue((self.root / 'finished').exists())
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait()
            worker.stderr.close()


if __name__ == '__main__':
    unittest.main()
