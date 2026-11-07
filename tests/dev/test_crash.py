import json
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import unittest
from agent_lab.store import Store

REPO = Path(__file__).resolve().parents[2]


def start_and_kill(root, database, operation, stage='after_tool_effect'):
    proc = subprocess.Popen([sys.executable, '-m', 'tests.crash_worker', str(root), str(database),
                             'crash', operation, stage], cwd=str(REPO), text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            if not selector.select(8):
                raise AssertionError('child did not reach crash window')
        line = proc.stdout.readline()
        if not line:
            raise AssertionError('child exited: ' + proc.stderr.read())
        ready = json.loads(line)
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=8)
        if proc.returncode != -signal.SIGKILL:
            raise AssertionError('child not killed by SIGKILL')
        return ready['task_id']
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=8)
        proc.stdout.close()
        proc.stderr.close()


def restart(root, database, operation, mode='resume'):
    proc = subprocess.run([sys.executable, '-m', 'tests.crash_worker', str(root), str(database), mode,
                           operation, 'none'], cwd=str(REPO), text=True, capture_output=True, timeout=10)
    if proc.returncode:
        raise AssertionError(proc.stderr)
    return json.loads(proc.stdout)


class CrashTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        self.root.mkdir()
        self.database = Path(self.tmp.name) / 'state' / 'db.sqlite'

    def test_sigkill_after_write_before_receipt_resumes_in_new_process(self):
        tid = start_and_kill(self.root, self.database, 'write_file')
        self.assertEqual((self.root / 'result.txt').read_text(), 'one\n')
        before = (self.root / 'result.txt').stat().st_mtime_ns
        with Store(self.database) as store:
            self.assertEqual(store.load(tid)['pending']['status'], 'running')
            self.assertEqual(store.events(tid)[-1]['kind'], 'tool_started')
        state = restart(self.root, self.database, 'write_file')
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(state['metrics']['recoveries'], 1)
        self.assertEqual(before, (self.root / 'result.txt').stat().st_mtime_ns)

    def test_sigkill_after_append_never_repeats_and_pauses(self):
        start_and_kill(self.root, self.database, 'append_file')
        state = restart(self.root, self.database, 'append_file')
        self.assertEqual(state['phase'], 'needs_confirmation')
        self.assertEqual((self.root / 'result.txt').read_text(), 'one\n')
        again = restart(self.root, self.database, 'append_file')
        self.assertEqual(again['phase'], 'needs_confirmation')
        self.assertEqual((self.root / 'result.txt').read_text(), 'one\n')

    def test_external_modification_after_kill_is_preserved(self):
        start_and_kill(self.root, self.database, 'write_file')
        (self.root / 'result.txt').write_text('external\n')
        state = restart(self.root, self.database, 'write_file')
        self.assertEqual(state['phase'], 'needs_confirmation')
        self.assertEqual((self.root / 'result.txt').read_text(), 'external\n')

    def test_kill_in_model_request_does_not_resubmit_unknown_paid_request(self):
        start_and_kill(self.root, self.database, 'write_file', 'before_model_request')
        state = restart(self.root, self.database, 'write_file')
        self.assertEqual((state['phase'], state['reason']), ('needs_confirmation', 'model_outcome_unknown'))
        self.assertIsNone(state['metrics']['cost_usd'])
        self.assertFalse((self.root / 'result.txt').exists())

    def test_expired_budget_still_reports_changed_inflight_target_as_unknown(self):
        tid = start_and_kill(self.root, self.database, 'write_file')
        (self.root / 'result.txt').write_text('external\n')
        with Store(self.database) as store:
            state = store.load(tid)
            state['deadline'] = 0
            store.save(state, 'test_expired')
        state = restart(self.root, self.database, 'write_file')
        self.assertEqual(state['phase'], 'needs_confirmation')
        self.assertEqual((self.root / 'result.txt').read_text(), 'external\n')
