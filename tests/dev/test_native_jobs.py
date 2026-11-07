"""Native-job durable claims/quarantine gates over controlled synthetic state."""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from agent_lab import native_jobs as jobs
from agent_lab.process_guard import rotate_generation, worker_guard, current_generation
from agent_lab.store import Store


def ident():
    return uuid.uuid4().hex


class NativeJobTests(unittest.TestCase):
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
        self.state = {'id': ident(), 'turn_id': ident(), 'workspace': str(self.root),
                      'phase': 'tool_pending', 'pending': {'status': 'running', 'operation_id': ident(), 'intent': self.intent},
                      'created_at': 1, 'messages': [], 'limits': {}, 'metrics': {}}
        self.store.save(self.state, 'created')

    def claim(self, **updates):
        args = {'root': self.root, 'database': self.database, 'task_id': self.state['id'],
                'turn_id': self.state['turn_id'], 'operation_id': self.state['pending']['operation_id'],
                'generation': self.generation, 'intent': self.intent}
        return jobs.claim_job(**{**args, **updates})

    def test_claim_is_bound_idempotent_metadata_and_active_marker_blocks_admission(self):
        reference = self.claim()
        self.assertEqual(self.claim(), reference)
        self.assertEqual(reference['task_id'], self.state['id'])
        self.assertNotIn('intent', reference)
        self.assertNotIn('args', reference)
        self.assertEqual(jobs.validate_reference(reference, self.root), reference)
        with self.assertRaises(jobs.JobBlocked): jobs.ensure_workspace_idle(self.root)
        with self.assertRaises(jobs.JobBlocked): jobs.require_quiescent(reference)
        self.assertEqual(self.store.load(self.state['id']), self.state)

    def test_only_exact_active_reference_may_enter_worker_and_all_runner_gates_block(self):
        reference = self.claim()
        with worker_guard(self.root, self.generation, native_job_reference=reference):
            pass
        with self.assertRaises(jobs.JobBlocked):
            with worker_guard(self.root, self.generation): pass
        with self.assertRaises(jobs.JobBlocked): rotate_generation(self.root)
        with self.assertRaises(jobs.JobBlocked):
            with self.store.lock(self.root): pass
        foreign = Path(self.tmp.name) / 'foreign.sqlite'
        with Store(foreign) as other:
            with self.assertRaises(jobs.JobBlocked):
                with other.lock(self.root): pass
        forged = {**reference, 'operation_id': ident()}
        with self.assertRaises(jobs.JobStateError):
            jobs.ensure_worker_allowed(self.root, forged)

    def test_confirmation_is_fixed_and_immutable_then_other_db_can_enter(self):
        reference = self.claim()
        for proof in ('', 'pid_not_found', 'manual_release'):
            with self.subTest(proof=proof), self.assertRaises(jobs.JobStateError):
                jobs.confirm_job(reference, proof)
        token = ident()
        jobs.register_worker(reference, token)
        self.assertEqual(jobs.confirm_job(reference, 'all_native_joined', token), reference)
        self.assertEqual(jobs.confirm_job(reference, 'all_native_joined', token), reference)
        with self.assertRaises(jobs.JobStateError): jobs.confirm_job(reference, 'worker_launch_failed')
        jobs.require_quiescent(reference)
        jobs.ensure_workspace_idle(self.root)
        foreign = Path(self.tmp.name) / 'foreign.sqlite'
        with Store(foreign) as other:
            with other.lock(self.root): pass
        self.assertNotEqual(current_generation(self.root), self.generation)

    def test_worker_registration_is_single_owner_and_proofs_require_exact_token(self):
        reference = self.claim()
        token = ident()
        for proof in ('all_native_joined', 'no_supervisor_launch'):
            with self.subTest(proof=proof), self.assertRaises(jobs.JobStateError):
                jobs.confirm_job(reference, proof, token)
        for invalid in ('', 'x' * 32, None):
            with self.subTest(token=invalid), self.assertRaises(jobs.JobStateError):
                jobs.register_worker(reference, invalid)
        jobs.register_worker(reference, token)
        jobs.register_worker(reference, token)
        with self.assertRaises(jobs.JobStateError): jobs.register_worker(reference, ident())
        with self.assertRaises(jobs.JobStateError): jobs.confirm_job(reference, 'worker_launch_failed')
        with self.assertRaises(jobs.JobStateError): jobs.confirm_job(reference, 'no_supervisor_launch', ident())
        with self.assertRaises(jobs.JobStateError): jobs.confirm_job(reference, 'no_supervisor_launch')
        jobs.confirm_job(reference, 'no_supervisor_launch', token)
        with self.assertRaises(jobs.JobStateError): jobs.register_worker(reference, token)
        self.assertNotIn(token, json.dumps(jobs.inspect_workspace(self.root, self.database)))
        self.assertNotIn(token, (self.root / '.agent-lab.native-job').read_text())

    def test_wrong_source_generation_intent_and_unicode_root_fail_before_marker(self):
        for updates in ({'turn_id': ident()}, {'operation_id': ident()}, {'generation': ident()},
                        {'intent': {**self.intent, 'args': {'changed': True}}}):
            with self.subTest(updates=updates), self.assertRaises(jobs.JobStateError):
                self.claim(**updates)
        self.assertFalse((self.root / '.agent-lab.native-job').exists())
        self.state['pending']['status'] = 'planned'
        self.store.save(self.state, 'planned')
        with self.assertRaises(jobs.JobStateError): self.claim()
        unicode_root = self.root.parent / 'é'; unicode_root.mkdir()
        with self.assertRaises(jobs.JobStateError): self.claim(root=unicode_root)

    def test_readonly_inspect_does_not_follow_foreign_db_or_change_artifacts(self):
        reference = self.claim()
        marker = self.root / '.agent-lab.native-job'
        before = (marker.read_bytes(), hashlib.sha256(self.database.read_bytes()).hexdigest())
        report = jobs.inspect_workspace(self.root)
        self.assertEqual(report['state'], 'claimed')
        self.assertTrue(report['workspace_quarantined'])
        self.assertNotIn(str(self.database), json.dumps(report))
        with patch.object(jobs.sqlite3, 'connect', side_effect=AssertionError('foreign DB must not be read')):
            with self.assertRaises(jobs.JobBlocked):
                jobs.ensure_workspace_idle(self.root, self.root.parent / 'other.sqlite')
            jobs.inspect_workspace(self.root, self.root.parent / 'other.sqlite')
        after = (marker.read_bytes(), hashlib.sha256(self.database.read_bytes()).hexdigest())
        self.assertEqual(after, before)

    def test_confirmed_db_can_repair_failed_marker_publish_only_with_explicit_db(self):
        reference = self.claim()
        token = ident()
        jobs.register_worker(reference, token)
        with patch.object(jobs, '_publish_marker', side_effect=OSError('injected')):
            with self.assertRaises(jobs.JobStateError): jobs.confirm_job(reference, 'all_native_joined', token)
        with self.assertRaises(jobs.JobBlocked): jobs.ensure_workspace_idle(self.root)
        before = (self.root / '.agent-lab.native-job').read_bytes()
        report = jobs.inspect_workspace(self.root, self.database)
        self.assertEqual(report['state'], 'quiescent')
        self.assertEqual((self.root / '.agent-lab.native-job').read_bytes(), before)
        jobs.ensure_workspace_idle(self.root, self.database)
        jobs.ensure_workspace_idle(self.root)

    def test_claim_publish_failure_keeps_unlaunched_row_and_retry_reuses_it(self):
        with patch.object(jobs, '_publish_marker', side_effect=OSError('injected')):
            with self.assertRaises(jobs.JobStateError): self.claim()
        self.assertFalse((self.root / '.agent-lab.native-job').exists())
        row = self.store.db.execute('SELECT reference,state FROM native_jobs').fetchone()
        self.assertEqual(row[1], 'claimed')
        reference = self.claim()
        self.assertEqual(reference, json.loads(row[0]))
        self.assertEqual(self.store.load(self.state['id']), self.state)
        jobs.confirm_job(reference, 'worker_launch_failed')

    def test_confirmation_sql_error_rolls_back_and_leaves_claimed_marker(self):
        reference = self.claim()
        with self.store.db:
            self.store.db.execute("CREATE TRIGGER fail_confirm BEFORE UPDATE ON native_jobs WHEN NEW.state='quiescent' BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(jobs.JobStateError): jobs.confirm_job(reference, 'worker_launch_failed')
        self.assertEqual(jobs.inspect_workspace(self.root, self.database)['state'], 'claimed')
        with self.assertRaises(jobs.JobBlocked): jobs.ensure_workspace_idle(self.root, self.database)
        with self.store.db: self.store.db.execute('DROP TRIGGER fail_confirm')
        jobs.confirm_job(reference, 'worker_launch_failed')

    def test_marker_lock_is_private_and_old_idle_inspection_does_not_create_it(self):
        lock = self.root / '.agent-lab.native-job.lock'
        jobs.ensure_workspace_idle(self.root)
        self.assertEqual(jobs.inspect_workspace(self.root)['state'], 'none')
        self.assertFalse(lock.exists())
        lock.write_bytes(b'')
        lock.chmod(0o644)
        with self.assertRaises(jobs.JobStateError): self.claim()
        self.assertFalse((self.root / '.agent-lab.native-job').exists())
        lock.unlink()
        reference = self.claim()
        self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
        jobs.confirm_job(reference, 'worker_launch_failed')

    def test_busy_marker_publisher_has_bounded_admission_wait(self):
        descriptor = os.open(self.root / '.agent-lab.native-job.lock', os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        release = threading.Timer(.2, lambda: fcntl.flock(descriptor, fcntl.LOCK_UN))
        release.start()
        started = time.monotonic()
        try:
            with patch.object(jobs, '_LOCK_WAIT_SECONDS', .04, create=True):
                with self.assertRaises(jobs.JobBlocked): self.claim()
            self.assertLess(time.monotonic() - started, 1)
            self.assertFalse((self.root / '.agent-lab.native-job').exists())
        finally:
            release.join(1)
            os.close(descriptor)

    def test_missing_explicit_database_is_never_created(self):
        missing = self.root.parent / 'missing.sqlite'
        with self.assertRaises(jobs.JobStateError): self.claim(database=missing)
        self.assertFalse(missing.exists())
        self.assertFalse((self.root / '.agent-lab.native-job').exists())

    def test_corrupt_linked_or_group_readable_marker_fails_closed(self):
        reference = self.claim()
        marker = self.root / '.agent-lab.native-job'
        original = marker.read_bytes()
        marker.write_text('{malformed')
        with self.assertRaises(jobs.JobBlocked): jobs.ensure_workspace_idle(self.root)
        marker.write_bytes(original); marker.chmod(0o644)
        with self.assertRaises(jobs.JobBlocked): jobs.ensure_workspace_idle(self.root)
        marker.chmod(0o600)
        alias = self.root / 'marker-alias'; os.link(marker, alias)
        with self.assertRaises(jobs.JobBlocked): jobs.ensure_workspace_idle(self.root)
        alias.unlink(); marker.unlink(); marker.symlink_to(self.database)
        with self.assertRaises(jobs.JobBlocked): jobs.ensure_workspace_idle(self.root)

    def test_old_confirmation_retry_cannot_overwrite_new_active_marker(self):
        old = self.claim()
        jobs.confirm_job(old, 'worker_launch_failed')
        self.state['pending']['operation_id'] = ident()
        self.store.save(self.state, 'next_operation')
        new = self.claim()
        jobs.confirm_job(old, 'worker_launch_failed')
        self.assertEqual(jobs.inspect_workspace(self.root)['job_id'], new['job_id'])
        with self.assertRaises(jobs.JobBlocked): jobs.ensure_workspace_idle(self.root)


if __name__ == '__main__':
    unittest.main()
