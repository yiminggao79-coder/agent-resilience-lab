"""Real-process regressions for worker fencing, without external effects."""
import importlib
import importlib.util
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest


WORKER = r'''
from pathlib import Path
import sys
from agent_lab.process_guard import worker_guard, StaleGeneration
root = Path(sys.argv[1])
expected = sys.argv[2]
mode = sys.argv[3]
if mode == 'late':
    print('waiting', flush=True)
    sys.stdin.readline()
try:
    with worker_guard(root, expected):
        print('executing', flush=True)
        if mode == 'holding':
            sys.stdin.readline()
        (root / 'effect').write_text('one effect')
    print('done', flush=True)
except StaleGeneration:
    print('stale', flush=True)
'''

ORPHAN = r'''
from pathlib import Path
import sys, time
from agent_lab.process_guard import worker_guard
root = Path(sys.argv[1])
with worker_guard(root, sys.argv[2]):
    (root / 'ready').write_text('ready')
    deadline = time.monotonic() + 10
    while not (root / 'release').exists():
        if time.monotonic() > deadline:
            raise SystemExit(2)
        time.sleep(.01)
    (root / 'effect').write_text('one effect')
(root / 'done').write_text('done')
'''

SUPERVISOR = r'''
import subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', sys.argv[1], *sys.argv[2:]],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
print(child.pid, flush=True)
time.sleep(15)
'''

LEASE_RETAINER = r'''
from pathlib import Path
import os, sys, time
fd = int(sys.argv[1])
root = Path(sys.argv[2])
os.fstat(fd)
(root / 'ready').write_text('inherited lease')
deadline = time.monotonic() + 10
while not (root / 'release').exists():
    if time.monotonic() > deadline:
        raise SystemExit(2)
    time.sleep(.01)
os.close(fd)
(root / 'done').write_text('lease closed')
'''

INHERITING_HOLDER = r'''
import subprocess, sys, time
from agent_lab.process_guard import active_lease_fd, worker_guard
with worker_guard(sys.argv[2], sys.argv[3]):
    fd = active_lease_fd()
    child = subprocess.Popen([sys.executable, '-c', sys.argv[1], str(fd), sys.argv[2]],
                             pass_fds=(fd,), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(child.pid, flush=True)
    if len(sys.argv) < 5 or sys.argv[4] != 'exit':
        time.sleep(15)
'''


class ProcessGuardTests(unittest.TestCase):
    def setUp(self):
        # Missing module is an explicit feature assertion in the initial RED run.
        self.assertIsNotNone(importlib.util.find_spec('agent_lab.process_guard'),
                             'the process guard feature is not implemented')
        self.guard = importlib.import_module('agent_lab.process_guard')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'work'
        self.root.mkdir()

    def start(self, script, *args):
        process = subprocess.Popen([sys.executable, '-c', script, *map(str, args)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, process)
        return process

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)

    def line(self, process):
        ready, _, _ = select.select([process.stdout], [], [], 5)
        self.assertTrue(ready, 'worker handshake timed out')
        line = process.stdout.readline().strip()
        self.assertTrue(line, 'worker exited before handshake')
        return line

    def wait_file(self, name):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (self.root / name).exists():
                return
            time.sleep(.01)
        self.fail('worker did not produce ' + name)

    def test_generation_persists_privately_and_replaces_atomically(self):
        self.assertIsNone(self.guard.current_generation(self.root))
        first = self.guard.rotate_generation(self.root)
        generation_file = self.root / '.agent-lab.generation'
        self.assertEqual(self.guard.current_generation(self.root), first)
        self.assertIsInstance(first, str)
        self.assertTrue(first)
        old_fd = os.open(generation_file, os.O_RDONLY)
        self.addCleanup(os.close, old_fd)
        second = self.guard.rotate_generation(self.root)
        self.assertNotEqual(first, second)
        self.assertEqual(self.guard.current_generation(self.root), second)
        self.assertEqual(os.read(old_fd, 1024).decode().strip(), first)
        self.assertEqual(stat.S_IMODE(generation_file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.root / '.agent-lab.execution.lock').stat().st_mode), 0o600)

    def test_none_is_allowed_only_while_both_generations_are_absent(self):
        with self.guard.worker_guard(self.root, None):
            pass
        with self.assertRaises(self.guard.StaleGeneration), self.guard.worker_guard(self.root, 'old'):
            self.fail('stale worker reached effect')
        token = self.guard.rotate_generation(self.root)
        with self.assertRaises(self.guard.StaleGeneration), self.guard.worker_guard(self.root, None):
            self.fail('ungenerated worker reached effect')
        with self.guard.worker_guard(self.root, token):
            pass

    def test_private_generation_mode_does_not_depend_on_process_umask(self):
        previous = os.umask(0o777)
        try:
            token = self.guard.rotate_generation(self.root)
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE((self.root / '.agent-lab.generation').stat().st_mode), 0o600)
        self.assertEqual(self.guard.current_generation(self.root), token)

    def test_active_lease_descriptor_is_scoped_and_restored_after_failure(self):
        self.assertTrue(hasattr(self.guard, 'active_lease_fd'), 'trusted lease inheritance is not implemented')
        self.assertIsNone(self.guard.active_lease_fd())
        token = self.guard.rotate_generation(self.root)
        with self.assertRaisesRegex(RuntimeError, 'effect failed'):
            with self.guard.worker_guard(self.root, token) as fd:
                self.assertEqual(self.guard.active_lease_fd(), fd)
                self.assertIsInstance(fd, int)
                self.assertFalse(os.get_inheritable(fd))
                os.fstat(fd)
                raise RuntimeError('effect failed')
        self.assertIsNone(self.guard.active_lease_fd())
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_inherited_lease_survives_sigkill_of_original_holder(self):
        self.assertTrue(hasattr(self.guard, 'active_lease_fd'), 'trusted lease inheritance is not implemented')
        token = self.guard.rotate_generation(self.root)
        holder = self.start(INHERITING_HOLDER, LEASE_RETAINER, self.root, token)
        child_pid = int(self.line(holder))
        self.addCleanup(self.kill_orphan, child_pid)
        self.wait_file('ready')
        holder.kill()
        holder.communicate(timeout=5)
        self.assertEqual(holder.returncode, -signal.SIGKILL)
        with self.assertRaises(self.guard.ExecutionBusy):
            self.guard.rotate_generation(self.root)
        with self.assertRaises(self.guard.ExecutionBusy), self.guard.worker_guard(self.root, token):
            self.fail('recovery overlapped trusted lease retainer')
        (self.root / 'release').write_text('release')
        self.wait_file('done')
        self.assertNotEqual(self.guard.rotate_generation(self.root), token)

    def test_inherited_lease_survives_normal_exit_of_original_holder(self):
        token = self.guard.rotate_generation(self.root)
        holder = self.start(INHERITING_HOLDER, LEASE_RETAINER, self.root, token, 'exit')
        child_pid = int(self.line(holder))
        self.addCleanup(self.kill_orphan, child_pid)
        self.wait_file('ready')
        _, stderr = holder.communicate(timeout=5)
        self.assertEqual(holder.returncode, 0, stderr)
        with self.assertRaises(self.guard.ExecutionBusy):
            self.guard.rotate_generation(self.root)
        (self.root / 'release').write_text('release')
        self.wait_file('done')
        self.assertNotEqual(self.guard.rotate_generation(self.root), token)

    def test_late_worker_cannot_execute_after_generation_rotation(self):
        old = self.guard.rotate_generation(self.root)
        worker = self.start(WORKER, self.root, old, 'late')
        self.assertEqual(self.line(worker), 'waiting')
        self.guard.rotate_generation(self.root)
        worker.stdin.write('continue\n')
        worker.stdin.flush()
        stdout, stderr = worker.communicate(timeout=5)
        self.assertEqual(worker.returncode, 0, stderr)
        self.assertEqual(stdout.strip(), 'stale')
        self.assertFalse((self.root / 'effect').exists())

    def test_executing_worker_blocks_rotation_and_another_worker(self):
        token = self.guard.rotate_generation(self.root)
        worker = self.start(WORKER, self.root, token, 'holding')
        self.assertEqual(self.line(worker), 'executing')
        started = time.monotonic()
        with self.assertRaises(self.guard.ExecutionBusy):
            self.guard.rotate_generation(self.root)
        self.assertLess(time.monotonic() - started, 1)
        with self.assertRaises(self.guard.ExecutionBusy), self.guard.worker_guard(self.root, token):
            self.fail('concurrent worker reached effect')
        self.assertEqual(self.guard.current_generation(self.root), token)
        worker.stdin.write('continue\n')
        worker.stdin.flush()
        stdout, stderr = worker.communicate(timeout=5)
        self.assertEqual(worker.returncode, 0, stderr)
        self.assertEqual(stdout.strip(), 'done')
        self.assertEqual((self.root / 'effect').read_text(), 'one effect')
        self.assertNotEqual(self.guard.rotate_generation(self.root), token)

    def test_sigkill_of_parent_does_not_release_child_owned_lease(self):
        token = self.guard.rotate_generation(self.root)
        parent = self.start(SUPERVISOR, ORPHAN, self.root, token)
        child_pid = int(self.line(parent))
        self.addCleanup(self.kill_orphan, child_pid)
        self.wait_file('ready')
        parent.kill()
        parent.communicate(timeout=5)
        self.assertEqual(parent.returncode, -signal.SIGKILL)
        os.kill(child_pid, 0)
        with self.assertRaises(self.guard.ExecutionBusy):
            self.guard.rotate_generation(self.root)
        with self.assertRaises(self.guard.ExecutionBusy), self.guard.worker_guard(self.root, token):
            self.fail('recovery overlapped surviving worker')
        (self.root / 'release').write_text('release')
        self.wait_file('done')
        self.assertEqual((self.root / 'effect').read_text(), 'one effect')
        self.assertNotEqual(self.guard.rotate_generation(self.root), token)

    @staticmethod
    def kill_orphan(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def test_lease_is_released_after_exception_and_worker_sigkill(self):
        token = self.guard.rotate_generation(self.root)
        with self.assertRaisesRegex(RuntimeError, 'effect failed'):
            with self.guard.worker_guard(self.root, token):
                raise RuntimeError('effect failed')
        worker = self.start(WORKER, self.root, token, 'holding')
        self.assertEqual(self.line(worker), 'executing')
        worker.kill()
        worker.communicate(timeout=5)
        self.assertFalse((self.root / 'effect').exists())
        self.assertNotEqual(self.guard.rotate_generation(self.root), token)

    def test_control_symlinks_are_never_followed(self):
        outside = self.root.parent / 'outside'
        outside.write_text('untouched')
        for name in ('.agent-lab.generation', '.agent-lab.execution.lock'):
            control = self.root / name
            # The preceding case may have created the regular lease file.
            control.unlink(missing_ok=True)
            control.symlink_to(outside)
            try:
                with self.subTest(name=name), self.assertRaises((OSError, ValueError)):
                    self.guard.rotate_generation(self.root)
                if name == '.agent-lab.generation':
                    with self.assertRaises((OSError, ValueError)):
                        self.guard.current_generation(self.root)
            finally:
                control.unlink()
            self.assertEqual(outside.read_text(), 'untouched')
        link = self.root.parent / 'root-link'
        link.symlink_to(self.root, target_is_directory=True)
        for root in (link, str(link) + '/'):
            with self.subTest(root=root), self.assertRaises((OSError, ValueError)):
                self.guard.rotate_generation(root)

    def test_special_and_hardlinked_control_files_fail_closed(self):
        outside = self.root.parent / 'outside'
        outside.write_text('untouched')
        for name in ('.agent-lab.generation', '.agent-lab.execution.lock'):
            control = self.root / name
            control.unlink(missing_ok=True)
            os.mkfifo(control)
            try:
                with self.subTest(name=name, kind='fifo'), self.assertRaises((OSError, ValueError)):
                    self.guard.rotate_generation(self.root)
            finally:
                control.unlink()
            os.link(outside, control)
            try:
                with self.subTest(name=name, kind='hardlink'), self.assertRaises((OSError, ValueError)):
                    self.guard.rotate_generation(self.root)
            finally:
                control.unlink()
            self.assertEqual(outside.read_text(), 'untouched')


if __name__ == '__main__':
    unittest.main()
