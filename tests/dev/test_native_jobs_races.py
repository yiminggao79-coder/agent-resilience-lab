"""Old confirmation/repair publication cannot erase a newer native claim."""
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from agent_lab import native_jobs as jobs
from agent_lab.process_guard import rotate_generation
from agent_lab.store import Store
from agent_lab.tools import Tools, ToolError, UncertainEffect


class NativeJobRaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = (Path(self.tmp.name) / 'work').resolve()
        self.root.mkdir()
        self.database = Path(self.tmp.name) / 'task.sqlite'
        self.store = Store(self.database)
        self.addCleanup(self.store.close)
        self.generation = rotate_generation(self.root)
        self.intent = {'name': 'run_tests', 'args': {}, 'policy': 'unsafe', 'test_start_dir': '.'}
        self.state = {'id': uuid.uuid4().hex, 'turn_id': uuid.uuid4().hex,
                      'workspace': str(self.root), 'phase': 'tool_pending',
                      'pending': {'status': 'running', 'operation_id': uuid.uuid4().hex,
                                  'intent': self.intent},
                      'created_at': 1, 'messages': [], 'limits': {}, 'metrics': {}}
        self.store.save(self.state, 'created')

    def claim(self):
        return jobs.claim_job(self.root, self.database, self.state['id'], self.state['turn_id'],
                              self.state['pending']['operation_id'], self.generation, self.intent)

    def race_old_publish_against_new_claim(self, old, old_action, *, repair_first=False):
        paused, release, new_started, new_finished = (threading.Event() for _ in range(4))
        original_publish = jobs._publish_marker
        errors, new_references, blocked = [], [], []

        def publish(directory, reference, state, proof):
            if (threading.current_thread().name == 'old-native-publisher'
                    and reference['job_id'] == old['job_id'] and state == 'quiescent'):
                paused.set()
                if not release.wait(5):
                    raise RuntimeError('synthetic publication barrier timed out')
            return original_publish(directory, reference, state, proof)

        def publish_old():
            try:
                old_action()
            except BaseException as error:
                errors.append(('old', type(error).__name__))

        def new_claim():
            new_started.set()
            try:
                if repair_first:
                    jobs.ensure_workspace_idle(self.root, self.database)
                new_references.append(self.claim())
            except jobs.JobBlocked:
                blocked.append(True)  # A nonblocking metadata lock may refuse and retry.
            except BaseException as error:
                errors.append(('new', type(error).__name__))
            finally:
                new_finished.set()

        old_thread = threading.Thread(target=publish_old, name='old-native-publisher')
        new_thread = threading.Thread(target=new_claim, name='new-native-claim')
        with patch.object(jobs, '_publish_marker', side_effect=publish):
            try:
                old_thread.start()
                self.assertTrue(paused.wait(3), 'old producer did not reach pre-publication barrier')
                self.state['pending']['operation_id'] = uuid.uuid4().hex
                self.store.save(self.state, 'next_operation')
                new_thread.start()
                self.assertTrue(new_started.wait(2))
                # Old code publishes the new claim here. Serialized code may
                # wait, or reject for a later retry; both are legitimate.
                new_finished.wait(1)
            finally:
                release.set()
                old_thread.join(4)
                if new_thread.ident is not None:
                    new_thread.join(4)
        self.assertFalse(old_thread.is_alive())
        self.assertFalse(new_thread.is_alive())
        self.assertEqual(errors, [])
        if blocked:
            if repair_first:
                jobs.ensure_workspace_idle(self.root, self.database)
            new_references.append(self.claim())
        self.assertEqual(len(new_references), 1)
        return new_references[0]

    def assert_new_claim_still_quarantines(self, reference):
        with self.assertRaises(jobs.JobBlocked):
            jobs.require_quiescent(reference)
        marker = jobs.inspect_workspace(self.root)
        self.assertEqual(marker['job_id'], reference['job_id'],
                         'old publication replaced the newer active marker')
        self.assertEqual(marker['state'], 'claimed')
        self.assertTrue(marker['workspace_quarantined'])
        with self.assertRaises(jobs.JobBlocked):
            jobs.ensure_workspace_idle(self.root)
        tools = Tools(self.root)
        intent = tools.prepare('write_file', {'path': 'must-not-run', 'content': 'blocked effect'})
        with self.assertRaises((ToolError, UncertainEffect)):
            tools.execute(intent, 2)
        self.assertFalse((self.root / 'must-not-run').exists())

    def test_old_confirmation_read_then_publish_cannot_wash_new_active_quarantine(self):
        old = self.claim()
        jobs.confirm_job(old, 'worker_launch_failed')
        new = self.race_old_publish_against_new_claim(
            old, lambda: jobs.confirm_job(old, 'worker_launch_failed'))
        self.assert_new_claim_still_quarantines(new)

    def test_old_matching_database_repair_cannot_wash_new_active_quarantine(self):
        old = self.claim()
        with patch.object(jobs, '_publish_marker', side_effect=OSError('synthetic publication failure')):
            with self.assertRaises(jobs.JobStateError):
                jobs.confirm_job(old, 'worker_launch_failed')
        # Durable DB proof, stale claimed marker: the supported repair window.
        jobs.require_quiescent(old)
        self.assertTrue(jobs.inspect_workspace(self.root)['workspace_quarantined'])
        new = self.race_old_publish_against_new_claim(
            old, lambda: jobs.ensure_workspace_idle(self.root, self.database), repair_first=True)
        self.assert_new_claim_still_quarantines(new)


if __name__ == '__main__':
    unittest.main()
